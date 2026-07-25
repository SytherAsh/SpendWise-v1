import re

import numpy as np
import pandas as pd

from app.services.merchant_normalizer import (
    SENTINELS,
    merge_prefix_chains,
    merge_whitespace_variants,
    normalize_recipients,
)

RAW_COLUMN_MAP = {
    "date": "Transaction_Date",
    "transaction_date": "Transaction_Date",
    "details": "Description",
    "description": "Description",
    "ref no./cheque no.": "Reference No./Cheque No.",
    "reference no./cheque no.": "Reference No./Cheque No.",
    "ref no./cheque no": "Reference No./Cheque No.",
    "ref no.": "Reference No./Cheque No.",
    "ref no": "Reference No./Cheque No.",
    "cheque no.": "Reference No./Cheque No.",
    "cheque no": "Reference No./Cheque No.",
    "debit": "Debit",
    "credit": "Credit",
    "balance": "Balance",
    "transaction_mode": "Transaction_Mode",
    "transaction mode": "Transaction_Mode",
    "dr/cr_indicator": "DR/CR_Indicator",
    "dr/cr indicator": "DR/CR_Indicator",
    "recipient_name": "Recipient_Name",
    "recipient name": "Recipient_Name",
    "upi_id": "UPI_ID",
    "upi id": "UPI_ID",
    "bank": "Bank",
    "note": "Note",
    "amount": "Amount",
    "transaction_id": "Transaction_ID",
    "transaction id": "Transaction_ID",
}


def _normalize_column_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower().replace("\n", " "))


def rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for column in df.columns:
        normalized = _normalize_column_name(column)
        if normalized in RAW_COLUMN_MAP:
            mapping[column] = RAW_COLUMN_MAP[normalized]
    return df.rename(columns=mapping)


def _ensure_description(df: pd.DataFrame) -> pd.DataFrame:
    if "Description" not in df and "Details" in df:
        df["Description"] = df["Details"]
    if "Description" in df and "Details" not in df:
        df["Details"] = df["Description"]
    if "Description" not in df:
        df["Description"] = None
    return df


def _remove_overlapping_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    if {"Transaction_Date", "Description", "Balance"}.issubset(df.columns):
        rows_before = len(df)
        duplicated = df.duplicated(subset=["Transaction_Date", "Description", "Balance"], keep="first")
        df = df.loc[~duplicated].reset_index(drop=True)
        if len(df) != rows_before:
            # keep the cleaned dataset deterministic by preserving the first appearance
            df = df.reset_index(drop=True)
    return df


def _drop_blank_rows(df: pd.DataFrame) -> pd.DataFrame:
    blank_rows = df.apply(
        lambda row: all(pd.isna(x) or str(x).strip() == "" for x in row),
        axis=1,
    )
    return df.loc[~blank_rows].reset_index(drop=True)


_CIF_IN_DESCRIPTION_RE = re.compile(r"\bCIF\s*:?\s*\d+", re.IGNORECASE)


def redact_insurance_cif(df: pd.DataFrame) -> pd.DataFrame:
    """Strip the account holder's raw CIF number out of Description for
    PMJJBY/PMSBY insurance-premium rows.

    SBI's own narration for these debits embeds it verbatim (e.g.
    'DEBIT PMJJBY UPTO 31-05-24 CIF: 90965844969') -- a bank-issued customer
    ID, not something that should ride through into the output CSV just
    because Description is otherwise kept verbatim. Scoped to the labeled
    'CIF:' prefix and to INSURANCE-mode rows only, so it doesn't touch
    unrelated numeric narration elsewhere.
    """
    if "Description" not in df.columns or "Transaction_Mode" not in df.columns:
        return df
    mask = df["Transaction_Mode"] == "INSURANCE"
    if mask.any():
        df.loc[mask, "Description"] = (
            df.loc[mask, "Description"]
            .astype(str)
            .str.replace(_CIF_IN_DESCRIPTION_RE, "CIF: [REDACTED]", regex=True)
        )
    return df


def _clean_description(df: pd.DataFrame) -> pd.DataFrame:
    if "Description" in df:
        # Excel's own display-only line-wrap shows up as a literal "\n" mid-word in
        # the exported cell text (e.g. "W" + "\n" + "ife", no real space at the
        # break). Deleting it outright -- not collapsing it into a space -- rejoins
        # the word correctly; that's also what the source notebook does. Collapsing
        # it into a space instead (the previous behavior here) was silently
        # splitting names like "Wife" -> "W ife" and "Rutu" -> "R utu" wherever a
        # wrap happened to land without a surrounding space.
        df["Description"] = df["Description"].astype(str).str.replace("\n", "", regex=False)
        df["Description"] = df["Description"].str.replace(r"\s+", " ", regex=True).str.strip()
    return df


def _sanitize_numeric_column(df: pd.DataFrame, column_name: str) -> pd.Series:
    if column_name not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)

    values = df[column_name].astype(str).str.replace(",", "", regex=False).str.strip()
    values = values.replace({"": None, "nan": None, "NaN": None, "None": None})
    return pd.to_numeric(values, errors="coerce").fillna(0.0)


def _determine_dr_cr(df: pd.DataFrame) -> pd.DataFrame:
    if "DR/CR_Indicator" not in df.columns:
        df["DR/CR_Indicator"] = None

    if len(df) == 0:
        return df

    # Row 0 has no prior balance to diff against, so it's derived from Credit/Debit
    # sign instead -- unconditionally, regardless of whatever extract_details already
    # guessed from the narration text (a non-matching row falls back to the literal
    # string "N/A", not NaN, so gating this on pd.isna() would silently skip it).
    if df.loc[0, "Credit"] > 0:
        df.loc[0, "DR/CR_Indicator"] = "CR"
    elif df.loc[0, "Debit"] > 0:
        df.loc[0, "DR/CR_Indicator"] = "DR"
    else:
        df.loc[0, "DR/CR_Indicator"] = None

    for i in range(1, len(df)):
        if pd.notna(df.loc[i, "Balance"]) and pd.notna(df.loc[i - 1, "Balance"]):
            balance_diff = df.loc[i, "Balance"] - df.loc[i - 1, "Balance"]
            df.loc[i, "DR/CR_Indicator"] = "CR" if balance_diff > 0 else "DR"

    return df


def determine_dr_cr(df: pd.DataFrame) -> pd.DataFrame:
    if "Balance" not in df.columns:
        return df
    return _determine_dr_cr(df)


def clean_recipient(name: str) -> str:
    if pd.isna(name):
        return "UNKNOWN"
    value = str(name).strip()
    if value.upper() in {"N/A", "NA", "NONE", ""}:
        return "UNKNOWN"
    if value.isdigit():
        return "PHONE_TRANSFER"
    return re.sub(r"\s+", " ", value)


def extract_details(description: str) -> pd.Series:
    if pd.isna(description):
        return pd.Series({
            "Transaction_Type": "N/A",
            "Transaction_Mode": "N/A",
            "DR/CR_Indicator": "N/A",
            "Transaction_ID": "N/A",
            "Recipient_Name": "N/A",
            "Bank": "N/A",
            "UPI_ID": "N/A",
            "Note": "N/A",
        })

    description_clean = str(description).replace("\n", "").strip()

    for pattern, handler in RULES:
        match = pattern.search(description_clean)
        if match:
            return handler(match)

    return fallback_extract(description_clean)


def _normalize_columns_for_output(df: pd.DataFrame) -> pd.DataFrame:
    output_columns = [
        "Transaction_Date",
        "Description",
        "Transaction_Type",
        "Transaction_Mode",
        "DR/CR_Indicator",
        "Transaction_ID",
        "Recipient_Name",
        "Recipient_Canonical",
        "Bank",
        "UPI_ID",
        "Note",
        "Debit",
        "Credit",
        "Balance",
        "Amount",
        "Reference No./Cheque No.",
    ]
    available = [col for col in output_columns if col in df.columns]
    return df[available].copy()


def normalize_statement_dataframe(raw_df: pd.DataFrame) -> pd.DataFrame:
    df = raw_df.copy()
    df = rename_columns(df)
    df = _ensure_description(df)
    df = _clean_description(df)
    df = _remove_overlapping_duplicates(df)
    df = _drop_blank_rows(df)

    if "Description" in df:
        extracted = df["Description"].apply(extract_details)
        df = pd.concat([df, extracted], axis=1)
        df = redact_insurance_cif(df)

    df["Debit"] = _sanitize_numeric_column(df, "Debit")
    df["Credit"] = _sanitize_numeric_column(df, "Credit")
    df["Balance"] = _sanitize_numeric_column(df, "Balance")

    if "Amount" not in df.columns:
        df["Amount"] = df["Credit"] - df["Debit"]

    df = determine_dr_cr(df)

    if "Transaction_Date" in df.columns:
        df["Transaction_Date"] = pd.to_datetime(
            df["Transaction_Date"],
            dayfirst=True,
            errors="coerce",
        )
        df["Transaction_Date"] = df["Transaction_Date"].dt.date.astype(str)

    if "Recipient_Name" not in df.columns:
        df["Recipient_Name"] = None
    df["Recipient_Name"] = df["Recipient_Name"].apply(clean_recipient)

    df["Recipient_Canonical"] = normalize_recipients(df)

    # Whitespace-variant merge runs first (exact match once spaces are
    # stripped -- very high confidence, so safe to apply before the looser
    # prefix-truncation tier) on the full column, not just unique values, so
    # the frequency-based canonical choice reflects real occurrence counts.
    canonical_values = [
        name for name in df["Recipient_Canonical"].tolist()
        if isinstance(name, str) and name not in SENTINELS
    ]
    whitespace_map = merge_whitespace_variants(canonical_values)
    df["Recipient_Canonical"] = df["Recipient_Canonical"].map(
        lambda name: whitespace_map.get(name, name) if isinstance(name, str) else name
    )

    names = [
        name for name in df["Recipient_Canonical"].unique()
        if name not in SENTINELS and pd.notna(name)
    ]
    prefix_map = merge_prefix_chains(names)
    df["Recipient_Canonical"] = df["Recipient_Canonical"].map(
        lambda name: prefix_map.get(name, name) if isinstance(name, str) else name
    )

    df.replace("N/A", None, inplace=True)
    df = df.replace([np.inf, -np.inf], None)
    df = df.astype(object)
    df = df.where(pd.notna(df), None)

    return _normalize_columns_for_output(df)


# The following rules are intentionally ported from the original SBI-specific
# statement parser in the notebook. They remain focused on the same bank's
# narration shapes, but they are wrapped here in pure Python for reuse.

DRCR_BY_TYPE = {"WDL TFR": "DR", "DEP TFR": "CR"}


def _type_of(m):
    t = m.group("type").upper()
    return "WDL TFR" if t.startswith("WDL") else "DEP TFR"


def _base(m, **overrides):
    t = _type_of(m)
    out = {
        "Transaction_Type": t,
        "Transaction_Mode": "N/A",
        "DR/CR_Indicator": DRCR_BY_TYPE[t],
        "Transaction_ID": "N/A",
        "Recipient_Name": "N/A",
        "Bank": "N/A",
        "UPI_ID": "N/A",
        "Note": "N/A",
    }
    out.update(overrides)
    return pd.Series(out)

RULE_MAIN = re.compile(
    r"(?P<type>(?:WDL|DEP)\s+TFR)\s+"
    r"(?P<mode>[A-Za-z]+)/(?P<drcr>DR|CR)/"
    r"(?P<id>\d+)/"
    r"(?P<name>[^/]+)/"
    r"(?P<bank>[A-Za-z]+)/"
    r"(?P<upi_id>[^\s/]+)"
    r"(?:\s*-?\d+/(?P<note>[A-Za-z]+))?"
)


def handle_main(m):
    return pd.Series({
        "Transaction_Type": _type_of(m),
        "Transaction_Mode": m.group("mode").upper(),
        "DR/CR_Indicator": m.group("drcr").upper(),
        "Transaction_ID": m.group("id"),
        "Recipient_Name": m.group("name").strip(),
        "Bank": m.group("bank").upper(),
        "UPI_ID": m.group("upi_id"),
        "Note": m.group("note") if m.group("note") else "N/A",
    })

RULE_IMPS_A = re.compile(
    r"(?P<type>(?:WDL|DEP)\s+TFR)\s+INB\s+IMPS(?P<id>\d+)/(?P<sender>[^/]+)/XX(?P<acct>\d+)/\s*"
    r"(?P<name>[^\d/]+?)\s+\d{6,}\s+AT",
)

def handle_imps_a(m):
    name = m.group("name").strip()
    return _base(
        m,
        Transaction_Mode="IMPS",
        Transaction_ID=m.group("id"),
        Recipient_Name=name if name else "N/A",
    )

RULE_IMPS_B = re.compile(
    r"(?P<type>(?:WDL|DEP)\s+TFR)\s+(?:INB\s+)?IMPS/(?P<id>\d+)/(?P<bank>[A-Za-z0-9]{2,5})-[Xx]{2}(?P<acct>\d+)-"
    r"(?P<sender>[^/]+?)\s*/\s*(?P<nickname>[^\d/]*?)\s+\d{6,}\s+AT",
)


def handle_imps_b(m):
    sender = m.group("sender").strip()
    remark = m.group("nickname").strip()
    name = sender if sender else remark
    note = remark if remark and remark.upper() != "NA" else "N/A"
    return _base(
        m,
        Transaction_Mode="IMPS",
        Transaction_ID=m.group("id"),
        Bank=m.group("bank").upper(),
        Recipient_Name=name if name else "N/A",
        Note=note,
    )

RULE_NEFT = re.compile(
    r"(?P<type>(?:WDL|DEP)\s+TFR)\s+NEFT\*(?P<ifsc>[A-Za-z0-9]+)\*(?P<utr>[A-Za-z0-9]+)\*"
    r"(?P<name>[^\d/]+?)\s+\d{6,}\s+AT",
)


def handle_neft(m):
    ifsc = m.group("ifsc")
    bank = ifsc[:4].upper() if len(ifsc) >= 4 else "N/A"
    return _base(
        m,
        Transaction_Mode="NEFT",
        Transaction_ID=m.group("utr"),
        Bank=bank,
        Recipient_Name=m.group("name").strip(),
    )

RULE_UPI_REVERSAL = re.compile(
    r"(?P<type>(?:WDL|DEP)\s+TFR)\s+UPI/(?:(?P<id1>\d+)/REVERSAL|REV/(?P<id2>\d+))"
)


def handle_upi_reversal(m):
    txn_id = m.group("id1") or m.group("id2") or "N/A"
    return _base(
        m,
        Transaction_Mode="UPI",
        Transaction_ID=txn_id,
        Recipient_Name="REVERSAL",
    )

RULE_ATM = re.compile(r"ATM\s*WDL\s+ATM\s*CASH\s+(?P<rest>.+)", re.IGNORECASE)


def handle_atm(m):
    rest = m.group("rest")
    cleaned = re.sub(r"^[\d\s]+", "", rest)
    cleaned = re.sub(r"^\+\s*", "", cleaned)
    location = re.sub(r"\s+", " ", cleaned).strip()
    return pd.Series({
        "Transaction_Type": "WDL TFR",
        "Transaction_Mode": "ATM",
        "DR/CR_Indicator": "DR",
        "Transaction_ID": "N/A",
        "Recipient_Name": "ATM_WITHDRAWAL",
        "Bank": "N/A",
        "UPI_ID": "N/A",
        "Note": location or "N/A",
    })

RULE_ATM_FEE = re.compile(r"DEBIT\s+ATMCard\s+AMC", re.IGNORECASE)


def handle_atm_fee(_m):
    return pd.Series({
        "Transaction_Type": "WDL TFR",
        "Transaction_Mode": "FEE",
        "DR/CR_Indicator": "DR",
        "Transaction_ID": "N/A",
        "Recipient_Name": "BANK_FEE",
        "Bank": "N/A",
        "UPI_ID": "N/A",
        "Note": "N/A",
    })

RULE_INTEREST = re.compile(r"INTERES\s*T?\s*CREDIT", re.IGNORECASE)


def handle_interest(_m):
    return pd.Series({
        "Transaction_Type": "DEP TFR",
        "Transaction_Mode": "INTEREST",
        "DR/CR_Indicator": "CR",
        "Transaction_ID": "N/A",
        "Recipient_Name": "INTEREST_CREDIT",
        "Bank": "N/A",
        "UPI_ID": "N/A",
        "Note": "N/A",
    })

RULE_INSURANCE = re.compile(r"PMJJBY|PMSBY", re.IGNORECASE)


def handle_insurance(_m):
    return pd.Series({
        "Transaction_Type": "WDL TFR",
        "Transaction_Mode": "INSURANCE",
        "DR/CR_Indicator": "DR",
        "Transaction_ID": "N/A",
        "Recipient_Name": "INSURANCE_PREMIUM",
        "Bank": "N/A",
        "UPI_ID": "N/A",
        "Note": "N/A",
    })

RULE_CHEQUE = re.compile(r"CLEARING\s*/\s*CHEQUE", re.IGNORECASE)


def handle_cheque(_m):
    return pd.Series({
        "Transaction_Type": "N/A",
        "Transaction_Mode": "CHEQUE",
        "DR/CR_Indicator": "N/A",
        "Transaction_ID": "N/A",
        "Recipient_Name": "CHEQUE_CLEARING",
        "Bank": "N/A",
        "UPI_ID": "N/A",
        "Note": "N/A",
    })

RULE_POS = re.compile(r"POS\s+ATM\s+PURCH.*?\d{4,}\s*(?P<name>[A-Z][A-Za-z ]+)$")


def handle_pos(m):
    name = re.sub(r"\s+", " ", m.group("name")).strip()
    return pd.Series({
        "Transaction_Type": "WDL TFR",
        "Transaction_Mode": "POS",
        "DR/CR_Indicator": "DR",
        "Transaction_ID": "N/A",
        "Recipient_Name": name if name else "N/A",
        "Bank": "N/A",
        "UPI_ID": "N/A",
        "Note": "N/A",
    })

RULE_OF_NAME = re.compile(r"(?P<type>(?:WDL|DEP)\s+TFR).*?\bO\s?F\b\s+(?P<name>.+?)\s+AT\s+\d")


def handle_of_name(m):
    name = re.sub(r"\s+", " ", m.group("name")).strip()
    return _base(m, Transaction_Mode="INB", Recipient_Name=name if name else "N/A")

RULE_INB_ENTITY = re.compile(
    r"(?P<type>(?:WDL|DEP)\s+TFR)\s+INB\s+(?P<name>[A-Za-z][A-Za-z0-9 ._-]{3,}?)\s+\d{6,}\s*(?:OF|AT)"
)


def handle_inb_entity(m):
    name = re.sub(r"\s+", " ", m.group("name")).strip()
    return _base(m, Transaction_Mode="INB", Recipient_Name=name if name else "N/A")

RULES = [
    (RULE_MAIN, handle_main),
    (RULE_IMPS_B, handle_imps_b),
    (RULE_IMPS_A, handle_imps_a),
    (RULE_NEFT, handle_neft),
    (RULE_UPI_REVERSAL, handle_upi_reversal),
    (RULE_ATM, handle_atm),
    (RULE_ATM_FEE, handle_atm_fee),
    (RULE_INTEREST, handle_interest),
    (RULE_INSURANCE, handle_insurance),
    (RULE_CHEQUE, handle_cheque),
    (RULE_POS, handle_pos),
    (RULE_OF_NAME, handle_of_name),
    (RULE_INB_ENTITY, handle_inb_entity),
]

transaction_modes = [
    "UPI",
    "INB",
    "IMP",
    "NEFT",
    "RTGS",
    "Cheque",
    "Cash Deposit",
    "Cash Withdrawal",
    "POS",
    "DD",
    "SWIFT",
    "Wire Transfer",
    "ECS",
    "Bill Pay",
    "M-wallet",
    "EMI",
    "EFT",
    "ACH",
]


def fallback_extract(description_clean: str) -> pd.Series:
    transaction_mode = "N/A"
    for mode in transaction_modes:
        if mode in description_clean:
            transaction_mode = mode
            break

    note_match = re.search(r"/\s*([A-Za-z]+)\s+\d+", description_clean)
    note = note_match.group(1) if note_match else "N/A"

    txn_id_match = re.search(r"\d{6,}", description_clean)
    txn_id = txn_id_match.group() if txn_id_match else "N/A"

    is_dep = "DEP TFR" in description_clean
    is_wdl = "WDL TFR" in description_clean

    return pd.Series({
        "Transaction_Type": "DEP TFR" if is_dep else ("WDL TFR" if is_wdl else "N/A"),
        "Transaction_Mode": transaction_mode,
        "DR/CR_Indicator": "CR" if is_dep else ("DR" if is_wdl else "N/A"),
        "Transaction_ID": txn_id,
        "Recipient_Name": description_clean.split("/")[1] if "/" in description_clean else "N/A",
        "Bank": "N/A",
        "UPI_ID": "N/A",
        "Note": note,
    })
