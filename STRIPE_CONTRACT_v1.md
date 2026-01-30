# FeeSink — STRIPE CONTRACT v1

Version: v2026.01.19-02  
Status: **LIVE / FROZEN**  
Scope: FeeSink — Stripe Checkout + Webhook (LIVE)

---

## ⚠️ LIVE CONTRACT — CHANGE CONTROL (P0)

This contract is **frozen** after a confirmed milestone:  
**STRIPE_LIVE_END2END_PASS (2026-01-19)**.

Any changes:
- are allowed **only** in a new file `STRIPE_CONTRACT_v2.md`
- require a new project phase and a separate milestone

Modifying this file is **forbidden**.

---

## 0. Purpose

This document defines the **canonical LIVE Stripe contract** for FeeSink.  
It fixes the **only allowed** logic for accepting payments in production.

---

## 1. Stripe Mode Invariant (P0)

- `FEESINK_STRIPE_MODE = live`
- Only `sk_live_*` and `whsec_live_*` keys are allowed
- TEST / LIVE mixing is strictly forbidden

Violation = **critical billing error**.

---

## 2. Checkout Creation (LIVE)

Endpoint:

POST /v1/stripe/checkout_sessions


### Rules (P0)

- The price is selected **only** from ENV:
  - `STRIPE_PRICE_ID_EUR_50`
- Request body **cannot** affect the price
- `price_id` is **never accepted** from the client

### Side effects

- A Stripe Checkout Session is created
- A mapping is stored:
  - `stripe_links(stripe_session_id → account_id)`

---

## 3. Webhook Acceptance

Endpoint:

POST /v1/webhooks/stripe


### The only event that may trigger a credit

- `checkout.session.completed`

### All other events

- are accepted (HTTP 200)
- **never** result in a credit

---

## 4. provider_events — Audit Only (P0)

- All Stripe events are stored in `provider_events`
- Deduplication by `provider_event_id`
- **provider_events is NOT a credit idempotency boundary**

Forbidden:
- using `provider_events` as a credit gate

---

## 5. Account Resolution (LIVE)

Resolution order:

1. `event.data.object.metadata.account_id`
2. `stripe_links.account_id` by `session.id`

If `account_id` cannot be resolved:
- credit is forbidden
- webhook returns HTTP 500
- Stripe retries the webhook

---

## 6. Price → Units Mapping (P0)

- `metadata.price_id` is mandatory
- Mapping is defined **only** in `PRICE_UNITS_MAPPING_v1.md`
- Fallbacks or calculations are forbidden

---

## 7. Credit Contract (TopUp)

Credit is allowed **only if**:

- event = `checkout.session.completed`
- `payment_status == paid`

Domain object:

```python
TopUp(
  account_id,
  tx_hash="stripe:<provider_event_id>",
  amount_usdt,
  credited_units,
  ts_utc
)
8. Idempotency (P0)
The only idempotency boundary for crediting is:

topups.tx_hash
Repeated webhooks:

never cause double credit

never block the first valid credit

9. Observability (Required)
Each LIVE credit MUST log:

provider_event_id

session_id

account_id

price_id

credited_units

decision (credited | dedup | failed)

Silent failure is forbidden.

10. Status
The Stripe LIVE contract is fixed and frozen.
Any change requires a new contract version and a new project phase.