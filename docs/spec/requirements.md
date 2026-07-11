# Requirements

## Functional

### Existing (SMS ingestion pipeline — live)
- Ingest single and bulk SMS/notification payloads from the Android app (`POST /api/data`,
  `POST /api/data/bulk`).
- Classify each message (`FINANCIAL_TRANSACTION`, `FAILED_TRANSACTION`, `OTP`, `PROMOTIONAL`,
  `BANKING_ALERT`, `BILL_REMINDER`, `UNKNOWN`) with a confidence score, per
  `ml_service/app/sms_parser.py`.
- Route low-confidence/unrecognized messages to an unknown-message review queue
  (`data/unknown_sms.csv`) rather than silently dropping them.
- Bulk-load a cleaned Excel workbook into Supabase (`POST /load-excel`).

### Current focus (bank-statement ingestion — in progress, see `CLAUDE.md`)
- Accept a raw bank-statement export, either **PDF** or **Excel/CSV**, as uploaded by a user via the
  website.
- Produce a clean, structured CSV: normalized date, description, amount, direction (DR/CR), plus an
  extracted/normalized merchant or recipient name per transaction.
- {{TODO: decide and record — synchronous vs. async processing for the upload; per-user scoping /
  auth model for the upload endpoint.}}

### Not yet started
- Website frontend for uploads + analytics.
- Full category classification (explicitly out of scope for the current task).

## Non-functional

{{TODO: fill in as they're decided — e.g. expected statement size/volume, latency budget for a
single-statement upload, whether PII (account numbers, names) must be masked before persistence,
retention policy for uploaded source files.}}

## Constraints

- Password-protected Excel exports must be decrypted locally (`msoffcrypto`) — the password itself
  must never be hardcoded into a notebook or script; read it from an environment variable or a
  local, gitignored config (see `docs/spec/security.md`).
- Raw statement files and personal transaction data stay out of git — `ml_preprocessing/CSVS/*` and
  `ml_service/data/*` are gitignored except for `.gitkeep`.
