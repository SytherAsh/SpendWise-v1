# Development Guidelines

## Git workflow

Solo project — work directly on `main`. See `CLAUDE.md`'s "Git & GitHub Workflow" section for the
full policy (commit style, when to ask before pushing, branch usage). This file doesn't repeat that
— it's the canonical copy.

## Commit style

Conventional-commits-flavored, imperative mood, one logical unit of work per commit (e.g. `Add
Excel bank-statement raw-extraction notebook`, `Remove Spring Boot backend`) — see recent git log for
the pattern this repo actually follows.

## Coding standards

- **Notebooks** (`ml_preprocessing/`): keep cells re-runnable top-to-bottom; extract genuinely
  reusable logic (regex rules, cleaning helpers) into a plain `.py` module (e.g.
  `merchant_normalizer.py`) once it's used from more than one notebook or needs test coverage.
- **`ml_service`**: Pydantic models in `app/schemas/`, routes thin (`app/routes/`), business logic in
  `app/service.py` / dedicated processor modules (`sms_parser.py`, `financial_sms_processor.py`,
  `excel_loader.py`).
- {{TODO: add linting/formatting tooling here once adopted (e.g. ruff/black, notebook output
  stripping via nbstripout) — none is currently enforced.}}

## Pre-commit checklist

- Run the relevant pytest suite (`ml_service/tests/`) for anything touching `ml_service`.
- Check for hardcoded credentials before committing anything under `ml_preprocessing/` — see
  `docs/spec/security.md`.
- Confirm `ml_preprocessing/CSVS/*` and `ml_service/data/*` changes are only `.gitkeep` — real data
  files must stay gitignored.
