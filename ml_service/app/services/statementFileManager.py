from datetime import datetime
from pathlib import Path
import re
from typing import Optional, Sequence

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
UPLOAD_ROOT = DATA_ROOT / "statement_uploads"
RAW_UPLOAD_ROOT = UPLOAD_ROOT / "raw"
PROCESSED_UPLOAD_ROOT = UPLOAD_ROOT / "processed"


def ensure_statement_upload_dirs() -> None:
    for path in (RAW_UPLOAD_ROOT, PROCESSED_UPLOAD_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def _sanitize_file_name(file_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_ ")
    return cleaned or "uploaded_file"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def save_raw_upload(file_name: str, content: bytes) -> Path:
    ensure_statement_upload_dirs()
    safe_name = _sanitize_file_name(file_name)
    timestamped_name = f"{Path(safe_name).stem}_{_timestamp()}{Path(safe_name).suffix}"
    saved_path = RAW_UPLOAD_ROOT / timestamped_name
    saved_path.write_bytes(content)
    return saved_path


def _processed_file_stem(original_name: Optional[str], account_metadata: Optional[Sequence[dict]]) -> str:
    """Prefer naming the output after the account holder / masked account number
    (from the first file's metadata) over the raw upload filename -- once more
    than one account's statements pass through the same raw/processed folders
    (e.g. two family members' exports), "cleaned_2020-2026_....csv" tells you
    nothing about whose data it is, but "cleaned_PRACHI_SAMEER_SAWANT_XXXXXXX3861_....csv"
    does. Falls back to the upload filename when no metadata was extracted
    (e.g. a plain CSV with no letterhead to read a name from).
    """
    if account_metadata:
        first = account_metadata[0]
        parts = [p for p in (first.get("account_holder_name"), first.get("masked_account_number")) if p]
        if parts:
            return _sanitize_file_name("_".join(parts))

    stem = Path(original_name or "cleaned_statements").stem
    return _sanitize_file_name(stem)


def save_processed_csv(
    df, original_name: Optional[str] = None, account_metadata: Optional[Sequence[dict]] = None
) -> Path:
    ensure_statement_upload_dirs()
    safe_stem = _processed_file_stem(original_name, account_metadata)
    output_name = f"cleaned_{safe_stem}_{_timestamp()}.csv"
    output_path = PROCESSED_UPLOAD_ROOT / output_name
    df.to_csv(output_path, index=False)
    return output_path


def resolve_processed_file(file_name: str) -> Path:
    """Resolve a user-supplied name to a file inside the processed/ directory.

    The name arrives from a URL path segment, so it is untrusted: without this
    check a traversal payload ("../../.env", or an absolute path) would let a
    caller read arbitrary files off the server. Taking only the basename strips
    any directory component, and the resolved-parent check is the actual
    guarantee -- it also refuses a symlink inside processed/ that points
    somewhere else.
    """
    candidate = Path(file_name).name
    if not candidate or candidate != file_name:
        raise ValueError("Invalid file name.")

    resolved = (PROCESSED_UPLOAD_ROOT / candidate).resolve()
    if resolved.parent != PROCESSED_UPLOAD_ROOT.resolve():
        raise ValueError("Invalid file name.")
    return resolved
