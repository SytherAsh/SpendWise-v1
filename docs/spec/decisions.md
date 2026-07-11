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
