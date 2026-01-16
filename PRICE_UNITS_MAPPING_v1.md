# PRICE_UNITS_MAPPING v1 — FeeSink

## Purpose

This document defines the **canonical mapping** between Stripe `price_id`
and credited units in FeeSink billing.

This mapping is **P0-critical**:
- it is used to compute `credited_units`,
- it must be synchronized with Stripe TEST/LIVE configuration,
- mismatch leads to paid events without credit.

---

## Global Billing Invariant (P0)

- **prepaid only**
- **1 unit = 1 check**
- credit is applied **only after confirmed payment**
- credit must be **idempotent by tx_hash**
- Stripe credit idempotency key:  
  `tx_hash = "stripe:<provider_event_id>"`

---

## Stripe TEST Mapping (ACTIVE)

### Environment
- Mode: **STRIPE_TEST_ONLY**
- Currency: **EUR**
- Billing model: one-time topup

### Canonical TEST price

| price_id                              | amount | credited_units |
|--------------------------------------|--------|----------------|
| price_1Sm6YZ1a011Sg5et7jxHXA8e        | 50 EUR | 5000 units     |

### Notes
- This `price_id` is confirmed via:
  - Stripe Dashboard (TEST)
  - Webhook payload (`metadata.price_id`)
  - ENV `STRIPE_PRICE_ID_EUR_50`
- Any other `price_id` in TEST is **unsupported** unless explicitly added here.

---

## Stripe LIVE Mapping (NOT ACTIVE)

⚠️ **LIVE mapping is intentionally NOT defined here yet.**

Rules:
- TEST and LIVE price_ids must never be mixed.
- LIVE mapping will be added only after:
  - Stripe TEST single-scenario is fully validated,
  - credit works end-to-end,
  - new Phase is opened explicitly.

---

## Webhook Source of Truth

For `checkout.session.completed` events:

- `price_id` is resolved from:
  1. `event.data.object.metadata.price_id` (primary, current setup)
  2. `event.data.object.line_items[].price.id` (future / optional)

If `price_id` cannot be resolved:
- credit MUST NOT be applied silently,
- provider_event must be marked as `unresolved_price_id`.

---

## Failure Modes (explicit)

The following are **invalid states** and must be logged:

- `price_id` not found in mapping
- `credited_units` computed as `None`
- paid Stripe event without matching mapping
- credit skipped without recorded reason

Silent failure is forbidden.

---

## Change Rules

- Any change to Stripe price configuration:
  - REQUIRES updating this document
  - REQUIRES restarting server in correct mode
- Mapping changes are **versioned by commit**, not by memory.

---

## Stripe LIVE Mapping (ACTIVE)

⚠️ **This section is the ONLY source of truth for Stripe LIVE price → units mapping.**

Any mismatch between:
- Stripe LIVE Dashboard prices
- ENV configuration
- this mapping

will result in **paid events without credit**.

---

### Environment
- Mode: **STRIPE_LIVE**
- Currency: **EUR**
- Billing model: **one-time topup**
- TEST and LIVE price_ids **must never be mixed**

---

### Canonical LIVE prices

| price_id            | amount | credited_units |
|---------------------|--------|----------------|
| <PRICE_ID_EUR_50>   | 50 EUR | 5000 units     |

> Replace `<PRICE_ID_EUR_50>` with the actual **LIVE** Stripe price_id  
> (must start with `price_` and belong to LIVE Dashboard).

---

### Credit Rules (P0)

- `credited_units` is resolved **only** via this table
- credit is applied **only after** `checkout.session.completed`
- idempotency key:
```

tx_hash = "stripe:<provider_event_id>"

```
- repeated webhooks MUST NOT increase balance

---

### Webhook Source of Truth

For `checkout.session.completed` events:

1. `event.data.object.metadata.price_id` (primary)
2. `event.data.object.line_items[].price.id` (fallback)

If `price_id`:
- is missing
- or not present in this table

➡️ credit **MUST NOT** be applied  
➡️ provider_event **MUST** be recorded with explicit failure reason

Silent failure is **forbidden**.

---

### Change Policy

- Any change in Stripe LIVE prices:
- REQUIRES updating this section
- REQUIRES restart of API server
- Changes are tracked via **git commit**, not memory
- TEST mapping above remains immutable

---

## End of Stripe LIVE Mapping
```

---

