"""
build_unified_dataset.py — merge the bank statement and the SMS pipeline into one
deduplicated transaction set, backfilling truncated statement recipient names with
the fuller names SMS carries.

Why this exists: ~84% of SMS financial transactions are already present in the bank
statement (same real-world transaction, two independent captures). The statement's
own recipient-name field is truncated to ~8 characters by the export process; SMS
carries the untruncated name. See docs/sms_pipeline.md "SMS vs. bank-statement
overlap" for the full audit this script encodes (2026-07-12).

Each SMS transaction gets exactly ONE outcome (enforced by construction):
  'backfilled'  -> unambiguous statement match, SMS name written in
  'no_name'     -> unambiguous statement match, but SMS had no name to offer
  'ambiguous'   -> confirmed a real statement duplicate (transaction counts agree
                   on both sides), but not safely 1:1-pairable by reference id ->
                   statement name left untouched, no new row created
  'new'         -> no statement match at all -> genuinely SMS-only -> new row

After the merge, Recipient_Canonical is recomputed from scratch over the WHOLE
unified dataset (not patched incrementally) using merchant_normalizer.py's UPI-ID
grouping + fuzzy clustering, then merge_prefix_chains() safely auto-merges
truncation-prefix variants (e.g. "SAMEER B" -> "SAMEER BALIRAM SAWA") without ever
merging two different real people who happen to share a prefix (see
merchant_normalizer._first_names_conflict / merge_prefix_chains docstrings — this
guards specifically against the failure mode found on this dataset where "PRACHI
SAMEER SAWANT" and "YASH SAMEER SAWANT" share a UPI ID and nearly all their tokens).
A small MANUAL_ALIASES map on top handles the remaining cases that are unsafe to
merge algorithmically but safe by real-world knowledge (e.g. "AIRTEL PREPAID R"
and "BHARTI AIRTEL LI" are the same telecom company, not a name-prefix relationship
merge_prefix_chains can detect) -- kept deliberately small; anything not obviously
a company/recurring-income-source name is left for a real manual review pass
instead of guessing.

Run from ml_service/:  python -m app.services.build_unified_dataset
Requires ml_service/data/captured_sms.csv to exist (real SMS capture).
"""
from __future__ import annotations

import logging
import os
import re

import pandas as pd

from app.services.merchant_normalizer import merge_prefix_chains, normalize_recipients, SENTINELS
from app.services.sms_pipeline import SmsPipeline

# Confirmed same real-world entity despite not chaining as a name-prefix (different
# word order / suffix, not a truncation) -- safe because these are companies or a
# single recurring income source, never a case of two different real people.
MANUAL_ALIASES = {
    "BHARTI AIRTEL LI": "AIRTEL",
    "AIRTEL PREPAID R": "AIRTEL",
    "WWW AIRTEL IN": "AIRTEL",
    "ZOMATO L": "ZOMATO",
    "ZOMATO ONLINE OR": "ZOMATO",
    "STIPEND NOMURA FEB 2026": "STIPEND NOMURA",
    "STIPEND NOMURA MAR 2026": "STIPEND NOMURA",
    "STIPEND NOMURA JUN 2026": "STIPEND NOMURA",
}

logging.disable(logging.INFO)

# This file lives at ml_service/app/services/ -- walk up to ml_service/, then to the
# repo root, so paths resolve correctly regardless of the working directory it's run
# from (matches the same pattern sms_pipeline.py uses for its own data/ paths).
_ML_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.dirname(_ML_SERVICE_ROOT)

WB_PATH = os.path.join(_REPO_ROOT, "ml_preprocessing", "CSVS", "SpendWise_4yrs_Clean_Merchants.xlsx")
OUT_PATH = os.path.join(_REPO_ROOT, "ml_preprocessing", "CSVS", "SpendWise_Unified_Merchants.xlsx")

# Ambiguous cases resolved by manual review (2026-07-12) — see
# ml_service/data/ambiguous_matches_review.xlsx for the full reasoning behind each.
RESOLVED_TEXT_HINT = {
    (500.0, "DEBIT", "2024-06-16"): "ATM_WITHDRAWAL",
    (10000.0, "DEBIT", "2026-04-25"): "PRACHI SAMEER SAW",
    (15000.0, "DEBIT", "2026-05-26"): "PRACHI SAMEER SAW",
}

# Confirmed NOT a statement duplicate despite sharing date+amount+direction with an
# unrelated statement row (coincidental collision) — the slice-wallet payment to
# MANOEUVRE EDUCATION vs. an unrelated same-amount "Loan Rep"/"Bill Pay" entry.
CONFIRMED_NOT_DUPLICATE = {(15000.0, "DEBIT", "2026-03-26")}


def clean_ref(v) -> str | None:
    """Normalize a reference id for string comparison across sources."""
    s = str(v).strip()
    if s.lower() in ("nan", "none", ""):
        return None
    s = re.sub(r"\.0$", "", s)
    return re.sub(r"[^A-Za-z0-9]", "", s).upper() or None


def build() -> pd.DataFrame:
    pl = SmsPipeline()
    df = pl.classify_and_extract(pl.structural_clean(pl.load()))
    fin = pl.dedup_transactions(df[df["classification_label"].eq("FINANCIAL_TRANSACTION")].copy())
    fin["ref_norm"] = fin["ref_id"].map(clean_ref)
    fin["day"] = fin["event_time"].dt.tz_convert("Asia/Kolkata").dt.date

    wb = pd.read_excel(WB_PATH)
    wb["ref_norm"] = wb["Transaction_ID"].map(clean_ref)
    wb["day"] = pd.to_datetime(wb["Transaction_Date"]).dt.date
    wb["dir"] = wb["DR/CR_Indicator"].map({"DR": "DEBIT", "CR": "CREDIT"})
    wb["amt"] = wb["Amount"].abs()
    wb = wb.reset_index(drop=False).rename(columns={"index": "_wb_idx"})
    wb_refs = set(wb["ref_norm"].dropna())

    outcomes = {"backfilled": 0, "no_name": 0, "ambiguous": 0, "new": 0}
    new_rows = []

    for _, r in fin.iterrows():
        matched_wb_idx = None
        outcome = None  # set exactly once below

        if r["ref_norm"] is not None and r["ref_norm"] in wb_refs:
            cand = wb[wb["ref_norm"] == r["ref_norm"]]
            if len(cand) == 1:
                matched_wb_idx = cand["_wb_idx"].iloc[0]

        if matched_wb_idx is None:
            cand = wb[(wb["day"] == r["day"]) & (wb["amt"] == r["amount"]) & (wb["dir"] == r["direction"])]
            key = (r["amount"], r["direction"], str(r["day"]))
            if key in CONFIRMED_NOT_DUPLICATE:
                outcome = "new"
                new_rows.append(r)
            elif len(cand) == 1:
                matched_wb_idx = cand["_wb_idx"].iloc[0]
            elif len(cand) > 1:
                hint = RESOLVED_TEXT_HINT.get(key)
                if hint:
                    hinted = cand[cand["Recipient_Name"].astype(str).str.contains(hint, case=False, na=False)]
                    if len(hinted) == 1:
                        matched_wb_idx = hinted["_wb_idx"].iloc[0]
                    else:
                        outcome = "ambiguous"
                else:
                    outcome = "ambiguous"
            # len(cand) == 0 falls through with matched_wb_idx still None, outcome still None

        if outcome is None:
            if matched_wb_idx is not None:
                if pd.notna(r["recipient_name"]) and str(r["recipient_name"]).strip():
                    wb.loc[wb["_wb_idx"] == matched_wb_idx, "Recipient_Name"] = r["recipient_name"]
                    outcome = "backfilled"
                else:
                    outcome = "no_name"
            else:
                outcome = "new"
                new_rows.append(r)

        outcomes[outcome] += 1

    assert sum(outcomes.values()) == len(fin), "every SMS row must get exactly one outcome"
    assert len(new_rows) == outcomes["new"]
    logging.disable(logging.NOTSET)
    print("Outcomes:", outcomes)

    new_df = pd.DataFrame(new_rows)
    appended = pd.DataFrame({
        "Transaction_Date": pd.to_datetime(new_df["day"]),
        "Debit": new_df.apply(lambda x: x["amount"] if x["direction"] == "DEBIT" else 0.0, axis=1),
        "Credit": new_df.apply(lambda x: x["amount"] if x["direction"] == "CREDIT" else 0.0, axis=1),
        "Balance": new_df["balance_after"],
        "Transaction_Mode": new_df["transaction_mode"],
        "DR/CR_Indicator": new_df["direction"].map({"DEBIT": "DR", "CREDIT": "CR"}),
        "Transaction_ID": new_df["ref_id"],
        "Recipient_Name": new_df["recipient_name"],
        "Bank": new_df["bank"],
        "UPI_ID": new_df["upi_id"],
        "Note": "SMS",
        "Amount": new_df.apply(lambda x: x["amount"] if x["direction"] == "CREDIT" else -x["amount"], axis=1),
        "Recipient_Canonical": pd.NA,
        "_source": "sms_only",
    })

    wb_out = wb.drop(columns=["_wb_idx", "ref_norm", "day", "dir", "amt"]).copy()
    wb_out["Transaction_Date"] = pd.to_datetime(wb_out["Transaction_Date"])
    wb_out["_source"] = "statement"

    unified = pd.concat([wb_out, appended], ignore_index=True)
    unified = unified.sort_values("Transaction_Date", kind="stable").reset_index(drop=True)

    # Recompute Recipient_Canonical from scratch over the whole unified dataset --
    # patching only the changed rows would leave the OLD canonical grouping (built
    # on truncated names) inconsistent with the now-much-fuller Recipient_Name column.
    unified["Recipient_Canonical"] = normalize_recipients(unified)
    names = [n for n in unified["Recipient_Canonical"].unique() if n not in SENTINELS and pd.notna(n)]
    prefix_map = merge_prefix_chains(names)
    unified["Recipient_Canonical"] = unified["Recipient_Canonical"].map(lambda n: prefix_map.get(n, n))
    unified["Recipient_Canonical"] = unified["Recipient_Canonical"].replace(MANUAL_ALIASES)

    return unified


if __name__ == "__main__":
    unified = build()
    unified.to_excel(OUT_PATH, index=False)
    print(f"Wrote {len(unified)} rows to {OUT_PATH}")
    print("  statement-sourced:", (unified["_source"] == "statement").sum())
    print("  sms-only:         ", (unified["_source"] == "sms_only").sum())
    print("  date range:       ", unified["Transaction_Date"].min(), "->", unified["Transaction_Date"].max())
