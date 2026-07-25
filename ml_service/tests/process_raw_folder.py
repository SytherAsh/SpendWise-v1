#!/usr/bin/env python3
"""Process a file already sitting in data/statement_uploads/raw/ and write the
one cleaned CSV to data/statement_uploads/processed/ -- the same two functions
the live API route uses (app.parsers.statementLoader.load_statement_data,
app.services.statementParser.normalize_statement_dataframe), just triggered by
hand instead of over HTTP. For manual drop-a-file-in-and-run use; the API
route is still the one to use for the real per-user upload flow.

Usage:
    # Picks the single most-recently-modified file in raw/ automatically.
    python process_raw_folder.py

    # Or name it explicitly (required if raw/ has more than one file, so this
    # never silently guesses which one you mean).
    python process_raw_folder.py 2020-2026_20260725_161922_123456.xlsx

    # Password: set BANK_STATEMENT_PASSWORD in .env, or pass --password.
    python process_raw_folder.py --password YOUR_PASSWORD
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ML_SERVICE_DIR = TESTS_DIR.parent
if (ML_SERVICE_DIR / "app").exists():
    sys.path.insert(0, str(ML_SERVICE_DIR))

from dotenv import load_dotenv

load_dotenv()

from app.parsers.statementLoader import StatementLoadError, load_statement_data
from app.services.statementFileManager import RAW_UPLOAD_ROOT, save_processed_csv
from app.services.statementParser import normalize_statement_dataframe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "file_name",
        nargs="?",
        default=None,
        help="Name of the file inside data/statement_uploads/raw/ to process. "
             "If omitted, the most recently modified file there is used.",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("BANK_STATEMENT_PASSWORD"),
        help="Password for a protected workbook. Defaults to the BANK_STATEMENT_PASSWORD env var.",
    )
    return parser.parse_args()


def pick_file(file_name: str | None) -> Path:
    if file_name:
        path = RAW_UPLOAD_ROOT / file_name
        if not path.exists():
            raise FileNotFoundError(f"'{file_name}' not found in {RAW_UPLOAD_ROOT}")
        return path

    candidates = [p for p in RAW_UPLOAD_ROOT.iterdir() if p.is_file() and p.name != ".gitkeep"]
    if not candidates:
        raise FileNotFoundError(f"No files in {RAW_UPLOAD_ROOT}. Drop a statement file there first.")
    if len(candidates) > 1:
        names = "\n".join(f"  - {p.name}" for p in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True))
        raise ValueError(
            f"Multiple files in {RAW_UPLOAD_ROOT}, pass one explicitly:\n{names}"
        )
    return candidates[0]


def main() -> int:
    args = parse_args()

    try:
        target = pick_file(args.file_name)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))
        return 1

    print(f"Processing: {target}")

    try:
        raw_df, account_metadata = load_statement_data(
            [(target.name, target.read_bytes())], password=args.password
        )
    except StatementLoadError as exc:
        print(f"Could not read this file: {exc}")
        return 1

    if raw_df.empty:
        print("No transaction rows were found in this file.")
        return 1

    cleaned_df = normalize_statement_dataframe(raw_df)
    output_path = save_processed_csv(cleaned_df, target.name, account_metadata=account_metadata)

    print(f"\nAccount metadata: {account_metadata}")
    print(f"Rows processed: {len(cleaned_df)}")
    print(f"Columns: {cleaned_df.columns.tolist()}")
    print(f"\nFinal CSV written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
