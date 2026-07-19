# Status

Flat task checklist for the active workstream. See `CLAUDE.md`'s "Current task focus" for the
full description of what's being built.

## Current: ML pipeline for bank-statement ingestion (PDF/Excel → clean CSV + merchant name)

- [x] Excel raw-extraction notebook (`CSV_PARSER.ipynb`) — decrypt password-protected `.xlsx`,
      locate the transaction table header, detect date format, dedupe overlapping yearly exports.
- [x] Excel narration parser extended (`Segregation.ipynb`) — IMPS/NEFT/UPI-reversal/ATM/POS/etc.
      regex rules beyond the original single pattern.
- [x] Bank-agnostic merchant/recipient name normalization (`merchant_normalizer.py`,
      `MerchantNormalization.ipynb`) — UPI-ID grouping + fuzzy clustering.
- [x] Design SMS↔statement reconciliation model and per-user scoping (ADR-0003, ADR-0004,
      2026-07-12) — order-independent, triggered on statement upload only, synchronous processing,
      no manual-alias step in the live pipeline. Recorded in `docs/spec/architecture.md`,
      `database.md`, `requirements.md`, `decisions.md`.
- [ ] Code-review pass found several bugs to fix before building the live endpoint (see chat history
      2026-07-12): `excel_loader.py`'s `row["X"] or default` NaN-truthiness bug, `determine_dr_cr`'s
      unhandled `balance_diff == 0` case and implicit sort-order dependency, `CSV_PARSER.ipynb`'s
      dead/miscomputed `is_self_transfer` flag, MerchantNormalization.ipynb's stale filename in its
      markdown cell.
- [ ] Add `user_id`, `account_number_masked` to `accounts`; `user_id` to `recipients`; `source`,
      `is_reconciled`, `ref_norm` to `transactions` (Supabase migration, per `docs/spec/database.md`).
- [ ] Build `POST /api/statements/upload` — raw extraction → narration parsing → clean/DR-CR →
      reconciliation → canonicalization → Supabase (per `docs/spec/architecture.md`'s data flow).
      Excel/CSV input first; PDF input plugs into the same pipeline from "narration parsing" onward.
- [ ] PDF statement parsing — greenfield, no code exists yet.
- [ ] Live per-user upload integration (website → pipeline → Supabase) — not started; website itself
      doesn't exist yet.

## Backlog / not yet scheduled

- Website frontend (upload UI + analytics view).
- Multi-bank support beyond SBI.
- Auth/per-user data scoping (see `docs/spec/security.md`).

## Recently completed (other workstreams)

- Removed the Spring Boot backend — FastAPI is now the sole backend.
- SMS pipeline: multi-class labeling (`FINANCIAL_TRANSACTION` / `OTP` / `PROMOTIONAL` / etc.) with
  confidence scoring, plus an unknown-message review queue.
