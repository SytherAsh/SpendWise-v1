import io
import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import msoffcrypto
import pandas as pd
from msoffcrypto.exceptions import InvalidKeyError

SUPPORTED_CSV_EXTENSIONS = {".csv"}
SUPPORTED_EXCEL_EXTENSIONS = {".xls", ".xlsx", ".xlsm", ".xlsb"}
SUPPORTED_EXTENSIONS = SUPPORTED_CSV_EXTENSIONS | SUPPORTED_EXCEL_EXTENSIONS


class StatementLoadError(Exception):
    """Base for statement-loading failures that are the caller's fault (bad file,
    wrong/missing password) rather than an internal bug -- callers map these to
    a 4xx response instead of letting them surface as a 500."""


class PasswordRequiredError(StatementLoadError):
    """The workbook is encrypted and no password was supplied."""


class InvalidPasswordError(StatementLoadError):
    """The workbook is encrypted and the supplied password did not decrypt it."""


class UnsupportedFileTypeError(StatementLoadError):
    """The uploaded file's extension isn't a statement format we can read."""


def truncate_at_first_blank_row(df: pd.DataFrame) -> pd.DataFrame:
    blank_rows = df.apply(
        lambda row: all(pd.isna(x) or str(x).strip() == "" for x in row),
        axis=1,
    )
    if blank_rows.any():
        first_blank = blank_rows[blank_rows].index[0]
        df = df.iloc[:first_blank]
    return df.reset_index(drop=True)


def _as_path_suffix(file_name: str) -> str:
    return Path(file_name).suffix.lower()


def find_table_start_row(df_no_header: pd.DataFrame, max_scan: int = 40) -> int:
    """Locate the real 'Date | Details | ... | Balance' transaction-table row.

    Bank exports (SBI in particular) prepend a variable-length letterhead block
    above the table -- account holder name, address, account number, IFSC, phone,
    etc. Reading the file at row 0 would treat that PII block as column headers/
    data instead of skipping it. Falls back to row 0 (no letterhead) if nothing
    matches within max_scan, so plain table-only CSV/Excel exports still work.
    """
    for i in range(min(max_scan, len(df_no_header))):
        row_values = [str(x).strip().lower() for x in df_no_header.iloc[i].tolist()]
        if "date" in row_values and "balance" in row_values:
            return i
    return 0


def mask_identifier(value, keep_last: int = 4) -> Optional[str]:
    """Mask a sensitive numeric identifier, keeping only the last few digits
    so the source account is still distinguishable without exposing it."""
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        return None
    if len(digits) <= keep_last:
        return "X" * len(digits)
    return "X" * (len(digits) - keep_last) + digits[-keep_last:]


def _split_label_value(cell_text) -> dict:
    """Parse 'Label  :  Value' lines out of a header cell into {label: value}."""
    result = {}
    for line in str(cell_text).split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = re.sub(r"\s+", " ", label.strip().lower())
        value = value.strip()
        if label and value:
            result[label] = value
    return result


_NAME_PREFIX_RE = re.compile(r"\s*(?:Mr\.|Mrs\.|Ms\.)?\s*([A-Za-z .]+)")


def parse_account_metadata(raw_no_header: pd.DataFrame, table_start_row: int) -> dict:
    """Extract account-holder metadata from the letterhead block above the
    transaction table (rows before `table_start_row`).

    Only ever returns the account holder's display name and a last-4-masked
    account number -- the raw account number, email, CIF/MICR/IFSC codes and
    branch contact details found along the way are used transiently to derive
    those two fields and are never included in the return value, so they can
    never end up in a DataFrame/CSV. See docs/spec/security.md.

    The name is read from the first column's first line of the first
    letterhead row that isn't a "Label : value" field (that's always the
    account holder's name in every SBI export seen so far -- first line of
    the row directly under the blank spacer, e.g. "Mrs. PRACHI SAMEER
    SAWANT\nNot Available" or "Mr. Yash Sameer Sawant\nyashsawnt...@gmail.com").
    Deliberately NOT anchored on "the first row containing an email" -- some
    account holders have no personal email on file ("Not Available"), and
    anchoring there previously fell through to the branch's own contact email
    further down and mis-captured its neighboring field label as the name.
    """
    header_block = raw_no_header.iloc[:table_start_row]

    fields: dict = {}
    name_line = None
    for _, row in header_block.iterrows():
        if name_line is None and len(row) and pd.notna(row.iloc[0]):
            first_line = str(row.iloc[0]).split("\n")[0].strip()
            if first_line and ":" not in first_line:
                name_line = first_line
        for cell in row.tolist():
            if pd.notna(cell):
                fields.update(_split_label_value(cell))

    holder_name = None
    if name_line:
        name_match = _NAME_PREFIX_RE.match(name_line)
        holder_name = name_match.group(1).strip() if name_match else name_line

    return {
        "account_holder_name": holder_name,
        "masked_account_number": mask_identifier(fields.get("account number")),
    }


def _decrypt_if_needed(content: bytes, password: Optional[str] = None) -> bytes:
    office_file = msoffcrypto.OfficeFile(io.BytesIO(content))
    if not office_file.is_encrypted():
        return content

    if not password:
        raise PasswordRequiredError(
            "This workbook is password protected. Provide the password to read it."
        )

    decrypted = io.BytesIO()
    try:
        office_file.load_key(password=password)
        office_file.decrypt(decrypted)
    except InvalidKeyError as exc:
        raise InvalidPasswordError(
            "The workbook could not be decrypted with the supplied password."
        ) from exc
    decrypted.seek(0)
    return decrypted.read()


def validate_file_type(file_name: str) -> None:
    """Reject unsupported uploads up front with a clear, user-facing message."""
    extension = _as_path_suffix(file_name)
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise UnsupportedFileTypeError(
            f"'{file_name}' is not a supported statement file. Supported types: {supported}."
        )


def _read_excel(content: bytes, password: Optional[str] = None) -> Tuple[pd.DataFrame, dict]:
    plain_bytes = _decrypt_if_needed(content, password=password)
    raw = pd.read_excel(io.BytesIO(plain_bytes), header=None)
    table_start = find_table_start_row(raw)
    metadata = parse_account_metadata(raw, table_start)
    df = pd.read_excel(io.BytesIO(plain_bytes), skiprows=table_start)
    return df, metadata


def _read_csv(content: bytes) -> Tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(io.BytesIO(content), header=None, dtype=str)
    table_start = find_table_start_row(raw)
    metadata = parse_account_metadata(raw, table_start)
    df = pd.read_csv(io.BytesIO(content), skiprows=table_start)
    return df, metadata


def read_statement_file(
    file_name: str, content: bytes, password: Optional[str] = None
) -> Tuple[pd.DataFrame, dict]:
    extension = _as_path_suffix(file_name)
    if extension in SUPPORTED_EXCEL_EXTENSIONS:
        return _read_excel(content, password=password)
    if extension in SUPPORTED_CSV_EXTENSIONS:
        return _read_csv(content)

    try:
        return _read_csv(content)
    except Exception:
        return _read_excel(content, password=password)


def load_statement_data(
    files: Iterable[Tuple[str, bytes]], password: Optional[str] = None
) -> Tuple[pd.DataFrame, List[dict]]:
    """Load and merge statement files.

    Returns (transactions_df, account_metadata) -- account_metadata is one
    {file_name, account_holder_name, masked_account_number} dict per input file,
    kept separate from the transaction rows on purpose: it's account-level
    (constant for a whole file), not transaction-level, so it doesn't belong
    stamped onto every row. See callers for how it's surfaced downstream.
    """
    dfs: List[pd.DataFrame] = []
    account_metadata: List[dict] = []

    for file_name, content in files:
        if not content:
            continue
        validate_file_type(file_name)
        df, metadata = read_statement_file(file_name, content, password=password)
        df = truncate_at_first_blank_row(df)
        if df.empty:
            continue
        dfs.append(df)
        account_metadata.append({"file_name": file_name, **metadata})

    merged = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return merged, account_metadata
