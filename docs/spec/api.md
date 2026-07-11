# API Reference (`ml_service`)

FastAPI service. Base: run locally with `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
(see `ml_service/app/main.py`, `ml_service/app/routes/`).

| Method | Path | Purpose | Handler |
| --- | --- | --- | --- |
| GET | `/` | Health check | `app/main.py` |
| POST | `/api/data` | Single SMS/notification ingestion (from Android) | `app/routes/ingest.py` |
| POST | `/api/data/bulk` | Bulk SMS ingestion (from Android) | `app/routes/ingest.py` |
| GET | `/api/data` | List raw SMS rows from Supabase `raw_sms` (filters: `financial_only`, `device_id`) | `app/routes/ingest.py` |
| GET | `/api/data/{record_id}` | Fetch a single raw SMS row | `app/routes/ingest.py` |
| GET | `/api/data/stats/summary` | Total/financial/non-financial counts + recent financial rows | `app/routes/ingest.py` |
| POST | `/api/data/{record_id}/reparse` | Re-run the current parser against a stored raw row, update in place | `app/routes/ingest.py` |
| GET | `/api/data/last-sync` | Last-synced `timestamp_ms` for a `device_id`, for incremental sync | `app/routes/ingest.py` |
| POST | `/transactions` | Create transaction manually | `app/routes/transaction.py` |
| GET | `/transactions?limit=50&offset=0` | List raw transactions | `app/routes/transaction.py` |
| GET | `/transactions/{transaction_id}` | Get a raw transaction | `app/routes/transaction.py` |
| GET | `/transactions/{transaction_id}/logic` | Analyze transaction logic | `app/routes/transaction.py` |
| POST | `/load-excel` | Bulk load transactions from a cleaned CSV/Excel workbook | `app/routes/bulk.py` |

## Request/response shapes

Pydantic models in `ml_service/app/schemas/transaction.py` — `TransactionCreate`,
`SupabaseTransaction`, `ParsedTransaction` (the SMS-parser output shape, including
`classification_label` / `classification_confidence` / `classification_reason`).

## Not yet built

- The bank-statement upload endpoint (PDF/Excel → clean CSV) described in `CLAUDE.md`'s current task
  focus. {{TODO: add its route, request/response shape, and sync-vs-async behavior here once built.}}
- Any auth/session model for a per-user upload — see `docs/spec/security.md`.
