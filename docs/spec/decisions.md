# Architecture Decision Records

Append-only log — add a new entry per decision, never edit or delete a past one (mark it
Superseded and link forward instead).

## Format

```
## ADR-NNNN: <title>
Date: YYYY-MM-DD
Status: Proposed | Accepted | Superseded by ADR-NNNN

**Context** — what prompted the decision.
**Decision** — what was decided.
**Consequences** — trade-offs accepted.
```

---

## ADR-0001: Remove the Spring Boot backend

Date: {{TODO: fill actual date}}
Status: Accepted

**Context** — The project originally had a Java/Spring Boot API layer (`backend/` /
`SpendWise_Backend/`) alongside the FastAPI `ml_service`, described in the old README as the
client-facing REST API.

**Decision** — Deleted the Spring Boot backend entirely. FastAPI (`ml_service`) is now the sole
backend surface.

**Consequences** — Simpler single-language backend; any REST surface a future website needs must be
added to `ml_service` rather than resurrecting the Java layer. Historical git commits/docs
referencing `SpendWise_Backend` predate this and should be read as stale.

---

## ADR-0002: Merchant-name canonicalization stops at UPI-linkage + structural prefix matching, not further fuzzy tuning

Date: 2026-07-11
Status: Accepted

**Context** — `merchant_normalizer.py`'s two-tier canonicalization (exact `UPI_ID` grouping +
fuzzy name clustering) was producing both over-merges (unrelated payees collapsed into one
canonical name, e.g. "BIKANER" absorbed into "SUNIL SH") and under-merges (the same payee left
split across canonical names, e.g. "AIRTEL" vs "AIRTEL P") when validated against the full real
dataset (467 distinct canonical names from `SpendWise_4yrs_Clean.xlsx`), not just hand-picked
examples. Root causes, confirmed against real UPI_ID/Bank data: (1) short, payment-gateway-
generated UPI codes (`paytmqr2`, `paytm.s1`, etc.) get reused across dozens of unrelated payees —
one such code covered 43 distinct names; (2) recipient names are truncated to ~7-8 characters by
the bank export, which both produces coincidental fuzzy-similarity collisions between unrelated
names and destroys enough information that a truncated name and its own full form score far
below any safe fuzzy-match threshold.

Two more-aggressive fixes were tried and rejected after full-dataset validation (not just the
motivating examples): a global `rapidfuzz.fuzz.partial_ratio` merge pass, and a global
`token_set_ratio` merge pass. Both looked clean on 4-5 hand-picked pairs but produced 750+ noisy
candidate pairs across the full 467-name set, including resurrecting collisions the UPI-tier fix
was built to reject (e.g. "JULFIKAR"/"BIKANER" back together at 72.7). Conclusion: at 7-8
character truncated strings, no single global fuzzy-similarity threshold safely separates true
matches from coincidence — the population is too dense for any cutoff to be both precise and
complete.

**Decision** — Canonicalization is capped at three techniques, each validated to have a clean
separation between true and false matches on the full dataset (not samples):

1. UPI-ID grouping, gated by both a max-distinct-names-per-group cap (junk gateway codes fan out
   across 11+ names; legitimate payee groups top out at 3) and a within-group fuzzy-similarity
   floor (complete-linkage clustering instead of majority vote, so genuine ties still merge).
2. Global fuzzy clustering (`cluster_by_fuzzy_name`, threshold 90) for rows without a usable UPI ID.
3. `find_prefix_variants` — a structural (not fuzzy-score) check for one canonical name being a
   literal prefix of another, surfaced as a review list, not auto-merged.

Anything the above three don't catch (e.g. "PRACHI S" / "MRS. PRACHI SAMEER SAW" — no shared UPI,
not a prefix relationship) is handled by a small hand-curated `manual_aliases` dict in the
notebook, populated by the account holder confirming candidates by eye. No further automated
fuzzy-matching work is planned on this module; the remaining ambiguity (~5% of rows, concentrated
in one-off transactions, not the high-frequency recurring payees) is judged not worth the
regression risk demonstrated by the two rejected approaches above.

**Consequences** — Merchant canonicalization will never be 100% automatic for this data source;
some fraction of rows will always need a human glance at `find_prefix_variants`' output per new
statement. A genuinely different technique (e.g. recurring-transaction fingerprinting by amount +
periodicity, or an external UPI-handle-to-registered-name lookup) would be needed to close that
gap further, and is out of scope here — it's a different feature (subscription/recurring-bill
detection) with its own privacy trade-offs (the lookup option means sending transaction data to a
third party), not an extension of this notebook.

---

## ADR-0003: SMS↔statement reconciliation is order-independent, triggered only on statement upload

Date: 2026-07-12
Status: Accepted

**Context** — The live product has two independent data sources per user: a continuous stream of
SMS/notification captures (Android app, always-on) and occasional bank-statement uploads (whenever
the user gets around to it). A user may connect SMS first and upload a statement later, upload a
statement first and connect SMS afterward, or interleave both indefinitely — the order is entirely
up to the user and not knowable in advance. Both sources can describe the same real-world
transaction, so without reconciliation the same transaction would be double-counted in analytics.
`ml_service/app/services/build_unified_dataset.py` already solved the matching logic once, offline,
as a one-time script over two local files (a fixed SMS CSV export and a fixed statement workbook)
with a hand-curated manual-alias step — this decision generalizes that logic into a live, repeatable,
per-account operation and removes the parts (manual aliases) that don't scale to arbitrary users.

**Decision**
- SMS ingestion never looks backward: every incoming message is inserted immediately as its own
  `transactions` row (`source = 'sms'`, `is_reconciled = false`). No matching is attempted at ingest
  time.
- Statement upload always looks backward: every upload (an account's first or a later one) runs one
  reconciliation pass matching the new rows against that account's existing Supabase state — both
  previously-persisted `source = 'statement'` rows (to make repeat/incremental statement uploads
  idempotent) and unreconciled `source = 'sms'` rows (to backfill/dedupe against the live stream).
- On a statement↔SMS match, the existing SMS row is updated in place (balance/ref/mode backfilled
  from the statement, `is_reconciled` set `true`) rather than inserting a second row — there is never
  more than one row per real-world transaction, so no read-time dedup is needed downstream.
- Matching priority per new statement row: exact `ref_norm` match first, falling back to
  date+amount+direction (statement↔SMS) or date+details+balance (statement↔statement) — same
  fallback structure `build_unified_dataset.build()` already uses.
- `accounts` gains `user_id` and `account_number_masked`; one `accounts` row per
  `(user_id, bank_name, account_number_masked)` is how repeat uploads for the same account are
  recognized (see `docs/spec/database.md`).
- Processing is synchronous — parse, clean, reconcile, and canonicalize inline, return the result in
  one HTTP response. Statement volumes are personal-scale (~2,000 rows/statement observed), well
  within a synchronous request budget; revisit if PDF/OCR parsing later changes that.
- Real per-user auth doesn't exist yet (website isn't built). The upload endpoint takes `user_id` as
  a required request field as a stopgap, so the DB shape is already user-scoped and no schema change
  is needed once real auth lands.

**Consequences** — Reconciliation logic lives once, keyed off DB state rather than fixed input files,
and is safe to call on every statement upload regardless of history. The `user_id`-as-request-field
stopgap is a known soft spot — it trusts the caller — and must be replaced with real auth
(`docs/spec/security.md`) before this is exposed beyond a trusted local frontend. Because SMS never
looks backward, a statement covering a period *before* SMS capture began will correctly find no SMS
counterparts (nothing to reconcile against yet) and every row lands as a plain new `source =
'statement'` insert — expected, not a bug.

---

## ADR-0004: Live merchant canonicalization drops the manual-alias step from ADR-0002

Date: 2026-07-12
Status: Accepted

**Context** — ADR-0002 accepted that merchant canonicalization tops out at ~95% automatic, with the
remainder resolved by a hand-curated `manual_aliases` dict populated by the account holder eyeballing
`find_prefix_variants` output. That works for a single offline dataset (the account holder's own
history) but does not generalize to a live multi-user endpoint — a new user has no pre-existing alias
dict, and building one requires a human reviewing their private transaction data, which isn't
something the account holder can do on a stranger's behalf.

**Decision** — The live statement-upload endpoint runs only the fully algorithmic tiers of
`merchant_normalizer.py`: `normalize_recipients` (UPI-ID grouping + fuzzy clustering) followed by
`merge_prefix_chains` (safe truncation-prefix auto-merge). No manual-alias step. Some under-merging
(the same ~5% class of cases ADR-0002 already identified as the ceiling of automatic matching) is an
accepted v1 limitation, not treated as a bug to fix before shipping.

**Consequences** — A minority of a new user's transactions may show up under more than one canonical
name for the same real merchant, until/unless a manual-correction affordance is added to the
dashboard later (out of scope for now — no UI exists yet to surface it). The offline notebook
workflow (`MerchantNormalization.ipynb`) keeps its manual-alias step for the account holder's own
historical dataset; that workflow and the live endpoint diverge in this one respect deliberately, not
by oversight.
