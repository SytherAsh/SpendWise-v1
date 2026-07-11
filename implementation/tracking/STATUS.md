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
- [ ] PDF statement parsing — greenfield, no code exists yet.
- [ ] Live per-user upload integration (website → pipeline → Supabase) — not started; website itself
      doesn't exist yet.
- [ ] Decide sync-vs-async processing model for an upload (see `docs/spec/requirements.md`).

## Backlog / not yet scheduled

- Website frontend (upload UI + analytics view).
- Multi-bank support beyond SBI.
- Auth/per-user data scoping (see `docs/spec/security.md`).

## Recently completed (other workstreams)

- Removed the Spring Boot backend — FastAPI is now the sole backend.
- SMS pipeline: multi-class labeling (`FINANCIAL_TRANSACTION` / `OTP` / `PROMOTIONAL` / etc.) with
  confidence scoring, plus an unknown-message review queue.
