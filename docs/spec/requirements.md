# Requirements

## Functional

### Existing (SMS ingestion pipeline — live)
- Ingest single and bulk SMS/notification payloads from the Android app (`POST /api/data`,
  `POST /api/data/bulk`).
- Classify each message (`FINANCIAL_TRANSACTION`, `FAILED_TRANSACTION`, `OTP`, `PROMOTIONAL`,
  `BANKING_ALERT`, `BILL_REMINDER`, `UNKNOWN`) with a confidence score, per
  `ml_service/app/parsers/sms_parser.py`.
- Route low-confidence/unrecognized messages to an unknown-message review queue
  (`data/review_queue_sms.csv`) rather than silently dropping them.
- Bulk-load a cleaned Excel workbook into Supabase (`POST /load-excel`).

### Current focus (bank-statement ingestion — in progress, see `CLAUDE.md`)
- Accept a raw bank-statement export, either **PDF** or **Excel/CSV**, as uploaded by a user via the
  website.
- Produce a clean, structured CSV: normalized date, description, amount, direction (DR/CR), plus an
  extracted/normalized merchant or recipient name per transaction.
- **Processing model (ADR-0003, decided 2026-07-12): synchronous.** Parse, clean, normalize, and
  reconcile inline; return the finished result in one HTTP response. Statement sizes are
  personal-scale (~2000 rows processes in seconds per the existing notebooks). Revisit if PDF/OCR
  parsing later makes this noticeably slow — an async job-status endpoint is the fallback design if so.
- **Per-user scoping (ADR-0003)**: every upload requires a `user_id`. Real auth doesn't exist yet
  (see `docs/spec/security.md`'s Auth model) — the upload endpoint takes `user_id` as a stopgap
  request field so the DB shape is already user-scoped, to be swapped for real auth without a schema
  change later.
- **Reconciliation against the SMS pipeline (ADR-0003)**: a statement upload must reconcile its rows
  against the user's existing SMS-sourced (and previously-uploaded statement-sourced) transactions —
  see `docs/spec/architecture.md`'s "Reconciliation model." Order the user provides SMS-access vs.
  a statement upload does not matter; the same reconciliation path handles both orderings.
- **Merchant canonicalization for the live endpoint is fully algorithmic** — UPI-ID grouping + fuzzy
  clustering + `merge_prefix_chains`, no manual-alias step (contrast with the offline notebook
  workflow in `MerchantNormalization.ipynb`, which includes a hand-curated alias dict — see
  ADR-0004). Some under-merging is an accepted v1 limitation.

### Not yet started
- Website frontend for uploads + analytics.
- Full category classification (explicitly out of scope for the current task).
- PDF statement parsing (greenfield — no code exists yet; will plug into the same pipeline stages
  as the Excel path from "narration parsing" onward, per `docs/spec/architecture.md`'s data flow).

## Non-functional

- Expected statement size/volume: personal-scale, ~2,000 transactions per multi-year statement
  (observed in `ml_preprocessing/CSVS`), well within synchronous-processing budget.
- Account numbers must be masked (last 4 digits only) before being surfaced or persisted anywhere —
  see `docs/spec/security.md`.
- {{TODO: still open — latency budget once PDF parsing exists (OCR may be slower than Excel
  parsing); retention policy for uploaded source files (current recommendation:
  process-and-discard, not yet locked in — see `docs/spec/database.md`).}}

## Constraints

- Password-protected Excel exports must be decrypted locally (`msoffcrypto`) — the password itself
  must never be hardcoded into a notebook or script; read it from an environment variable or a
  local, gitignored config (see `docs/spec/security.md`).
- Raw statement files and personal transaction data stay out of git — `ml_preprocessing/CSVS/*` and
  `ml_service/data/*` are gitignored except for `.gitkeep`.
