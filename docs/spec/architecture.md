# Architecture

For full module-by-module depth on the live SMS pipeline, see
[`../sms_pipeline.md`](../sms_pipeline.md) — this file stays at the whole-system-in-one-paragraph
level and links out rather than duplicating that detail.

## System overview

Two ingestion paths currently feed the same eventual store (Supabase), plus one still-manual offline
path:

1. **SMS/notification path (live)** — Android app → `ml_service` (FastAPI) →
   `app/parsers/sms_parser.py` / `app/services/sms_pipeline.py` classify and structure each message →
   Supabase.
2. **Excel bulk-load path (live)** — a cleaned Excel workbook → `ml_service/app/parsers/excel_loader.py`
   → `POST /load-excel` → Supabase.
3. **Bank-statement path (in progress — current task focus)** — a raw PDF/Excel bank-statement
   export → (new pipeline, see `CLAUDE.md`) → clean structured CSV → intended to feed the same
   Supabase tables as path 2, uploaded by a user via the website (website not yet built).

Offline/manual today: `ml_preprocessing/` notebooks (`CSV_PARSER.ipynb` → `Segregation.ipynb` →
`MerchantNormalization.ipynb`) are run by hand against exported bank Excel files to produce a clean
workbook. This is the prior art the live bank-statement pipeline (path 3) should extend rather than
reimplement — see `CLAUDE.md`'s "Relevant existing code."

## Module breakdown

| Module | Path | Responsibility |
| --- | --- | --- |
| SMS ingestion & classification | `ml_service/app/parsers/sms_parser.py`, `app/services/sms_pipeline.py` | Parse/classify raw SMS text into structured transactions |
| Excel bulk ingestion | `ml_service/app/parsers/excel_loader.py`, `app/routes/bulk.py` | Load a cleaned workbook into Supabase |
| Categorization (separate concern) | `ml_service/app/routes/categorize.py` | Rule-based keyword categorizer — unrelated to merchant extraction |
| Transaction API | `ml_service/app/routes/transaction.py`, `app/services/persistence.py` | CRUD + logic endpoints over persisted transactions |
| Persistence | `ml_service/app/clients/supabase_client.py`, `app/schemas/transaction.py` | Supabase client + Pydantic shapes (`TransactionCreate`, `SupabaseTransaction`, `ParsedTransaction`) |
| Offline Excel ETL (prior art) | `ml_preprocessing/CSV_PARSER.ipynb`, `Segregation.ipynb` | Decrypt + parse raw bank Excel exports into a clean workbook |
| Merchant normalization | `ml_preprocessing/MerchantNormalization.ipynb`, `merchant_normalizer.py` | Canonicalize recipient/merchant names via UPI-ID grouping, fuzzy clustering, and a manual-review pass (`find_prefix_variants` + a curated alias dict) for the residual truncation-prefix cases neither tier can safely resolve alone |
| Bank-statement pipeline (new) | {{TODO: fill in once the module/route exists}} | PDF/Excel statement → clean CSV + merchant name, per-user upload |
| Website/frontend | {{TODO: not yet started}} | Upload UI + analytics display |

## Data flow (current-task path)

```
raw bank statement (PDF or Excel)
  -> {{TODO: new parsing module}}
  -> clean structured CSV (date, description, amount, direction, merchant name)
  -> {{TODO: how it reaches Supabase / the per-user analytics view}}
```

## Key architecture decisions

See `docs/spec/decisions.md` for the append-only ADR log. Notable existing decision: the Java/Spring
Boot backend was removed — FastAPI (`ml_service`) is the sole backend surface (see decision log for
when/why once recorded).
