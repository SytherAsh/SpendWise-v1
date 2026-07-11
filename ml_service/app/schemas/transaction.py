from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator


# -------------------------------------------------------
# Incoming payload from Android app
# -------------------------------------------------------

class SmsPayload(BaseModel):
    """Schema for incoming SMS data from the mobile app"""
    id: str                                   # Unique record ID generated on phone
    source: str                               # Should always be "sms" now
    sender: Optional[str] = None              # SMS sender ID (e.g. HDFCBK)
    body: Optional[str] = None                # The full SMS text
    timestamp_ms: int | str                   # Unix timestamp in ms or date string
    timestamp_human: str                      # Human readable time
    device_id: str                            # Unique phone identifier
    sent_to_backend: bool = False             # Local sync status


# -------------------------------------------------------
# Parser output — one object per SMS
# -------------------------------------------------------

class ParsedTransaction(BaseModel):
    """Result of parsing a raw notification/SMS body into structured transaction fields"""
    amount: Optional[float] = None
    direction: Optional[str] = None          # "DEBIT" or "CREDIT"
    bank: Optional[str] = None
    upi_id: Optional[str] = None
    recipient_name: Optional[str] = None
    transaction_mode: Optional[str] = None   # UPI, IMPS, NEFT, RTGS, ATM, CARD, CASH, etc.
    account_suffix: Optional[str] = None     # Last 4 digits of account/card
    balance_after: Optional[float] = None
    ref_id: Optional[str] = None             # Reference / transaction ID from SMS body
    is_financial: bool = False               # Whether the message was identified as financial
    classification_label: str = "UNKNOWN"
    classification_confidence: float = 0.0
    classification_reason: Optional[str] = None

    @field_validator("amount", "balance_after", mode="before")
    @classmethod
    def coerce_numeric(cls, v):
        """Accept strings like '12,345.67' and coerce them to float"""
        if v is None:
            return None
        if isinstance(v, str):
            v = v.replace(",", "").strip()
            try:
                return float(v)
            except ValueError:
                return None
        return v

    @field_validator("direction", mode="before")
    @classmethod
    def normalize_direction(cls, v):
        if v is None:
            return None
        v = str(v).upper().strip()
        if v in ("DEBIT", "DR"):
            return "DEBIT"
        if v in ("CREDIT", "CR"):
            return "CREDIT"
        return None

    @field_validator("account_suffix", mode="before")
    @classmethod
    def normalize_account_suffix(cls, v):
        """Keep only the last 4 digits"""
        if v is None:
            return None
        digits = "".join(filter(str.isdigit, str(v)))
        return digits[-4:] if len(digits) >= 4 else (digits or None)

    @field_validator("classification_confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, v):
        if v is None:
            return 0.0
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


# -------------------------------------------------------
# Manual transaction creation (from Excel/API)
# -------------------------------------------------------

class TransactionCreate(BaseModel):
    transaction_reference: str
    transaction_date: Optional[str] = None
    amount: Optional[float] = None
    debit: Optional[float] = None
    credit: Optional[float] = None
    balance: Optional[float] = None
    transaction_mode: Optional[str] = None
    dr_cr_indicator: Optional[str] = None
    note: Optional[str] = None
    recipient_name: Optional[str] = None
    bank: Optional[str] = None
    upi_id: Optional[str] = None


# -------------------------------------------------------
# Supabase row model for a persisted transaction
# -------------------------------------------------------

class SupabaseTransaction(BaseModel):
    """Validated representation of a row being written to the Supabase transactions table"""
    account_id: str
    recipient_id: Optional[str] = None
    transaction_reference: Optional[str] = None
    transaction_date: Optional[str] = None       # ISO 8601 UTC string
    amount: Optional[float] = None
    debit: Optional[float] = None
    credit: Optional[float] = None
    balance: Optional[float] = None
    transaction_mode: Optional[str] = None
    dr_cr_indicator: Optional[str] = None        # "DR" or "CR"
    note: Optional[str] = None

    @field_validator("amount", "debit", "credit", "balance", mode="before")
    @classmethod
    def coerce_numeric(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            v = v.replace(",", "").strip()
            try:
                return float(v)
            except ValueError:
                return None
        import math
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v

    @field_validator("dr_cr_indicator", mode="before")
    @classmethod
    def normalize_indicator(cls, v):
        if v is None:
            return None
        v = str(v).upper().strip()
        if v in ("DEBIT", "DR"):
            return "DR"
        if v in ("CREDIT", "CR"):
            return "CR"
        return v