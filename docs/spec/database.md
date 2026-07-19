# Database (Supabase / Postgres)

Schema-init SQL lives in the root `README.md` — copy here as it evolves so this doc stays the source
of truth for schema *decisions*, not just the raw DDL.

## Tables (current)

- **`accounts`** — `id`, `bank_name`, `account_type`, `created_at`, plus (new, ADR-0003)
  `user_id`, `account_number_masked`.
- **`recipients`** — `id`, `name`, `upi_id`, `bank_name`, `created_at`, plus (new, ADR-0003) `user_id`.
  Populated from `merchant_normalizer.py`'s canonical recipient names where the statement pipeline is
  the source. Scoped per-user — fuzzy clustering must never run across two users' data, even
  incidentally.
- **`transactions`** — `id`, `account_id` (FK → accounts), `recipient_id` (FK → recipients),
  `transaction_reference`, `transaction_date`, `amount`, `debit`, `credit`, `balance`,
  `transaction_mode`, `dr_cr_indicator`, `note`, `created_at`, plus (new, ADR-0003):
  - `source` — `'sms'` or `'statement'`: which pipeline produced this row.
  - `is_reconciled` — whether this row has been matched against the other source.
  - `ref_norm` — normalized transaction reference, persisted so reconciliation on a later upload
    doesn't need to re-derive it from scratch (see `build_unified_dataset.clean_ref`).

Indexes: `accounts.bank_name`, `accounts.(user_id, account_number_masked)` (new),
`recipients.upi_id`, `recipients.user_id` (new), `transactions.account_id`,
`transactions.recipient_id`, `transactions.created_at desc`, `transactions.(account_id, ref_norm)`
(new — reconciliation lookup).

## Design decisions

- `recipients` is a separate table from `transactions` (not a denormalized text column) specifically
  so that merchant-name normalization (`merchant_normalizer.py`'s UPI-ID grouping + fuzzy clustering)
  can converge multiple raw name spellings onto one canonical `recipients` row.
- **Per-user scoping (ADR-0003)**: one `accounts` row per `(user_id, bank_name, account_number_masked)`.
  A repeat/incremental statement upload for the same account matches this existing row rather than
  creating a new one; new rows in the upload are reconciled against that account's existing
  `transactions` (see `docs/spec/architecture.md`'s "Reconciliation model") instead of replacing them.
- **SMS↔statement dedup (ADR-0003)**: on a match, the existing `transactions` row is updated in place
  (its `source` stays `'sms'`, `is_reconciled` flips to `true`, fields get backfilled from the
  statement) rather than inserting a second row for the same real-world transaction. This keeps
  "one row per real-world transaction" true at all times, so no read-time dedup is ever needed by the
  dashboard/analytics layer.

## Not yet decided

- Multi-user scoping / row-level security — see `docs/spec/security.md`.
- Whether uploaded source statement files (PDF/Excel) are persisted anywhere, or processed and
  discarded. Current recommendation (not yet locked in): process and discard, don't persist the raw
  file — simplest, most privacy-conscious, matches the synchronous upload model.
