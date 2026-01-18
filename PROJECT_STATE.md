# FeeSink — Project State

**Project:** FeeSink  
**Last updated:** 2026-01-17  
**Status:** MVP-ready / Pre-sales  
**Mode:** P0

---

## TL;DR

FeeSink достиг состояния **технической и биллинговой готовности**.  
Core-инварианты зафиксированы smoke-тестами и CI-guards.  
Проект готов к **pre-sales и первым платным пользователям**.

---

## Current Phase

### Phase 4 — MVP Ready / Pre-Sales ✅

**Definition of Done:**
- Stripe LIVE billing verified end-to-end
- Storage contracts stable and covered by smoke
- Idempotency guarantees enforced
- CI guards prevent regressions
- Product canon fixed (prepaid units only)

**Status:** ACHIEVED

---

## Verified Milestones

### Billing & Payments
- Stripe LIVE mode configured and verified
- One-time top-ups supported
- Idempotent processing by `tx_hash`
- TEST/LIVE kill-switch via `FEESINK_STRIPE_MODE`
- No subscriptions, no custody, no fiat storage

### Storage
- SQLite storage fully split into small modules (≤700 LOC)
- Storage contracts verified by `db_smoke_sqlite.py`
- Invariants enforced:
  - prepaid balance only
  - 1 check = 1 unit
  - no negative balance
  - no double charge on retries

### CI & Safety Guards
- `import_smoke.py` — fail-fast import guard (PASS)
- `db_smoke_sqlite.py` — billing smoke (PASS)
- `lint_module_size.py` — module size guard ≤700 LOC (PASS)
- CI order enforced:
  1. import_smoke
  2. sqlite smoke
  3. size guard
- Smoke logs uploaded as CI artifacts on failure

### Architecture
- API and storage split into deterministic modules
- Facade pattern preserved
- Large historical patch scripts archived (`scripts/_archive/`)
- No active module exceeds size limits

---

## Product Canon (Locked)

The following rules are **non-negotiable**:

- Prepaid balance only
- 1 check = 1 unit
- Charge occurs strictly after check event fixation
- Idempotency by `dedup_key` / `tx_hash`
- Insufficient funds is a valid state, not an error
- No subscriptions
- No custody
- No fiat balance storage

---

## What Is NOT Done Yet

### P0 (Required for Sales)
- Product-facing README (one-pager style)
- Final pricing table for units
- Stable public HTTPS endpoint (non-ngrok)
- Minimal onboarding instructions

### P1 (Post First Users)
- Simple dashboard or CLI onboarding helper
- Usage notifications / alerts
- Additional storage backend (optional)

---

## Next Intended Phase

### Phase 5 — First Paying Users

**Goal:**
- Accept first real payments from external users
- Validate pricing and usage patterns
- Keep scope minimal, avoid feature creep

**Exit Criteria:**
- ≥1 external paying user
- ≥1 real-world usage scenario confirmed
- No billing regressions detected by CI

---

## Notes

This document is the **single source of truth** for project readiness.  
Any downgrade of status requires explicit justification and revision.
