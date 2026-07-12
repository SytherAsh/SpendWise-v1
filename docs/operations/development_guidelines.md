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

- **Notebooks** (`ml_preprocessing/`): `.ipynb` files and their `CSVS/` data artifacts only — no
  `.py` modules live here. Keep cells re-runnable top-to-bottom; extract genuinely reusable logic
  (regex rules, cleaning helpers) into a plain `.py` module under `ml_service/app/services/` (e.g.
  `merchant_normalizer.py`, `build_unified_dataset.py`) once it's used from more than one notebook
  or needs test coverage — import it back into the notebook via a `sys.path` bootstrap (see
  `SMS_Pipeline.ipynb` / `BuildUnifiedDataset.ipynb` for the pattern).
- **`ml_service`**: Pydantic models in `app/schemas/`, routes thin (`app/routes/`), business logic in
  `app/services/` (`persistence.py`, `sms_pipeline.py`, `merchant_normalizer.py`,
  `build_unified_dataset.py`), parsing/extraction in `app/parsers/` (`sms_parser.py`,
  `excel_loader.py`), external clients in `app/clients/` (`supabase_client.py`).
- {{TODO: add linting/formatting tooling here once adopted (e.g. ruff/black, notebook output
  stripping via nbstripout) — none is currently enforced.}}

## Pre-commit checklist

- Run the relevant pytest suite (`ml_service/tests/`) for anything touching `ml_service`.
- Check for hardcoded credentials before committing anything under `ml_preprocessing/` — see
  `docs/spec/security.md`.
- Confirm `ml_preprocessing/CSVS/*` and `ml_service/data/*` changes are only `.gitkeep` — real data
  files must stay gitignored.
