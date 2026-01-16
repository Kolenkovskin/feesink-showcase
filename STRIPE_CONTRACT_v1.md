# STRIPE_CONTRACT_v1.md

Version: v2026.01.05-02
Status: TEST / CANONICAL
Scope: FeeSink – Stripe Checkout + Webhook (TEST ONLY)

---

## 0. Purpose

This document defines the **canonical Stripe contract** for FeeSink in **Stripe TEST** mode.
Its primary goal is to prevent regressions and repeated integration failures by fixing:

* the exact event that triggers credit,
* idempotency boundaries,
* what deduplication may and may NOT block,
* required DB side effects.

This contract has higher priority than implementation convenience.

---

## 1. Stripe Mode Invariant (P0)

* Only **Stripe TEST** is allowed while this contract is active.
* Mixing test/live keys, prices, or webhooks is forbidden.

Required:

* `STRIPE_SECRET_KEY` starts with `sk_test_`
* `STRIPE_WEBHOOK_SECRET` corresponds to `stripe listen` TEST endpoint

If violated → integration is considered invalid.

---

## 2. Checkout Creation Contract

Endpoint:

```
POST /v1/stripe/checkout_sessions
```

Input:

```json
{
  "price_id": "price_..."
}
```

Requirements:

* Authorization: Bearer token (dev token allowed in TEST)
* `price_id` MUST match an entry in `PRICE_UNITS_MAPPING_v1.md`

Side effects:

* A Stripe Checkout Session is created
* A row is inserted/upserted into `stripe_links`

`stripe_links`:

* `stripe_session_id` (PK)
* `account_id`
* `created_at_utc`

Failure to create `stripe_links` = contract violation.

---

## 3. Webhook Event Acceptance

Endpoint:

```
POST /v1/webhooks/stripe
```

Accepted Stripe event:

* `checkout.session.completed`

Ignored events (must NOT fail webhook):

* `payment_intent.*`
* `charge.*`
* `price.*`
* `product.*`

Ignored events must return HTTP 200.

---

## 4. provider_events (Dedup Scope)

On **every** webhook call:

* The raw Stripe event MUST be inserted into `provider_events`

`provider_events` dedup rule:

* UNIQUE(`provider`, `provider_event_id`)

**CRITICAL RULE (P0):**

> Deduplication of `provider_events` MUST NOT block credit logic.

Allowed behavior:

* If `provider_event_id` already exists → continue processing
* provider_events is **audit log**, not a processing gate

Forbidden behavior:

* Returning early because provider_event already exists
* Using provider_events as idempotency for credit

---

## 5. Account Resolution Contract

For `checkout.session.completed`:

Resolution order:

1. `metadata.account_id` (preferred)
2. `stripe_links.account_id` via `session.id`

If account_id cannot be resolved:

* Webhook returns HTTP 500
* provider_event is still recorded
* Credit MUST NOT be attempted

This is a hard failure.

---

## 6. Price → Units Mapping

* `metadata.price_id` MUST be present
* If missing → treat as contract violation

Mapping source:

* Environment variables
* `PRICE_UNITS_MAPPING_v1.md`

Example:

```
STRIPE_PRICE_ID_EUR_50=price_1Sm6YZ1a011Sg5et7jxHXA8e
```

Failure cases:

* price_id missing
* price_id not mapped

Result:

* HTTP 500
* provider_event recorded
* credit skipped

---

## 7. Credit (TopUp) Contract

Credit is triggered **only** by:

* `checkout.session.completed`
* `payment_status == paid`

Domain object:

```python
TopUp(
  account_id,
  tx_hash,
  amount_usdt,
  credited_units,
  ts
)
```

Rules:

* `tx_hash = "stripe:<provider_event_id>"`
* `ts` MUST be UTC

---

## 8. Credit Idempotency (P0)

**The ONLY idempotency boundary for credit:**

```
topups.tx_hash
```

Rules:

* `storage.credit_topup()` MUST be idempotent by `tx_hash`
* Duplicate webhook retries MUST NOT double-credit
* Duplicate provider_event MUST NOT block first credit

Forbidden:

* provider_event-based idempotency
* silent skip of credit

---

## 9. Failure Semantics

If credit fails:

* Webhook returns HTTP 500
* provider_event remains recorded
* Stripe will retry

On retry:

* credit MUST be attempted again
* success depends only on `topups.tx_hash`

---

## 10. Observability (Required Logs)

On `checkout.session.completed`:

Log MUST include:

* provider_event_id
* decision (credited | credit_failed | unresolved_mapping | price_not_mapped)
* resolved_account_id
* price_id
* credited_units
* exception (if any)

Silent failure is forbidden.

---

## 11. Status

* Stripe TEST canonical contract
* Stripe LIVE is explicitly OUT OF SCOPE

Next step after closure:

* Freeze TEST
* Open new chat for LIVE migration
