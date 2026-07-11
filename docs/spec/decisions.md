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

{{TODO: log future decisions here as they're made — e.g. sync-vs-async statement processing, PDF
parsing library choice, per-user auth model for uploads.}}

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
