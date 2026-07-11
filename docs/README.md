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
task focus"):

| Document | Contents |
| --- | --- |
| [ml_service_pipeline_summary.md](ml_service_pipeline_summary.md) | Module-by-module architecture reference for the current `ml_service` (FastAPI) SMS pipeline |
| [sms_to_true_financial.md](sms_to_true_financial.md) | Function/line-level reference for the SMS → `true_financial_sms.csv` flow |
| [code-audit-and-data-quality-report.md](code-audit-and-data-quality-report.md) | Audit of the SMS ingestion/parsing pipeline, bugs found, and the labeling/confidence refactor |
| [transaction-analysis-report.md](transaction-analysis-report.md) | Statistical/data-quality report on the transaction dataset |
| [walkthrough.md](walkthrough.md) | Full system walkthrough (Android → FastAPI → CSV/Supabase) |

## Rule

New markdown docs land in one of the categories above (`spec/`, `operations/`, or a to-be-created
add-on tier) — never loose at `docs/` root. If a new doc doesn't fit an existing category, that's a
signal to ask before inventing a new top-level folder.
