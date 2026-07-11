# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

SpendWise is a personal finance platform. It ingests transaction data from
multiple sources, cleans and structures it, and will expose it for analytics
on a website.

## Current task focus (read this first)

The active work is an **ML pipeline for bank-statement ingestion**:

- **Input**: a raw bank-statement export — either a **PDF** or an
  **Excel/CSV** file — as provided by a bank, containing only unstructured
  fields like description and amount (no clean columns, no category, no
  merchant name).
- **Output**: a clean, structured CSV with normalized columns (date,
  description, amount, direction, etc.) plus an extracted/normalized
  **merchant or recipient name** per transaction. Full category
  classification (food/travel/bills/etc.) is explicitly **out of scope**
  for this task.
- **Integration model**: a user uploads their statement file via the
  website; the pipeline processes it (synchronously or async) and returns
  the clean structured data for that user's analytics. This is a live,
  per-user upload flow, not just an offline notebook exercise.
- **PDF parsing does not exist anywhere in this repo yet** — this is
  greenfield work. Excel/CSV parsing has a prior art (see below) that
  should be reused/extended rather than rebuilt from scratch.

## Explicitly out of scope / do not conflate

- **Spring Boot backend** — previously part of this repo
  (`SpendWise_Backend/` / `backend/`), it has been **deleted**. It is no
  longer part of the architecture. Ignore any lingering references to it in
  git history or stale docs.
- **Transaction categorization** (`ml_service/app/routes/categorize.py`) —
  an existing rule-based keyword categorizer. It's unrelated to the current
  task's deliverable (merchant extraction, not category assignment).
- **SMS ingestion pipeline** (Android app → `ml_service` →
  `sms_parser.py` / `financial_sms_processor.py`) — a separate, **still
  live** pipeline that parses SMS/notification text captured from the
  user's phone into transactions. It solves a different problem (SMS text
  → transaction) from the current task (PDF/Excel statement → clean CSV).
  Don't merge the two pipelines or assume they share requirements.

## Relevant existing code

- `ml_preprocessing/Segregation.ipynb` — the existing Excel bank-statement
  ETL notebook: decrypts password-protected `.xlsx` exports
  (`msoffcrypto`), regex-parses the bank's `Description` column into
  transaction type/mode/DR-CR/recipient/UPI ID/bank, cleans and renames
  columns, infers DR/CR by comparing balance deltas, and exports
  `SpendWise2k26.xlsx`. This is the closest existing analog for
  Excel-statement parsing — mine it for reusable regex/cleaning logic
  (e.g. `clean_recipient`, `determine_dr_cr`, `validate_dataframe`) rather
  than rewriting from scratch.
- `ml_service/app/excel_loader.py`, `ml_service/app/routes/bulk.py` —
  existing Excel ingestion into Supabase (`/load-excel` endpoint).
- `ml_service/app/schemas/transaction.py` — Pydantic models for
  transaction shapes already in use (`TransactionCreate`,
  `SupabaseTransaction`) — reuse/extend these for the new pipeline's
  output shape where sensible.
- No existing code touches PDFs.

## Doc map (`docs/`)

Current and accurate:
- `docs/ml_service_pipeline_summary.md` — most authoritative, up-to-date
  architecture reference for the current `ml_service` (FastAPI) SMS
  pipeline, module by module.
- `docs/sms_to_true_financial.md` — function/line-level reference for the
  SMS → `true_financial_sms.csv` pipeline.
- `docs/code-audit-and-data-quality-report.md` — audit of the current SMS
  ingestion/parsing pipeline, bugs found, and the refactor that added
  labels/confidence scoring.
- `docs/transaction-analysis-report.md` — statistical/data-quality report
  on the transaction dataset (bank workbook + SMS financial file).
- `docs/walkthrough.md` — full system walkthrough (Android → FastAPI →
  CSV/Supabase); Spring Boot references removed to match current scope.

These describe the **SMS pipeline**, not the new PDF/Excel-statement
pipeline — useful for patterns (parsing, dedup, confidence scoring) but not
authoritative for this task.

## Repo layout

- `ml_preprocessing/` — Jupyter notebooks for offline ETL/EDA
  (`Segregation.ipynb`, `EDA.ipynb`, `Analyse.ipynb`, `Raw_SmS.ipynb`,
  `Stats.ipynb`) and their CSV/Excel artifacts under `CSVS/`.
- `ml_service/` — FastAPI Python service: SMS ingestion, Excel bulk-load,
  rule-based categorization, Supabase persistence. This is where the new
  statement-upload pipeline will likely live (new route + module), unless
  otherwise decided.
