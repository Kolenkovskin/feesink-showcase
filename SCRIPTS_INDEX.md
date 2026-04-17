# FeeSink — Scripts Index (canonical)
Version: v2026.01.05-01
TZ: Europe/Tallinn (timestamps in logs are UTC unless noted)

Этот файл — **каноническая карта scripts/**: что делает каждый скрипт, когда его запускать, и что считать успехом.

---

## P0 инварианты запуска

1) **Один сценарий = один режим**
- Сейчас работаем **STRIPE_TEST_ONLY** (sk_test + test price_id + whsec из stripe listen).

2) **Единая БД**
- Все проверки и webhook должны попадать в один и тот же `FEESINK_SQLITE_DB`.

3) **Server vs Probes**
- `db_probe_*.py` **не требует** запущенного `server.py`, *если* ты просто читаешь SQLite.
- Но если ты хочешь увидеть цепочку “checkout → webhook → topup → balance”, тогда:
  - `server.py` должен быть **запущен** (он принимает webhook и создаёт записи),
  - а `db_probe_stripe_chain.py` запускается **после оплаты/webhook**, чтобы проверить, что записалось в БД.

4) **SQLite locking**
- Если `server.py` активно пишет в SQLite, probe может словить “database is locked”.
- Поэтому: проба либо после “тишины”, либо делай паузу 1–2 сек, либо останавливай сервер на время чтения (если нужно).

5) **Логи скриптов (правило проекта)**
- Каждый скрипт при запуске должен дописывать свой вывод в `C:\Users\User\PycharmProjects\feesink\logs\<script_family>.txt`
- На сегодня это **реализовано минимум в v13** для `apply_patch_server_stripe_topup_signature_fix_*`.
- Остальные скрипты — кандидаты на выравнивание под правило (см. TODO ниже).

---

## Быстрый “канонический” порядок для Stripe TEST

1) Запусти сервер:
- `feesink\api\server.py`

2) Скопируй DEV token из startup-banner (строка вида `[DEV] Issued token ...`)

3) Создай checkout session через API:
- POST `/v1/stripe/checkout_sessions` с `price_id=$env:STRIPE_PRICE_ID_EUR_50`

4) Открой `checkout_session.url` в браузере и оплати тестовой картой Stripe.

5) Запусти `db_probe_stripe_chain.py` и проверь:
- `stripe_links` найден по `session.id`
- `provider_events` содержит `checkout.session.completed`
- `topups` содержит `tx_hash=stripe:<event_id>`
- `accounts.balance_units` увеличился

---

## Index: db_probe_* (диагностика БД)

### 1) db_probe_stripe_chain.py
**Назначение:** полная проверка цепочки Stripe: `provider_events → stripe_links → topups → account`.
- **Вход:** `FEESINK_SQLITE_DB`, `STRIPE_PRICE_ID_EUR_50`
- **Выход:** печатает FAIL/OK и причину (missing link / missing topup / mismatch price_id).
- **Когда запускать:** после попытки оплаты и прихода webhook.
- **Успех:** topup найден + balance_units увеличился.

### 2) db_probe_stripe_credit.py
**Назначение:** точечная проверка факта credit (есть ли topup и/или изменился баланс).
- **Когда:** после webhook.

### 3) db_probe_stripe_event_payload.py
**Назначение:** быстро посмотреть payload последнего provider_event (что реально лежит в raw_event_json).
- **Когда:** когда подозрение “metadata не дошла / price_id пустой / account_id не резолвится”.

---

## Index: apply_patch_* (патчи server.py)

> Важно: эти патч-скрипты — “операционная хирургия”. Мы их держим, чтобы быстро и воспроизводимо менять `server.py` без ручных ошибок.
> Долгосрочно мы сведём их в 1–2 канонических патча и один общий лог.

### CREDIT fixes
#### 1) apply_patch_server_stripe_credit_fix.py (v01)
**Назначение:** ранняя попытка стабилизировать credit path Stripe webhook (исторический).
**Статус:** deprecated (заменён последующими версиями).

#### 2) apply_patch_server_stripe_credit_fix_v02.py
**Назначение:** итерация credit path.
**Статус:** deprecated.

#### 3) apply_patch_server_stripe_credit_fix_v03.py
**Назначение:** итерация credit path.
**Статус:** deprecated.

#### 4) apply_patch_server_stripe_credit_fix_v04.py
**Назначение:** итерация credit path.
**Статус:** deprecated.

#### 5) apply_patch_server_stripe_credit_fix_v05.py
**Назначение:** итерация credit path.
**Статус:** deprecated.

### Dedup / provider_events / webhook plumbing
#### 6) apply_patch_server_stripe_dedup_continue_v06.py
**Назначение:** поменять поведение dedup так, чтобы dedup provider_event **не блокировал** credit-логику.
**Статус:** active (если текущий server.py реально содержит нужный блок).

#### 7) apply_patch_server_stripe_provider_event_call_fix_v07.py
**Назначение:** фикс вызова записи provider_event (контракт storage: NOT NULL поля / корректный insert).
**Статус:** active.

### TopUp model / signature fixes
#### 8) apply_patch_server_stripe_topup_frozen_fix_v08.py
**Назначение:** заменить создание TopUp через пустой конструктор + setattr на конструктор TopUp(...).
**Статус:** исторический; может не совпасть с текущим `server.py` (у тебя был FOUND_MATCHES=0 на v08, позже стало 1 на v09).

#### 9) apply_patch_server_stripe_topup_model_fix_v10.py
**Назначение:** попытка выровнять поля TopUp под ожидаемую модель.
**Статус:** deprecated (оказался “не туда”: AFTER совпал с BEFORE, реально ничего не менял).

#### 10) apply_patch_server_stripe_topup_model_fix_v11.py
**Назначение:** поменять TopUp на поля `topup_id/created_at_utc` (ошибочно для текущей модели).
**Статус:** deprecated / dangerous (ломает, потому что текущий `TopUp` ожидает `(account_id, tx_hash, amount_usdt, credited_units, ts)`).

#### 11) apply_patch_server_stripe_topup_signature_fix_v12.py
**Назначение:** автоматом исправить TopUp-сигнатуру, но отказался из-за FOUND_MATCHES=2.
**Статус:** failed (refused to patch).

#### 12) apply_patch_server_stripe_topup_signature_fix_v13.py
**Назначение:** **канонический откат** TopUp-создания в `server.py` к реальной сигнатуре модели:
`TopUp(account_id=..., tx_hash=..., amount_usdt=..., credited_units=..., ts=now)`
- **Дополнительно:** ведёт лог в
`C:\Users\User\PycharmProjects\feesink\logs\apply_patch_server_stripe_topup_signature_fix.txt`
- **Статус:** active (последний применённый рабочий фикс для TopUp-сигнатуры).

---

## Index: demo tick

### run_demo_tick.py
**Назначение:** прогон воркера/тика для prepaid/check charging в демо-режимах.
**Когда:** не для Stripe checkout; для воркерной части.
**Статус:** active.

### run_demo_tick_twice.py
**Назначение:** проверить идемпотентность/дедуп на двух тиках подряд.
**Статус:** active.

---

## TODO (следующий этап скриптов)

1) Ввести `scripts/stripe_test_banner.py` (один файл):
- печатает CWD, FEESINK_SQLITE_DB, STRIPE_PRICE_ID_EUR_50 и “expected price_id”,
- печатает “SERVER_EXPECTED: running|stopped” как подсказку (ручной флаг),
- пишет лог в `logs/stripe_test_banner.txt`.

2) Нормализовать логирование для всех `apply_patch_*` и `db_probe_*`:
- каждый семейный скрипт дописывает в один `logs/<family>.txt`,
- в каждой записи: TS_UTC + VERSION + SHA1_BEFORE/AFTER (если патч) + exit code.


## Scripts Index

(see previous content above)

## Stripe TEST — Canonical Patch Set (PASS)

* **server.py**: `apply_patch_server_stripe_webhook_accountid_topup_fix_v18`

  * anchor-based patch (no regex guessing)
  * ensures account_id resolution + correct TopUp constructor
* **sqlite.py**: `apply_patch_sqlite_credit_topup_compat_v01`

  * storage compat fix for credit_topup (AttributeError resolved)

**Verification:** `scripts/db_probe_stripe_chain.py` → PASS

* event_id: `evt_1SmJaG1a011Sg5etroLY0d5P`
* idempotency: provider_event + tx_hash OK
