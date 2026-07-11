"""
service.py — Supabase persistence layer for SpendWise.

Provides reusable, safe CRUD helpers for:
  - accounts
  - recipients
  - transactions

All functions are:
  - null-safe (safe_value)
  - exception-safe (callers get None / False on failure, not a crash)
  - dedup-aware (transaction_exists before insert)
  - logged
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from app.clients.supabase_client import supabase

load_dotenv()
logger = logging.getLogger(__name__)

# Unused elsewhere in this module today; kept for whatever wires it up next.
# Not fatal — a missing .env must not block raw SMS capture from starting.
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
if ACCOUNT_ID is None:
    logger.warning("ACCOUNT_ID not found in .env — Supabase-backed account lookups may be affected.")


# -------------------------------------------------------
# Utility
# -------------------------------------------------------

def safe_value(v: Any) -> Any:
    """
    Return None for NaN / Inf / 'nan' strings so Supabase never
    receives invalid numeric values.
    """
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, str) and v.lower() in ("nan", "none", "null", ""):
        return None
    return v


def _direction_to_indicator(direction: Optional[str]) -> Optional[str]:
    """Convert DEBIT/CREDIT → DR/CR as stored in the transactions table."""
    if direction == "DEBIT":
        return "DR"
    if direction == "CREDIT":
        return "CR"
    return None


# -------------------------------------------------------
# accounts
# -------------------------------------------------------

def get_or_create_account(bank_name: Optional[str], account_suffix: Optional[str] = None) -> Optional[str]:
    """
    Look up an account row by bank_name (and optionally suffix).
    Creates one if it doesn't exist.

    Returns the account UUID or None on failure.
    """
    bank_name = safe_value(bank_name) or "UNKNOWN_BANK"
    account_suffix = safe_value(account_suffix)

    try:
        # account_suffix column does not exist in the current schema — query by bank_name only
        res = (
            supabase.table("accounts")
            .select("id")
            .eq("bank_name", bank_name)
            .limit(1)
            .execute()
        )

        if res.data:
            return res.data[0]["id"]

        # Create new account row
        payload: Dict[str, Any] = {
            "bank_name": bank_name,
            "account_type": "SAVINGS",
        }
        # NOTE: add account_suffix to payload once the column exists in Supabase

        new = supabase.table("accounts").insert(payload).execute()
        account_id = new.data[0]["id"]
        logger.info("Created new account | bank=%s suffix=%s id=%s", bank_name, account_suffix, account_id)
        return account_id

    except Exception as exc:
        logger.error("get_or_create_account failed | bank=%s | error=%s", bank_name, exc)
        return None


# -------------------------------------------------------
# recipients
# -------------------------------------------------------

def get_or_create_recipient(
    name: Optional[str],
    upi_id: Optional[str],
    bank: Optional[str],
) -> Optional[str]:
    """
    Look up a recipient by UPI ID (most specific), then by name.
    Creates one if it doesn't exist.

    Returns the recipient UUID or None on failure.
    """
    name   = safe_value(name)   or "UNKNOWN"
    upi_id = safe_value(upi_id)
    bank   = safe_value(bank)   or "UNKNOWN_BANK"

    try:
        # Primary match: UPI ID (globally unique)
        if upi_id:
            res = (
                supabase.table("recipients")
                .select("id")
                .eq("upi_id", upi_id)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["id"]

        # Secondary match: name + bank
        else:
            res = (
                supabase.table("recipients")
                .select("id")
                .eq("name", name)
                .eq("bank_name", bank)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["id"]

        # Create new recipient
        payload: Dict[str, Any] = {
            "name":      name,
            "bank_name": bank,
        }
        if upi_id:
            payload["upi_id"] = upi_id

        new = supabase.table("recipients").insert(payload).execute()
        recipient_id = new.data[0]["id"]
        logger.info("Created new recipient | name=%s upi=%s id=%s", name, upi_id, recipient_id)
        return recipient_id

    except Exception as exc:
        logger.error("get_or_create_recipient failed | name=%s | error=%s", name, exc)
        return None


# -------------------------------------------------------
# Deduplication
# -------------------------------------------------------

def transaction_exists(
    account_id: str,
    amount: Optional[float],
    direction: Optional[str],
    transaction_date: Optional[str],
    ref_id: Optional[str] = None,
) -> bool:
    """
    Check for a duplicate transaction before inserting.

    Dedup strategy (in priority order):
    1. If ref_id present → match on (account_id + transaction_reference)
    2. Else match on (account_id + amount + dr_cr_indicator + approximate date window)
       using the date portion of transaction_date (YYYY-MM-DD).

    The 2-minute bucket dedup is handled upstream in app/sms_pipeline.py;
    here we do a DB-level check to prevent cross-session duplicates.
    """
    if not account_id or amount is None:
        return False

    try:
        indicator = _direction_to_indicator(direction)

        # Strategy 1: reference ID is the strongest dedup key
        if safe_value(ref_id):
            res = (
                supabase.table("transactions")
                .select("id")
                .eq("account_id", account_id)
                .eq("transaction_reference", str(ref_id))
                .limit(1)
                .execute()
            )
            if res.data:
                logger.info("Duplicate detected by ref_id=%s", ref_id)
                return True

        # Strategy 2: amount + direction + same day
        if safe_value(transaction_date):
            date_prefix = str(transaction_date)[:10]   # "YYYY-MM-DD"
            res = (
                supabase.table("transactions")
                .select("id")
                .eq("account_id", account_id)
                .eq("amount", amount)
                .eq("dr_cr_indicator", indicator)
                .gte("transaction_date", f"{date_prefix}T00:00:00+00:00")
                .lte("transaction_date", f"{date_prefix}T23:59:59+00:00")
                .limit(1)
                .execute()
            )
            if res.data:
                logger.info(
                    "Duplicate detected by amount=%.2f date=%s dir=%s",
                    amount, date_prefix, direction,
                )
                return True

        return False

    except Exception as exc:
        logger.warning("transaction_exists check failed | error=%s", exc)
        return False     # On failure, allow insert (safer than blocking)


# -------------------------------------------------------
# transactions
# -------------------------------------------------------

def insert_transaction(
    account_id: str,
    recipient_id: Optional[str],
    row: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Insert a single transaction row into Supabase.

    Args:
        account_id:    FK to accounts table.
        recipient_id:  FK to recipients table (may be None).
        row:           Dict with all transaction fields.

    Returns:
        The inserted row dict, or None on failure.
    """
    try:
        direction = safe_value(row.get("direction")) or safe_value(row.get("dr_cr_indicator"))
        indicator = _direction_to_indicator(direction) if direction in ("DEBIT", "CREDIT") else safe_value(direction)

        # Always use absolute value for calculations to be robust against signed inputs
        amount_raw = abs(float(safe_value(row.get("amount")) or 0.0))
        debit_val  = amount_raw if indicator == "DR" else 0.0
        credit_val = amount_raw if indicator == "CR" else 0.0
        
        # Signed amount: DR must be negative, CR must be positive per transactions_amount_consistency_chk
        final_amount = credit_val - debit_val

        # Validate transaction_mode against allowed list:
        # 'UPI','INB','NEFT','IMPS','CARD','CASH','NETBANKING','ACH','ATM','POS','RTGS','OTHER'
        allowed_modes = {"UPI", "INB", "NEFT", "IMPS", "CARD", "CASH", "NETBANKING", "ACH", "ATM", "POS", "RTGS"}
        mode = str(safe_value(row.get("transaction_mode")) or "OTHER").upper()
        if mode not in allowed_modes:
            mode = "OTHER"

        payload: Dict[str, Any] = {
            "account_id":            account_id,
            "recipient_id":          recipient_id,
            "transaction_reference": safe_value(row.get("ref_id")) or safe_value(row.get("transaction_reference")),
            "transaction_date":      safe_value(row.get("transaction_date")),
            "amount":                final_amount,
            "debit":                 debit_val,
            "credit":                credit_val,
            "balance":               safe_value(row.get("balance_after")) or safe_value(row.get("balance")) or 0.0,
            "transaction_mode":      mode,
            "dr_cr_indicator":       indicator,
            "note":                  safe_value(row.get("note")) or safe_value(row.get("body")),
        }

        res = supabase.table("transactions").insert(payload).execute()
        if res.data:
            logger.info(
                "Inserted transaction | id=%s | amount=%.2f | dir=%s",
                res.data[0].get("id"), final_amount, indicator,
            )
            return res.data[0]
        return None

    except Exception as exc:
        logger.error("insert_transaction failed | error=%s", exc)
        return None


# -------------------------------------------------------
# High-level orchestration
# -------------------------------------------------------

def persist_sms_transaction(parsed, timestamp_iso: Optional[str] = None, body: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Full pipeline for persisting a parsed SMS transaction to Supabase.

    Args:
        parsed:        ParsedTransaction from parse_sms_body().
        timestamp_iso: UTC ISO 8601 string for the transaction date.
        body:          Raw SMS body (stored as note).

    Returns:
        Inserted row dict, or None if skipped / failed.
    """
    if not parsed.is_financial or parsed.amount is None or parsed.direction is None:
        logger.debug("Skipping non-financial or incomplete transaction")
        return None

    # 1. Resolve / create account
    account_id = get_or_create_account(
        bank_name=parsed.bank,
        account_suffix=parsed.account_suffix,
    )
    if not account_id:
        logger.error("Could not resolve account — skipping Supabase insert")
        return None

    # 2. Deduplication check
    is_dup = transaction_exists(
        account_id=account_id,
        amount=parsed.amount,
        direction=parsed.direction,
        transaction_date=timestamp_iso,
        ref_id=parsed.ref_id,
    )
    if is_dup:
        logger.info("Skipping duplicate transaction | amt=%.2f | dir=%s", parsed.amount, parsed.direction)
        return None

    # 3. Resolve / create recipient
    recipient_id = get_or_create_recipient(
        name=parsed.recipient_name,
        upi_id=parsed.upi_id,
        bank=parsed.bank,
    )

    # 4. Build row and insert
    row = {
        "ref_id":           parsed.ref_id,
        "transaction_date": timestamp_iso,
        "amount":           parsed.amount,
        "direction":        parsed.direction,
        "transaction_mode": parsed.transaction_mode,
        "balance_after":    parsed.balance_after,
        "body":             body,
    }

    return insert_transaction(account_id, recipient_id, row)


# -------------------------------------------------------
# Backwards-compatible single-transaction creator (used by bulk.py)
# -------------------------------------------------------

def create_single_transaction(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a transaction from a manually constructed dict (e.g. from Excel loader).
    Raises ValueError if the insert fails.
    """
    bank   = safe_value(row.get("bank"))
    upi_id = safe_value(row.get("upi_id"))
    name   = safe_value(row.get("recipient_name"))

    account_id   = get_or_create_account(bank)
    recipient_id = get_or_create_recipient(name, upi_id, bank)

    result = insert_transaction(account_id, recipient_id, row)
    if result is None:
        raise ValueError("Transaction insert failed")
    return result


# -------------------------------------------------------
# Query helpers (unchanged API)
# -------------------------------------------------------

def get_transaction_by_id(transaction_id: str) -> Optional[Dict[str, Any]]:
    result = (
        supabase.table("transactions")
        .select("*")
        .eq("id", transaction_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def list_transactions(limit: int = 50, offset: int = 0):
    end = offset + limit - 1
    result = (
        supabase.table("transactions")
        .select("*")
        .order("id", desc=True)
        .range(offset, end)
        .execute()
    )
    return result.data or []


def get_transaction_logic(transaction: Dict[str, Any]) -> Dict[str, Any]:
    debit  = safe_value(transaction.get("debit"))  or 0
    credit = safe_value(transaction.get("credit")) or 0
    amount = safe_value(transaction.get("amount")) or 0

    if debit > 0:
        direction        = "DEBIT"
        effective_amount = debit
    elif credit > 0:
        direction        = "CREDIT"
        effective_amount = credit
    else:
        direction        = safe_value(transaction.get("dr_cr_indicator")) or "UNKNOWN"
        effective_amount = amount

    if effective_amount >= 100_000:
        bucket = "HIGH"
    elif effective_amount >= 10_000:
        bucket = "MEDIUM"
    else:
        bucket = "LOW"

    return {
        "transaction_id":        transaction.get("id"),
        "transaction_reference": transaction.get("transaction_reference"),
        "direction":             direction,
        "effective_amount":      effective_amount,
        "size_bucket":           bucket,
        "transaction_mode":      safe_value(transaction.get("transaction_mode")) or "UNKNOWN",
        "note":                  safe_value(transaction.get("note")),
    }