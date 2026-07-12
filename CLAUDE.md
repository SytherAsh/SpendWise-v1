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
  `sms_parser.py` / `sms_pipeline.py`) — a separate, **still
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
- `ml_service/app/parsers/excel_loader.py`, `ml_service/app/routes/bulk.py` —
  existing Excel ingestion into Supabase (`/load-excel` endpoint).
- `ml_service/app/schemas/transaction.py` — Pydantic models for
  transaction shapes already in use (`TransactionCreate`,
  `SupabaseTransaction`) — reuse/extend these for the new pipeline's
  output shape where sensible.
- No existing code touches PDFs.

## Doc map (`docs/`)

Current and accurate:
- `docs/sms_pipeline.md` — authoritative architecture reference for the current
  `ml_service` (FastAPI) SMS pipeline: flow, module-by-module (parser,
  batch processor, ingest routes, Supabase persistence), data artifacts,
  and known gaps. Consolidated from four earlier docs on 2026-07-11 after
  they were found to have drifted from the code (one described a
  downstream pipeline stage that was never built).
- `docs/transaction-analysis-report.md` — statistical/data-quality report
  on the transaction dataset (bank workbook + SMS financial file).

These describe the **SMS pipeline**, not the new PDF/Excel-statement
pipeline — useful for patterns (parsing, dedup, confidence scoring) but not
authoritative for this task.

## Repo layout

- `ml_preprocessing/` — Jupyter notebooks for offline ETL/EDA
  (`Segregation.ipynb`, `EDA.ipynb`, `Analyse.ipynb`, `CSV_PARSER.ipynb`,
  `MerchantNormalization.ipynb`) and their CSV/Excel artifacts under `CSVS/`.
  `SMS_Pipeline.ipynb` is a cell-by-cell reference walkthrough of the live
  `ml_service/app/services/sms_pipeline.py` — it imports and runs the real
  pipeline code rather than reimplementing it, so it can't drift out of sync.
  `Raw_SmS.ipynb` is earlier exploratory work that predates the current
  SMS-pipeline architecture; kept for history, not authoritative.
- `ml_service/` — FastAPI Python service: SMS ingestion, Excel bulk-load,
  rule-based categorization, Supabase persistence. This is where the new
  statement-upload pipeline will likely live (new route + module), unless
  otherwise decided.

## Jupyter Notebook Workflow

- **Never execute a notebook cell (or a whole notebook) on your own initiative.** The user runs all
  notebook code themselves in their own Jupyter session — running it yourself burns tokens/time for
  no benefit, since the user has to have the notebook open anyway.
- Default mode: the user tells you which notebook they're working on (e.g. "I'm in
  `Segregation.ipynb`"); you give them the code for a cell (or cells); they copy-paste it in and run
  it themselves, then report back outputs/errors if needed.
- Only run a notebook (via `jupyter nbconvert --execute`, a NotebookEdit + execution tool, or
  similar) if the user **explicitly** asks you to run the whole notebook. A request for code, a fix,
  or an explanation is not an implicit request to execute anything.
- This applies to every notebook under `ml_preprocessing/`, not just the one currently open.

## Documentation Index (full)

Full navigation, including the spec/operations tiers below: `docs/README.md`. At minimum:

| Document | Contents | Consult when |
| --- | --- | --- |
| `docs/spec/vision.md` | Product vision, success criteria, target users | Defining user-facing features or evaluating scope |
| `docs/spec/requirements.md` | Functional and non-functional requirements | Adding or changing any feature requirement |
| `docs/spec/architecture.md` | System architecture, module breakdown, data flow | Any cross-module work, new module, or data-flow change |
| `docs/spec/decisions.md` | Architecture Decision Records (ADRs) | Before proposing a new architectural approach |
| `docs/spec/api.md` | REST endpoint reference (`ml_service`) | Adding/changing a FastAPI route |
| `docs/spec/database.md` | Supabase schema + design decisions | Any schema change or new table |
| `docs/spec/security.md` | Auth model, secrets handling, data-access rules | Touching credentials, `.env`, or anything bank-statement-related |
| `docs/operations/development_guidelines.md` | Branching, commit style, coding standards | Code style questions; before committing |
| `docs/operations/testing.md` | Testing strategy per surface | Writing or updating tests |

The pre-existing SMS-pipeline docs listed above under "Doc map" remain authoritative for that surface
and are also indexed in `docs/README.md`.

## Module Map

| Module | Path | Responsibility |
| --- | --- | --- |
| SMS ingestion & classification | `ml_service/app/parsers/sms_parser.py`, `app/services/sms_pipeline.py` | Parse/classify raw SMS text into structured transactions |
| Excel bulk ingestion | `ml_service/app/parsers/excel_loader.py`, `app/routes/bulk.py` | Load a cleaned workbook into Supabase |
| Categorization (separate concern) | `ml_service/app/routes/categorize.py` | Rule-based keyword categorizer — unrelated to merchant extraction |
| Transaction API | `ml_service/app/routes/transaction.py`, `app/services/persistence.py` | CRUD + logic endpoints over persisted transactions |
| Persistence | `ml_service/app/clients/supabase_client.py`, `app/schemas/transaction.py` | Supabase client + Pydantic shapes |
| Offline Excel ETL (prior art) | `ml_preprocessing/CSV_PARSER.ipynb`, `Segregation.ipynb` | Decrypt + parse raw bank Excel exports into a clean workbook |
| Merchant normalization | `ml_preprocessing/MerchantNormalization.ipynb`, `merchant_normalizer.py` | Canonicalize recipient/merchant names |
| Bank-statement pipeline (new) | {{TODO: fill in once built}} | PDF/Excel statement → clean CSV + merchant name |
| Website/frontend | {{TODO: not yet started}} | Upload UI + analytics display |

## Documentation Structure & Creation

Docs live under `docs/spec/`, `docs/operations/`, or the existing SMS-pipeline reference files at
`docs/` root (pre-dating this structure — see `docs/README.md`). Task tracking lives at
`implementation/tracking/STATUS.md` (single flat checklist; move to epic-based tracking only if the
project grows enough parallel workstreams to need it). New markdown files must land in one of these
existing categories — never loose at `docs/` or `implementation/` root. If something doesn't fit,
ask before inventing a new top-level folder.

### Security invariants

- Never hardcode a bank-statement decryption password (or any credential) in a notebook or script —
  this repo is public. Read from an environment variable instead.
- Raw statement files and personal transaction exports never enter git —
  `ml_preprocessing/CSVS/*` and `ml_service/data/*` are gitignored except `.gitkeep`.
- Account numbers are masked (last 4 digits only) before being surfaced anywhere; the
  `_raw_sensitive` metadata block in `CSV_PARSER.ipynb` is debug-only and must never be saved to a
  CSV/DataFrame or persisted to Supabase.

See `docs/spec/security.md` for the full write-up.

### Architectural invariants

- The SMS-ingestion pipeline and the bank-statement pipeline solve different problems and must not
  be merged or assumed to share requirements (see "Explicitly out of scope" above).
- Transaction categorization (`routes/categorize.py`) is a separate concern from merchant/recipient
  extraction — don't conflate the two deliverables.
- Reusable parsing/cleaning logic used from more than one notebook belongs in a plain `.py` module
  (e.g. `merchant_normalizer.py`), not copy-pasted across notebooks.

### Infrastructure constraints

- Supabase is the persistence layer (`ml_service/app/supabase_client.py`); no other datastore is in
  use. {{TODO: record here if/when that changes.}}

## Git & GitHub Workflow

### Git Operations

- Always ask for explicit confirmation before pushing to GitHub — never push proactively.
- Never force-push (`--force`, `--force-with-lease`) under any circumstance.
- Never rewrite history (rebase, amend a pushed commit, squash) unless explicitly requested for the
  specific commit(s) in question.
- Never delete a local or remote branch without approval.
- Never modify repository settings (branch protection, webhooks, secrets, collaborators, CI
  permissions) without approval.
- Verify the current branch before making any commit — never assume.
- Keep the working tree clean: no stray debug files, commented-out code, or unrelated changes
  bundled into a commit.

### Commit Policy

- Commit only after completing a logical unit of work — not mid-edit.
- Use conventional-commits format (see `docs/operations/development_guidelines.md`).
- Never commit code that fails to build or fails its test suite, unless explicitly asked for a
  checkpoint commit — say so in the message (e.g. `wip: checkpoint, tests not passing`).
- Run the relevant test suite(s) for whatever was touched before recommending a commit or push.

### Working on `main`

- Solo project — work directly on `main`. Feature branches and pull requests are **not** part of the
  normal workflow; do not create them by default.
- Commit each completed unit of work straight to `main`, then ask before pushing.
- Branches remain available if a risky/experimental change warrants isolation — optional, never the
  default.
- The repo is currently sitting on `feature/supabase`, ahead of `main` — {{TODO: decide/record when
  and how that gets merged back, since the standing preference above assumes `main` is the working
  branch going forward.}}

### Repository Safety

- Never commit secrets, API keys, credentials, or `.env` files.
- Protect the existing project structure — don't reorganize directories as a side effect of unrelated
  work.
- Prefer incremental, reviewable changes over large refactors.

### Communication

Before any potentially destructive or irreversible action — force-push, history rewrite, branch
deletion, repo settings change, or anything else that can't be undone — stop and ask for confirmation
first.
