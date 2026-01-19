# STRIPE_WEBHOOK_CONTRACT v1 — FeeSink

Version: v2026.01.19-02  
Status: **LIVE / FROZEN**  
Scope: Stripe Webhooks (LIVE)

---

## ⚠️ LIVE WEBHOOK CONTRACT — FROZEN

Контракт заморожен после подтверждённого:
**STRIPE_LIVE_END2END_PASS (2026-01-19)**.

Изменения запрещены.  
Расширения — только через `STRIPE_WEBHOOK_CONTRACT_v2.md`.

---

## Supported Event (LIVE)

Единственный event, допускающий credit:

- `checkout.session.completed`

---

## Canonical LIVE Payload Assumptions

- `event.type = checkout.session.completed`
- `payment_status = paid`
- `event.data.object.id = session.id`
- `metadata.account_id` — обязателен
- `metadata.price_id` — обязателен

---

## Resolution Rules

### account_id

1. `metadata.account_id`
2. `stripe_links.account_id`

Отсутствие → HTTP 500, credit запрещён.

---

### price_id

- Берётся **только** из `metadata.price_id`
- Mapping через `PRICE_UNITS_MAPPING_v1.md`

---

## Credit Rules (P0)

- Credit → только через `TopUp`
- Idempotency → только `topups.tx_hash`
- provider_events — audit only

---

## Failure Semantics

- Любая ошибка credit → HTTP 500
- provider_event сохраняется
- Stripe retry допустим и ожидаем

---

## Status

LIVE webhook контракт заморожен.  
Используется как эталон production-поведения.