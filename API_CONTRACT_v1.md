# FeeSink — API CONTRACT v1 (canonical)

Version: v2026.01.19-03  
Status: LIVE / ACTIVE  
Timezone: Europe/Tallinn  
(All timestamps in logs are UTC)

This document defines the **canonical external API contract** of FeeSink (MVP).
Only behavior described here is considered supported.

---

## 0) Global invariants (P0)

These rules are absolute:

- **Prepaid only** (no subscriptions)
- **1 check = 1 unit**
- Charging happens **strictly after the check fact**
- Idempotency:
  - `provider_events.provider_event_id` — UNIQUE (audit only)
  - `topups.tx_hash` — UNIQUE (**the only idempotency boundary for crediting**)

---

## 1) Authentication (Bearer token)

All protected endpoints require:

Authorization: Bearer <TOKEN>


MVP note:
- In DEV, the token is issued / bound by the server
- External token issuance is a later phase

---

## 2) GET /v1/accounts/balance ✅ (P1)

### Purpose
Retrieve the current prepaid balance of the account.

### Headers
Authorization: Bearer <TOKEN>


### Response fields (canonical)

- `balance_units` — integer, current prepaid balance in units
- `units_per_check` — always `1` (explicit invariant)
- `status` — string, one of:
  - `"active"`
  - `"paused"`
  - `"inactive"`
  - `"unknown"` (if internal status is not recognized)

### Success response (200)

```json
{
  "account": {
    "account_id": "demo-user",
    "balance_units": 5000,
    "status": "active",
    "units_per_check": 1
  }
}
Errors
401 unauthorized — missing or invalid token

500 internal_error — storage or service failure

3) POST /v1/stripe/checkout_sessions ✅
Purpose
Create a Stripe Checkout Session and receive a payment URL.

Headers
Authorization: Bearer <TOKEN>
Content-Type: application/json
Pricing rule (P0)
The price is selected only from ENV STRIPE_PRICE_ID_EUR_50

The client cannot control pricing or unit amounts

Success response (200)
{
  "checkout_session": {
    "id": "cs_live_...",
    "url": "https://checkout.stripe.com/c/pay/..."
  }
}
4) POST /v1/webhooks/stripe ✅ (Stripe → FeeSink)
Supported event
checkout.session.completed → credit units

Other events
Always respond HTTP 200

Ignored or stored for audit only

5) PowerShell example: Get balance
$token = "<TOKEN>"

Invoke-RestMethod `
  -Method Get `
  -Uri "https://feesink.com/v1/accounts/balance" `
  -Headers @{ Authorization = "Bearer $token" }
6) PowerShell example: Create checkout session
$token = "<TOKEN>"

Invoke-RestMethod `
  -Method Post `
  -Uri "https://feesink.com/v1/stripe/checkout_sessions" `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body "{}"
Canonical note
If real API behavior contradicts this document,
this document must be updated first — otherwise the behavior is considered a bug.