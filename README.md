# FeeSink

**Prepaid endpoint checks. No subscriptions.**

FeeSink is a minimal HTTP endpoint checking API with a strict prepaid billing model.
You buy units once and spend **exactly 1 unit per performed check**.

No subscriptions. No hidden costs. No negative balance.

---

## Canonical rules (P0)

These rules are absolute:

* **Prepaid only** — checks are performed only if you have units
* **1 check = 1 unit** — always
* **Charging happens after the check**
* **Idempotent billing** — retries never double-charge
* **Zero balance is a valid state**, not an error

If any rule above is violated, it is a billing defect.

---

## How FeeSink works (conceptually)

**Token → Pay → Balance → Check**

1. You generate a token (this is your account ID)
2. You pay once via Stripe (€50 → 5000 units)
3. Units are credited to that token
4. Each performed check consumes 1 unit

When the balance reaches 0, checks stop.
Nothing is charged beyond prepaid units.

---

## 1. Token (account)

FeeSink has **no registration and no users**.

Your **token *is* your account**.

* You generate it yourself (any long random string)
* FeeSink does not store emails or personal data
* Anyone with the token can spend its units

⚠️ **Important**

* Losing the token means permanent loss of access
* Funds tied to a lost token cannot be recovered

---

## 2. Payment (Stripe LIVE)

FeeSink supports **one prepaid product only**:

* **€50 → 5000 units**

Rules:

* Payments are one-time (no subscriptions)
* Price and units are fixed on the server
* Repeated Stripe webhooks never credit twice

Payment is initiated via:

* the main website: [https://feesink.com](https://feesink.com)
* or the API endpoint `POST /v1/stripe/checkout_sessions`

---

## 3. Check your balance

```
GET https://feesink.com/v1/accounts/balance
Authorization: Bearer <TOKEN>
```

Example response:

```json
{
  "account": {
    "account_id": "your-token",
    "balance_units": 5000,
    "status": "active",
    "units_per_check": 1
  }
}
```

Notes:

* `balance_units` is never negative
* `status` reflects internal account state

---

## 4. Add an endpoint to check

```
POST https://feesink.com/v1/endpoints
Authorization: Bearer <TOKEN>
Content-Type: application/json

{
  "url": "https://example.com/healthz",
  "interval_seconds": 300,
  "enabled": true
}
```

Rules:

* Only HTTP GET checks are performed
* Each performed check costs **1 unit**
* Charging happens **after** the check
* Retries are idempotent

FeeSink does **not** provide alerts, dashboards or SLA guarantees.

---

## When checks stop

Checks stop when:

* balance reaches 0
* endpoint is disabled or removed

This is expected behavior and **not an error**.

To continue, top up again using the same token.

---

## What FeeSink is (and is not)

FeeSink **is**:

* a prepaid endpoint checking API
* deterministic and audit-friendly
* safe against double charging

FeeSink **is not**:

* a monitoring dashboard
* an alerting system
* an uptime/SLA service
* a subscription product

---

## Canonical references

* Product definition: `docs/PRODUCT_CANON.md`
* Sale readiness: `docs/EXIT_CRITERIA.md`
* API contract: `API_CONTRACT_v1.md`

If something is not described there, it is not part of the product.

---

**FeeSink principle**

> *Monitoring stops when money stops — never later.*


This repository contains a production-grade billing core and a public showcase branch.

Stripe TEST identifiers are shown for demonstration; LIVE identifiers are not public.