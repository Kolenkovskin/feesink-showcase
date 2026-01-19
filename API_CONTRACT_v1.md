---

### Файл: `docs/API_CONTRACT_v1.md` (или текущий путь в проекте) — полная замена

```markdown
# FeeSink — API_CONTRACT v1 (canonical)

Version: v2026.01.19-03  
Status: LIVE / ACTIVE  
TZ: Europe/Tallinn (timestamps in logs are UTC)

Этот документ — канонический контракт API FeeSink для внешнего пользователя (MVP).
Описывает только реально поддерживаемое.

---

## 0) Глобальные инварианты (P0)

- Prepaid only (нет подписок)
- 1 check = 1 unit
- Списание строго после факта проверки
- Идемпотентность:
  - provider_events: `provider_event_id` UNIQUE (audit only)
  - credit: `topups.tx_hash` UNIQUE (единственная граница идемпотентности credit)

---

## 1) Аутентификация (Bearer token)

Все защищённые эндпоинты требуют:

```

Authorization: Bearer <TOKEN>

```

MVP: токен выдаётся/привязывается сервером в DEV, внешний issuance — следующий этап.

---

## 2) GET /v1/accounts/balance ✅ (P1)

### Назначение
Получить текущий prepaid баланс аккаунта.

### Заголовки
```

Authorization: Bearer <TOKEN>

````

### Поля ответа (канон)
- `balance_units` — целое число, текущий prepaid баланс в units
- `units_per_check` — всегда `1` (явный инвариант)
- `status` — строка, одно из:
  - `"active"`
  - `"paused"`
  - `"inactive"`
  - `"unknown"` (если внутренний статус не распознан)

### Успешный ответ (200)
```json
{
  "account": {
    "account_id": "demo-user",
    "balance_units": 5000,
    "status": "active",
    "units_per_check": 1
  }
}
````

### Ошибки

* 401 `unauthorized` — токен отсутствует/невалидный
* 500 `internal_error` — внутренняя ошибка storage/сервиса

---

## 3) POST /v1/stripe/checkout_sessions ✅

### Назначение

Создать Stripe Checkout Session и получить ссылку для оплаты.

### Заголовки

```
Authorization: Bearer <TOKEN>
Content-Type: application/json
```

### Правило цены (P0)

Цена выбирается **только** из ENV `STRIPE_PRICE_ID_EUR_50`.
Клиент не управляет ценой.

### Успешный ответ (200)

```json
{
  "checkout_session": {
    "id": "cs_live_...",
    "url": "https://checkout.stripe.com/c/pay/..."
  }
}
```

---

## 4) POST /v1/webhooks/stripe ✅ (Stripe → FeeSink)

Поддерживаемый event:

* `checkout.session.completed` (only credit)

Остальные события:

* HTTP 200 (ignored / audit)

---

## 5) Пример PowerShell: Balance

```powershell
$token = "<TOKEN>"

Invoke-RestMethod `
  -Method Get `
  -Uri "https://feesink.com/v1/accounts/balance" `
  -Headers @{ Authorization = "Bearer $token" }
```

---

## 6) Пример PowerShell: Create Checkout

```powershell
$token = "<TOKEN>"

Invoke-RestMethod `
  -Method Post `
  -Uri "https://feesink.com/v1/stripe/checkout_sessions" `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body "{}"
```

