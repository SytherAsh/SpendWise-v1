# Architecture

For full module-by-module depth on the live SMS pipeline, see
[`../sms_pipeline.md`](../sms_pipeline.md) — this file stays at the whole-system-in-one-paragraph
level and links out rather than duplicating that detail.

## System overview

Two ingestion paths currently feed the same eventual store (Supabase), plus one still-manual offline
path:

1. **SMS/notification path (live)** — Android app → `ml_service` (FastAPI) →
   `app/parsers/sms_parser.py` / `app/services/sms_pipeline.py` classify and structure each message →
   Supabase, inserted immediately as `transactions.source = 'sms'`. No backward-looking matching
   happens at ingest time — see "Reconciliation model" below.
2. **Excel bulk-load path (live)** — a cleaned Excel workbook → `ml_service/app/parsers/excel_loader.py`
   → `POST /load-excel` → Supabase.
3. **Bank-statement path (in progress — current task focus)** — a raw PDF/Excel bank-statement
   export → (new pipeline, see `CLAUDE.md`) → clean structured CSV → reconciled against existing
   SMS/statement data for the account → Supabase, uploaded by a user via the website (website not
   yet built).

Offline/manual today: `ml_preprocessing/` notebooks (`CSV_PARSER.ipynb` → `Segregation.ipynb` →
`MerchantNormalization.ipynb`) are run by hand against exported bank Excel files to produce a clean
workbook. This is the prior art the live bank-statement pipeline (path 3) should extend rather than
reimplement — see `CLAUDE.md`'s "Relevant existing code." `ml_service/app/services/build_unified_dataset.py`
is the prior art for path 3's reconciliation step specifically (see below) — it currently runs as a
one-off offline script over two local files; the live endpoint generalizes the same matching logic to
run against Supabase state on every statement upload.

## Reconciliation model (SMS ↔ statement — ADR-0003)

The SMS-ingestion pipeline and the bank-statement pipeline remain independent parsers (different
input shapes, different code, per `CLAUDE.md`'s architectural invariants) — but their *output* lands
in the same `transactions` table and must not be double-counted when the same real-world transaction
is captured by both. Reconciliation is the layer that resolves that, and it is intentionally
**order-independent**:

- SMS ingestion never looks backward — every incoming message is inserted immediately as its own row
  (`source = 'sms'`, `is_reconciled = false`). Whether or not a statement exists yet for that account
  is irrelevant at this point.
- Statement upload always looks backward — every upload (the account's first, or a later one)
  triggers one reconciliation pass against whatever already exists in Supabase for that account,
  regardless of which source produced it or in what order the user provided their data.

This means a user can connect SMS first and upload a statement months later, upload a statement first
and connect SMS afterward, or interleave both — the same single code path handles all three, because
reconciliation always matches "the new statement's rows" against "the account's existing DB state,"
never against "whatever arrived first."

Per-upload reconciliation, in priority order for each new statement row:

1. **Duplicate-of-existing-statement-row check** — `ref_norm` exact match, else
   date+details+balance match, against previously-persisted `source = 'statement'` rows for this
   account. Match → skip (handles incremental/repeat statement uploads without duplicating).
2. **Match-against-SMS check** — `ref_norm` exact match, else date+amount+direction match, against
   existing `source = 'sms', is_reconciled = false` rows for this user (same logic as
   `build_unified_dataset.build()`, generalized from CSV file inputs to Supabase queries). Match →
   **update that SMS row in place** with the statement's fields (exact balance, ref id, mode) and set
   `is_reconciled = true`, rather than inserting a second row — avoids ever having two rows for one
   real-world transaction to de-duplicate at read time.
3. **No match** — insert as a new `source = 'statement'` row.

After insert/update, merchant canonicalization (`merchant_normalizer.py`) is recomputed over that
user's **entire** transaction set, not just the new rows — same reasoning `build_unified_dataset.py`
already documents (patching only changed rows leaves old clustering inconsistent with newly-available
names). Canonicalization for the live endpoint is fully algorithmic (UPI-ID grouping + fuzzy
clustering + `merge_prefix_chains`) with no manual-alias step — see ADR-0004.

## Module breakdown

| Module | Path | Responsibility |
| --- | --- | --- |
| SMS ingestion & classification | `ml_service/app/parsers/sms_parser.py`, `app/services/sms_pipeline.py` | Parse/classify raw SMS text into structured transactions |
| Excel bulk ingestion | `ml_service/app/parsers/excel_loader.py`, `app/routes/bulk.py` | Load a cleaned workbook into Supabase |
| Categorization (separate concern) | `ml_service/app/routes/categorize.py` | Rule-based keyword categorizer — unrelated to merchant extraction |
| Transaction API | `ml_service/app/routes/transaction.py`, `app/services/persistence.py` | CRUD + logic endpoints over persisted transactions |
| Persistence | `ml_service/app/clients/supabase_client.py`, `app/schemas/transaction.py` | Supabase client + Pydantic shapes (`TransactionCreate`, `SupabaseTransaction`, `ParsedTransaction`) |
| Offline Excel ETL (prior art) | `ml_preprocessing/CSV_PARSER.ipynb`, `Segregation.ipynb` | Decrypt + parse raw bank Excel exports into a clean workbook |
| Merchant normalization | `ml_service/app/services/merchant_normalizer.py` (notebook: `ml_preprocessing/MerchantNormalization.ipynb`) | Canonicalize recipient/merchant names via UPI-ID grouping + fuzzy clustering (fully algorithmic in the live endpoint — see ADR-0004); the notebook additionally does a manual-review pass (`find_prefix_variants` + a curated alias dict) which is offline-only, not part of the live pipeline |
| SMS↔statement reconciliation (new) | {{TODO: fill in once the module/route exists — generalizes `ml_service/app/services/build_unified_dataset.py`}} | Match new statement rows against existing SMS/statement rows for the account; dedupe + backfill in place. See "Reconciliation model" above and ADR-0003. |
| Bank-statement pipeline (new) | {{TODO: fill in once the module/route exists — `POST /api/statements/upload`}} | PDF/Excel statement → clean CSV + merchant name → reconciled → Supabase, per-user upload |
| Website/frontend | {{TODO: not yet started}} | Upload UI + analytics display |

## Data flow (current-task path)

```text
raw bank statement (PDF or Excel)
  -> raw extraction (decrypt, locate table header, detect date format)
  -> narration parsing (bank-specific regex rules, e.g. Segregation.ipynb's cascade)
  -> clean + DR/CR inference
  -> reconciliation against existing Supabase state for the account (see above)
  -> merchant canonicalization, recomputed over the user's whole transaction set
  -> Supabase (transactions/recipients/accounts) + response to the uploading client
```

## Key architecture decisions

See `docs/spec/decisions.md` for the append-only ADR log. Notable existing decision: the Java/Spring
Boot backend was removed — FastAPI (`ml_service`) is the sole backend surface (see decision log for
when/why once recorded).
