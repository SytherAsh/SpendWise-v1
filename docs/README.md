# Documentation Index

Navigation for everything under `docs/`. Start at the root [`CLAUDE.md`](../CLAUDE.md) — it states
the current task focus and links here for depth.

| Document | Contents | Consult when |
| --- | --- | --- |
| [spec/vision.md](spec/vision.md) | Product vision, success criteria, target users | Defining user-facing features or evaluating scope |
| [spec/requirements.md](spec/requirements.md) | Functional and non-functional requirements | Adding or changing any feature requirement |
| [spec/architecture.md](spec/architecture.md) | System architecture, module breakdown, data flow | Any cross-module work, new module, or data-flow change |
| [spec/decisions.md](spec/decisions.md) | Architecture Decision Records (ADRs) | Before proposing a new architectural approach |
| [spec/api.md](spec/api.md) | REST endpoint reference (`ml_service`) | Adding/changing a FastAPI route |
| [spec/database.md](spec/database.md) | Supabase schema + design decisions | Any schema change or new table |
| [spec/security.md](spec/security.md) | Auth model, secrets handling, data-access rules | Touching credentials, `.env`, or anything bank-statement-related |
| [operations/development_guidelines.md](operations/development_guidelines.md) | Branching, commit style, coding standards | Code style questions; before committing |
| [operations/testing.md](operations/testing.md) | Testing strategy per surface | Writing or updating tests |

## Existing SMS-pipeline reference docs (pre-dating this index)

These describe the **SMS ingestion pipeline** specifically — accurate and still authoritative for
that surface, but not for the new PDF/Excel statement-upload pipeline (see `CLAUDE.md`'s "Current
task focus"). As of 2026-07-11 this tier was consolidated from five docs down to two: the other three
(`ml_service_pipeline_summary.md`, `sms_to_true_financial.md`, `code-audit-and-data-quality-report.md`,
`walkthrough.md`) had drifted from the actual code — one described a downstream pipeline stage that
was never built — and were folded into `sms_pipeline.md`, which was re-verified against the code.

| Document | Contents |
| --- | --- |
| [sms_pipeline.md](sms_pipeline.md) | Module-by-module architecture reference for the `ml_service` (FastAPI) SMS pipeline: flow, parser/processor/persistence internals, data artifacts, known gaps |
| [transaction-analysis-report.md](transaction-analysis-report.md) | Statistical/data-quality report on the transaction dataset (bank workbook + SMS financial file) |

## Rule

New markdown docs land in one of the categories above (`spec/`, `operations/`, or a to-be-created
add-on tier) — never loose at `docs/` root. If a new doc doesn't fit an existing category, that's a
signal to ask before inventing a new top-level folder.
