# FeeSink — Storage Contract (P0)
Version: v2026.01.05-01
TZ: UTC everywhere in DB fields (*_utc)
Scope: Storage boundary (Domain ↔ Storage). This is the **source of truth** for persistence semantics.

---

## 0) Principles (P0)

### 0.1 Storage is the source of truth for idempotency
- **Provider events**: idempotency is enforced by `provider_events.provider_event_id` UNIQUE.
- **Topups**: idempotency is enforced by `topups.tx_hash` UNIQUE.
- **Checks**: idempotency is enforced by `check_events.dedup_key` UNIQUE.

### 0.2 No silent failures
Storage methods must not swallow exceptions. If storage can’t persist due to contract violations, it must raise a clear exception (or return a structured non-ok result where specified).

### 0.3 Time fields are timezone-aware UTC
- All timestamps stored as ISO-8601 with UTC offset (`...+00:00`) or as UTC epoch; implementation choice is storage-specific, but **meaning is UTC**.

### 0.4 Boundary type invariant (P0): Account.status is `str`
- **Storage returns** `Account.status` as plain `str` (e.g. `"active"`, `"paused"`, `"deleted"`).
- Domain may cast to Enum internally, but storage boundary is `str`.

---

## 1) Tables & columns (contract-level)

> Exact SQL lives in `schema.sql`. This file defines the **meaning** and **invariants**.

### 1.1 `accounts`
- `account_id` (PK, TEXT)
- `balance_units` (INTEGER, >= 0)
- `status` (TEXT) — **P0 boundary type = str**
- `created_at_utc` (TEXT/ISO, NOT NULL)
- `updated_at_utc` (TEXT/ISO, NOT NULL)

Invariants:
- `balance_units` never negative.
- `updated_at_utc` changes on any account mutation.

### 1.2 `tokens`
- `token` (PK, TEXT)
- `account_id` (FK → accounts.account_id)
- `created_at_utc` (NOT NULL)
- `revoked_at_utc` (nullable)

Invariants:
- Token auth checks must treat revoked tokens as unauthorized.

### 1.3 `endpoints`
- `endpoint_id` (PK, TEXT)
- `account_id` (FK)
- `url` (TEXT)
- `is_paused` (INTEGER 0/1)
- `created_at_utc` (NOT NULL)
- `updated_at_utc` (NOT NULL)

### 1.4 `endpoint_leases`
- `endpoint_id` (PK/FK)
- `worker_id` (TEXT)
- `created_at_utc` (NOT NULL)  **P0: must be explicitly set by storage**
- `expires_at_utc` (NOT NULL)  **P0: must be explicitly set by storage**

Invariants:
- A lease is valid iff `now_utc < expires_at_utc`.
- Only one active lease per endpoint.

### 1.5 `check_events`
- `check_event_id` (PK, TEXT)
- `endpoint_id` (FK)
- `dedup_key` (UNIQUE, TEXT) — P0 idempotency key
- `scheduled_at_utc` (NOT NULL)
- `performed_at_utc` (NOT NULL)
- `http_status` (INTEGER, nullable)
- `ok` (INTEGER 0/1)
- `charged_units` (INTEGER, typically 1)
- `created_at_utc` (NOT NULL)

Invariants:
- **1 check = 1 unit** in MVP unless policy says otherwise.
- Charging occurs **post-check** and must be idempotent by `dedup_key`.

### 1.6 `provider_events`
- `provider_event_id` (UNIQUE, TEXT) — e.g. `evt_...`
- `provider` (TEXT) — `"stripe"`
- `event_type` (TEXT) — e.g. `checkout.session.completed`
- `status` (TEXT, NOT NULL) — e.g. `"received"`, `"processed"`, `"ignored"`, `"failed"`
- `received_at_utc` (NOT NULL)
- `processed_at_utc` (nullable)
- `raw_event_json` (TEXT/JSON, NOT NULL) — full payload (or sanitized full payload)
- optional parsed fields (recommended for ops/debug):
  - `stripe_session_id` (TEXT, nullable)
  - `account_id` (TEXT, nullable)
  - `price_id` (TEXT, nullable)
  - `payment_status` (TEXT, nullable)
  - `credited_units` (INTEGER, nullable)

Invariants:
- Insert is idempotent by `provider_event_id` UNIQUE.
- Storage must support **retrying webhook** without duplicate rows.

### 1.7 `stripe_links`
Maps Stripe Checkout Session to FeeSink account.
- `stripe_session_id` (PK, TEXT) — `cs_test_...` / `cs_live_...`
- `account_id` (TEXT, NOT NULL)
- `stripe_customer_id` (TEXT, nullable)
- `created_at_utc` (NOT NULL)

Invariant:
- Upsert by `stripe_session_id` is allowed (same mapping).

### 1.8 `topups`
Balance credits (prepaid).
- `topup_id` (PK, TEXT) — recommended: same as `tx_hash`
- `account_id` (TEXT, NOT NULL)
- `tx_hash` (UNIQUE, TEXT) — e.g. `stripe:<provider_event_id>` or onchain hash
- `amount_usdt` (TEXT/DECIMAL as string, NOT NULL)
- `credited_units` (INTEGER, NOT NULL)
- `created_at_utc` (NOT NULL)

Invariants:
- Idempotency is **only** by `tx_hash` UNIQUE.
- `credited_units` must be consistent for the same `tx_hash` across retries.

---

## 2) Storage API (contract-level)

### 2.1 `ensure_schema() -> None`
- Creates/ensures tables + indexes exist.
- Must be safe to call on every boot.

### 2.2 Accounts / tokens

#### `ensure_account(account_id: str) -> AccountRow`
Behavior:
- If account doesn’t exist: create with `balance_units=0`, `status="active"`, set `created_at_utc=now`, `updated_at_utc=now`.
- If exists: update `updated_at_utc=now` and return row.

Return shape (dict-like):
- `{account_id, balance_units, status, created_at_utc?, updated_at_utc}` (created may be omitted if not needed elsewhere, but recommended to keep consistent)

#### `issue_token(account_id: str) -> str`
- Creates token row and returns token.

#### `get_account_by_token(token: str) -> Optional[AccountRow]`
- Returns account row if token exists and not revoked; else None.

---

## 3) Endpoints & leasing

#### `create_endpoint(account_id: str, url: str, ...) -> EndpointRow`
- Creates endpoint for account.

#### `patch_endpoint(endpoint_id: str, ...) -> EndpointRow`
- Updates endpoint fields; updates `updated_at_utc`.

#### `delete_endpoint(endpoint_id: str) -> None`
- Hard delete or soft delete is storage-specific; contract requires it becomes non-checkable.

#### `acquire_endpoint_lease(endpoint_id: str, worker_id: str, lease_seconds: int, now_utc: datetime) -> LeaseResult`
Result:
- `{acquired: bool, lease_expires_at_utc: str|datetime, reason: str}`
Rules:
- If no lease or expired: acquire and set **created_at_utc + expires_at_utc** (P0).
- If active lease held by other worker: return acquired=False.

---

## 4) Checks + charging (Phase 2 canon)

#### `record_check_and_charge(...) -> ChargeResult`
Inputs (minimum):
- `endpoint_id`
- `dedup_key` (UNIQUE)
- `scheduled_at_utc`
- `performed_at_utc`
- `ok`, `http_status`
- `charged_units` (usually 1)

Behavior:
- Idempotent by `dedup_key` UNIQUE:
  - If already exists: return `{inserted: False, charged: False, ...}`.
  - If inserted and balance sufficient: decrement balance and mark charged.
  - If inserted and balance depleted: do not decrement below 0; return charged=False.
Return:
- `{inserted: bool, charged: bool, balance_after: int, reason: str}`

---

## 5) Stripe (Phase 3 canon)

### 5.1 Provider event persistence

#### `insert_provider_event(event: ProviderEvent) -> InsertProviderEventResult`
Must persist:
- `provider_event_id`, `provider`, `event_type`, `status`, `received_at_utc`, `raw_event_json`
Also persist optional parsed fields if provided.

Idempotency:
- If `provider_event_id` already exists:
  - Return `{inserted: False, existing_status: "...", reason:"dedup"}` (no exception)

### 5.2 Stripe session → account mapping

#### `upsert_stripe_link(stripe_session_id: str, account_id: str, stripe_customer_id: Optional[str], created_at_utc: datetime) -> None`
- Upsert allowed.

#### `resolve_account_by_stripe_session(stripe_session_id: str) -> Optional[str]`
- Returns `account_id` from `stripe_links` or None.

### 5.3 Balance credit via topup

Domain model used by API webhook code (current canon):
`TopUp(account_id, tx_hash, amount_usdt, credited_units, ts)`  
*(ts is a UTC datetime; storage will map it to `topups.created_at_utc` and may also use `topup_id=tx_hash`.)*

#### `credit_topup(topup: TopUp) -> CreditTopupResult`
Behavior:
- Translate TopUp → `topups` insert:
  - `topup_id = topup.tx_hash`
  - `created_at_utc = topup.ts`
- Idempotency strictly by `topups.tx_hash` UNIQUE:
  - If tx_hash exists: return `{inserted: False, credited: False, reason:"dedup"}`
  - If inserted: increment `accounts.balance_units += topup.credited_units` and return `{inserted: True, credited: True, balance_after, reason:"credited"}`

Return:
- `{inserted: bool, credited: bool, balance_after: int, reason: str}`

---

## 6) Prohibited patterns (P0)

- ❌ Treating “provider_event already exists” as a reason to **skip** balance credit.
  - Provider event dedup ≠ topup dedup.  
  - Topup idempotency is **only** by `tx_hash`.

- ❌ Silent exception swallowing with “dedup assumed”.
  - If storage fails to insert due to NOT NULL/shape errors — that’s a contract violation; bubble up clearly.

- ❌ Naive datetimes without UTC meaning.

---

## 7) Minimal operational probes (recommended)

- Provider event latest:
  - latest `provider_events` by `received_at_utc`
- Stripe link lookup:
  - `stripe_links` by `stripe_session_id`
- Topup lookup:
  - `topups` by `tx_hash`
- Account balance:
  - `accounts.balance_units`

(Probe scripts live in `scripts/` and are indexed separately.)
