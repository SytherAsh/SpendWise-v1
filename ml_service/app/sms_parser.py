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
]

RECIPIENT_CREDIT_PATTERNS = [
    re.compile(
        r"(?:transfer(?:red)?\s+from|received\s+from|from|by\s+a/c\s+linked\s+to)\s+"
        r"(?!\s*(?:Rs\.?|INR|₹))([A-Za-z][A-Za-z0-9 &'._-]{1,35})",
        re.IGNORECASE,
    ),
]

# Words that terminate a recipient name
RECIPIENT_TERMINATORS = re.compile(
    r"\s+(?:on|ref|via|txn|at|for|Refno|using|through|dated)\b",
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

# Failed transaction indicators
FAILURE_GATE = re.compile(
    r"\b(fail(?:ed)?|decline(?:d)?|unsuccessful|reversed|rejected|cancelled)\b",
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
    for m in AMOUNT_PATTERN.finditer(body):
        raw = m.group(1) or m.group(2)
        if raw:
            try:
                return _parse_amount(raw)
            except ValueError:
                continue
    return None


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
            return name if name else None

    return None

def _fallback_merchant_extraction(body: str, sender: str, bank: Optional[str]) -> Optional[str]:
    # 1. Look for known merchants in body
    known_merchants = re.compile(
        r"\b(Rapido|Uber|Ola|Swiggy|Zomato|Amazon|Flipkart|Myntra|Airtel|Jio|Vi|Blinkit|Zepto)\b", 
        re.IGNORECASE
    )
    match = known_merchants.search(body)
    if match:
        return match.group(1).title()
    
    # 2. Use sender if it's not a generic bank sender
    if sender and sender.strip() and sender.lower() != "partner":
        # Clean sender like "AD-SBIUPI-S" -> "SBIUPI"
        clean_sender = re.sub(r"^[A-Z]{2}-|-?[A-Z]$", "", sender).strip()
        # If the clean sender is just the bank (like SBIUPI), don't use it as merchant
        if bank and clean_sender.upper().startswith(bank.upper()):
            return None
        if "UPI" in clean_sender.upper():
            return None
        return clean_sender

    return None


def _is_spam(body: str, sender: Optional[str]) -> bool:
    """Return True if the message looks like an ad / promotional spam."""
    if sender and SPAM_SENDER_PATTERN.search(sender):
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

    if not body:
        return result

    # Gate 1 — must contain at least one financial keyword
    if not FINANCIAL_KEYWORDS.search(body):
        return result

    # Gate 2 — reject spam / promotions
    if _is_spam(body, sender):
        logger.debug("Skipping spam/promotional message from sender=%s", sender)
        return result

    # Gate 3 — reject failed transactions
    if FAILURE_GATE.search(body):
        logger.info("Skipping failed/declined transaction message: %s", body[:50])
        return result

    result.is_financial = True

    # --- Amount ---
    result.amount = _extract_first_amount(body)

    # --- Direction ---
    body_lower = body.lower()
    if "credited" in body_lower:
        result.direction = "CREDIT"
    elif "debited" in body_lower:
        result.direction = "DEBIT"
    elif DEBIT_KEYWORDS.search(body):
        result.direction = "DEBIT"
    elif CREDIT_KEYWORDS.search(body):
        result.direction = "CREDIT"

    # Sanity: if no amount or no direction, not a true financial transaction
    if result.amount is None or result.direction is None:
        result.is_financial = False

    # --- Bank ---
    result.bank = _detect_bank_from_sender(sender) or _detect_bank_from_body(body)

    # --- UPI ID ---
    result.upi_id = _extract_upi_id(body)

    # --- Transaction mode ---
    result.transaction_mode = _extract_mode(body)

    # --- Account suffix ---
    result.account_suffix = _extract_account_suffix(body)

    # --- Balance ---
    result.balance_after = _extract_balance(body)

    # --- Reference ID ---
    result.ref_id = _extract_ref_id(body)

    # --- Recipient ---
    result.recipient_name = _extract_recipient(body, result.direction)
    if not result.recipient_name:
        result.recipient_name = _fallback_merchant_extraction(body, sender, result.bank)

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
