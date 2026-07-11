"""
sms_pipeline.py — Staged, re-runnable pipeline: raw captured SMS -> clean financial transactions.

Built stage by stage. Each stage is a pure DataFrame -> DataFrame transform so the
whole thing is re-runnable over the raw file at any time (the raw capture is never
mutated). Paths are anchored to the ml_service/ root, so it runs from any cwd.

Stages:
  1. Structural clean   — load, drop exact resends, normalize timestamps, sort.   [this commit]
  2/3. Classify+extract — one parse_sms_body() pass per row.                       [next]
  4. Transaction dedup  — collapse SMS+notification pairs by (amount,dir,window).  [next]
  5. Validate           — extraction-rate + balance-vs-amount audits.             [next]
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Anchor all data paths to ml_service/ (this file lives in ml_service/app/).
_ML_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _data_path(name: str) -> str:
    return os.path.join(_ML_SERVICE_ROOT, "data", name)


# Raw capture schema written by routes/ingest.py — the pipeline's only input contract.
RAW_REQUIRED_COLUMNS = ["id", "sender", "body", "timestamp_ms"]

# All data is from an Indian user + Indian banks; timestamps and bank date-stamps
# ("...on 09Nov22...") are IST. We attribute dates in IST so late-night transactions
# land on the correct calendar day (UTC would push them to the previous day).
IST = "Asia/Kolkata"

# A message with confidence below this (or labeled UNKNOWN) goes to the human-review
# queue rather than being trusted outright.
REVIEW_CONFIDENCE_THRESHOLD = 0.75

# Cross-channel dedup window: an SMS and its app-notification twin arrive within a
# minute or two. Two genuine same-amount payments are essentially never this close,
# so a tight window collapses duplicates without merging distinct transactions.
DEDUP_WINDOW = "2min"

# Final output column order for the clean financial CSV.
FINANCIAL_COLUMNS = [
    "event_time", "date", "amount", "direction", "bank", "transaction_mode",
    "recipient_name", "upi_id", "ref_id", "balance_after",
    "classification_confidence", "sender", "body",
]

REVIEW_COLUMNS = [
    "event_time", "sender", "body", "predicted_label",
    "classification_confidence", "review_status", "true_label",
]


class SmsPipeline:
    """Transforms data/captured_sms.csv into clean, deduplicated financial transactions."""

    def __init__(
        self,
        input_file: Optional[str] = None,
        clean_output: Optional[str] = None,
        financial_output: Optional[str] = None,
        review_output: Optional[str] = None,
    ):
        self.input_file = input_file or _data_path("captured_sms.csv")
        self.clean_output = clean_output or _data_path("clean_sms_eda.csv")
        self.financial_output = financial_output or _data_path("true_financial_sms.csv")
        self.review_output = review_output or _data_path("review_queue_sms.csv")

    # ------------------------------------------------------------------
    # Stage 1 — structural cleaning (content-agnostic)
    # ------------------------------------------------------------------

    def load(self) -> pd.DataFrame:
        """Read the raw capture file and validate its schema."""
        if not os.path.exists(self.input_file):
            raise FileNotFoundError(
                f"{self.input_file} not found — sync some SMS from the app first."
            )
        df = pd.read_csv(self.input_file)
        missing = [c for c in RAW_REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(
                f"{self.input_file} is missing required columns {missing}; "
                f"found {list(df.columns)}."
            )
        logger.info("Loaded %d raw rows from %s", len(df), self.input_file)
        return df

    def structural_clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """Stage 1: make rows mechanically sound before any interpretation.

        - drop exact resends (same body + same timestamp = the app synced it twice)
        - normalize mixed timestamp formats (epoch-ms or ISO string) -> tz-aware UTC
        - drop rows whose timestamp can't be parsed (counted, not silently swallowed)
        - sort oldest-first and derive a plain `date` column

        Deliberately does NOT filter by year — the raw file legitimately spans several
        years and every one of them is real transaction history.
        """
        n0 = len(df)

        # Normalize text columns up front so downstream never sees NaN in body/sender.
        df = df.copy()
        df["body"] = df["body"].fillna("").astype(str)
        df["sender"] = df["sender"].fillna("").astype(str)

        # 1. Exact resend removal.
        df = df.drop_duplicates(subset=["body", "timestamp_ms"])
        n_exact = n0 - len(df)

        # 2. Timestamp normalization — handle epoch-ms and ISO strings in one column,
        #    then express in IST so dates match the user's / bank's local calendar day.
        numeric_ms = pd.to_numeric(df["timestamp_ms"], errors="coerce")
        dt_from_ms = pd.to_datetime(numeric_ms, unit="ms", utc=True, errors="coerce")
        string_ms = df["timestamp_ms"].where(numeric_ms.isna())
        dt_from_str = pd.to_datetime(string_ms, utc=True, errors="coerce", format="mixed")
        df["event_time"] = dt_from_ms.fillna(dt_from_str).dt.tz_convert(IST)

        # 3. Drop unparseable timestamps.
        bad = df["event_time"].isna()
        n_bad = int(bad.sum())
        if n_bad:
            logger.warning("Dropping %d rows with unparseable timestamps", n_bad)
        df = df[~bad]

        # 4. Sort + derive IST calendar date.
        df = df.sort_values("event_time", ascending=True).reset_index(drop=True)
        df["date"] = df["event_time"].dt.date.astype(str)

        logger.info(
            "Stage 1 done | in=%d exact_resends=%d bad_ts=%d out=%d",
            n0, n_exact, n_bad, len(df),
        )
        return df

    # ------------------------------------------------------------------
    # Stage 2 + 3 — classify and extract (one parser pass per row)
    # ------------------------------------------------------------------

    def classify_and_extract(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run parse_sms_body() once per row and materialize its output as columns.

        Classification (Stage 2) and field extraction (Stage 3) happen together in
        a single parser call, so this is one pass, not two. The parser is the shared
        engine used by the live ingest route too — same logic, same results.
        """
        # Imported here (not at module top) so Stage 1 has no parser dependency.
        from app.parsers.sms_parser import parse_sms_body

        parsed = df.apply(lambda r: parse_sms_body(r["body"], r["sender"]), axis=1)
        df = df.copy()
        df["classification_label"] = [p.classification_label for p in parsed]
        df["classification_confidence"] = [p.classification_confidence for p in parsed]
        df["amount"] = [p.amount for p in parsed]
        df["direction"] = [p.direction for p in parsed]
        df["bank"] = [p.bank for p in parsed]
        df["transaction_mode"] = [p.transaction_mode for p in parsed]
        df["recipient_name"] = [p.recipient_name for p in parsed]
        df["upi_id"] = [p.upi_id for p in parsed]
        df["ref_id"] = [p.ref_id for p in parsed]
        df["balance_after"] = [p.balance_after for p in parsed]

        counts = df["classification_label"].value_counts().to_dict()
        logger.info("Stage 2/3 done | labels=%s", counts)
        return df

    # ------------------------------------------------------------------
    # Stage 4 — transaction dedup (collapse SMS + notification twins)
    # ------------------------------------------------------------------

    def dedup_transactions(self, financial: pd.DataFrame) -> pd.DataFrame:
        """Collapse cross-channel duplicates among financial rows.

        The same payment can arrive as a bank SMS and an app notification with
        different text but the same amount, direction, and near-identical time.
        We group on (amount, direction, 2-minute bucket) and keep ONE row per group,
        preferring the bank SMS: the row that has a ref_id, then higher confidence.

        Uses a tight time window (not same calendar day) on purpose — the same
        amount recurring on different days (e.g. a monthly recharge) is a genuinely
        distinct transaction and must not be merged.
        """
        if financial.empty:
            return financial

        df = financial.copy()
        df["_bucket"] = df["event_time"].dt.floor(DEDUP_WINDOW)
        # Rank within each duplicate group: has-ref first, then confidence, then earliest.
        df["_has_ref"] = df["ref_id"].notna().astype(int)
        df = df.sort_values(
            ["amount", "direction", "_bucket", "_has_ref", "classification_confidence", "event_time"],
            ascending=[True, True, True, False, False, True],
        )
        n_before = len(df)
        df = df.drop_duplicates(subset=["amount", "direction", "_bucket"], keep="first")
        n_removed = n_before - len(df)

        df = df.drop(columns=["_bucket", "_has_ref"]).sort_values("event_time").reset_index(drop=True)
        logger.info("Stage 4 done | financial_in=%d cross_channel_removed=%d out=%d",
                    n_before, n_removed, len(df))
        return df

    # ------------------------------------------------------------------
    # Review queue — low-confidence / UNKNOWN rows for manual labeling
    # ------------------------------------------------------------------

    def build_review_queue(self, df: pd.DataFrame) -> pd.DataFrame:
        """Rows the classifier isn't sure about — the highest-leverage thing to label."""
        review = df[
            df["classification_label"].eq("UNKNOWN")
            | (df["classification_confidence"] < REVIEW_CONFIDENCE_THRESHOLD)
        ].copy()
        if review.empty:
            return pd.DataFrame(columns=REVIEW_COLUMNS)
        review["predicted_label"] = review["classification_label"]
        review["review_status"] = np.where(
            review["classification_label"].eq("UNKNOWN"), "needs_label", "needs_review"
        )
        review["true_label"] = ""
        return review[REVIEW_COLUMNS].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Stage 5 — validation / quality audit (measures, never mutates)
    # ------------------------------------------------------------------

    def validate(self, financial: pd.DataFrame) -> dict:
        """Content-level quality checks — the numbers a rate table alone would hide."""
        if financial.empty:
            return {"financial_rows": 0}

        def rate(col: str) -> str:
            return f"{100 * financial[col].notna().mean():.1f}%"

        both = financial[financial["balance_after"].notna()]
        amount_eq_balance = int((both["amount"] - both["balance_after"]).abs().lt(0.01).sum())

        return {
            "financial_rows": len(financial),
            "direction_split": financial["direction"].value_counts().to_dict(),
            "extraction_rates": {
                "amount": rate("amount"), "direction": rate("direction"),
                "bank": rate("bank"), "ref_id": rate("ref_id"),
                "recipient_name": rate("recipient_name"), "upi_id": rate("upi_id"),
            },
            "amount_eq_balance_suspects": amount_eq_balance,
            "amount_total": round(float(financial["amount"].sum()), 2),
            "amount_max": round(float(financial["amount"].max()), 2),
        }

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run(self, write: bool = True) -> dict:
        """Execute all stages end to end and (optionally) write output CSVs."""
        df = self.classify_and_extract(self.structural_clean(self.load()))

        financial = df[df["classification_label"].eq("FINANCIAL_TRANSACTION")].copy()
        financial = self.dedup_transactions(financial)
        review = self.build_review_queue(df)

        # Flatten multi-line bodies so the output CSV stays one row per record.
        for frame in (financial, review):
            if not frame.empty:
                frame["body"] = frame["body"].str.replace(r"[\r\n]+", " ", regex=True).str.slice(0, 300)

        summary = {
            "total_clean_rows": len(df),
            "label_counts": df["classification_label"].value_counts().to_dict(),
            "review_rows": len(review),
            "validation": self.validate(financial),
        }

        if write:
            df.to_csv(self.clean_output, index=False)
            financial[FINANCIAL_COLUMNS].to_csv(self.financial_output, index=False)
            review.to_csv(self.review_output, index=False)
            summary["outputs"] = {
                "clean": self.clean_output,
                "financial": self.financial_output,
                "review": self.review_output,
            }
        return summary


# ------------------------------------------------------------------
# CLI — run the full pipeline and print a summary
# ------------------------------------------------------------------

if __name__ == "__main__":
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )

    summary = SmsPipeline().run(write=True)
    print("\n=== Pipeline summary ===")
    print(json.dumps(summary, indent=2, default=str))
