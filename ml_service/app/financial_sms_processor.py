"""
financial_sms_processor.py — Batch processor for CSV-based SMS pipeline.

Reads captured_sms.csv, deduplicates, parses, and writes:
  - clean_sms_eda.csv       (all records, cleaned)
  - true_financial_sms.csv  (only true financial transactions)

Optionally bulk-pushes financial transactions to Supabase.
The CSV pipeline is entirely independent of Supabase availability.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class FinancialSmsProcessor:
    """
    Handles processing of raw SMS/Notification data to extract precise
    financial details, segregate spam, and deduplicate cross-platform entries.
    """

    def __init__(
        self,
        input_file: str = "data/captured_sms.csv",
        output_file: str = "data/clean_sms_eda.csv",
        financial_output_file: str = "data/true_financial_sms.csv",
    ):
        self.input_file            = input_file
        self.output_file           = output_file
        self.financial_output_file = financial_output_file

    # ------------------------------------------------------------------
    # Internal parsing
    # ------------------------------------------------------------------

    def _parse_financial_sms(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Use centralized logic from sms_parser to extract precise financial details.
        This guarantees the CSV perfectly matches what goes into Supabase.
        """
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.dirname(__file__)))
        from app.sms_parser import parse_sms_body

        df["body"]   = df["body"].fillna("").astype(str)
        df["sender"] = df["sender"].fillna("").astype(str)

        # Apply the parser
        parsed_results = df.apply(lambda row: parse_sms_body(row["body"], row["sender"]), axis=1)

        # Extract to columns
        df["parsed_is_financial"] = [p.is_financial for p in parsed_results]
        df["parsed_amount"]       = [p.amount for p in parsed_results]
        df["parsed_direction"]    = [p.direction for p in parsed_results]
        df["parsed_ref_id"]       = [p.ref_id for p in parsed_results]
        df["parsed_entity"]       = [p.recipient_name for p in parsed_results]
        df["parsed_bank"]         = [p.bank for p in parsed_results]

        # Fix the recipient and entity columns to use the properly cleaned entity
        df["recipient"] = df["parsed_entity"]
        df["entity"] = df["parsed_entity"]

        return df

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def process_all(self, push_to_supabase: bool = False) -> Optional[dict]:
        """
        Execute the full CSV pipeline:
          1. Read captured_sms.csv
          2. Remove exact duplicates
          3. Normalize timestamps (handles epoch ms AND ISO strings)
          4. Filter to current year (2026)
          5. Parse regex → financial fields
          6. Cross-platform deduplication (2-min window)
          7. Save clean_sms_eda.csv and true_financial_sms.csv
          8. (Optional) Bulk-push financial records to Supabase

        Args:
            push_to_supabase: If True, attempt to persist each financial row
                              to Supabase.  Failures are logged and ignored.

        Returns:
            Summary dict, or None if input file is missing.
        """
        if not os.path.exists(self.input_file):
            logger.error("Input file %s not found. Sync some data first.", self.input_file)
            print(f"Error: {self.input_file} not found. Please sync some data first!")
            return None

        print(f"Loading {self.input_file}...")
        df = pd.read_csv(self.input_file)

        # 1. Remove exact duplicates
        initial_count = len(df)
        df = df.drop_duplicates(subset=["body", "timestamp_ms"])
        print(f"Removed {initial_count - len(df)} duplicate records.")

        # 2. Normalise mixed timestamp formats (epoch ms OR ISO strings)
        numeric_ms  = pd.to_numeric(df["timestamp_ms"], errors="coerce")
        dt_from_ms  = pd.to_datetime(numeric_ms, unit="ms", utc=True, errors="coerce")
        dt_from_str = pd.to_datetime(df["timestamp_ms"], utc=True, errors="coerce")
        df["parsed_datetime"] = dt_from_ms.fillna(dt_from_str)

        # 3. Drop invalid timestamps
        invalid = df["parsed_datetime"].isna()
        if invalid.any():
            print(f"Dropped {invalid.sum()} records with missing/invalid timestamps.")
            df = df[~invalid]

        df = df.sort_values("parsed_datetime", ascending=True)
        df["date"] = df["parsed_datetime"].dt.date.astype(str)

        # 5. Parse financial details
        print("Parsing financial details using Regex...")
        df = self._parse_financial_sms(df)

        # Promote parsed columns → canonical columns
        df["is_financial"] = df["parsed_is_financial"]
        df["amount"]       = df["parsed_amount"]
        df["direction"]    = df["parsed_direction"]
        df["ref_id"]       = df["parsed_ref_id"]
        df["entity"]       = df["parsed_entity"]

        cols_to_drop = [
            "parsed_is_financial", "parsed_amount",
            "parsed_direction", "parsed_ref_id", "parsed_entity",
        ]
        df = df.drop(columns=cols_to_drop)

        # 6. Cross-platform deduplication (2-min window)
        pre_dedup = len(df)
        df["time_bucket_2m"] = df["parsed_datetime"].dt.floor("2min")
        df = df.drop_duplicates(subset=["amount", "direction", "time_bucket_2m"])
        df = df.drop(columns=["time_bucket_2m", "parsed_datetime"])
        cross_dup = pre_dedup - len(df)
        if cross_dup:
            print(f"Removed {cross_dup} cross-platform duplicates (SMS + Notification overlap).")

        # 7. Save CSVs
        df.to_csv(self.output_file, index=False)
        print(f"Success! Clean data saved to: {self.output_file}")

        financial_df = df[df["is_financial"] == True]
        financial_df.to_csv(self.financial_output_file, index=False)
        print(f"Saved strictly financial data to: {self.financial_output_file}")

        # 8. Optional Supabase push
        supabase_pushed = 0
        supabase_skipped = 0
        if push_to_supabase:
            supabase_pushed, supabase_skipped = self._push_to_supabase(financial_df)

        summary = {
            "total_records":                len(df),
            "financial_count":              len(financial_df),
            "cross_platform_duplicates_removed": cross_dup,
        }
        if push_to_supabase:
            summary["supabase_pushed"]  = supabase_pushed
            summary["supabase_skipped"] = supabase_skipped

        return summary

    # ------------------------------------------------------------------
    # Optional Supabase push
    # ------------------------------------------------------------------

    def _push_to_supabase(self, financial_df: pd.DataFrame) -> tuple[int, int]:
        """
        Attempt to persist each financial row to Supabase.

        Uses persist_sms_transaction() from service.py.
        Import is deferred so that the class can run without Supabase if needed.
        """
        try:
            from app.sms_parser import parse_sms_body, normalize_timestamp
            from app.service import persist_sms_transaction
        except ImportError as exc:
            logger.error("Cannot import Supabase service: %s", exc)
            return 0, len(financial_df)

        pushed  = 0
        skipped = 0

        for _, row in financial_df.iterrows():
            try:
                # Re-parse to get structured ParsedTransaction object
                parsed = parse_sms_body(
                    body=str(row.get("body", "")),
                    sender=str(row.get("sender", "")),
                )

                if not parsed.is_financial:
                    skipped += 1
                    continue

                ts_iso = normalize_timestamp(row.get("timestamp_ms"))
                result = persist_sms_transaction(
                    parsed=parsed,
                    timestamp_iso=ts_iso,
                    body=str(row.get("body", "")),
                )
                if result:
                    pushed += 1
                else:
                    skipped += 1

            except Exception as exc:
                logger.error("Supabase push failed for row id=%s | %s", row.get("id"), exc)
                skipped += 1

        print(f"Supabase: pushed={pushed}, skipped/duplicate={skipped}")
        return pushed, skipped


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )

    parser = argparse.ArgumentParser(description="SpendWise CSV pipeline")
    parser.add_argument(
        "--push-supabase",
        action="store_true",
        default=False,
        help="Also push financial transactions to Supabase after CSV processing",
    )
    args = parser.parse_args()

    processor = FinancialSmsProcessor()
    result    = processor.process_all(push_to_supabase=args.push_supabase)
    if result:
        print("\n=== Summary ===")
        for k, v in result.items():
            print(f"  {k}: {v}")
