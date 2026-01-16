## README.md — каноническая версия (Stripe TEST)

````md
# FeeSink

FeeSink — сервис prepaid-биллинга для HTTP-проверок (endpoint watchdog).
Модель: **prepaid only**, 1 check = 1 unit, строгая идемпотентность.

Проект сейчас находится в режиме **Stripe TEST (single-scenario)**.

---

## Текущий статус проекта

**Этап:** Phase 3 — Stripe TEST  
**Режим:** один сценарий, одна БД, один price_id

### Что работает
- HTTP API v1
- Prepaid-баланс (units)
- Stripe Checkout (TEST)
- Webhook `checkout.session.completed`
- Запись `provider_events`
- Mapping `stripe_session_id → account_id`
- Идемпотентность по `topups.tx_hash`

### Что проверяется / стабилизируется
- Credit topup (увеличение `accounts.balance_units`)
- Поведение при retry webhook
- Логирование причин `credit_failed`

---

## Stripe TEST — single-scenario (канон)

### Инварианты
- Используется **только** `sk_test_*`
- Используется **один** TEST `price_id`
- Используется **одна** SQLite БД
- Dedup `provider_events` **не блокирует credit**
- Идемпотентность credit — **только** по `topups.tx_hash`

---

## Переменные окружения (обязательно)

```text
FEESINK_SQLITE_DB=C:\Users\User\PycharmProjects\feesink\feesink.db
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID_EUR_50=price_...
````

> Все переменные задаются **через Run / Debug Configurations**
> `.env` не используется для секретов.

---

## Локальный запуск API

```powershell
(.venv) PS> python feesink/api/server.py
```

Ожидаемый стартовый баннер:

* MODE: STRIPE_TEST_ONLY
* SQLITE_DB: путь к feesink.db
* STRIPE_SECRET_KEY prefix: sk_test
* Issued token for account_id=demo-user

---

## Stripe Checkout — пошагово

### 1. Получить token

Берётся из стартового баннера:

```
[DEV] Issued token for account_id=demo-user: <TOKEN>
```

### 2. Создать checkout session

```powershell
$token="<TOKEN>"

$resp = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8789/v1/stripe/checkout_sessions" `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body (@{ price_id = $env:STRIPE_PRICE_ID_EUR_50 } | ConvertTo-Json)

$resp.checkout_session | Select id, url
```

### 3. Перейти по `url` и оплатить

Использовать тестовую карту Stripe:

```
4242 4242 4242 4242
MM/YY — любые
CVC — любые
```

### 4. Проверить результат

```powershell
python scripts/db_probe_stripe_chain.py
```

Ожидаемо:

* provider_event: `checkout.session.completed`
* stripe_links: FOUND
* price mapping: match = True
* topup: FOUND
* account.balance_units > 0

---

## Скрипты (важно)

* `scripts/db_probe_*.py` — **диагностика**, сервер не требуется
* `scripts/apply_patch_*.py` — **одноразовые патчи**

  * каждый патч:

    * делает `.bak`
    * печатает BEFORE / AFTER
    * пишет лог в `logs/*.txt`

Назначение каждого скрипта описано в **SCRIPTS_INDEX.md**.

---

## Документация — источник истины

* `PROJECT_STATE.md` — текущее состояние проекта
* `SPEC.md` — канон поведения
* `storage_contract.md` — контракт storage ↔ domain
* `STRIPE_CONTRACT_v1.md` — Stripe API (Checkout)
* `STRIPE_WEBHOOK_CONTRACT_v1.md` — webhook канон

Если факт в коде ≠ документации — **ошибка**.

---

## Правило чатов

* Один чат = один этап
* Перед новым чатом:

  * обновить документацию
  * зафиксировать PROJECT_STATE
  * определить ACTIVE_FOCUS

Без этого продолжение запрещено.

```

---

