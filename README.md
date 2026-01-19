# FeeSink

**Prepaid endpoint monitoring API. Pay once — spend per check.**

FeeSink — минималистичный API-сервис мониторинга HTTP-эндпоинтов с моделью **prepaid billing**:
- без подписок,
- без автосписаний,
- **1 check = 1 unit**.

Service: https://feesink.com

---

## Quick Start (3 steps)

### Step 1 — Generate a token (API key)

FeeSink использует **self-issued token**: вы создаёте токен сами.  
Токен **идентифицирует ваш аккаунт**. Сохраняйте его как пароль.

Рекомендуемо:
- password manager → generate random 32+ chars
- или любой длинный случайный ключ

**PowerShell генерация (рекомендуемо):**
```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$token = "t_" + [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
$token
Step 2 — Pay (€50 → 5000 units)
http
Copy code
POST https://feesink.com/v1/stripe/checkout_sessions
Authorization: Bearer <TOKEN>
Content-Type: application/json
Ответ:

json
Copy code
{
  "checkout_session": {
    "url": "https://checkout.stripe.com/..."
  }
}
Открой url и заверши оплату.

Step 3 — Check balance
PowerShell

powershell
Copy code
$token = "<TOKEN>"

Invoke-RestMethod `
  -Method Get `
  -Uri "https://feesink.com/v1/accounts/balance" `
  -Headers @{ Authorization = "Bearer $token" }
Ответ:

json
Copy code
{
  "account": {
    "account_id": "your-token-or-account",
    "balance_units": 5000,
    "status": "active",
    "units_per_check": 1
  }
}
Billing model (important)
Prepaid only

1 check = 1 unit

Списание строго после факта проверки

Нет подписок, нет автосписаний

Когда баланс = 0 → проверки не выполняются

API
Полный контракт см. в API_CONTRACT_v1.md