# FeeSink — API_CONTRACT v1 (canonical)
Version: v2026.01.19-02
TZ: Europe/Tallinn (timestamps in logs are UTC)
Status: LIVE / ACTIVE

Этот документ — **канонический контракт API** FeeSink для внешнего пользователя (MVP).
Он описывает только реально поддерживаемое.

---

## 0) Глобальные инварианты (P0)

- Prepaid only (нет подписок)
- 1 check = 1 unit
- Списание только после факта проверки
- Idempotency:
  - provider_events: provider_event_id UNIQUE (audit only)
  - credit: topups.tx_hash UNIQUE (единственная граница идемпотентности credit)

---

## 1) Аутентификация (Bearer token)

Все защищённые эндпоинты требуют:

Authorization: Bearer <TOKEN>

yaml
Copy code

В MVP токен выдаётся сервером при старте (DEV) / либо будет выдаваться в онбординге (следующий этап).

---

## 2) GET /v1/accounts/balance  ✅ (P1)

### Назначение
Получить текущий prepaid баланс аккаунта.

### Заголовки
Authorization: Bearer <TOKEN>

bash
Copy code

### Успешный ответ (200)
```json
{
  "account": {
    "account_id": "demo-user",
    "balance_units": 5000,
    "status": "active"
  }
}
Ошибки
401 unauthorized — токен отсутствует/невалидный

3) POST /v1/stripe/checkout_sessions ✅
Назначение
Создать Stripe Checkout Session и получить ссылку для оплаты.

Заголовки
pgsql
Copy code
Authorization: Bearer <TOKEN>
Content-Type: application/json
Правило цены (P0)
Цена выбирается только из ENV STRIPE_PRICE_ID_EUR_50.
Клиент не управляет ценой.

Успешный ответ (200)
json
Copy code
{
  "checkout_session": {
    "id": "cs_live_...",
    "url": "https://checkout.stripe.com/c/pay/..."
  }
}
4) POST /v1/webhooks/stripe ✅ (Stripe → FeeSink)
Поддерживаемый event:

checkout.session.completed (only credit)

Остальные события:

HTTP 200 (ignored / audit)

5) Пример PowerShell: Balance
powershell
Copy code
$resp = Invoke-RestMethod -Method Get `
  -Uri "https://feesink.com/v1/accounts/balance" `
  -Headers @{ Authorization = "Bearer $token" }

$resp.account | Format-List