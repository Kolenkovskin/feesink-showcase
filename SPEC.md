# FeeSink — SPEC (Unified)

Version: **v2026.01.05-STRIPE-TEST-PHASE3-01**
Status: **ACTIVE / Stripe TEST**
TZ: UTC

---

## 0. Purpose of this document

This SPEC is the **single source of truth** for runtime behavior of FeeSink.

Rules:

* If code behavior ≠ SPEC → **code is wrong**
* If documentation ≠ SPEC → **documentation must be updated**
* SPEC has priority over README, comments, and ad-hoc explanations

---

## 1. Global invariants (P0)

These invariants are **non-negotiable**.

### 1.1 Billing model

* **Prepaid only**
* No postpaid, no negative balance
* 1 check = 1 unit

### 1.2 Idempotency (P0)

| Operation             | Idempotency key                     |
| --------------------- | ----------------------------------- |
| Stripe provider event | `provider_events.provider_event_id` |
| Credit (topup)        | `topups.tx_hash`                    |

Rules:

* Provider event dedup **must not block credit**
* Credit idempotency is enforced **only** at `topups.tx_hash`

---

## 2. Stripe integration — scope

### 2.1 Stripe mode

Current mode:

* **STRIPE_TEST_ONLY**
* `sk_test_*` only
* `price_*` from TEST catalog only

Mixing test/live is **forbidden**.

---

## 3. Stripe payment flow (canonical)

### 3.1 Checkout creation

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

Rules:

* Requires valid Bearer token
* Resolves `account_id` from token
* Creates Stripe Checkout Session
* Writes mapping:

  * `stripe_links(stripe_session_id → account_id)`

---

### 3.2 Webhook processing — allowed events

Accepted Stripe events:

* `checkout.session.completed` **(ONLY ONE THAT CAN CREDIT)**

Ignored events (audit only):

* `payment_intent.*`
* `charge.*`

---

## 4. Stripe webhook — canonical behavior

### 4.1 Event ingestion

On **every** webhook:

1. Verify Stripe signature
2. Insert into `provider_events`
3. Deduplicate by `provider_event_id`

Dedup rules:

* Dedup **does not stop processing**
* Dedup only skips re-insertion

---

### 4.2 Credit path (P0)

Triggered **only if**:

* `event.type == checkout.session.completed`
* `payment_status == paid`

Resolution steps:

1. Resolve `session.id`
2. Resolve `account_id`:

   * `stripe_links.stripe_session_id`
3. Resolve `price_id`:

   * `event.metadata.price_id`
4. Resolve `credited_units`:

   * via `PRICE_UNITS_MAPPING`

If any step fails → **credit must not happen**

---

### 4.3 Domain TopUp creation (P0)

TopUp **must** be created via domain constructor:

```python
TopUp(
    account_id: AccountId,
    tx_hash: TxHash,
    amount_usdt: Decimal,
    credited_units: int,
    ts: datetime
)
```

Forbidden:

* setattr-based construction
* partial objects
* storage-side shape fixes

All validation happens **before** storage call.

---

### 4.4 Credit execution

Call:

```
storage.credit_topup(topup)
```

Rules:

* Storage enforces idempotency via `tx_hash`
* Duplicate tx_hash → no exception, no double credit
* Any exception = internal error

---

## 5. Observability & diagnostics

### 5.1 Mandatory logging fields (Stripe webhook)

Every `checkout.session.completed` handling **must log**:

* provider_event_id
* event_type
* session_id
* account_id
* price_id
* credited_units
* decision

Recommended extra field:

* `resolve_account_id_reason`

  * `stripe_links_missing`
  * `metadata_missing`
  * `session_id_missing`

---

## 6. Scripts & operations

Rules:

* Patch scripts are **operational tools**, not runtime code
* Every script version must:

  * Print version header
  * Print BEFORE / AFTER context
  * Write execution log into `/logs/*.txt`

Runtime **must never depend** on scripts.

---

## 7. Current phase status

Phase: **Stripe TEST — Phase 3**

State:

* Checkout creation: ✅ OK
* Webhook delivery: ✅ OK
* Event persistence: ✅ OK
* Credit path: **IN STABILIZATION**

ACTIVE_FOCUS:

> Final stabilization of Stripe TEST credit path

---

## 8. Exit criteria for Stripe TEST

Stripe TEST phase is considered **DONE** when:

* One checkout → one webhook → one topup
* `accounts.balance_units` increases correctly
* Retry webhook does not double credit
* No silent failures

Only after that:

* Transition to Stripe LIVE is allowed

---

**End of SPEC**
