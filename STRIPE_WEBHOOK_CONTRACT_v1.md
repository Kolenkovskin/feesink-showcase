# STRIPE_WEBHOOK_CONTRACT v1 — FeeSink

## Purpose

This document defines the **canonical contract** for handling Stripe webhooks
in FeeSink Phase 3 (Stripe).

Scope:
- Stripe TEST only (current phase)
- Single-scenario correctness
- Idempotent crediting

---

## Supported Event (v1)

### checkout.session.completed

This is the **only** Stripe event that triggers credit in Phase 3.

---

## Canonical Payload Assumptions (TEST)

For `checkout.session.completed` in current setup:

- `event.type` = `checkout.session.completed`
- `event.data.object.payment_status` = `paid`
- `event.data.object.id` = `session.id`
- `event.data.object.customer` = `null` (allowed)
- `event.data.object.line_items` = **absent** (allowed)
- `event.data.object.metadata` **MUST contain**:
  - `account_id`
  - `price_id`

These assumptions are **confirmed by real payloads** in Stripe TEST.

---

## Source of Truth Resolution

### session.id
Resolved from:
- `event.data.object.id`

### account_id
Resolved in order:
1. `event.data.object.metadata.account_id` (primary, REQUIRED)
2. (future) lookup via `stripe_links.session_id`

If `account_id` cannot be resolved:
- credit MUST NOT be applied
- provider_event MUST be marked as unresolved

---

### price_id
Resolved in order:
1. `event.data.object.metadata.price_id` (primary, REQUIRED)
2. `event.data.object.line_items[].price.id` (future / optional)

If `price_id` cannot be resolved:
- credit MUST NOT be applied
- provider_event MUST be marked as unresolved

---

## Credited Units Calculation

- `price_id` MUST exist in `PRICE_UNITS_MAPPING_v1.md`
- `credited_units` is derived **only** from mapping
- No dynamic calculation or fallback is allowed

If mapping is missing:
- credit MUST NOT be applied
- provider_event MUST be marked as unresolved_price_id

---

## Idempotency Rules (P0)

### provider_events
- Unique by `(provider, provider_event_id)`
- Used for **audit and observability only**
- provider_event dedup MUST NOT block credit

### topups (credit idempotency)
- Unique by `tx_hash`
- Stripe tx_hash format:
