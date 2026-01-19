# STRIPE_CONTRACT_v1 — FeeSink

Version: v2026.01.19-02  
Status: **LIVE / FROZEN**  
Scope: FeeSink — Stripe Checkout + Webhook (LIVE)

---

## ⚠️ LIVE CONTRACT — CHANGE CONTROL (P0)

Этот контракт **заморожен** после подтверждённого факта:
**STRIPE_LIVE_END2END_PASS (2026-01-19)**.

Любые изменения:
- допускаются **только** в новом файле `STRIPE_CONTRACT_v2.md`,
- требуют нового этапа проекта и отдельного milestone.

Изменение данного файла запрещено.

---

## 0. Purpose

Канонический LIVE-контракт Stripe для FeeSink.  
Фиксирует **единственно допустимую** логику приёма денег в production.

---

## 1. Stripe Mode Invariant (P0)

- `FEESINK_STRIPE_MODE=live`
- Используются **только** `sk_live_*`, `whsec_live_*`
- TEST/LIVE mixing запрещён

Нарушение = критическая ошибка биллинга.

---

## 2. Checkout Creation (LIVE)

Endpoint:

POST /v1/stripe/checkout_sessions

yaml
Copy code

Правила (P0):

- Цена выбирается **только** из ENV:
  - `STRIPE_PRICE_ID_EUR_50`
- Тело запроса не влияет на цену
- `price_id` **не принимается** от клиента

Side effects:

- Создаётся Stripe Checkout Session
- Записывается `stripe_links(stripe_session_id → account_id)`

---

## 3. Webhook Acceptance

Endpoint:

POST /v1/webhooks/stripe

yaml
Copy code

Единственный событие, которое может привести к credit:

- `checkout.session.completed`

Все прочие события:
- принимаются (HTTP 200)
- **никогда** не приводят к зачислению средств

---

## 4. provider_events — Audit Only (P0)

- Все события записываются в `provider_events`
- Dedup по `provider_event_id`
- **Dedup не является idempotency credit**

Запрещено:
- использовать provider_events как gate для credit

---

## 5. Account Resolution (LIVE)

Порядок:

1. `event.data.object.metadata.account_id`
2. `stripe_links.account_id` по `session.id`

Если `account_id` не разрешён:
- credit запрещён
- webhook → HTTP 500
- Stripe выполнит retry

---

## 6. Price → Units Mapping (P0)

- `metadata.price_id` обязателен
- Mapping только через `PRICE_UNITS_MAPPING_v1.md`
- Fallback или расчёты запрещены

---

## 7. Credit Contract (TopUp)

Credit возможен **только если**:

- событие = `checkout.session.completed`
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
Единственная граница идемпотентности credit:

Copy code
topups.tx_hash
Повторы webhook:

не приводят к двойному зачислению

не блокируют первый credit

9. Observability (Required)
Каждый LIVE credit логирует:

provider_event_id

session_id

account_id

price_id

credited_units

decision (credited | dedup | failed)

Silent failure запрещён.

10. Status
Stripe LIVE контракт зафиксирован и заморожен.
Любая правка → новый контракт и новый этап проекта.