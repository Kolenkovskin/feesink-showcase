# PROJECT_STATE — FeeSink

## Meta

* Project: FeeSink
* Date: 2026-01-05
* TZ: Europe/Tallinn
* Mode: STRIPE_TEST_ONLY

---

## Current Phase

**Phase 3 — Stripe TEST (single-scenario)**

Цель этапа: добиться полностью воспроизводимого сценария

```
POST /v1/stripe/checkout_sessions
→ Stripe Checkout
→ webhook checkout.session.completed
→ provider_events recorded
→ TopUp credited
→ accounts.balance_units updated
```

---

## What Works (Confirmed)

1. **Stripe Checkout Session**

   * Endpoint `/v1/stripe/checkout_sessions` стабильно создаёт session.
   * `stripe_links` корректно пишет `session_id → account_id`.

2. **Webhook Delivery**

   * Stripe webhook доставляется.
   * Signature validation OK.
   * Событие `checkout.session.completed` получено.

3. **provider_events**

   * События Stripe записываются.
   * Dedup по `provider_event_id` работает.
   * Повторные webhook не дублируют события.

4. **Price Mapping**

   * `metadata.price_id` совпадает с `STRIPE_PRICE_ID_EUR_50`.
   * `credited_units = 5000` вычисляется корректно.

---

## What Fails (Blocking)

### ❌ TopUp Credit Path

* `TopUp` **не всегда создаётся корректно**.
* Фиксированы ошибки:

  * `TypeError`
  * `AttributeError`

Причина:

* Рассинхрон между:

  * сигнатурой `TopUp` в `feesink.domain.models`
  * фактическим созданием `TopUp` в `server.py`

**Каноническая сигнатура (факт):**

```python
TopUp(account_id, tx_hash, amount_usdt, credited_units, ts)
```

Любые дополнительные поля (`topup_id`, `created_at_utc`) **недопустимы**.

---

## Database State

* `provider_events`: записи есть
* `stripe_links`: записи есть
* `topups`: **пусто**
* `accounts.balance_units`: не увеличивается

Вывод: credit не проходит до storage.

---

## Scripts Situation

Каталог `scripts/` содержит серию patch-скриптов (v01–v13),
созданных итеративно для локализации ошибки.

Проблемы:

* отсутствовала единая точка канона
* версии не были связаны с документацией

Решение:

* ввести `SCRIPTS_INDEX.md`
* закрепить правило логирования запусков в `logs/*.txt`

---

## ACTIVE_FOCUS (next chat)

**ACTIVE_FOCUS (from 2026-01-06, Europe/Tallinn)**

Задача:

* Привести `server.py` в строгое соответствие сигнатуре `TopUp`
* Убедиться, что `credit_topup()` вызывается и успешно пишет `topups`

Ограничения:

* Stripe TEST ONLY
* Один сценарий
* Без рефакторинга вне credit path

---

## Exit Criteria for Phase 3 TEST

Этап считается завершённым, когда:

1. Один checkout → один webhook → один topup
2. `accounts.balance_units` увеличен
3. Повтор webhook не создаёт дубликат

---

## Project State

(see previous content above)

## Stripe TEST — Credit Status

* **PASS** (2026-01-05, Europe/Tallinn): `checkout.session.completed` → credit executed.

  * event_id: `evt_1SmJaG1a011Sg5etroLY0d5P`
  * tx_hash: `stripe:evt_1SmJaG1a011Sg5etroLY0d5P`
  * credited_units: `5000`
  * idempotency: provider_event dedup + tx_hash dedup **OK**
  * verification: `scripts/db_probe_stripe_chain.py` shows `topups.found=True`, `accounts.balance_units>0`

## Stripe LIVE — Milestone

Status: CONFIGURED (PASS)

Scope:
- Stripe LIVE one-time topup
- Webhook-driven credit (checkout.session.completed)
- Idempotent processing (tx_hash, provider_event_id)

Evidence:
- LIVE event processed: evt_1SmjUm1a011Sg5etKqSCjSzK
- LIVE session: cs_live_a1xC1f0caukTSofmJjTQjqjrcO8rLc6KVv6C0nEdNcz8YUPkuGZQlrekLK
- Price: price_1SmiXl1a011Sg5etLdMAEPFr (50 EUR → 5000 units)
- Credit applied, balance_units > 0

Operational notes:
- FEESINK_STRIPE_MODE=live
- Production requires env price_id = 50 EUR
- Test prices must not be mapped in LIVE

Conclusion:
Stripe billing pipeline is fully configured and validated end-to-end in LIVE.
