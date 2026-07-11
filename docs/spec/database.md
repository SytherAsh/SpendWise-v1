# Database (Supabase / Postgres)

Schema-init SQL lives in the root `README.md` — copy here as it evolves so this doc stays the source
of truth for schema *decisions*, not just the raw DDL.

## Tables (current)

- **`accounts`** — `id`, `bank_name`, `account_type`, `created_at`.
- **`recipients`** — `id`, `name`, `upi_id`, `bank_name`, `created_at`. Populated from
  `merchant_normalizer.py`'s canonical recipient names where the statement pipeline is the source.
- **`transactions`** — `id`, `account_id` (FK → accounts), `recipient_id` (FK → recipients),
  `transaction_reference`, `transaction_date`, `amount`, `debit`, `credit`, `balance`,
  `transaction_mode`, `dr_cr_indicator`, `note`, `created_at`.

Indexes: `accounts.bank_name`, `recipients.upi_id`, `transactions.account_id`,
`transactions.recipient_id`, `transactions.created_at desc`.

## Design decisions

- `recipients` is a separate table from `transactions` (not a denormalized text column) specifically
  so that merchant-name normalization (`merchant_normalizer.py`'s UPI-ID grouping + fuzzy clustering)
  can converge multiple raw name spellings onto one canonical `recipients` row.
- {{TODO: record here once decided — how the new bank-statement pipeline's per-user scoping maps onto
  `accounts`/`transactions` (one `accounts` row per uploaded statement? per bank? per user?).}}

## Not yet decided

- Multi-user scoping / row-level security — see `docs/spec/security.md`.
- Whether uploaded source statement files (PDF/Excel) are persisted anywhere, or processed and
  discarded.
