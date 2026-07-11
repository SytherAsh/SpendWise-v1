"""
ingest.py — FastAPI routes for SMS/notification ingestion.

Flow per message:
  1. Parse body with parse_sms_body() (used for the API response + Supabase only)
  2. Save the raw fields to local CSV (always — existing behaviour preserved).
     The CSV stores only what the phone actually sent (id/sender/body/timestamps/
     device_id) — no parser output. Classification/extraction is always re-derived
     downstream from `body` by app/services/sms_pipeline.py, so the raw file stays
     valid across parser changes.
  3. If financial → attempt Supabase persist via persist_sms_transaction()
     - On Supabase failure: log & continue; CSV is NOT affected
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime
from typing import List, Optional

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException, Query
# pyrefly: ignore [missing-import]
from pydantic import BaseModel

from app.schemas.transaction import SmsPayload, ParsedTransaction
from app.parsers.sms_parser import parse_sms_body, normalize_timestamp
from app.clients.supabase_client import supabase
from app.services.persistence import persist_sms_transaction, safe_value

logger = logging.getLogger(__name__)
router = APIRouter()

# -------------------------------------------------------
# Supabase table for raw incoming SMS (GET endpoints)
# -------------------------------------------------------
RAW_TABLE = "raw_sms"

# Local CSV path (relative to working directory of uvicorn launch)
CSV_FILE = "data/captured_sms.csv"

# Sync checkpoint: tracks last timestamp_ms per device_id
SYNC_STATE_FILE = "data/sync_state.json"

# Raw CSV schema — capture only, no parser output. Keep this list stable;
# changing it after captured_sms.csv already exists will misalign the header.
RAW_CSV_FIELDS = ["id", "sender", "body", "timestamp_ms", "timestamp_human", "device_id"]


# -------------------------------------------------------
# Response models
# -------------------------------------------------------

class IngestResponse(BaseModel):
    status: str
    raw_id: str
    is_financial: bool
    transaction_id: Optional[str] = None
    parsed: Optional[ParsedTransaction] = None


class BulkIngestResponse(BaseModel):
    total: int
    financial: int
    non_financial: int
    transactions_created: int
    errors: list[str]


class RawSmsOut(BaseModel):
    id: str
    source: str
    sender: str | None
    body: str
    timestamp_ms: int
    timestamp_human: str
    device_id: str
    is_financial: bool
    parsed_amount: float | None
    parsed_direction: str | None
    parsed_bank: str | None
    transaction_id: str | None
    created_at: str | None


# -------------------------------------------------------
# CSV helpers
# -------------------------------------------------------

def _warn_if_header_mismatch() -> None:
    """Log a loud warning if an existing captured_sms.csv has a stale header.

    Doesn't block the write (CSV capture must never fail a phone sync) — just
    surfaces the drift so it can't silently misalign columns unnoticed.
    """
    if not os.path.isfile(CSV_FILE):
        return
    try:
        with open(CSV_FILE, mode="r", newline="", encoding="utf-8") as f:
            header = next(csv.reader(f), None)
        if header is not None and header != RAW_CSV_FIELDS:
            logger.warning(
                "captured_sms.csv header %s does not match the current raw schema %s — "
                "archive or reset the file before continuing, or rows will misalign.",
                header, RAW_CSV_FIELDS,
            )
    except (OSError, StopIteration):
        pass


def save_to_csv(data_dict: dict) -> None:
    """Append a single record to the local CSV file."""
    _warn_if_header_mismatch()
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RAW_CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(data_dict)


def batch_save_to_csv(records: list[dict]) -> None:
    """Append a list of records to the local CSV file in one pass."""
    if not records:
        return
    _warn_if_header_mismatch()
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RAW_CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)


def _build_csv_record(payload: SmsPayload) -> dict:
    """Build a flat dict of pure raw capture fields — no parser output.

    Classification/extraction always happens downstream by re-parsing `body`
    (see app/services/sms_pipeline.py), so the raw file stays valid across
    parser changes and never needs to be re-captured from the phone.
    """
    return {
        "id":              payload.id,
        "sender":          payload.sender,
        "body":            payload.body,
        "timestamp_ms":    payload.timestamp_ms,
        "timestamp_human": payload.timestamp_human,
        "device_id":       payload.device_id,
    }


def _try_supabase_persist(
    parsed: ParsedTransaction,
    payload: SmsPayload,
) -> Optional[str]:
    """
    Attempt to persist a financial transaction to Supabase.

    Returns the transaction UUID on success, None on failure or skip.
    CSV pipeline is completely independent of this function.
    """
    try:
        timestamp_iso = normalize_timestamp(payload.timestamp_ms)
        result = persist_sms_transaction(
            parsed=parsed,
            timestamp_iso=timestamp_iso,
            body=payload.body,
        )
        if result:
            return str(result.get("id"))
        return None
    except Exception as exc:
        logger.error(
            "Supabase persist failed for id=%s | error=%s",
            payload.id,
            exc,
            exc_info=True,
        )
        return None


# -------------------------------------------------------
# POST /api/data — Single SMS ingest
# -------------------------------------------------------

@router.post("/api/data", response_model=IngestResponse)
async def ingest_single(payload: SmsPayload):
    """
    Receive a single SMS.
    Saves to local CSV unconditionally.
    If financial, also attempts Supabase persistence.
    """
    try:
        parsed = parse_sms_body(payload.body, sender=payload.sender)

        # 1. CSV (always)
        record = _build_csv_record(payload)
        save_to_csv(record)
        logger.info("✅ Saved to CSV: id=%s sender=%s", payload.id, payload.sender)

        # Track sync checkpoint
        _update_sync_timestamp(payload.device_id, payload.timestamp_ms)

        # 2. Supabase (only for financial transactions; failure is non-fatal)
        transaction_id: Optional[str] = None
        if parsed.is_financial:
            transaction_id = _try_supabase_persist(parsed, payload)
            if transaction_id:
                logger.info("💾 Persisted to Supabase: txn_id=%s", transaction_id)
            else:
                logger.warning(
                    "⚠️ Supabase persist skipped/failed for id=%s (CSV still saved)",
                    payload.id,
                )

        return IngestResponse(
            status="ok",
            raw_id=payload.id,
            is_financial=parsed.is_financial,
            transaction_id=transaction_id,
            parsed=parsed,
        )

    except Exception as exc:
        logger.error("Ingest failed for id=%s | error=%s", payload.id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# -------------------------------------------------------
# POST /api/data/bulk — Batch ingest
# -------------------------------------------------------

@router.post("/api/data/bulk", response_model=BulkIngestResponse)
async def ingest_bulk(payloads: List[SmsPayload]):
    """
    Receive multiple SMS records.
    All records are written to CSV in one batch.
    Financial ones are individually sent to Supabase.
    Errors are collected and returned; they never abort the batch.
    """
    financial_count      = 0
    non_financial_count  = 0
    transactions_created = 0
    errors: list[str]    = []
    csv_records: list[dict] = []

    for payload in payloads:
        try:
            parsed = parse_sms_body(payload.body, sender=payload.sender)

            if parsed.is_financial:
                financial_count += 1
            else:
                non_financial_count += 1

            csv_records.append(_build_csv_record(payload))

            # Supabase — only for financial, non-fatal
            if parsed.is_financial:
                txn_id = _try_supabase_persist(parsed, payload)
                if txn_id:
                    transactions_created += 1
                else:
                    logger.warning(
                        "Supabase skipped/failed for id=%s (CSV will still be saved)",
                        payload.id,
                    )

        except Exception as exc:
            errors.append(f"{payload.id}: {exc!s}")
            logger.error("Bulk item parse error id=%s | %s", payload.id, exc)

    # Batch write CSV — even if some Supabase inserts failed
    try:
        batch_save_to_csv(csv_records)
        logger.info("📁 Batch CSV saved: %d records", len(csv_records))
    except Exception as exc:
        logger.error("Failed to write batch to CSV: %s", exc)
        errors.append(f"CSV_WRITE_ERROR: {exc!s}")

    # Track sync checkpoint — use the latest timestamp from the batch
    if payloads:
        latest_ts = max(
            (p.timestamp_ms for p in payloads),
            key=lambda t: int(float(t)) if t else 0,
            default=0,
        )
        _update_sync_timestamp(payloads[0].device_id, latest_ts)

    return BulkIngestResponse(
        total=len(payloads),
        financial=financial_count,
        non_financial=non_financial_count,
        transactions_created=transactions_created,
        errors=errors,
    )


# -------------------------------------------------------
# GET /api/data — List raw SMS (from Supabase raw_sms table)
# -------------------------------------------------------

@router.get("/api/data")
async def list_raw(
    limit:          int  = Query(default=50, ge=1, le=500),
    offset:         int  = Query(default=0, ge=0),
    financial_only: bool = Query(default=False, description="Return only financial messages"),
    device_id:      str | None = Query(default=None, description="Filter by device"),
):
    """List raw captured SMS with optional filters"""
    query = supabase.table(RAW_TABLE).select("*")

    if financial_only:
        query = query.eq("is_financial", True)
    if device_id:
        query = query.eq("device_id", device_id)

    end    = offset + limit - 1
    result = query.order("timestamp_ms", desc=True).range(offset, end).execute()

    return {
        "count":  len(result.data or []),
        "offset": offset,
        "items":  result.data or [],
    }


# -------------------------------------------------------
# GET /api/data/{record_id} — Fetch single raw record
# -------------------------------------------------------

@router.get("/api/data/{record_id}")
async def get_raw(record_id: str):
    """Retrieve a single raw notification/SMS record by its UUID"""
    result = (
        supabase.table(RAW_TABLE)
        .select("*")
        .eq("id", record_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Record not found")
    return result.data[0]


# -------------------------------------------------------
# GET /api/data/stats/summary — Dashboard stats
# -------------------------------------------------------

@router.get("/api/data/stats/summary")
async def get_stats(device_id: str | None = Query(default=None)):
    """Returns summary stats for SMS records."""
    base_query = supabase.table(RAW_TABLE)

    total_q = base_query.select("id", count="exact")
    if device_id:
        total_q = total_q.eq("device_id", device_id)
    total_result = total_q.execute()
    total = total_result.count or 0

    fin_q = base_query.select("id", count="exact").eq("is_financial", True)
    if device_id:
        fin_q = fin_q.eq("device_id", device_id)
    fin_result = fin_q.execute()
    financial = fin_result.count or 0

    recent_q = (
        base_query.select("*")
        .eq("is_financial", True)
        .order("timestamp_ms", desc=True)
        .limit(5)
    )
    if device_id:
        recent_q = recent_q.eq("device_id", device_id)
    recent_result = recent_q.execute()

    return {
        "total_sms":        total,
        "financial_sms":    financial,
        "non_financial_sms": total - financial,
        "recent_financial": recent_result.data or [],
    }


# -------------------------------------------------------
# POST /api/data/{record_id}/reparse — Re-parse with latest parser
# -------------------------------------------------------

@router.post("/api/data/{record_id}/reparse")
async def reparse_record(record_id: str):
    """
    Re-parse an existing raw record using the latest parser logic.
    Useful after improving the parser to retroactively extract better data.
    """
    result = (
        supabase.table(RAW_TABLE)
        .select("*")
        .eq("id", record_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Record not found")

    record = result.data[0]
    parsed = parse_sms_body(record.get("body", ""), sender=record.get("sender"))

    supabase.table(RAW_TABLE).update({
        "is_financial":        parsed.is_financial,
        "parsed_amount":       parsed.amount,
        "parsed_direction":    parsed.direction,
        "parsed_bank":         parsed.bank,
        "parsed_mode":         parsed.transaction_mode,
        "parsed_upi_id":       parsed.upi_id,
        "parsed_recipient":    parsed.recipient_name,
        "parsed_account_suffix": parsed.account_suffix,
        "parsed_balance":      parsed.balance_after,
        "parsed_ref_id":       parsed.ref_id,
    }).eq("id", record_id).execute()

    return {
        "status":    "reparsed",
        "record_id": record_id,
        "parsed":    parsed,
    }


# -------------------------------------------------------
# Sync state helpers
# -------------------------------------------------------

def _read_sync_state() -> dict:
    """Read the sync state JSON file. Returns {device_id: last_timestamp_ms}."""
    if not os.path.isfile(SYNC_STATE_FILE):
        return {}
    try:
        with open(SYNC_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _write_sync_state(state: dict) -> None:
    """Persist the sync state to disk."""
    os.makedirs(os.path.dirname(SYNC_STATE_FILE), exist_ok=True)
    with open(SYNC_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _update_sync_timestamp(device_id: str, timestamp_ms) -> None:
    """Update the last-synced timestamp for a device if it's newer."""
    try:
        ts = int(float(timestamp_ms))
    except (TypeError, ValueError):
        return
    state = _read_sync_state()
    current = state.get(device_id, 0)
    if ts > current:
        state[device_id] = ts
        _write_sync_state(state)


# -------------------------------------------------------
# GET /api/data/last-sync — Sync checkpoint for Android app
# -------------------------------------------------------

@router.get("/api/data/last-sync")
async def get_last_sync(
    device_id: str = Query(..., description="Device ID to check sync state for"),
):
    """
    Returns the timestamp (epoch ms) of the last SMS received from this device.
    The Android app should call this before bulk sync and only send
    messages with timestamp_ms > last_sync_timestamp.

    Response:
        {
            "device_id": "d4d93bcf95df41a4",
            "last_sync_timestamp": 1778242012162,
            "last_sync_human": "2026-05-08 17:36:52"
        }

    If no data has been synced yet, last_sync_timestamp will be 0.
    """
    state = _read_sync_state()
    last_ts = state.get(device_id, 0)

    human = ""
    if last_ts > 0:
        try:
            human = datetime.fromtimestamp(last_ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, ValueError):
            human = "unknown"

    return {
        "device_id": device_id,
        "last_sync_timestamp": last_ts,
        "last_sync_human": human,
    }


# -------------------------------------------------------
# Helper
# -------------------------------------------------------

def _timestamp_to_date(timestamp_ms: int) -> str:
    """Convert Unix milliseconds timestamp to YYYY-MM-DD date string"""
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d")
