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

{{TODO: not yet decided. The current SMS-ingestion and Excel bulk-load endpoints have no
authentication — fine for a solo/local-only deployment, but must be addressed before the website
upload flow goes live with real per-user data. Decide and record: session/token model, whether
`ACCOUNT_ID` generalizes to a real multi-user identity, row-level security in Supabase.}}

## Compliance

{{TODO: fill in if/when this handles data for anyone beyond the account holder — e.g. data retention,
deletion requests, PII minimization beyond the account-number masking above.}}
