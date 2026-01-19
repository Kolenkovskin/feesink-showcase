# FeeSink — Project State

**Project:** FeeSink  
**Last updated:** 2026-01-19  
**Status:** MVP-ready / LIVE  
**Mode:** P0

---

## TL;DR

FeeSink достиг состояния **фактической LIVE-готовности с приёмом реальных платежей**.  
Stripe LIVE проверен end-to-end: оплата → webhook → credit → баланс.  
Биллинг-инварианты соблюдены, регрессий не выявлено.

---

## Current Phase

### Phase 5 — Stripe LIVE / First Real Payments ✅

**Definition of Done:**
- Stripe LIVE включён (`FEESINK_STRIPE_MODE=live`)
- Реальный платёж выполнен и подтверждён банком
- Webhook доставлен (HTTP 200)
- Средства зачислены в prepaid-баланс
- Идемпотентность подтверждена
- Persistent storage используется

**Status:** ACHIEVED

---

## Verified Milestones

### Billing & Payments

- STRIPE_TEST_CONTRACT_FROZEN | 2026-01-19 | TEST canon locked
- STRIPE_ENV_AUDIT_PASS | 2026-01-19 | `scripts/stripe_env_audit.py` SUMMARY=PASS
- STRIPE_LIVE_ENABLED | 2026-01-19 | `FEESINK_STRIPE_MODE=live`
- **STRIPE_LIVE_END2END_PASS | 2026-01-19 | real payment → webhook → credit → balance**
- STRIPE_WEBHOOK_DELIVERY_OK | 2026-01-19 | `checkout.session.completed` delivered (200)
- STRIPE_CREDIT_CONFIRMED | 2026-01-19 | `topups` + `accounts.balance_units` updated

### Storage

- SQLITE_PERSISTENT_DISK_ENABLED | 2026-01-19 | `/var/data/feesink.db`
- STORAGE_CONTRACT_PASS | 2026-01-17 | `db_smoke_sqlite.py`
- CREDIT_IDEMPOTENCY_CONFIRMED | 2026-01-19 | dedup by `tx_hash`

### CI & Safety Guards

- IMPORT_SMOKE_PASS | 2026-01-17 | `import_smoke.py`
- SQLITE_SMOKE_PASS | 2026-01-17 | `db_smoke_sqlite.py`
- MODULE_SIZE_GUARD_PASS | 2026-01-17 | ≤700 LOC
- STRIPE_ENV_AUDIT_REQUIRED | enforced | pre-LIVE & LIVE

---

## Product Canon (Locked)

Non-negotiable invariants:

- Prepaid balance only
- 1 check = 1 unit
- Charge strictly after check fixation
- Idempotency by `dedup_key` / `tx_hash`
- Insufficient funds is a valid state
- No subscriptions
- No custody
- No fiat balance storage

---

## What Is NOT Done Yet

### P1 — External UX & API

- Public onboarding flow
- Product-level README (non-dev)
- Public balance endpoint contract
- Post-payment account summary response

### P1 — Ops

- Stripe LIVE smoke (checkout → webhook → balance delta)
- CI wiring for LIVE (read-only guards)

---

## Next Intended Phase

### Phase 6 — First External Users

**Goal:**
- Onboard first external (non-demo) users
- Validate API usage pattern
- Observe real usage + billing behavior

**Exit Criteria:**
- ≥1 external paying account
- ≥1 real endpoint checked
- No billing regressions detected

---

## Notes

This document is the **single source of truth** for LIVE readiness.  
Any downgrade requires explicit revision and justification.

