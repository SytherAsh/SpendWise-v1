"""
sms_parser.py — Hardened SMS parsing with centralized compiled regex.

All patterns are compiled once at module load for performance.
Returns a ParsedTransaction with all fields safely extracted.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Dict, Optional, Any

from app.schemas.transaction import ParsedTransaction

logger = logging.getLogger(__name__)


# -------------------------------------------------------
# Bank sender-ID → bank name mapping (centralized)
# -------------------------------------------------------
SENDER_TO_BANK: Dict[str, str] = {
    # HDFC
    "HDFCBK": "HDFC", "HDFCBN": "HDFC", "HDFCSC": "HDFC",
    # SBI
    "SBIINB": "SBI",  "SBIPSG": "SBI",  "SBIUPI": "SBI",
    "SBISMS": "SBI",
    # ICICI
    "ICICIB": "ICICI", "ICICIS": "ICICI", "ICICIT": "ICICI",
    # AXIS
    "AXISBK": "AXIS", "AXISBN": "AXIS",
    # KOTAK
    "KOTAKB": "KOTAK", "KOTAKS": "KOTAK",
    # YES
    "YESBNK": "YES", "YESBAK": "YES",
    # Payments / wallets
    "PAYTM":   "PAYTM",
    "GPAY":    "GPAY",
    "PHONEPE": "PHONEPE",
    # Other banks
    "INDBNK": "INDIAN",
    "BOIIND": "BOI",
    "PNBSMS": "PNB",
    "CENTBK": "CENTRAL",
    "CANBNK": "CANARA",
    "UBOI":   "UNION",
    "IDFCFB": "IDFC",
    "FEDBK":  "FEDERAL",
    "BANDNK": "BANDHAN",
    "RBLBNK": "RBL",
    "DENABN": "DENA",
    "SYNBNK": "SYNDICATE",
}

# Body-level bank name scan (order matters — longer first to avoid partial matches)
BODY_BANK_KEYWORDS = [
    ("PHONEPE", "PHONEPE"),
    ("HDFC",    "HDFC"),
    ("ICICI",   "ICICI"),
    ("AXIS",    "AXIS"),
    ("KOTAK",   "KOTAK"),
    ("BANDHAN", "BANDHAN"),
    ("YES BANK","YES"),
    ("SBI",     "SBI"),
    ("PNB",     "PNB"),
    ("BOI",     "BOI"),
    ("CANARA",  "CANARA"),
    ("UNION",   "UNION"),
    ("IDFC",    "IDFC"),
    ("FEDERAL", "FEDERAL"),
    ("PAYTM",   "PAYTM"),
    ("GPAY",    "GPAY"),
]


# -------------------------------------------------------
# Centralized compiled regex patterns
# -------------------------------------------------------

# Amount: Rs.500, Rs 1,500.00, INR 2000, ₹3,000.50, "debited by 500"
AMOUNT_PATTERN = re.compile(
    r"(?:Rs\.?\s*|INR\s*|₹\s*)([0-9,]+(?:\.[0-9]{1,2})?)"
    r"|(?:debited by|credited (?:with|by))\s+([0-9,]+(?:\.[0-9]{1,2})?)",
    re.IGNORECASE | re.UNICODE,
)

# Available balance — matches "Avl Bal Rs.12,345.67", "balance is INR 500", "Avl Amt Rs.100"
BALANCE_PATTERN = re.compile(
    r"(?:avl\.?\s*(?:bal(?:ance)?|amt)|avail(?:able)?\s*bal(?:ance)?|balance\s+(?:is\s+)?)"
    r"[\s:]*(?:Rs\.?\s*|INR\s*|₹\s*)([0-9,]+(?:\.[0-9]{1,2})?)",
    re.IGNORECASE | re.UNICODE,
)

# Account / card suffix — "a/c XX1234", "acct *5678", "card ending 9012", "A/c X7686"
ACCOUNT_PATTERN = re.compile(
    r"(?:a/?c|acct|account|card)\s*(?:no\.?\s*)?(?:[X*x]+\s*)([0-9]{4,6})"
    r"|(?:ending|last\s+(?:4|four)\s+digits?)\s+(?:with\s+)?([0-9]{4})",
    re.IGNORECASE,
)

# UPI ID — user@oksbi, abc@ybl, name@paytm  (exclude email TLDs)
UPI_PATTERN = re.compile(
    r"\b([a-zA-Z0-9._-]+@(?!(?:com|org|net|in|co|gov|edu)\b)[a-zA-Z]{2,20})\b",
    re.IGNORECASE,
)

# Transaction mode — order longest first to avoid POS → OS mishap
MODE_PATTERN = re.compile(
    r"\b(NETBANKING|NET\s*BANKING|PHONEPE|PAYTM|NEFT|RTGS|IMPS|NACH|CASH"
    r"|WALLET|CHEQUE|CHQ|ACH|ECS|UPI|ATM|POS|CARD)\b",
    re.IGNORECASE,
)

# Normalize extracted mode tokens
MODE_ALIASES: Dict[str, str] = {
    "NET BANKING": "NETBANKING",
    "CHQ": "CHEQUE",
    "POS": "CARD",         # POS terminal = card transaction
}

# Debit signal words
DEBIT_KEYWORDS = re.compile(
    r"\b(debit(?:ed)?|spent|paid|sent|withdraw(?:n|al)?|purchase(?:d)?"
    r"|charged|transfer(?:red)?|deducted)\b",
    re.IGNORECASE,
)

# Credit signal words
CREDIT_KEYWORDS = re.compile(
    r"\b(credit(?:ed)?|received|deposited|refund(?:ed)?|cashback"
    r"|reversed|added)\b",
    re.IGNORECASE,
)

# Financial message gate — must match at least one of these to proceed
FINANCIAL_KEYWORDS = re.compile(
    r"(?:debit|credit|UPI|IMPS|NEFT|RTGS|Rs\.?|INR|₹|transaction|payment"
    r"|withdraw|a/?c\s*[X*]*\d{4}|balance|transfer|ATM|POS|debited by|credited)",
    re.IGNORECASE,
)

# Reference / transaction ID — common Indian bank SMS formats
REF_PATTERN = re.compile(
    r"(?:Ref(?:\s*No)?|RefNo|Txn\s*(?:ID|No\.?)|Transaction\s*(?:ID|Number)|UPI\s*Ref"
    r"|TxnNo|UTR|IMPS\s*Ref)[:\s-]*([A-Za-z0-9]{6,20})",
    re.IGNORECASE,
)

# Recipient extraction — "to NAME", "paid to SHOP", "trf to ENTITY"
RECIPIENT_DEBIT_PATTERNS = [
    re.compile(
        r"(?:trf\s+to|transfer(?:red)?\s+to|paid\s+to|sent\s+to|to)\s+"
        r"([A-Za-z][A-Za-z0-9 &'._-]{1,35})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:withdrawn\s+at)\s+([A-Za-z][A-Za-z0-9 &'._-]{1,35})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:and\s+a/c\s+)([A-Za-z0-9*Xx]+)(?:\s+credited)",
        re.IGNORECASE,
    ),
]

RECIPIENT_CREDIT_PATTERNS = [
    # SBI IMPS credit: "...by a/c linked to mobile 9XXXXXX567-SAMEER BALIRAM SAWA (IMPS Ref no ...".
    # The payer name sits between the masked mobile (ending in '-') and the parenthesised ref.
    # Highest priority: the generic "by NAME" pattern otherwise backtracks onto the safety
    # line "If not done by you, call ..." and returns "you".
    re.compile(
        r"linked\s+to\s+mobile\s+[0-9Xx]+\s*-\s*([A-Za-z][A-Za-z .]{1,35}?)\s*(?:\(|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:transfer(?:red)?\s+from|received\s+from|from|\bby)\s+"
        r"(?!\s*(?:Rs\.?|INR|₹))([A-Za-z][A-Za-z0-9 &'._-]{1,35})",
        re.IGNORECASE,
    ),
]

# Never a real counterparty — these come from safety boilerplate ("If not done by you")
# or partial matches on structural words.
RECIPIENT_JUNK = {"you", "u", "your", "a", "a/c", "ac", "the", "call", "block", "upi"}

# Words that terminate a recipient name
RECIPIENT_TERMINATORS = re.compile(
    r"\s+(?:on|ref|via|txn|at|for|Refno|using|through|dated|dt|a/c|ac|account|avl|balance|info|from)\b",
    re.IGNORECASE,
)

# Spam/ad message indicators (sender or body)
SPAM_SENDER_PATTERN = re.compile(
    r"(?:AIRTEL|JIO|VI|650025|BURKIN|ZUDIO|PTRENG|TATASKY|TATSKY|AMAZON)",
    re.IGNORECASE,
)
SPAM_BODY_PATTERN = re.compile(
    r"(?:recharge|data\s*pack|data\s*loan|validity|playlist|subscription|claim"
    r"|OTT|netflix|jiohotstar|zee5|apple\s*music|free\s*access|call\s*alert"
    r"|hello\s*tune|missed\s*call|offer\s*expires|cashback\s*offer|discount\s*code"
    r"|amazon\.in|flat\s*₹|chance\s*to\s*win|assured|free\s*delivery|win\s*₹)",
    re.IGNORECASE,
)

# Raw phone-number sender (e.g. "+916354614184") — legitimate banks/services always
# use alphanumeric sender IDs (SBIUPI, HDFCBK) or short codes (56321), never a raw
# 10+-digit mobile number. A phone-number sender is a strong scam signal.
PHONE_SENDER_PATTERN = re.compile(r"^\+?\d{10,}$")

# URL shorteners — legit transaction-confirmation SMS never carry links. Common in
# betting/loan/phishing spam ("...Withdraw now h0a.us/xyz").
URL_SHORTENER_PATTERN = re.compile(
    r"(?:h0a\.us|p-y\.tm|2lm\.in|ha2\.online|bit\.ly|tinyurl|t\.co/|goo\.gl"
    r"|\b[a-z0-9-]+\.(?:us|online|xyz|link|club|site|top)/)",
    re.IGNORECASE,
)

# Betting / gambling / fake-winnings spam that mimics transaction wording
# ("Transaction successfully done of Rs.98,650 to your Rummy A/C. Withdraw it now").
GAMBLING_PATTERN = re.compile(
    r"\b(?:rummy|teen\s*patti|casino|betting|bet\s*now|bonus\s*a/?c"
    r"|withdraw\s*(?:it|now)|winning\s*amount|lottery|jackpot|rummy\s*bank)\b",
    re.IGNORECASE,
)

# Collect / request-money — a request to pay, not a completed transaction. The amount
# is conditional ("On approving, Rs.345 will be debited" / "has requested Rs500 frm u.
# Once approved..."), so it must never be counted as a real transaction.
COLLECT_REQUEST_PATTERN = re.compile(
    r"(?:has\s+requested|requested\s+(?:money|rs\.?|inr|₹)|requested\s+money\s+(?:from|on)"
    r"|collect\s+request|(?:on|once)\s+approv(?:ing|ed)|will\s+be\s+debited\s+from\s+your)",
    re.IGNORECASE,
)

# Genuine completed OUTGOING payments that don't use "debited" wording — telecom
# recharges, internet-banking transfers, booking payments. These are real spending
# and were being dropped as promo/unknown; recover them as DEBIT transactions.
PAYMENT_CONFIRMATION_PATTERN = re.compile(
    r"(?:recharge of (?:inr|rs)[\d.,\s]+is success"
    r"|recharge of rs[\d.,\s]+successfully credited"
    r"|prepaid recharge of rs[\d.,\s]+is success"
    r"|we have processed rs[\d.,\s]+for your"
    r"|\bINB txn of rs"
    r"|thank you for the payment of rs)",
    re.IGNORECASE,
)

# Telecom recharge confirmation — used to force DEBIT direction, set the recipient to
# the operator, and SUPPRESS ref extraction (the "Order Id"/"Transaction ID" in these
# is a per-SMS telecom id, not a bank reference; keeping it would make the 2-3 SMS of
# one recharge look like distinct transactions and defeat dedup).
TELECOM_CONFIRMATION_PATTERN = re.compile(
    r"(?:for your airtel mobile|to your airtel|airtel prepaid|prepaid recharge of"
    r"|recharge of (?:inr|rs)[\d.,\s]+is success)",
    re.IGNORECASE,
)

# Autopay/mandate SETUP — no money moves at creation; the real recurring debit arrives
# later as its own SMS. Excluded (unless the message is itself a debit).
MANDATE_SETUP_PATTERN = re.compile(
    r"(?:UPI[-\s]?Mandate for|e-?mandate\b|mandate .{0,25}(?:created|set\s?up)|autopay .{0,25}(?:created|set\s?up))",
    re.IGNORECASE,
)

# Failed transaction indicators
FAILURE_GATE = re.compile(
    r"\b(fail(?:ed)?|decline(?:d)?|unsuccessful|reversed|rejected|cancelled)\b",
    re.IGNORECASE,
)

# Explicit message labels used across the pipeline
SMS_LABELS = {
    "FINANCIAL_TRANSACTION",
    "FAILED_TRANSACTION",
    "OTP",
    "PROMOTIONAL",
    "BANKING_ALERT",
    "BILL_REMINDER",
    "UNKNOWN",
}

OTP_PATTERN = re.compile(
    r"\b(?:otp|one[-\s]*time\s*password|verification\s*code|auth(?:entication)?\s*code|login\s*code|passcode|secure\s*code)\b",
    re.IGNORECASE,
)

PROMOTIONAL_PATTERN = re.compile(
    r"\b(?:cashback|offer(?:s)?|discount|promo(?:tion)?|loan|credit\s*card|insurance|recharge\s*offer|sale|deal|ad(?:vertisement)?|apply\s*now|buy\s*now|subscribe|install\s*app|win\s*\w*|free\s*delivery|festival\s*offer|shopping\s*offer)\b",
    re.IGNORECASE,
)

BANKING_ALERT_PATTERN = re.compile(
    r"\b(?:kyc\s*reminder|statement\s*available|e[-\s]*statement|password\s*changed|card\s*blocked|card\s*dispatched|security\s*(?:alert|notification)|account\s*locked|otp\s*for\s*login|profile\s*updated|address\s*updated|mobile\s*number\s*updated)\b",
    re.IGNORECASE,
)

FAILED_TRANSACTION_PATTERN = re.compile(
    r"\b(?:failed|declined|pending|reversed|timed\s*out|cancelled|canceled|unsuccessful|rejected|could\s*not\s*process|unable\s*to\s*process)\b",
    re.IGNORECASE,
)

BILL_REMINDER_PATTERN = re.compile(
    r"\b(?:bill\s*reminder|payment\s*due|due\s*date|outstanding\s*amount|emi\s*due|utility\s*bill|electricity\s*bill|water\s*bill|gas\s*bill|statement\s*due|reminder\s*to\s*pay)\b",
    re.IGNORECASE,
)

FINANCIAL_CONTEXT_PATTERN = re.compile(
    r"\b(?:debited?|credited?|withdrawn?|withdrawal|deposited?|transferred?|transfer|paid|received|sent|purchase(?:d)?|charged|deducted|refund(?:ed)?|cashback|imps|neft|rtgs|upi|atm|pos|card|transaction|payment)\b",
    re.IGNORECASE,
)

BALANCE_WORD_PATTERN = re.compile(
    r"\b(?:balance|avl\.?\s*bal|available\s*balance|avail(?:able)?\s*bal|remaining\s*balance)\b",
    re.IGNORECASE,
)

MERCHANT_NOISE_PATTERN = re.compile(
    r"\b(?:LTD|LIMITED|PVT|PRIVATE|SERVICES|SERVICE|CITY|MUMBAI|DELHI|PUNE|BANGALORE|BENGALURU|HYDERABAD|CHENNAI|KOLKATA|NOIDA|GURGAON|CORP|CORPORATION|ENTERPRISES|INDIA)\b",
    re.IGNORECASE,
)



# -------------------------------------------------------
# Public helpers
# -------------------------------------------------------

def normalize_timestamp(timestamp_ms: Any) -> Optional[str]:
    """
    Convert epoch milliseconds or ISO string to UTC ISO 8601 string.

    Returns a timezone-aware string like "2026-01-01T15:37:29+00:00"
    or None if conversion fails.
    """
    from datetime import datetime, timezone

    if timestamp_ms is None:
        return None

    # Try epoch milliseconds
    try:
        ms = float(timestamp_ms)
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError, OSError):
        pass

    # Try ISO string
    try:
        s = str(timestamp_ms).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, AttributeError):
        pass

    return None


def is_valid_year(timestamp_ms: Any, target_year: int = 2026) -> bool:
    """Check if a timestamp belongs to the target year."""
    if not timestamp_ms:
        return False
    iso = normalize_timestamp(timestamp_ms)
    if iso:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(iso)
            return dt.year == target_year
        except Exception:
            pass
    return str(target_year) in str(timestamp_ms)


def sms_body_hash(body: Optional[str], sender: Optional[str] = None) -> str:
    """SHA-256 of the normalised body+sender — used as a dedup key."""
    text = f"{(sender or '').strip().upper()}|{(body or '').strip()}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def _keywords_hit(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _clean_entity_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    cleaned = _normalize_text(name)
    cleaned = re.sub(r"[\s\-_,.]+$", "", cleaned)
    cleaned = MERCHANT_NOISE_PATTERN.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_.,")
    cleaned = re.sub(r"\b(?:A/C|AC|ACC|ACCOUNT|CARD|BALANCE|AVL|AVAILABLE|AVAIL)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _extract_transaction_amount(body: str, direction: Optional[str]) -> Optional[float]:
    """Extract the transaction amount with a small amount of contextual scoring.

    The balance figure ("Avl Bal Rs 642.92") is the classic trap — in some SBI
    formats it sits far enough from the transaction amount that both share the
    same context window, and a naive scorer picks the (usually larger) balance.
    We resolve that directly: BALANCE_PATTERN already pinpoints the balance number,
    so any amount candidate occupying that exact span is excluded outright.
    """
    text = body or ""

    # Spans of the balance number(s) — candidates overlapping these are the balance.
    balance_spans = [m.span(1) for m in BALANCE_PATTERN.finditer(text)]

    def _overlaps_balance(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= bs[0] or span[0] >= bs[1]) for bs in balance_spans)

    candidates = []
    for match in AMOUNT_PATTERN.finditer(text):
        grp = 1 if match.group(1) else 2
        raw = match.group(grp)
        if not raw:
            continue
        if _overlaps_balance(match.span(grp)):
            continue  # this number is the available balance, not the transaction amount
        context_start = max(0, match.start() - 70)
        context_end = min(len(text), match.end() + 70)
        context = text[context_start:context_end]
        score = 0
        lower_context = context.lower()
        if direction == "DEBIT" and DEBIT_KEYWORDS.search(context):
            score += 5
        if direction == "CREDIT" and CREDIT_KEYWORDS.search(context):
            score += 5
        if re.search(r"\b(debited|credited|paid|sent|received|transferred|withdrawn|deposited)\b", lower_context):
            score += 3
        if BALANCE_WORD_PATTERN.search(context):
            score -= 3
        if re.search(r"\b(refund|cashback|salary|stipend|interest)\b", lower_context):
            score += 1
        try:
            amount = _parse_amount(raw)
        except ValueError:
            continue
        # position is a stable tie-breaker: the transaction amount precedes the
        # balance in Indian bank SMS, so prefer the earlier match on a score tie.
        candidates.append((score, -match.start(), amount))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _score_financial_confidence(body: str, parsed_amount: Optional[float], direction: Optional[str]) -> float:
    score = 0.0
    lower = body.lower()
    if parsed_amount is not None:
        score += 0.35
    if direction in {"DEBIT", "CREDIT"}:
        score += 0.30
    if FINANCIAL_CONTEXT_PATTERN.search(body):
        score += 0.20
    if re.search(r"\b(refno|ref\s*no|txn|utr|transaction\s*(?:id|number)|imps\s*ref|upi\s*ref)\b", lower):
        score += 0.05
    if re.search(r"@", body):
        score += 0.05
    if re.search(r"\b(account|a/?c|bank|branch)\b", lower):
        score += 0.05
    return min(score, 0.98)


def _classify_sms(body: Optional[str], sender: Optional[str] = None) -> tuple[str, float, str]:
    text = _normalize_text(body)
    sender_text = _normalize_text(sender).upper()
    lower = text.lower()

    if not text:
        return "UNKNOWN", 0.0, "Empty body"

    if OTP_PATTERN.search(text):
        return "OTP", 0.99, "Matched OTP or verification pattern"

    # Scam gate — reject fake-transaction spam before it can reach the financial path.
    # These mimic transaction wording (amount + "credited"/"done") but carry huge fake
    # sums that would pollute analytics; catch them by sender/link/gambling signals.
    if PHONE_SENDER_PATTERN.match(sender_text.strip()):
        return "PROMOTIONAL", 0.97, "Raw phone-number sender (scam/spam, never a real bank)"
    if URL_SHORTENER_PATTERN.search(text):
        return "PROMOTIONAL", 0.96, "Contains a URL shortener (spam/phishing, not a bank SMS)"
    if GAMBLING_PATTERN.search(text):
        return "PROMOTIONAL", 0.96, "Betting/gambling spam mimicking a transaction"

    # Collect / request-money — a request to pay, not a completed transaction.
    if COLLECT_REQUEST_PATTERN.search(text):
        return "UNKNOWN", 0.20, "Collect/request-money (conditional, not a completed transaction)"

    # Mandate/autopay SETUP — not a payment (no money moved). Exclude, unless the
    # message is itself an actual debit (a real recurring charge mentioning "mandate").
    if MANDATE_SETUP_PATTERN.search(text) and not re.search(r"\bdebited\b", lower):
        return "UNKNOWN", 0.30, "Mandate/autopay setup (no money moved yet)"

    # Completed payment/recharge confirmations that lack "debited" wording — recover
    # these as real DEBIT transactions (they were previously dropped as promo/unknown).
    # Runs BEFORE the promo gate so Airtel recharge confirmations aren't lost to it.
    if PAYMENT_CONFIRMATION_PATTERN.search(text) and _extract_transaction_amount(text, "DEBIT") is not None:
        return "FINANCIAL_TRANSACTION", 0.90, "Completed payment/recharge confirmation"

    # If promotional cues are strong (cashback, win, use code, offer)
    # prefer PROMOTIONAL even when an amount appears — these are marketing
    # messages that mention amounts, not real transactions.
    promo_hit = PROMOTIONAL_PATTERN.search(text) or SPAM_BODY_PATTERN.search(text) or (sender_text and SPAM_SENDER_PATTERN.search(sender_text))
    promo_explicit = re.search(r"\b(use code|code:|win\b|cashback|assured|chance to win|apply now|buy now|order now|offer|coupon)\b", text, re.IGNORECASE)
    if promo_hit:
        # If there's a clear transaction indicator (ref/txn/UPI/payment summary), keep checking;
        # otherwise treat as promotional. If explicit promo keywords are present, classify promotional.
        if not (REF_PATTERN.search(text) or re.search(r"\b(payment summary|payment successful|transaction of|transaction is successful|your transaction|payment processed|you paid|payment of|you paid using|you paid via)\b", text, re.IGNORECASE)):
            return "PROMOTIONAL", 0.96, "Matched promotional/spam pattern (marketing with amount)"
        if promo_explicit:
            return "PROMOTIONAL", 0.96, "Explicit promotional keywords present"

    # Demote messages that talk about potential/conditional payments (not confirmed transactions).
    # Keep this narrow: many valid bank SMS include safety disclaimers like "If not you, call ...".
    conditional_pattern = re.compile(
        r"\b(payment is being processed|in case the payment fails|will be added as previous dues|"
        r"may be added as previous dues|added as previous dues|payment pending|request(?:ed)? money from you)\b",
        re.IGNORECASE,
    )
    if conditional_pattern.search(text):
        return "UNKNOWN", 0.20, "Conditional or uncertain payment language detected"

    if BANKING_ALERT_PATTERN.search(text):
        return "BANKING_ALERT", 0.94, "Matched banking alert pattern"

    if FAILED_TRANSACTION_PATTERN.search(text):
        return "FAILED_TRANSACTION", 0.97, "Matched failed transaction pattern"

    if BILL_REMINDER_PATTERN.search(text):
        return "BILL_REMINDER", 0.92, "Matched bill reminder pattern"

    direction = None
    if re.search(r"\bcredited\b", lower):
        direction = "CREDIT"
    elif re.search(r"\bdebited\b", lower):
        direction = "DEBIT"
    elif CREDIT_KEYWORDS.search(text):
        direction = "CREDIT"
    elif DEBIT_KEYWORDS.search(text):
        direction = "DEBIT"

    amount = _extract_transaction_amount(text, direction)
    if amount is not None and direction and FINANCIAL_CONTEXT_PATTERN.search(text):
        confidence = _score_financial_confidence(text, amount, direction)
        reason = "Amount, direction, and financial context detected"
        return "FINANCIAL_TRANSACTION", confidence, reason

    return "UNKNOWN", 0.25, "Insufficient financial evidence"


# -------------------------------------------------------
# Private extraction helpers
# -------------------------------------------------------

def _parse_amount(text: str) -> float:
    """Remove commas and convert to float."""
    return float(text.replace(",", "").strip())


def _detect_bank_from_sender(sender: Optional[str]) -> Optional[str]:
    """Map an SMS sender code to a known bank name."""
    if not sender:
        return None
    upper = sender.upper().strip()
    for code, bank in SENDER_TO_BANK.items():
        if code in upper:
            return bank
    return None


def _detect_bank_from_body(body: str) -> Optional[str]:
    """Scan body text for known bank names."""
    upper = body.upper()
    for keyword, bank in BODY_BANK_KEYWORDS:
        if keyword in upper:
            return bank
    return None


def _extract_first_amount(body: str) -> Optional[float]:
    """
    Return the first (usually transaction) amount from the SMS body.
    Handles Rs., INR, ₹ prefixes and 'debited by / credited with N' patterns.
    """
    return _extract_transaction_amount(body, None)


def _extract_balance(body: str) -> Optional[float]:
    """Extract available balance if present."""
    m = BALANCE_PATTERN.search(body)
    if m:
        try:
            return _parse_amount(m.group(1))
        except ValueError:
            pass
    return None


def _extract_account_suffix(body: str) -> Optional[str]:
    """Return last-4 digits of account/card mentioned in the SMS."""
    m = ACCOUNT_PATTERN.search(body)
    if m:
        digits = m.group(1) or m.group(2)
        if digits:
            return digits[-4:]
    return None


def _extract_upi_id(body: str) -> Optional[str]:
    """Extract UPI VPA from the SMS body."""
    m = UPI_PATTERN.search(body)
    if m:
        return m.group(1)
    return None


def _extract_mode(body: str) -> Optional[str]:
    """Extract transaction mode (UPI, IMPS, NEFT …) from body."""
    m = MODE_PATTERN.search(body)
    if m:
        raw = m.group(1).upper().replace(" ", "")
        # Resolve aliases
        return MODE_ALIASES.get(raw, raw)
    return None


def _extract_ref_id(body: str) -> Optional[str]:
    """Extract reference / transaction ID from the SMS body."""
    m = REF_PATTERN.search(body)
    if m:
        return m.group(1).strip()
    return None


def _extract_recipient(body: str, direction: Optional[str]) -> Optional[str]:
    if not direction:
        return None

    patterns = RECIPIENT_DEBIT_PATTERNS if direction == "DEBIT" else RECIPIENT_CREDIT_PATTERNS
    for p in patterns:
        match = p.search(body)
        if match:
            # Group 1 is the name
            name = match.group(1).strip()

            # Apply terminators to cut off trailing garbage
            term_match = RECIPIENT_TERMINATORS.search(name)
            if term_match:
                name = name[:term_match.start()].strip()

            # Clean trailing punctuation
            name = re.sub(r"[-_.,]+$", "", name).strip()

            # Reject boilerplate/junk matches (e.g. "you" from "if not done by you")
            # and fall through to the next pattern instead of returning garbage.
            if not name or name.lower() in RECIPIENT_JUNK:
                continue
            return _clean_entity_name(name)

    return None

def _fallback_merchant_extraction(body: str, sender: str, bank: Optional[str]) -> Optional[str]:
    # 1. Look for known merchants in body
    known_merchants = re.compile(
        r"\b(Rapido|Uber|Ola|Swiggy|Zomato|Amazon|Flipkart|Myntra|Airtel|Jio|Vi|Blinkit|Zepto)\b", 
        re.IGNORECASE
    )
    match = known_merchants.search(body)
    if match:
        return _clean_entity_name(match.group(1).title())
    
    # 2. Use sender if it's not a generic bank sender
    if sender and sender.strip() and sender.lower() != "partner":
        # Clean sender like "AD-SBIUPI-S" -> "SBIUPI"
        clean_sender = re.sub(r"^[A-Z]{2}-|-?[A-Z]$", "", sender).strip()
        # If the clean sender is just the bank (like SBIUPI), don't use it as merchant
        if bank and clean_sender.upper().startswith(bank.upper()):
            return None
        if "UPI" in clean_sender.upper():
            return None
        return _clean_entity_name(clean_sender)

    return None


def _fallback_account_recipient(body: str, user_account_suffix: Optional[str]) -> Optional[str]:
    """Find an alternative account number in the body that is NOT the user's account."""
    matches = list(ACCOUNT_PATTERN.finditer(body))
    for match in matches:
        acc_num = match.group(1) or match.group(2)
        if acc_num and acc_num != user_account_suffix:
            return f"A/C *{acc_num}"
    return None


def _is_spam(body: str, sender: Optional[str]) -> bool:
    """Return True if the message looks like an ad / promotional spam."""
    sender_text = sender or ""
    if sender_text and SPAM_SENDER_PATTERN.search(sender_text):
        # Some bank senders share roots with telcos — only flag if no financial kwds
        if not FINANCIAL_KEYWORDS.search(body or ""):
            return True
    if SPAM_BODY_PATTERN.search(body or ""):
        return True
    return False


# -------------------------------------------------------
# Public entry point
# -------------------------------------------------------

def parse_sms_body(body: Optional[str], sender: Optional[str] = None) -> ParsedTransaction:
    """
    Parse a raw SMS or notification body into structured transaction fields.

    Args:
        body:   Full raw SMS text.
        sender: SMS sender ID (e.g. "JD-SBIUPI-S").

    Returns:
        ParsedTransaction — all fields are Optional; is_financial=False if not financial.
    """
    result = ParsedTransaction()
    text = _normalize_text(body)
    label, confidence, reason = _classify_sms(text, sender)
    result.classification_label = label if label in SMS_LABELS else "UNKNOWN"
    result.classification_confidence = confidence
    result.classification_reason = reason

    if not text:
        return result

    if result.classification_label != "FINANCIAL_TRANSACTION":
        result.is_financial = False
        return result

    result.is_financial = True

    # --- Amount ---
    result.amount = _extract_first_amount(text)

    # --- Direction ---
    body_lower = text.lower()
    # Telecom recharge confirmations say the phone balance was "credited" — but from
    # the user's bank side that is money OUT. Force DEBIT before the "credited" check.
    if TELECOM_CONFIRMATION_PATTERN.search(text) and "debited" not in body_lower:
        result.direction = "DEBIT"
    elif "credited" in body_lower:
        result.direction = "CREDIT"
    elif "debited" in body_lower:
        result.direction = "DEBIT"
    elif DEBIT_KEYWORDS.search(text):
        result.direction = "DEBIT"
    elif CREDIT_KEYWORDS.search(text):
        result.direction = "CREDIT"
    elif PAYMENT_CONFIRMATION_PATTERN.search(text):
        # INB/booking confirmations are money going OUT.
        result.direction = "DEBIT"

    # Sanity: if no amount or no direction, demote to UNKNOWN for downstream review.
    if result.amount is None or result.direction is None:
        result.is_financial = False
        result.classification_label = "UNKNOWN"
        result.classification_confidence = min(result.classification_confidence, 0.35)
        result.classification_reason = "Insufficient financial evidence after extraction"
        return result

    # --- Bank ---
    result.bank = _detect_bank_from_sender(sender) or _detect_bank_from_body(text)

    # --- UPI ID ---
    result.upi_id = _extract_upi_id(text)

    # --- Transaction mode ---
    result.transaction_mode = _extract_mode(text)

    # --- Account suffix ---
    result.account_suffix = _extract_account_suffix(text)

    # --- Balance ---
    result.balance_after = _extract_balance(text)

    # --- Reference ID ---
    # For pure telecom recharge confirmations, the "Order Id"/"Transaction ID" is a
    # per-SMS telecom id, not a bank reference — suppress it so the 2-3 SMS of one
    # recharge stay dedupable (the pipeline collapses them by amount + target phone).
    is_telecom_confirmation = (
        TELECOM_CONFIRMATION_PATTERN.search(text) and "debited" not in body_lower
    )
    result.ref_id = None if is_telecom_confirmation else _extract_ref_id(text)

    # --- Recipient ---
    if is_telecom_confirmation:
        result.recipient_name = "Airtel"
    else:
        result.recipient_name = _extract_recipient(text, result.direction)
        if not result.recipient_name:
            result.recipient_name = _fallback_merchant_extraction(text, sender or "", result.bank)
        if not result.recipient_name:
            result.recipient_name = _fallback_account_recipient(text, result.account_suffix)

    result.recipient_name = _clean_entity_name(result.recipient_name)

    # Recompute confidence for successful financial extraction with richer signals.
    result.classification_confidence = max(
        result.classification_confidence,
        _score_financial_confidence(text, result.amount, result.direction),
    )
    result.classification_label = "FINANCIAL_TRANSACTION"
    result.classification_reason = "Validated financial transaction with structured fields extracted"

    logger.info(
        "Parsed SMS | financial=%s | amt=%.2f | dir=%s | bank=%s | mode=%s | ref=%s",
        result.is_financial,
        result.amount or 0.0,
        result.direction,
        result.bank,
        result.transaction_mode,
        result.ref_id,
    )

    return result
