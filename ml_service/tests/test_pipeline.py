#!/usr/bin/env python3
"""Manual test pipeline for bank statement ingestion.

Run SBI statement files through the same loading and normalization pipeline used by
ml_service, then optionally save intermediate CSVs for inspection.

Usage examples (run from the ml_service/tests/ directory, or pass an absolute
input path from anywhere):
    # Set BANK_STATEMENT_PASSWORD in your shell first (never pass secrets on the CLI):
    #   PowerShell:  $env:BANK_STATEMENT_PASSWORD = "..."
    #   Bash:        export BANK_STATEMENT_PASSWORD="..."

    python test_pipeline.py \
        "C:/Users/yashs/Desktop/Journey/SpendWise/ml_preprocessing/CSVS/SBI/Pswd_Protected/2020-2026.xlsx" \
        --interactive

    python test_pipeline.py \
        data/statements/statement1.csv data/statements/statement2.csv \
        --output-dir pipeline-debug --save-steps
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

TESTS_DIR = Path(__file__).resolve().parent
ML_SERVICE_DIR = TESTS_DIR.parent  # ml_service/ (contains the "app" package)
if (ML_SERVICE_DIR / "app").exists():
    sys.path.insert(0, str(ML_SERVICE_DIR))
else:
    sys.path.insert(0, str(TESTS_DIR))

load_dotenv()  # picks up BANK_STATEMENT_PASSWORD (and friends) from the repo-root .env

from app.parsers.statementLoader import load_statement_data
from app.services.statementParser import (
    SENTINELS,
    _clean_description,
    _drop_blank_rows,
    _ensure_description,
    _normalize_columns_for_output,
    _remove_overlapping_duplicates,
    clean_recipient,
    determine_dr_cr,
    extract_details,
    merge_prefix_chains,
    merge_whitespace_variants,
    normalize_recipients,
    normalize_statement_dataframe,
    redact_insurance_cif,
    rename_columns,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run and inspect the bank statement ingestion pipeline step by step."
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="One or more CSV/XLSX/XLSB bank statement files to process.",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("BANK_STATEMENT_PASSWORD"),
        help="Password for password-protected Excel files. "
             "Defaults to the BANK_STATEMENT_PASSWORD environment variable.",
    )
    parser.add_argument(
        "--output-dir",
        default="tmp_pipeline_outputs",
        help="Directory where pipeline CSV outputs are written (gitignored by default).",
    )
    parser.add_argument(
        "--save-steps",
        action="store_true",
        help="Save every intermediate step to CSV for manual inspection.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Pause after every intermediate CSV is saved so you can open and inspect it "
             "before the pipeline moves to the next step. Implies --save-steps.",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="Skip comparison between stepwise output and normalize_statement_dataframe().",
    )
    args = parser.parse_args()
    if args.interactive:
        args.save_steps = True
    return args


def ensure_output_dir(base_dir: Path) -> Path:
    path = base_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    path.mkdir(parents=True, exist_ok=True)
    return path


class PipelineHalted(Exception):
    """Raised when the user quits out of an --interactive run."""


def save_dataframe(df: pd.DataFrame, path: Path, description: str) -> None:
    df.to_csv(path, index=False)
    print(f"Saved {description}: {path} ({len(df)} rows, {len(df.columns)} cols)")


def confirm_step(path: Path, interactive: bool) -> None:
    """Pause after a step CSV is written so the user can open and eyeball it.

    Only engaged with --interactive. 'o' shells out to the OS default app for the
    file (Excel/VS Code/etc.) so the user doesn't have to alt-tab and navigate to
    a timestamped folder by hand for every single step.
    """
    if not interactive:
        return
    while True:
        choice = input(
            f"    -> inspect {path.name} now, then [Enter]=continue  o=open file  q=quit: "
        ).strip().lower()
        if choice == "":
            return
        if choice == "o":
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except Exception as exc:
                print(f"    Could not open file automatically: {exc}")
            continue
        if choice == "q":
            raise PipelineHalted(f"Stopped by user after step: {path.name}")
        print("    Unrecognized input, try again.")


def save_and_confirm(df: pd.DataFrame, path: Path, description: str, interactive: bool) -> None:
    save_dataframe(df, path, description)
    confirm_step(path, interactive)


def print_summary(df: pd.DataFrame, label: str) -> None:
    print(f"\n--- {label} ---")
    print(f"Rows: {len(df)}, Columns: {len(df.columns)}")
    print("Columns:", ", ".join(df.columns.tolist()))
    print(df.head(5).to_string(index=False))
    missing = df.isna().sum()
    print("Missing values by column:")
    print(missing[missing > 0].to_string() if missing.any() else "None")


def run_stepwise_pipeline(
    raw_df: pd.DataFrame, save_steps: bool, output_dir: Path, interactive: bool = False
) -> pd.DataFrame:
    df = raw_df.copy()
    if save_steps:
        save_and_confirm(df, output_dir / "01_raw_merged.csv", "raw merged input", interactive)

    df = rename_columns(df)
    print_summary(df, "After rename_columns")
    if save_steps:
        save_and_confirm(df, output_dir / "02_renamed_columns.csv", "after renaming columns", interactive)

    df = _ensure_description(df)
    print_summary(df, "After ensure_description")
    if save_steps:
        save_and_confirm(df, output_dir / "03_ensured_description.csv", "after ensuring description", interactive)

    df = _clean_description(df)
    print_summary(df, "After clean_description")
    if save_steps:
        save_and_confirm(df, output_dir / "04_cleaned_description.csv", "after cleaning descriptions", interactive)

    df = _remove_overlapping_duplicates(df)
    print_summary(df, "After remove_overlapping_duplicates")
    if save_steps:
        save_and_confirm(df, output_dir / "05_removed_duplicates.csv", "after duplicate removal", interactive)

    df = _drop_blank_rows(df)
    print_summary(df, "After drop_blank_rows")
    if save_steps:
        save_and_confirm(df, output_dir / "06_dropped_blank_rows.csv", "after dropping blank rows", interactive)

    if "Description" in df.columns:
        extracted = df["Description"].apply(extract_details)
        df = pd.concat([df, extracted], axis=1)
        df = redact_insurance_cif(df)
        print_summary(df, "After extract_details")
        if save_steps:
            save_and_confirm(df, output_dir / "07_extracted_details.csv", "after extracting details", interactive)

    for column_name in ("Debit", "Credit", "Balance"):
        if column_name in df.columns:
            values = (
                df[column_name]
                .astype(str)
                .str.replace(",", "", regex=False)
                .str.strip()
                .replace({"": None, "nan": None, "NaN": None, "None": None})
            )
            df[column_name] = pd.to_numeric(values, errors="coerce").fillna(0.0)
    if save_steps:
        save_and_confirm(df, output_dir / "08_numeric_sanitized.csv", "after numeric sanitization", interactive)

    if "Amount" not in df.columns:
        df["Amount"] = df.get("Credit", 0.0) - df.get("Debit", 0.0)
    print_summary(df, "After amount calculation")
    if save_steps:
        save_and_confirm(df, output_dir / "09_amount_calculated.csv", "after amount calculation", interactive)

    df = determine_dr_cr(df)
    print_summary(df, "After determine_dr_cr")
    if save_steps:
        save_and_confirm(df, output_dir / "10_dr_cr_determined.csv", "after DR/CR determination", interactive)

    if "Transaction_Date" in df.columns:
        df["Transaction_Date"] = pd.to_datetime(
            df["Transaction_Date"],
            dayfirst=True,
            errors="coerce",
        )
        df["Transaction_Date"] = df["Transaction_Date"].dt.date.astype(str)
    if save_steps:
        save_and_confirm(
            df, output_dir / "11_dates_normalized.csv", "after transaction date normalization", interactive
        )

    if "Recipient_Name" not in df.columns:
        df["Recipient_Name"] = None
    df["Recipient_Name"] = df["Recipient_Name"].apply(clean_recipient)
    df["Recipient_Canonical"] = normalize_recipients(df)

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
    if save_steps:
        save_and_confirm(
            df, output_dir / "12_normalized_recipients.csv", "after recipient normalization", interactive
        )

    df.replace("N/A", None, inplace=True)
    df = df.replace([np.inf, -np.inf], None)
    df = df.astype(object)
    df = df.where(pd.notna(df), None)
    df = _normalize_columns_for_output(df)

    print_summary(df, "Final stepwise output")
    if save_steps:
        save_and_confirm(df, output_dir / "13_final_stepwise_output.csv", "final stepwise output", interactive)

    return df


def export_account_metadata_to_env(account_metadata: list) -> None:
    """Expose account-holder name / masked account number via env vars for any
    downstream step (e.g. a future self-transfer or per-account categorization
    pass) instead of stamping them onto every transaction row.

    Safe here only because this is a single sequential CLI process. Do NOT
    port this pattern to the FastAPI route -- a process-wide env var would
    leak one user's account info into a concurrently-handled request for
    another user. The route returns account_metadata as a separate top-level
    response field instead.
    """
    if not account_metadata:
        return
    print("\nAccount metadata (kept out of the transaction rows):")
    for meta in account_metadata:
        print(f"  {meta['file_name']}: {meta.get('account_holder_name')} / {meta.get('masked_account_number')}")

    primary = account_metadata[0]
    if primary.get("account_holder_name"):
        os.environ["STATEMENT_ACCOUNT_HOLDER_NAME"] = primary["account_holder_name"]
    if primary.get("masked_account_number"):
        os.environ["STATEMENT_MASKED_ACCOUNT_NUMBER"] = primary["masked_account_number"]


def main() -> int:
    args = parse_args()
    output_base = ensure_output_dir(Path(args.output_dir))
    file_paths = [Path(path).expanduser() for path in args.files]

    files_to_load = []
    for path in file_paths:
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        files_to_load.append((path.name, path.read_bytes()))

    print("Loading input files:")
    for path in file_paths:
        print(f" - {path}")

    raw_df, account_metadata = load_statement_data(files_to_load, password=args.password)
    if raw_df.empty:
        print("No rows could be read from the uploaded file(s). Check the file format and password.")
        return 1

    export_account_metadata_to_env(account_metadata)

    print_summary(raw_df, "Raw merged data")
    if args.save_steps:
        save_and_confirm(raw_df, output_base / "00_raw_merged.csv", "raw merged data", args.interactive)

    try:
        final_stepwise = run_stepwise_pipeline(raw_df, args.save_steps, output_base, args.interactive)
    except PipelineHalted as exc:
        print(f"\n{exc}")
        print(f"Partial outputs saved to: {output_base}")
        return 1

    final_toplevel = normalize_statement_dataframe(raw_df)
    save_dataframe(final_toplevel, output_base / "final_normalize_statement_dataframe.csv", "final normalized output")

    if not args.no_compare:
        print("\nComparing final stepwise output with normalize_statement_dataframe() output...")
        if final_stepwise.shape != final_toplevel.shape:
            print(
                f"Shape mismatch: stepwise={final_stepwise.shape}, toplevel={final_toplevel.shape}"
            )
        else:
            differences = (final_stepwise.fillna("") != final_toplevel.fillna(""))
            if differences.values.any():
                diff_columns = [
                    col for col in final_stepwise.columns
                    if (final_stepwise[col].fillna("") != final_toplevel[col].fillna("")).any()
                ]
                print(f"Columns with differences: {diff_columns}")
            else:
                print("Both outputs match exactly on shape and values.")

    print(f"\nSaved pipeline outputs to: {output_base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
