# Security

## Secrets handling

- `ml_service/.env` holds `SUPABASE_URL`, `SUPABASE_KEY`, `ACCOUNT_ID` — never commit this file
  (already gitignored).
- Bank-statement decryption passwords (for password-protected `.xlsx` exports, via `msoffcrypto`)
  must **never** be hardcoded in a notebook or script. Read from an environment variable (e.g.
  `BANK_STATEMENT_PASSWORD`) or an untracked local config — this repo is public on GitHub, so
  anything committed is permanently visible in git history even if later removed.
- If a hardcoded credential is ever found in a diff before commit, treat it as a blocker: redact and
  use an env var instead, don't commit "just this once."

## Data handling

- Raw bank-statement files and personal transaction exports never enter git —
  `ml_preprocessing/CSVS/*` and `ml_service/data/*` are gitignored except `.gitkeep`.
- Account numbers are masked before being surfaced anywhere (see `mask_identifier` /
  `parse_statement_header` in `CSV_PARSER.ipynb` — keeps only the last 4 digits).
- The `_raw_sensitive` metadata block parsed from a statement header (email, full account number,
  CIF, MICR, IFSC, branch contact info) is for debugging within the notebook session only — never
  write it to a CSV/DataFrame that gets saved or persisted to Supabase.

## Auth model

Real per-user auth is not yet built (the website itself doesn't exist yet). Per ADR-0003, the
statement-upload endpoint (`POST /api/statements/upload`) takes `user_id` as a required request
field as an interim stopgap — this trusts the caller and provides no actual access control, but it
means `accounts`/`recipients`/`transactions` are already user-scoped in the schema (see
`docs/spec/database.md`) so real auth can be swapped in later (session/token model, row-level
security in Supabase) without a schema migration. This stopgap must not be exposed beyond a trusted
local frontend — treat it as a placeholder, not a security boundary.

{{TODO: still open — session/token model, whether `ACCOUNT_ID` generalizes to a real multi-user
identity, row-level security in Supabase. Must be resolved before the website goes live with real
per-user data beyond the account holder's own testing.}}

## Compliance

{{TODO: fill in if/when this handles data for anyone beyond the account holder — e.g. data retention,
deletion requests, PII minimization beyond the account-number masking above.}}
