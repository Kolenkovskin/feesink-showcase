# FeeSink — Storage Contract (P0)

Version: v2026.01.05-01  
Timezone: **UTC everywhere** in DB fields (`*_utc`)  
Scope: Storage boundary (Domain ↔ Storage)  
This document is the **source of truth** for persistence semantics.

---

## 0) Principles (P0)

### 0.1 Storage is the source of truth for idempotency
- **Provider events**: idempotency by `provider_events.provider_event_id` UNIQUE
- **Topups**: idempotency by `topups.tx_hash` UNIQUE
- **Checks**: idempotency by `check_events.dedup_key` UNIQUE

### 0.2 No silent failures
Storage methods must never swallow exceptions.  
If persistence fails due to a contract violation, storage must raise a clear error  
(or return an explicit non-ok result where specified).

### 0.3 Time fields are UTC
- All timestamps are stored as UTC (`ISO-8601 +00:00` or UTC epoch)
- Storage implementation may vary, but **semantic meaning is UTC**

### 0.4 Boundary type invariant (P0)
`Account.status` is always returned from storage as **plain `str`**  
(e.g. `"active"`, `"paused"`, `"deleted"`).

---

## 1) Tables & columns (contract-level)

> Exact SQL definitions live in `schema.sql`.  
> This document defines **meaning and invariants**, not DDL.

---

### 1.1 `accounts`
Fields:
- `account_id` (PK, TEXT)
- `balance_units` (INTEGER, ≥ 0)
- `status` (TEXT) — **P0 boundary type = str**
- `created_at_utc` (TEXT, NOT NULL)
- `updated_at_utc` (TEXT, NOT NULL)

Invariants:
- `balance_units` is never negative
- `updated_at_utc` changes on any mutation

---

### 1.2 `tokens`
- `token` (PK, TEXT)
- `account_id` (FK → accounts.account_id)
- `created_at_utc` (NOT NULL)
- `revoked_at_utc` (nullable)

Invariant:
- Revoked tokens must be treated as unauthorized

---

### 1.3 `endpoints`
- `endpoint_id` (PK, TEXT)
- `account_id` (FK)
- `url` (TEXT)
- `is_paused` (INTEGER 0/1)
- `created_at_utc` (NOT NULL)
- `updated_at_utc` (NOT NULL)

---

### 1.4 `endpoint_leases`
- `endpoint_id` (PK/FK)
- `worker_id` (TEXT)
- `created_at_utc` (NOT NULL) **(P0: explicitly set by storage)**
- `expires_at_utc` (NOT NULL) **(P0: explicitly set by storage)**

Invariants:
- Lease is valid iff `now_utc < expires_at_utc`
- Only one active lease per endpoint

---

### 1.5 `check_events`
- `check_event_id` (PK, TEXT)
- `endpoint_id` (FK)
- `dedup_key` (UNIQUE, TEXT) — **P0 idempotency key**
- `scheduled_at_utc` (NOT NULL)
- `performed_at_utc` (NOT NULL)
- `http_status` (INTEGER, nullable)
- `ok` (INTEGER 0/1)
- `charged_units` (INTEGER, usually 1)
- `created_at_utc` (NOT NULL)

Invariants:
- **1 check = 1 unit** (MVP)
- Charging is **post-check**
- Idempotency strictly by `dedup_key`

---

### 1.6 `provider_events`
Audit-only table.

Required fields:
- `provider_event_id` (UNIQUE, TEXT)
- `provider` (TEXT) — `"stripe"`
- `event_type` (TEXT)
- `status` (TEXT)
- `received_at_utc` (NOT NULL)
- `processed_at_utc` (nullable)
- `raw_event_json` (TEXT, NOT NULL)

Optional parsed fields (recommended):
- `stripe_session_id`
- `account_id`
- `price_id`
- `payment_status`
- `credited_units`

Invariants:
- Insert is idempotent by `provider_event_id`
- Retried webhooks must not create duplicates

---

### 1.7 `stripe_links`
Maps Stripe Checkout Session → FeeSink account.

- `stripe_session_id` (PK, TEXT)
- `account_id` (TEXT, NOT NULL)
- `stripe_customer_id` (TEXT, nullable)
- `created_at_utc` (NOT NULL)

Invariant:
- Upsert by `stripe_session_id` is allowed

---

### 1.8 `topups`
Prepaid balance credits.

- `topup_id` (PK, TEXT) — recommended: equals `tx_hash`
- `account_id` (TEXT, NOT NULL)
- `tx_hash` (UNIQUE, TEXT)
- `amount_usdt` (TEXT / DECIMAL-as-string)
- `credited_units` (INTEGER)
- `created_at_utc` (NOT NULL)

Invariants:
- Idempotency **only** by `tx_hash`
- Same `tx_hash` must always credit the same units

---

## 2) Storage API (contract-level)

### 2.1 `ensure_schema()`
- Creates or validates tables and indexes
- Must be safe to call on every boot

---

### 2.2 Accounts & tokens

#### `ensure_account(account_id: str)`
Behavior:
- Create account if missing (`balance_units=0`, `status="active"`)
- Update `updated_at_utc` on every call

---

### 2.3 Endpoints & leasing

#### `acquire_endpoint_lease(...)`
Rules:
- If no lease or expired → acquire
- If active lease exists → reject
- Storage must set `created_at_utc` and `expires_at_utc`

---

## 3) Checks & charging (Phase 2 canon)

#### `record_check_and_charge(...)`
Behavior:
- Idempotent by `dedup_key`
- Dedup **before** balance check
- Never allow balance < 0

Return:
- `{inserted, charged, balance_after, reason}`

---

## 4) Stripe & topups (Phase 3 canon)

### Credit via `credit_topup(topup)`
- Idempotent strictly by `tx_hash`
- Balance increment is atomic with topup insert

---

## 5) Prohibited patterns (P0)

- ❌ Using `provider_events` as a credit gate  
- ❌ Silent exception swallowing  
- ❌ Naive (non-UTC) timestamps  

---

## Final statement

Storage is not an implementation detail.  
**Storage is part of the contract.**

If persistence semantics violate this document,  
the system is considered **broken**, even if APIs appear to work.
