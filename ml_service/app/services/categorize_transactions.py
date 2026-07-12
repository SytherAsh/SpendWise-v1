"""
categorize_transactions.py — produce a fully-categorized transaction CSV, ready for
model training, from the unified dataset (build_unified_dataset.py) plus the existing
manually-labeled file.

Why this exists: the unified dataset (2,086 rows) is bigger than the existing labeled
file (1,810 rows) — 239 statement rows were never labeled at all, plus the 169
genuinely-SMS-only rows are new. This script closes that gap so every row in the final
CSV has a category, using three tiers of decreasing confidence:

  1. original_label     — an existing human label, carried over by reference id.
  2. recipient_lookup    — no existing label for THIS transaction, but the same
                            Recipient_Canonical was already labeled elsewhere — reuse
                            its most common category (majority vote per recipient).
  3. heuristic            — no existing label anywhere for this recipient. Classified
                            by: known family/friend name, business keyword/brand
                            match, or (as a last resort, matching this dataset's own
                            base rate — the majority of a personal UPI history really
                            is P2P) defaulted to Transfers/person. Only a name with NO
                            signal at all falls to Miscellaneous.

Every heuristic decision found several real false-classification bugs during
development (documented inline on each check below) — this module is the
consolidated, fixed version of that iteration, not the first draft.

Run from ml_service/:  python -m app.services.categorize_transactions
"""
from __future__ import annotations

import logging
import os
import re

import pandas as pd

from app.services.build_unified_dataset import build as build_unified

logging.disable(logging.INFO)

_ML_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.dirname(_ML_SERVICE_ROOT)

LABELED_PATH = os.path.join(_REPO_ROOT, "ml_preprocessing", "CSVS", "SpendWise_4yrs_Labelled_v2_transfer_type.xlsx")
OUT_PATH = os.path.join(_REPO_ROOT, "ml_preprocessing", "CSVS", "SpendWise_Final_Labeled.xlsx")


def clean_ref(v) -> str | None:
    s = str(v).strip()
    if s.lower() in ("nan", "none", ""):
        return None
    s = re.sub(r"\.0$", "", s)
    return re.sub(r"[^A-Za-z0-9]", "", s).upper() or None


# ------------------------------------------------------------------
# Heuristic classifier (tier 3) — person vs. merchant/institution
# ------------------------------------------------------------------

FAMILY_PATTERNS = [r"PRACHI", r"\bSAMEER\b"]
FRIEND_NAME_HINTS = [r"ALPHA", r"VIHAA", r"SHUBHAM", r"PRATHAM"]

BUSINESS_ROOTS = re.compile(
    r"(HOTEL|STORE|MART\b|FOOD|SWEET|PVT|\bLTD\b|PHARMAC|MEDICAL|RESTAURAN|CAFE|KITCHEN"
    r"|BAKER|TRAVEL|CENTRE|CENTER|SERVIC|ENTERPRIS|TRAD|AGENC|SHOP|DAIRY|JUICE"
    r"|TECHNOLOG|\bTECH\b|SOLUTION|\bSOL\b|SYSTEM|VENTURE|INDUSTR|CORP|COMPAN|GROUP"
    r"|FOUNDATION|TRUST|SOCIETY|\bBANK\b|INSURANC|UNIVERSIT|COLLEGE|SCHOOL|INSTITUT"
    # \b after HOSPITAL -- bare "HOSPITAL" (no boundary) matched inside "HOSPITALITY"
    # ("All Time High Hospitality LLP" is a hospitality/catering business, not Medical).
    r"|HOSPITAL\b|CLINIC|CHEMIS|DIAGNOSTIC|MALL\b|PLAZA|COMPLEX|BRANDS|FASHION"
    r"|FAST ?FOOD|ROLLS|BURGER|PIZZA|CHANA ?BHANDAR|SNACKS?|BEVERAGE|ONLINE|DIGITAL|EDUCATION"
    r"|ACADEMY|COACHING|ELECTRONIC|CINEMA|DEPOT|BOOK ?DEPOT|GENERAL\b|ESCAPE ?ROOM"
    r"|MYSTERY ?ROOM|SHARBAT|WALE\b|DHABA|BHEL|VADA ?PAV|MEDIC|SUPERMARKET|PROVISION"
    r"|CATERER|BANQUET|CLOTHING|GARMENTS|JEWELL?ERS?|SALON|SPA\b|GYM\b|FITNESS"
    r"|HOSPITALITY|\bLLP\b|\bLLC\b|\bINC\b|PAYMENT|STATIONAR|STATIONER|SOFTW)",
    re.IGNORECASE,
)
KNOWN_BRANDS = re.compile(
    r"(ZOMATO|SWIGGY|AMAZON|FLIPKART|GOOGLE|PAYTM|PHONEPE|AIRTEL|\bJIO\b|NETFLIX"
    r"|HOTSTAR|SPOTIFY|\bUBER\b|\bOLA\b|IRCTC|MYNTRA|AJIO|BIGBASKET|BLINKIT|ZEPTO|DUNZO"
    r"|DOMINOS?|MCDONALD|\bKFC\b|STARBUCKS|BHARTIPAY|BHARATPE|MOBIKWIK|RAZORPAY|\bSLICE\b"
    r"|GROUPON|PIZZA ?HUT|SUBWAY|BURGER ?KING|CCD\b|CAFE ?COFFEE|HALDIRAM|AMUL|NESTLE"
    r"|CRED\b|CCAVENUE|AVENUES|VODAFONE|\bVI\b|IDEA\b)",
    re.IGNORECASE,
)
# Core-banking-system transfer codes (CBS = Core Banking System) -- a generic bank
# routing tag, not a person or a categorizable merchant.
BANK_CODE_PATTERN = re.compile(r"\bCBSSB", re.IGNORECASE)
# ATM withdrawal codes: standalone "ATM" ("HDF ATM CHMUM009") or concatenated with no
# space ("ATMSB"). These end in digits/letters, same surface shape as a real person's
# UPI handle ("VIHAANSHINDE18") -- must be excluded before the person-shape fallback.
ATM_CODE_PATTERN = re.compile(r"^ATM|\bATM\b", re.IGNORECASE)
# Income/payroll credits, not a P2P transfer to a person.
INCOME_PATTERN = re.compile(r"\bSTIPEND\b|\bSALARY\b|\bPAYROLL\b|\bDIVIDEND\b", re.IGNORECASE)
# Generic transaction-type labels, not a specific payee -- extraction sometimes inserts
# a stray space mid-word ("Bil l Pay", "Loa n Rep", "F amily"), the same artifact class
# fixed in sms_parser.py's recipient extraction. Checked on the WHITESPACE-STRIPPED
# name so the space position doesn't matter.
GENERIC_LABEL_WORDS = {
    "BILLPAY", "LOANREPAYMENT", "LOANREP", "TRANSFER", "TRANSFR", "IMPSTRANSFER",
    "IMPSTRAN", "FAMILY", "FRIENDS", "SELF", "CASHDEPOSIT",
}
GARBAGE_PATTERN = re.compile(r"CHEQUE|\bCSB\b|^MMRDA$|^SBIMOPS$|^UNKNOWN$|^NAN$", re.IGNORECASE)
SENTINEL_NAMES = {"PHONE_TRANSFER", "UNKNOWN", "NAN", ""}
# IITM/IIT/railway/metro don't match BUSINESS_ROOTS -- separate small pattern rather
# than bloating it with transit/education-specific terms.
INSTITUTION_PATTERN = re.compile(r"\bIITM\b|\bIIT\b|\bW ?RLY\b|RAILWAY|\bRLY\b|METR", re.IGNORECASE)

CATEGORY_HINTS = [
    (re.compile(r"ZOMATO|SWIGGY|HOTEL|RESTAURAN|CAFE|KITCHEN|FOOD|SWEET|JUICE|DAIRY|BURGER|ROLLS|BAKER|SNACKS?|MCDONALD|KFC|STARBUCKS|DOMINOS?|CHANA ?BHANDAR|PIZZA|SHARBAT|WALE|DHABA|BHEL|VADA ?PAV|SUBWAY|CCD|CAFE ?COFFEE|HALDIRAM|AMUL|NESTLE", re.I), "Food / Dine Out"),
    (re.compile(r"PHARMAC|MEDICAL|CLINIC|HOSPITAL\b|DIAGNOSTIC|CHEMIS|MEDIC\b", re.I), "Medical"),
    (re.compile(r"AIRTEL|JIO\b|NETFLIX|HOTSTAR|SPOTIFY|RECHARGE|VODAFONE|\bVI\b|IDEA\b", re.I), "Subscriptions"),
    (re.compile(r"IRCTC|UBER|OLA\b|TRAVEL|METR|MMRDA|\bW ?RLY\b|RAILWAY|\bRLY\b", re.I), "Travel"),
    (re.compile(r"AMAZON|FLIPKART|MYNTRA|AJIO|MALL\b|SHOP|STORE|MART\b|BOOK ?DEPOT|CLOTHING|GARMENTS|JEWELL?ERS?", re.I), "Shopping"),
    (re.compile(r"BIGBASKET|BLINKIT|ZEPTO|DUNZO|GROCER|SUPERMARKET|PROVISION", re.I), "Groceries"),
    (re.compile(r"UNIVERSIT|COLLEGE|SCHOOL|INSTITUT|EDUCATION|ACADEMY|COACHING|\bIITM\b|\bIIT\b", re.I), "Fees & Debt"),
    (re.compile(r"CINEMA|MYSTERY ?ROOM|ESCAPE ?ROOM", re.I), "Entertainment"),
    (re.compile(r"SALON|SPA\b|GYM\b|FITNESS", re.I), "Cosmetics"),
]


def _looks_like_a_name(name: str) -> bool:
    if not re.match(r"^[A-Za-z][A-Za-z .]*[0-9\-]*$", name):
        return False
    return len(name) >= 2


def classify_heuristic(name) -> tuple[str, object, str]:
    """Tier-3 fallback: classify a recipient with no existing label anywhere.

    Returns (category, transfer_type, reason). transfer_type is only set for
    category == 'Transfers'.
    """
    name = "" if pd.isna(name) else str(name).strip()
    if not name or name.upper() in SENTINEL_NAMES:
        return "Miscellaneous", pd.NA, "no usable recipient name"

    if GARBAGE_PATTERN.search(name):
        return "Miscellaneous", pd.NA, "garbage/reference-like string"

    if BANK_CODE_PATTERN.search(name):
        return "Miscellaneous", pd.NA, "core-banking-system routing code, not a person"

    stripped = re.sub(r"[^A-Za-z]", "", name).upper()
    if stripped in GENERIC_LABEL_WORDS:
        return "Miscellaneous", pd.NA, "generic transaction-type label, not a specific payee"

    if any(re.search(p, name, re.I) for p in FAMILY_PATTERNS):
        return "Transfers", "family", "matches known family name"

    if any(re.search(p, name, re.I) for p in FRIEND_NAME_HINTS):
        return "Transfers", "friend", "matches known friend name"

    brand_hit = KNOWN_BRANDS.search(name)
    biz_hit = BUSINESS_ROOTS.search(name)
    inst_hit = INSTITUTION_PATTERN.search(name)
    if brand_hit or biz_hit or inst_hit:
        cat = "Miscellaneous"
        for pat, c in CATEGORY_HINTS:
            if pat.search(name):
                cat = c
                break
        reason = "known brand" if brand_hit else ("institution/transit code" if inst_hit else "business keyword")
        return cat, pd.NA, f"matches {reason}"

    # Both checked before the person-shape fallback: an ATM code and a person's UPI
    # handle have the same surface shape (letters + trailing digits), and income
    # credits ("STIPEND NOMURA JUN 2026") read as Title-Case text just like a name.
    if ATM_CODE_PATTERN.search(name):
        return "Miscellaneous", pd.NA, "ATM withdrawal code, not a person"
    if INCOME_PATTERN.search(name):
        return "Miscellaneous", pd.NA, "income/payroll credit, not a P2P transfer"

    if _looks_like_a_name(name):
        return "Transfers", "person", "no business signal, looks like a person name"

    return "Miscellaneous", pd.NA, "no resolvable signal"


# ------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------

def categorize(unified: pd.DataFrame, labeled: pd.DataFrame) -> pd.DataFrame:
    """Attach category + transfer_type to every row of the unified dataset."""
    unified = unified.copy()
    unified["ref_norm"] = unified["Transaction_ID"].map(clean_ref)
    labeled = labeled.copy()
    labeled["ref_norm"] = labeled["transaction_id"].map(clean_ref)

    labeled_small = (
        labeled[["ref_norm", "category", "transfer_type"]]
        .dropna(subset=["ref_norm"])
        .drop_duplicates("ref_norm")
    )
    merged = unified.merge(labeled_small, on="ref_norm", how="left")

    matched_mask = merged["category"].notna()

    # Tier 2 lookup: recipient -> most common category among already-labeled rows.
    labeled_rows = merged[matched_mask]
    canon_to_cat = (
        labeled_rows[labeled_rows["Recipient_Canonical"].notna()]
        .groupby("Recipient_Canonical")["category"]
        .agg(lambda s: s.value_counts().idxmax())
    )
    canon_to_tt = (
        labeled_rows[labeled_rows["Recipient_Canonical"].notna() & (labeled_rows["category"] == "Transfers")]
        .groupby("Recipient_Canonical")["transfer_type"]
        .agg(lambda s: s.value_counts().idxmax() if s.notna().any() else pd.NA)
    )

    final_category, final_transfer_type, label_source = [], [], []
    for _, row in merged.iterrows():
        if pd.notna(row["category"]):
            final_category.append(row["category"])
            final_transfer_type.append(row["transfer_type"])
            label_source.append("original_label")
            continue
        canon = row["Recipient_Canonical"]
        if pd.notna(canon) and canon in canon_to_cat:
            cat = canon_to_cat[canon]
            final_category.append(cat)
            final_transfer_type.append(canon_to_tt.get(canon, pd.NA) if cat == "Transfers" else pd.NA)
            label_source.append("recipient_lookup")
            continue
        cat, tt, reason = classify_heuristic(row["Recipient_Name"] if pd.notna(row["Recipient_Name"]) else canon)
        final_category.append(cat)
        final_transfer_type.append(tt)
        label_source.append(f"heuristic: {reason}")

    merged["category"] = final_category
    merged["transfer_type"] = final_transfer_type
    merged["label_source"] = label_source
    return merged.drop(columns=["ref_norm"])


if __name__ == "__main__":
    unified = build_unified()
    labeled = pd.read_excel(LABELED_PATH)
    result = categorize(unified, labeled)

    assert result["category"].isna().sum() == 0, "every row must have a category"
    assert len(result) == len(unified), "row count must be preserved"

    result.to_excel(OUT_PATH, index=False)

    print(f"Wrote {len(result)} rows to {OUT_PATH}")
    print("\nLabel source breakdown:")
    print(result["label_source"].apply(lambda s: s.split(":")[0]).value_counts().to_string())
    print("\nFinal category distribution:")
    print(result["category"].value_counts().to_string())
