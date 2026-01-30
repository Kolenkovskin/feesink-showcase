# STRIPE_WEBHOOK_CONTRACT v1 — FeeSink

Version: v2026.01.24-01  
Status: **LIVE / FROZEN**  
Scope: Stripe Webhooks (LIVE)

---

## ⚠️ LIVE WEBHOOK CONTRACT — FROZEN

This contract was frozen after confirmed milestones:

- **STRIPE_LIVE_END2END_PASS** (2026-01-19)
- **PROVIDER_EVENTS_AUDIT_PASS** (2026-01-24)  
  Audit fields in `provider_events` are proven to be written and readable  
  (SQLite probe PASS).

Changes are forbidden.  
Any extension is allowed **only** via `STRIPE_WEBHOOK_CONTRACT_v2.md`.

---

## Supported Event (LIVE)

The **only** event allowed to trigger a credit:

- `checkout.session.completed`

---

## Canonical LIVE Payload Assumptions

- `event.type = checkout.session.completed`
- `payment_status = paid`
- `event.data.object.id = session.id`
- `metadata.account_id` — REQUIRED
- `metadata.price_id` — REQUIRED

---

## Resolution Rules

### account_id resolution

Resolution order:

1. `metadata.account_id`
2. `stripe_links.account_id`

If resolution fails:
- HTTP 500 is returned
- credit is forbidden
- Stripe retry is expected

---

### price_id resolution

- Taken **only** from `metadata.price_id`
- Mapped strictly via `PRICE_UNITS_MAPPING_v1.md`
- Fallbacks or calculations are forbidden

---

## Provider Events (Audit Only) — REQUIRED (P1)

`provider_events` is an **audit log**, not a business decision source.

For **every webhook delivery** (including retries), the following MUST be stored:

- `provider` = `"stripe"`
- `provider_event_id` = `event.id`
- `event_type` = `event.type`
- `status` = `"received"` on intake
- `received_at_utc` = intake timestamp (UTC)
- `processed_at_utc` = processing completion timestamp (UTC), if processed
- `account_id` = resolved account_id (if known)
- `credited_units` = NULL until credit, or value if determined/applied
- `raw_event_json` = raw JSON payload (for diagnostics)

---

## Audit Fields — REQUIRED (Signature Proof)

Mandatory audit fields for signature verifiability:

- `raw_body_sha256` (TEXT, hex)
- `signature_verified_at_utc` (TEXT, UTC ISO8601)

### Audit field rules

1. `raw_body_sha256` MUST be calculated from **raw HTTP body bytes**, before JSON parsing.  
2. `signature_verified_at_utc` MUST be set **at the moment of successful Stripe-Signature verification** (UTC).  
3. If the signature is invalid — `signature_verified_at_utc` MUST NOT be set.

---

## Credit Rules (P0)

- Credit is performed **only** via `TopUp`
- Credit idempotency boundary: **`topups.tx_hash`**
- Re-delivered webhooks must never cause double credit:
  - duplicates → decision `duplicate_tx_hash` (or equivalent), HTTP 200

---

## Failure Semantics

### Signature failure

- Invalid signature → HTTP 400
- Credit is forbidden
- `provider_event` MAY be stored as `"received"`, but audit timestamp is not set

### Credit failure

- Any credit error → HTTP 500
- `provider_event` MUST be stored  
  (audit fields set if signature was verified)

### Retry policy

- Stripe retries are expected on HTTP 5xx
- Exactly-once credit is guaranteed by `tx_hash` idempotency

---

## Status

The LIVE webhook contract is frozen.  
It defines the reference production behavior.
