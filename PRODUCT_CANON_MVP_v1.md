# FeeSink — Product Canon (MVP v1)

Version: v2026.01.03  
Status: canonical, Phase 2 validated by demo run

This document defines the **canonical MVP product rules**.
If any other document or code contradicts this file — **this file has priority**.

---

## 1) What FeeSink is (short)

FeeSink is a service that **sells checks**.

A *check* is a single fact of attempting to check an HTTP endpoint.
The check result (`ok` / `fail`) **does not affect charging**.

---

## 2) Canonical product promise

The user buys **prepaid units**.  
Each performed check **deterministically** consumes **exactly 1 unit**.

No subscriptions.  
No plans.  
No postpaid billing.

---

## 3) Absolute product invariants (P0)

These rules are **not negotiable** and cannot be weakened without an explicit decision:

- prepaid balance only  
- 1 check = 1 unit  
- charging happens **strictly after the check fact** (post-check)  
- retrying a check must never cause double charging  
- retrying a payment event must never cause double crediting  
- zero balance is a valid state, not an error  
- the system prefers **not to perform a check** rather than perform it without correct charging  

---

## 4) Canonical charging model (Phase 2)

### Current canon (SQLite / demo / dev)

- The canonical charge record is a row in the `check_events` table
- Idempotency is enforced via `UNIQUE(dedup_key)`
- `dedup_key` format:  
  `endpoint_id + ":" + scheduled_at_utc`
- `scheduled_at_utc` is mandatory and always UTC
- If `balance_units < 1`:
  - the check is **not performed**
  - no `check_events` record is created
  - no partial side effects occur

### `charges` table

- **Not used** in MVP Phase 2
- Reserved for future phases
- Not a source of truth in the current canon

---

## 5) What the user actually buys

The user does **not** buy:
- uptime,
- SLA,
- reports,
- guarantees.

The user buys **check attempts**.

Each attempt is:
- counted,
- charged,
- reproducible.

---

## 6) MVP scope (what is included)

The MVP **includes**:

- payment intake → conversion to units
- idempotent crediting of units
- adding / pausing / removing endpoints
- check scheduler
- deterministic unit charging
- clean refusal on depletion with no side effects

---

## 7) Explicit non-goals (MVP)

The MVP **does not include**:

- subscriptions
- pricing plans
- auto-renewal
- monitoring dashboards
- alerting
- reports
- analytics
- SLA / guarantees
- user roles
- visual UI

Any of these features are allowed **only after MVP**.

---

## 8) Economic goal of the MVP

The goal of the MVP is:
> to reach the first **real payments** as quickly as possible

This implies:
- minimal UX,
- minimal UI,
- maximum billing reliability.

Any task that does not move toward payment is **out of MVP scope**.

---

## 9) Compatibility with future phases

Despite MVP minimalism:

- all events must be auditable
- idempotency keys must be stable
- the storage model must be extensible
- MVP decisions must not block:
  - Stripe
  - Postgres
  - reconciliation
  - reporting

---

## 10) Final canonical statement

FeeSink is a billing core for checks.

If the system violates the rule  
**“1 performed check = 1 charged unit”**,  
this is a **bug**, not a feature.

The MVP is considered valid **only if this canon is strictly enforced**.
