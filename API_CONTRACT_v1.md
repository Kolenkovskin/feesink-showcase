**002 — 2026-01-05 19:08 (Europe/Tallinn) — Орион**

## API_CONTRACT_v1.md (полная замена)

```md
# FeeSink — API_CONTRACT v1 (canonical)
Version: v2026.01.05-01
TZ: Europe/Tallinn (timestamps in logs are UTC)

Этот документ — **канонический контракт API** для FeeSink.
Он описывает только то, что реально поддерживается кодом в текущем этапе
(**Stripe TEST single-scenario**).

---

## 0) Глобальные инварианты (P0)

1) **Один режим за раз**
- Сейчас допустим только: **STRIPE_TEST_ONLY**
- Любые инструкции ниже предполагают:
  - `sk_test_*`
  - test `price_id`
  - webhook secret из `stripe listen`

2) **Одна БД**
- Все API-запросы, webhook и probe-скрипты должны работать с **одним и тем же**
  `FEESINK_SQLITE_DB`.

3) **Idempotency**
- Provider-level dedup: `provider_events(provider_event_id)` UNIQUE
- Credit-level dedup: `topups(tx_hash)` UNIQUE  
  👉 dedup **не имеет права** блокировать попытку credit.

---

## 1) Аутентификация (DEV)

В режиме STRIPE_TEST_ONLY сервер при старте **выдаёт DEV-токен**.

### Где взять токен
При запуске `feesink/api/server.py` в stdout:

```

[DEV] Issued token for account_id=demo-user: <TOKEN>

```

Используется как Bearer Token для всех API-вызовов.

---

## 2) POST /v1/stripe/checkout_sessions

### Назначение
Создать Stripe Checkout Session и получить ссылку для оплаты.

### Заголовки
```

Authorization: Bearer <DEV_TOKEN>
Content-Type: application/json

````

### Body
```json
{
  "price_id": "price_XXXXXXXX"
}
````

* `price_id` **обязан** совпадать с `STRIPE_PRICE_ID_EUR_50`
* Сервер не принимает произвольные цены

### Успешный ответ (200)

```json
{
  "checkout_session": {
    "id": "cs_test_...",
    "url": "https://checkout.stripe.com/c/pay/..."
  }
}
```

---

## 3) Как получить ПОЛНУЮ ссылку оплаты (PowerShell)

```powershell
$resp = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8789/v1/stripe/checkout_sessions" `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body (@{ price_id = $env:STRIPE_PRICE_ID_EUR_50 } | ConvertTo-Json)

$resp.checkout_session | Select-Object id, url | Format-List
```

👉 **Используй поле `url` целиком** — это и есть ссылка для оплаты.

---

## 4) Stripe Checkout — тестовые реквизиты оплаты

Используются стандартные Stripe TEST данные:

* **Card number:** `4242 4242 4242 4242`
* **Expiry:** любая будущая дата (например `12/34`)
* **CVC:** любые 3 цифры (например `123`)
* **ZIP / Postal code:** любой (например `12345`)

Оплата считается успешной.

---

## 5) Webhook (Stripe → FeeSink)

### Endpoint

```
POST /v1/webhooks/stripe
```

### Поддерживаемый event (P0)

* `checkout.session.completed`
  👉 **единственный** event, который может приводить к credit.

Остальные (`payment_intent.*`, `charge.*`) **логируются и игнорируются**.

---

## 6) Канонический credit-flow (Stripe TEST)

После успешной оплаты **обязана** выполняться цепочка:

1. Записан `provider_events`:

   * `provider_event_id = evt_...`
   * `event_type = checkout.session.completed`

2. Найден `stripe_links` по `session.id`:

   * `stripe_session_id = cs_test_...`
   * `account_id` определён

3. Выполнен credit:

   * создан `topups` с:

     * `tx_hash = stripe:<event_id>`
     * `credited_units = <from price mapping>`
   * `accounts.balance_units` увеличен

Если любой шаг не выполнен — это **BUG**, а не “нормальное поведение”.

---

## 7) Модель TopUp (канон)

```python
TopUp(
    account_id: AccountId,
    tx_hash: TxHash,
    amount_usdt: Decimal,
    credited_units: int,
    ts: datetime
)
```

❌ Запрещены альтернативные поля:

* `topup_id`
* `created_at_utc`
* `setattr` после пустого конструктора

---

## 8) Проверка результата (ручная)

После оплаты **обязательно** выполнить:

```powershell
python scripts\db_probe_stripe_chain.py
```

Ожидаемо:

* `stripe_links found = True`
* `topup found = True`
* `balance_units > 0`

---

## 9) Типовые ошибки и их смысл

* **401 Invalid token**
  → используешь не DEV-токен из текущего запуска сервера

* **topup missing**
  → credit не был выполнен (ошибка в webhook / TopUp)

* **TypeError / AttributeError при credit**
  → несоответствие сигнатуры `TopUp` канону

---

## 10) Границы контракта

Этот документ **не описывает**:

* Stripe LIVE
* подписки
* возвраты
* multi-price
* UI beyond success page

Всё это — будущие версии контракта.

```


