# DO NOT PATCH BY GUESSING REGEX; ALWAYS EXTRACT CONTEXT FIRST
# Path: feesink/api/server.py
r"""
FeeSink — API skeleton (Self-Service v1) + minimal HTML success page
API_CONTRACT: v2026.01.01-API-01 (docs/API_CONTRACT_v1.md)

This server provides:
- JSON API per v1 contract (Bearer token auth)
- Minimal HTML "success page" that shows token + links (token link UX)

Run (PowerShell, from repo root):
  .\.venv\Scripts\python.exe -m feesink.api.server

Env:
  FEESINK_API_HOST=127.0.0.1
  FEESINK_API_PORT=8789
  FEESINK_STORAGE=memory|sqlite   (default: memory)
  FEESINK_SQLITE_DB=feesink.db    (default: feesink.db in repo root)
  FEESINK_SCHEMA_SQL=schema.sql   (default: schema.sql in repo root)

Dev convenience:
  FEESINK_DEV_TOKEN=<token>       (optional; if set, links token->FEESINK_DEV_ACCOUNT)
  FEESINK_DEV_ACCOUNT=demo-user   (default: demo-user)

TopUp (dev-mode only, Phase "first sale"):
  FEESINK_TOPUP_MODE=dev|off      (default: dev)

Stripe Checkout Session (Phase 3 wiring):
  STRIPE_SECRET_KEY=<sk_...>              (required for /v1/stripe/checkout_sessions and webhook fallback fetch)
  STRIPE_PRICE_ID_EUR_50=<price_...>      (required; maps to credited_units=5000 in webhook)
  STRIPE_SUCCESS_URL=<https://...>        (required)
  STRIPE_CANCEL_URL=<https://...>         (required)
"""

from __future__ import annotations

import html
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple
from wsgiref.simple_server import make_server
from wsgiref.util import setup_testing_defaults

# ----------------------------
# Version banner (must print at startup)
# ----------------------------

API_VERSION = "FEESINK-API-SKELETON v2026.01.05-04-STRIPE-TEST-DEDUP-PROCESS-FIX-01"


def _safe_getattr(mod, name: str, default: str) -> str:
    try:
        return getattr(mod, name)
    except Exception:
        return default


def _sha256_hex_prefix(s: str, n: int = 8) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def _print_startup_banner() -> None:
    """
    P0: deterministic startup banner to prevent Stripe test/live mixing.

    Prints (safe):
      - MODE: STRIPE_TEST_ONLY
      - LISTEN: http://{host}:{port}
      - STORAGE kind + (if sqlite) absolute DB path + basename
      - STRIPE_SECRET_KEY prefix (first 7 chars)
      - STRIPE_WEBHOOK_SECRET hash-prefix (sha256, first 8)

    Kill-switch:
      If any STRIPE_* env is present (stripe configured intent),
      then STRIPE_SECRET_KEY must start with sk_test_ and STRIPE_WEBHOOK_SECRET must be set.
    """

    # Best-effort import of versions from other components (must not crash)
    worker_v = "unknown"
    sqlite_v = "unknown"
    try:
        from feesink.runtime import worker as worker_mod  # type: ignore

        worker_v = _safe_getattr(worker_mod, "FEESINK_WORKER_VERSION", "unknown")
    except Exception:
        pass
    try:
        from feesink.storage import sqlite as sqlite_mod  # type: ignore

        sqlite_v = _safe_getattr(sqlite_mod, "STORAGE_VERSION", "unknown")
    except Exception:
        pass

    # --- Resolve listen target (safe) ---
    host = (os.getenv("FEESINK_API_HOST") or "127.0.0.1").strip()
    port_raw = (os.getenv("FEESINK_API_PORT") or "8789").strip()
    try:
        port = int(port_raw)
    except Exception:
        print(f"FATAL: FEESINK_API_PORT must be int, got: {port_raw!r}")
        raise SystemExit(2)

    # --- Resolve storage + DB path (safe) ---
    storage_kind = (os.getenv("FEESINK_STORAGE") or "memory").strip().lower()
    db_abs_path: Optional[str] = None
    db_basename: Optional[str] = None
    if storage_kind == "sqlite":
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        db_rel = os.getenv("FEESINK_SQLITE_DB", "feesink.db")
        db_abs_path = os.path.join(repo_root, db_rel)
        db_basename = os.path.basename(db_abs_path)

    # --- Stripe intent detection (any STRIPE_* env implies intent) ---
    stripe_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    whsec = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    stripe_price = (os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip()
    stripe_success = (os.getenv("STRIPE_SUCCESS_URL") or "").strip()
    stripe_cancel = (os.getenv("STRIPE_CANCEL_URL") or "").strip()

    stripe_intent = any([stripe_key, whsec, stripe_price, stripe_success, stripe_cancel])

    # --- Stripe mode gate (P0): explicit test/live selection ---
    stripe_mode = (os.getenv('FEESINK_STRIPE_MODE') or 'test').strip().lower()
    if stripe_mode not in ('test', 'live'):
        print(f"FATAL: FEESINK_STRIPE_MODE must be 'test' or 'live' (got {stripe_mode!r})")
        raise SystemExit(2)


    print("=" * 80)
    print(f"MODE: STRIPE_{stripe_mode.upper()}")
    print(f"LISTEN: http://{host}:{port}")

    if storage_kind == "sqlite":
        # db_abs_path is not secret; it is crucial for avoiding “wrong DB”
        print(f"STORAGE: sqlite")
        print(f"SQLITE_DB: {db_abs_path} (basename={db_basename})")
    else:
        print(f"STORAGE: {storage_kind}")

    if stripe_intent:
        # Kill-switch conditions
        if not stripe_key:
            print("FATAL: STRIPE intent detected but STRIPE_SECRET_KEY is missing")
            raise SystemExit(2)
        expected_prefix = "sk_test_" if stripe_mode == "test" else "sk_live_"
        if not stripe_key.startswith(expected_prefix):
            print(
                f"FATAL: STRIPE_SECRET_KEY must start with {expected_prefix} "
                f"for FEESINK_STRIPE_MODE={stripe_mode!r} (got prefix={stripe_key[:7]!r})"
            )
            raise SystemExit(2)
        if not whsec:
            print("FATAL: STRIPE intent detected but STRIPE_WEBHOOK_SECRET is missing")
            raise SystemExit(2)

        print(f"STRIPE_SECRET_KEY prefix: {stripe_key[:7]}")
        print(f"STRIPE_WEBHOOK_SECRET hash-prefix: {_sha256_hex_prefix(whsec, 8)}")
    else:
        print("STRIPE: not configured (no STRIPE_* envs)")

    # Keep the original banner (versions) — do not change semantics
    print(API_VERSION)
    print(f"WORKER: {worker_v}")
    print(f"SQLITE:  {sqlite_v}")
    print("=" * 80)


UTC = timezone.utc


def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _json_response(status: int, payload: Dict[str, Any], headers: Optional[list] = None):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hdrs = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if headers:
        hdrs.extend(headers)
    return status, hdrs, body


def _html_response(status: int, html_text: str, headers: Optional[list] = None):
    body = html_text.encode("utf-8")
    hdrs = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if headers:
        hdrs.extend(headers)
    return status, hdrs, body


def _error(status: int, code: str, message: str, details: Optional[Dict[str, Any]] = None):
    return _json_response(
        status,
        {
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            }
        },
    )


def _read_json(environ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except Exception:
        length = 0
    if length <= 0:
        return None, "empty_body"
    raw = environ["wsgi.input"].read(length)
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, "invalid_json"
    if not isinstance(obj, dict):
        return None, "json_not_object"
    return obj, None


def _read_raw_body(environ) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except Exception:
        length = 0
    if length <= 0:
        return b""
    return environ["wsgi.input"].read(length)


def _stripe_parse_sig_header(sig_header: str) -> tuple[Optional[int], Optional[str]]:
    """
    Stripe-Signature header format:
      t=timestamp,v1=signature[,v0=...]
    We only need (t, v1).
    """
    if not sig_header:
        return None, None
    ts: Optional[int] = None
    v1: Optional[str] = None
    for part in sig_header.split(","):
        part = part.strip()
        if part.startswith("t="):
            try:
                ts = int(part[2:])
            except Exception:
                ts = None
        elif part.startswith("v1="):
            v1 = part[3:].strip()
    return ts, v1


def _stripe_verify_signature(raw_body: bytes, sig_header: str, secret: str, tolerance_sec: int = 300) -> bool:
    """
    Minimal Stripe webhook signature verification (v1) without stripe SDK.

    - Must have Stripe-Signature header with t and v1
    - expected = HMAC_SHA256(secret, f"{t}.{payload}") (hex)
    - Compare expected to v1 using constant-time compare
    - Enforce timestamp tolerance to reduce replay window
    """
    if not secret or not str(secret).strip():
        return False
    t, v1 = _stripe_parse_sig_header(sig_header)
    if t is None or not v1:
        return False

    now = int(time.time())
    if abs(now - int(t)) > int(tolerance_sec):
        return False

    signed_payload = (str(t) + ".").encode("utf-8") + raw_body
    expected = hmac.new(str(secret).encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, v1)
    except Exception:
        return False


def _get_bearer_token(environ) -> Optional[str]:
    auth = environ.get("HTTP_AUTHORIZATION") or ""
    m = re.match(r"^\s*Bearer\s+(.+?)\s*$", auth, re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def _get_query_param(environ, name: str) -> Optional[str]:
    qs = environ.get("QUERY_STRING") or ""
    for part in qs.split("&"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
        else:
            k, v = part, ""
        if k == name:
            return v
    return None


# ----------------------------
# Minimal token store (MVP v1)
# ----------------------------

class TokenStore:
    """
    In v1 we use a simple in-process mapping token -> account_id.
    Persistence + rotation is out-of-scope for this skeleton.

    NOTE:
    - This is sufficient for local dev and for validating the API contract.
    - For production we will persist tokens in storage (Phase 4).
    """

    def __init__(self) -> None:
        self._token_to_account: Dict[str, str] = {}

    def issue_token(self, account_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._token_to_account[token] = account_id
        return token

    def link_token(self, token: str, account_id: str) -> None:
        self._token_to_account[token] = account_id

    def resolve(self, token: str) -> Optional[str]:
        return self._token_to_account.get(token)


# ----------------------------
# Storage wiring
# ----------------------------

def _make_storage():
    """
    Storage selection:
      FEESINK_STORAGE=memory|sqlite
    """
    storage_kind = (os.getenv("FEESINK_STORAGE") or "memory").strip().lower()
    if storage_kind == "sqlite":
        # Best-effort wiring to existing SQLiteStorage (Phase 2)
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        db_path = os.path.join(repo_root, os.getenv("FEESINK_SQLITE_DB", "feesink.db"))
        schema_path = os.path.join(repo_root, os.getenv("FEESINK_SCHEMA_SQL", "schema.sql"))
        from feesink.storage.sqlite import SQLiteStorage, SQLiteStorageConfig  # type: ignore

        return SQLiteStorage(SQLiteStorageConfig(db_path=db_path, schema_sql_path=schema_path))

    from feesink.storage.memory import InMemoryStorage  # type: ignore

    return InMemoryStorage()


# ----------------------------
# Domain helpers (best-effort)
# ----------------------------

def _ensure_account(storage, account_id: str) -> None:
    storage.ensure_account(account_id)


def _get_account(storage, account_id: str):
    return storage.get_account(account_id)


def _list_endpoints(storage, account_id: str):
    return storage.list_endpoints(account_id)


def _add_endpoint(storage, endpoint_obj) -> None:
    storage.add_endpoint(endpoint_obj)


def _update_endpoint(storage, endpoint_obj) -> None:
    storage.update_endpoint(endpoint_obj)


def _delete_endpoint(storage, account_id: str, endpoint_id: str) -> bool:
    # There is no explicit delete in earlier phases; keep skeleton behavior:
    # attempt storage.delete_endpoint if exists, otherwise emulate by disabling.
    if hasattr(storage, "delete_endpoint"):
        storage.delete_endpoint(account_id, endpoint_id)
        return True

    eps = storage.list_endpoints(account_id)
    for ep in eps:
        if ep.endpoint_id == endpoint_id:
            try:
                from feesink.domain.models import Endpoint, PausedReason  # type: ignore

                disabled = Endpoint(
                    endpoint_id=ep.endpoint_id,
                    account_id=ep.account_id,
                    url=ep.url,
                    interval_minutes=ep.interval_minutes,
                    enabled=False,
                    next_check_at=ep.next_check_at,
                    paused_reason=PausedReason.MANUAL,
                )
                storage.update_endpoint(disabled)
                return True
            except Exception:
                return False
    return False


# ----------------------------
# TopUp (dev-mode) helpers
# ----------------------------

# Pricing policy (CANON) — do not duplicate business constants here.
try:
    from feesink.config.canon import MIN_TOPUP_USDT as TOPUP_MIN_USDT  # type: ignore
    from feesink.config.canon import USDT_TO_UNITS_RATE as _USDT_TO_UNITS_RATE  # type: ignore
except Exception:  # pragma: no cover (dev fallback)
    # Fallback values ONLY to keep local skeleton usable if package layout differs.
    TOPUP_MIN_USDT = Decimal("50")
    _USDT_TO_UNITS_RATE = 100

TOPUP_UNITS_PER_USDT = Decimal(str(_USDT_TO_UNITS_RATE))


def _parse_amount_usdt(value: Any) -> Tuple[Optional[Decimal], Optional[str]]:
    """
    Accepts:
    - JSON number (int/float) or string
    Returns Decimal or error reason.
    """
    if value is None:
        return None, "missing_amount_usdt"

    if isinstance(value, (int,)):
        d = Decimal(value)
        return d, None

    if isinstance(value, float):
        # Avoid binary float surprises: go through string representation.
        d = Decimal(str(value))
        return d, None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None, "empty_amount_usdt"
        try:
            return Decimal(s), None
        except (InvalidOperation, ValueError):
            return None, "invalid_amount_usdt"

    return None, "invalid_amount_usdt_type"


def _amount_to_credited_units(amount_usdt: Decimal) -> Tuple[Optional[int], Optional[str]]:
    """
    Enforce integer units:
      credited_units = amount_usdt * rate must be an integer.
    """
    if amount_usdt <= 0:
        return None, "amount_usdt_must_be_gt_0"

    units = amount_usdt * TOPUP_UNITS_PER_USDT
    # Must be an integer
    if units != units.to_integral_value():
        return None, "amount_usdt_must_map_to_integer_units"
    credited_units = int(units)
    if credited_units <= 0:
        return None, "credited_units_must_be_gt_0"
    return credited_units, None


# ----------------------------
# Stripe API helpers (no SDK)
# ----------------------------

def _stripe_api_post_form(secret_key: str, path: str, form: Dict[str, str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    POST https://api.stripe.com{path} with application/x-www-form-urlencoded and Bearer auth.
    Returns (json, error_reason).
    """
    try:
        data = urllib.parse.urlencode(form).encode("utf-8")
        req = urllib.request.Request(
            url="https://api.stripe.com" + path,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "feesink-api-skeleton/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            try:
                obj = json.loads(raw.decode("utf-8"))
            except Exception:
                return None, "stripe_invalid_json"
            if not isinstance(obj, dict):
                return None, "stripe_json_not_object"
            return obj, None
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
            obj = json.loads(raw.decode("utf-8"))
            # Stripe error JSON is usually {"error": {...}}
            return None, json.dumps(obj, ensure_ascii=False)[:2000]
        except Exception:
            return None, f"stripe_http_error_{getattr(e, 'code', 'unknown')}"
    except Exception as e:
        return None, f"stripe_request_failed:{type(e).__name__}"


def _stripe_api_get_json(secret_key: str, path: str, query: Optional[Dict[str, str]] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    GET https://api.stripe.com{path}?{query} with Bearer auth.
    Returns (json, error_reason).
    """
    try:
        url = "https://api.stripe.com" + path
        if query:
            url += "?" + urllib.parse.urlencode(query)

        req = urllib.request.Request(
            url=url,
            method="GET",
            headers={
                "Authorization": f"Bearer {secret_key}",
                "User-Agent": "feesink-api-skeleton/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            try:
                obj = json.loads(raw.decode("utf-8"))
            except Exception:
                return None, "stripe_invalid_json"
            if not isinstance(obj, dict):
                return None, "stripe_json_not_object"
            return obj, None
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
            obj = json.loads(raw.decode("utf-8"))
            return None, json.dumps(obj, ensure_ascii=False)[:2000]
        except Exception:
            return None, f"stripe_http_error_{getattr(e, 'code', 'unknown')}"
    except Exception as e:
        return None, f"stripe_request_failed:{type(e).__name__}"


# ----------------------------
# App / Routing
# ----------------------------

class FeeSinkApiApp:
    def __init__(self):
        self.storage = _make_storage()
        self.tokens = TokenStore()

        self.topup_mode = (os.getenv("FEESINK_TOPUP_MODE") or "dev").strip().lower()

        # Dev bootstrap token (local)
        # This is only for skeleton usability; production provisioning is via Stripe webhook.
        dev_token = os.getenv("FEESINK_DEV_TOKEN", "").strip()
        dev_account = os.getenv("FEESINK_DEV_ACCOUNT", "demo-user").strip()
        if dev_token:
            _ensure_account(self.storage, dev_account)
            self.tokens.link_token(dev_token, dev_account)
            print(f"[DEV] Linked FEESINK_DEV_TOKEN to account_id={dev_account}")
            print(f"[DEV] Success page: http://127.0.0.1:8789/ui/success?token={dev_token}")
        else:
            # Issue a token and print it once for local testing.
            _ensure_account(self.storage, dev_account)
            token = self.tokens.issue_token(dev_account)
            print(f"[DEV] Issued token for account_id={dev_account}: {token}")
            print(f"[DEV] Success page: http://127.0.0.1:8789/ui/success?token={token}")

    # ---------- auth ----------
    def _auth_account_id(self, environ) -> Tuple[Optional[str], Optional[Tuple[int, list, bytes]]]:
        token = _get_bearer_token(environ) or _get_query_param(environ, "token")
        if not token:
            return None, _error(401, "unauthorized", "Missing Bearer token")
        account_id = self.tokens.resolve(token)
        if not account_id:
            return None, _error(401, "unauthorized", "Invalid token")
        return account_id, None

    def _auth_token_and_account(self, environ) -> Tuple[Optional[str], Optional[str], Optional[Tuple[int, list, bytes]]]:
        """
        Helper for endpoints that must preserve the *token* (e.g., Stripe metadata).
        Returns (token, account_id, err_response).
        """
        token = _get_bearer_token(environ) or _get_query_param(environ, "token")
        if not token:
            return None, None, _error(401, "unauthorized", "Missing Bearer token")
        account_id = self.tokens.resolve(token)
        if not account_id:
            return token, None, _error(401, "unauthorized", "Invalid token")
        return token, account_id, None

    # ---------- minimal UI ----------
    def handle_get_ui_success(self, environ):
        token = _get_bearer_token(environ) or _get_query_param(environ, "token")
        if not token:
            return _html_response(
                200,
                "<h1>FeeSink v1</h1><p>Missing token. Provide <code>?token=...</code> or Bearer token.</p>",
            )

        account_id = self.tokens.resolve(token)
        if not account_id:
            return _html_response(
                401,
                "<h1>Unauthorized</h1><p>Invalid token.</p>",
            )

        acc = _get_account(self.storage, account_id)
        balance = int(getattr(acc, "balance_units", 0))
        status = getattr(getattr(acc, "status", None), "value", getattr(acc, "status", "active"))

        token_esc = html.escape(token)
        acc_esc = html.escape(str(account_id))
        status_esc = html.escape(str(status))

        me_link = f"/v1/me?token={token_esc}"

        # Minimal "start" instructions (PowerShell-native, to avoid curl quoting traps)
        ps_add = (
            "Invoke-RestMethod -Method Post \"http://127.0.0.1:8789/v1/endpoints\" `\n"
            "  -Headers @{ Authorization = \"Bearer " + token_esc + "\" } `\n"
            "  -ContentType \"application/json\" `\n"
            "  -Body '{\"url\":\"https://example.org\",\"interval_minutes\":5}'"
        )

        html_text = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>FeeSink v1 — Success</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }}
    code, pre {{ background: #f5f5f5; padding: 2px 6px; border-radius: 6px; }}
    pre {{ padding: 12px; overflow:auto; }}
    .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 16px; max-width: 900px; }}
    .row {{ margin: 8px 0; }}
    a {{ text-decoration: none; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>FeeSink v1</h1>
    <div class="row"><b>account_id</b>: <code>{acc_esc}</code></div>
    <div class="row"><b>status</b>: <code>{status_esc}</code></div>
    <div class="row"><b>balance_units</b>: <code>{balance}</code></div>
    <hr/>
    <div class="row"><b>Your token</b> (Bearer):</div>
    <pre>{token_esc}</pre>
    <div class="row">
      Quick links:
      <ul>
        <li><a href="{me_link}">/v1/me (token in query)</a></li>
        <li><a href="/healthz">/healthz</a></li>
      </ul>
    </div>
    <div class="row"><b>PowerShell example</b> (add endpoint):</div>
    <pre>{html.escape(ps_add)}</pre>
  </div>
</body>
</html>
"""
        return _html_response(200, html_text)

    # ---------- API ----------
    def handle_get_me(self, environ):
        account_id, err = self._auth_account_id(environ)
        if err:
            return err

        acc = _get_account(self.storage, account_id)

        status = getattr(getattr(acc, "status", None), "value", getattr(acc, "status", "active"))
        account_payload = {
            "account_id": account_id,
            "status": status,
            "balance_units": int(getattr(acc, "balance_units", 0)),
        }

        endpoints_payload = []
        for ep in _list_endpoints(self.storage, account_id):
            endpoints_payload.append(
                {
                    "endpoint_id": ep.endpoint_id,
                    "url": ep.url,
                    "interval_minutes": ep.interval_minutes,
                    "enabled": bool(getattr(ep, "enabled", True)),
                    "paused_reason": getattr(getattr(ep, "paused_reason", None), "value", None),
                    "next_check_at_utc": _utc_iso(ep.next_check_at) if getattr(ep, "next_check_at", None) else None,
                    "last_check_at_utc": None,
                    "last_result": None,
                    "last_http_status": None,
                    "last_error_class": None,
                }
            )

        return _json_response(
            200,
            {
                "account": account_payload,
                "endpoints": endpoints_payload,
            },
        )

    def handle_post_topups(self, environ):
        if self.topup_mode != "dev":
            return _error(404, "not_found", "Route not found")

        account_id, err = self._auth_account_id(environ)
        if err:
            return err

        body, e = _read_json(environ)
        if e:
            return _error(400, "invalid_request", "Invalid JSON body", {"reason": e})

        amount_raw = body.get("amount_usdt")
        tx_hash = (body.get("tx_hash") or "").strip()

        if not tx_hash:
            return _error(422, "unprocessable_entity", "tx_hash is required", {"field": "tx_hash"})

        amount_usdt, reason = _parse_amount_usdt(amount_raw)
        if reason:
            return _error(422, "unprocessable_entity", "amount_usdt is invalid", {"field": "amount_usdt", "reason": reason})
        assert amount_usdt is not None

        if amount_usdt < TOPUP_MIN_USDT:
            return _error(
                422,
                "unprocessable_entity",
                "amount_usdt is below minimal top-up",
                {"field": "amount_usdt", "min_usdt": str(TOPUP_MIN_USDT)},
            )

        credited_units, reason2 = _amount_to_credited_units(amount_usdt)
        if reason2:
            return _error(
                422,
                "unprocessable_entity",
                "amount_usdt does not map to integer credited_units",
                {"field": "amount_usdt", "reason": reason2},
            )
        assert credited_units is not None

        now = datetime.now(tz=UTC)

        try:
            from feesink.domain.models import TopUp  # type: ignore
        except Exception:
            return _error(500, "internal_error", "TopUp model not available")

        try:
            topup = TopUp(
                account_id=account_id,
                tx_hash=tx_hash,
                amount_usdt=amount_usdt,
                credited_units=credited_units,
                ts=now,
            )
        except Exception as ex:
            return _error(422, "unprocessable_entity", "TopUp validation failed", {"exception": type(ex).__name__})

        try:
            res = self.storage.credit_topup(topup)
        except Exception as ex:
            # Storage contract: tx_hash uniqueness => inserted False (not exception),
            # so exception here is a real internal/storage failure.
            return _error(500, "internal_error", "Failed to credit topup", {"exception": type(ex).__name__})

        # Return updated balance (read-after-write)
        acc = _get_account(self.storage, account_id)
        balance_units = int(getattr(acc, "balance_units", 0))

        return _json_response(
            200,
            {
                "credited": {
                    "inserted": bool(getattr(res, "inserted", False)),
                    "account_id": account_id,
                    "tx_hash": tx_hash,
                    "amount_usdt": str(amount_usdt),
                    "credited_units": int(credited_units),
                    "balance_units": balance_units,
                    "ts_utc": _utc_iso(now),
                    "mode": "dev",
                }
            },
        )

    def handle_post_endpoints(self, environ):
        account_id, err = self._auth_account_id(environ)
        if err:
            return err

        body, e = _read_json(environ)
        if e:
            return _error(400, "invalid_request", "Invalid JSON body", {"reason": e})
        url = (body.get("url") or "").strip()
        interval = body.get("interval_minutes")

        if not url:
            return _error(422, "unprocessable_entity", "url is required", {"field": "url"})
        if not isinstance(interval, int):
            return _error(422, "unprocessable_entity", "interval_minutes must be int", {"field": "interval_minutes"})
        if interval not in (1, 5, 15):
            return _error(422, "unprocessable_entity", "interval_minutes must be one of 1,5,15", {"field": "interval_minutes"})

        endpoint_id = "ep_" + secrets.token_hex(8)

        try:
            from feesink.domain.models import Endpoint  # type: ignore
        except Exception:
            return _error(500, "internal_error", "Endpoint model not available")

        now = datetime.now(tz=UTC)
        try:
            ep = Endpoint(
                endpoint_id=endpoint_id,
                account_id=account_id,
                url=url,
                interval_minutes=interval,
                enabled=True,
                next_check_at=now,
                paused_reason=None,
            )
        except Exception as ex:
            return _error(422, "unprocessable_entity", "Endpoint validation failed", {"exception": type(ex).__name__})

        try:
            _add_endpoint(self.storage, ep)
        except Exception as ex:
            return _error(500, "internal_error", "Failed to add endpoint", {"exception": type(ex).__name__})

        return _json_response(
            200,
            {
                "endpoint": {
                    "endpoint_id": endpoint_id,
                    "url": url,
                    "interval_minutes": interval,
                    "enabled": True,
                }
            },
        )

    def handle_patch_endpoint(self, environ, endpoint_id: str):
        account_id, err = self._auth_account_id(environ)
        if err:
            return err

        body, e = _read_json(environ)
        if e:
            return _error(400, "invalid_request", "Invalid JSON body", {"reason": e})

        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return _error(422, "unprocessable_entity", "enabled must be boolean", {"field": "enabled"})

        eps = _list_endpoints(self.storage, account_id)
        found = None
        for ep in eps:
            if ep.endpoint_id == endpoint_id:
                found = ep
                break
        if not found:
            return _error(404, "not_found", "Endpoint not found", {"endpoint_id": endpoint_id})

        try:
            from feesink.domain.models import Endpoint, PausedReason  # type: ignore
        except Exception:
            return _error(500, "internal_error", "Endpoint model not available")

        try:
            ep2 = Endpoint(
                endpoint_id=found.endpoint_id,
                account_id=found.account_id,
                url=found.url,
                interval_minutes=found.interval_minutes,
                enabled=bool(enabled),
                next_check_at=found.next_check_at,
                paused_reason=None if enabled else PausedReason.MANUAL,
            )
        except Exception as ex:
            return _error(422, "unprocessable_entity", "Endpoint validation failed", {"exception": type(ex).__name__})

        try:
            _update_endpoint(self.storage, ep2)
        except Exception as ex:
            return _error(500, "internal_error", "Failed to update endpoint", {"exception": type(ex).__name__})

        return _json_response(
            200,
            {
                "endpoint": {
                    "endpoint_id": ep2.endpoint_id,
                    "url": ep2.url,
                    "interval_minutes": ep2.interval_minutes,
                    "enabled": bool(ep2.enabled),
                }
            },
        )

    def handle_delete_endpoint(self, environ, endpoint_id: str):
        account_id, err = self._auth_account_id(environ)
        if err:
            return err

        ok = _delete_endpoint(self.storage, account_id, endpoint_id)
        if not ok:
            return _error(404, "not_found", "Endpoint not found", {"endpoint_id": endpoint_id})
        return _json_response(200, {"ok": True})

    # ---------- Alerts test (dev convenience) ----------
    def handle_post_alerts_test(self, environ):
        # Keeping as a stub; contract may evolve.
        return _json_response(200, {"ok": True})

    # ---------- Stripe Checkout Session ----------
    def handle_post_stripe_checkout_sessions(self, environ):
        """
        POST /v1/stripe/checkout_sessions

        Creates Stripe Checkout Session for top-up product and stores stripe_links mapping:
          stripe_session_id -> account_id

        This endpoint requires:
          STRIPE_SECRET_KEY
          STRIPE_PRICE_ID_EUR_50
          STRIPE_SUCCESS_URL
          STRIPE_CANCEL_URL
        """
        token, account_id, err = self._auth_token_and_account(environ)
        if err:
            return err
        assert token is not None
        assert account_id is not None

        secret_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
        price_id = (os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip()
        success_url = (os.getenv("STRIPE_SUCCESS_URL") or "").strip()
        cancel_url = (os.getenv("STRIPE_CANCEL_URL") or "").strip()

        if not secret_key:
            return _error(500, "internal_error", "STRIPE_SECRET_KEY is not set", {})
        if not price_id:
            return _error(500, "internal_error", "STRIPE_PRICE_ID_EUR_50 is not set", {})
        if not success_url:
            return _error(500, "internal_error", "STRIPE_SUCCESS_URL is not set", {})
        if not cancel_url:
            return _error(500, "internal_error", "STRIPE_CANCEL_URL is not set", {})

        # Create session
        form = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            # Link back to account via durable server-side table (stripe_links),
            # but we also add a best-effort hint to metadata for diagnostics:
            "metadata[token]": token,
            "metadata[account_id]": str(account_id),
            "metadata[price_id]": str(price_id),
        }

        obj, err2 = _stripe_api_post_form(secret_key, "/v1/checkout/sessions", form)
        if err2 or not obj:
            return _error(502, "bad_gateway", "Stripe request failed", {"reason": err2})

        session_id = (obj.get("id") or "").strip()
        session_url = (obj.get("url") or "").strip()
        customer_id = obj.get("customer")
        if isinstance(customer_id, str):
            customer_id = customer_id.strip()
        else:
            customer_id = None

        if not session_id or not session_url:
            return _error(502, "bad_gateway", "Stripe response missing session id/url", {"stripe_id": session_id or None})

        # Durable mapping: session_id -> account_id (customer_id may be null at creation time)
        if not hasattr(self.storage, "upsert_stripe_link"):
            return _error(500, "internal_error", "Storage does not support stripe_links", {})
        try:
            self.storage.upsert_stripe_link(account_id=str(account_id), stripe_session_id=session_id, stripe_customer_id=customer_id)  # type: ignore[attr-defined]
        except Exception as ex:
            return _error(500, "internal_error", "Failed to store stripe link", {"exception": type(ex).__name__})

        return _json_response(
            200,
            {
                "checkout_session": {
                    "id": session_id,
                    "url": session_url,
                }
            },
        )

    # ---------- Stripe Webhook ----------
    def handle_post_webhooks_stripe(self, environ):
        """
        Stripe Webhook v1 (Phase 3) — canonical, without external SDK.

        Accept ONLY: checkout.session.completed

        Invariants (P0):
        - verify signature BEFORE any durable writes
        - dedup MUST be done strictly via storage UNIQUE (provider_events) — no "except -> dedup"
        - IMPORTANT: dedup by event_id must NOT short-circuit processing (Stripe retries can arrive after partial failure)
        - resolve account_id via stripe_links (session_id -> account_id)
        - credit idempotently via tx_hash = "stripe:" + event.id
        - on internal/storage/mapping failures: return non-2xx to force Stripe retry (do NOT silently ack)
        """
        whsec = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
        sig_header = (environ.get("HTTP_STRIPE_SIGNATURE") or "").strip()
        raw = _read_raw_body(environ)

        # 0) Verify signature (NO DB writes before this)
        if not _stripe_verify_signature(raw, sig_header, whsec):
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "decision": "signature_fail",
                        "event_id": None,
                        "event_type": None,
                        "session_id": None,
                        "payment_status": None,
                        "account_id": None,
                        "price_id": None,
                        "credited_units": None,
                    },
                    ensure_ascii=False,
                )
            )
            return _error(400, "invalid_signature", "Invalid Stripe signature")

        try:
            event = json.loads(raw.decode("utf-8"))
        except Exception:
            return _error(400, "invalid_request", "Invalid JSON body")

        event_id = (event.get("id") or "").strip() or None
        event_type = (event.get("type") or "").strip() or None

        if not event_id:
            return _error(400, "invalid_request", "Missing Stripe event id")

        # 1) Gate by type
        if event_type != "checkout.session.completed":
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "decision": "ignored",
                        "event_id": event_id,
                        "event_type": event_type,
                        "session_id": None,
                        "payment_status": None,
                        "account_id": None,
                        "price_id": None,
                        "credited_units": None,
                    },
                    ensure_ascii=False,
                )
            )
            return _json_response(200, {"ok": True})

        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        session = (data.get("object") or {}) if isinstance(data.get("object"), dict) else {}

        session_id = (session.get("id") or "").strip() or None
        payment_status = (session.get("payment_status") or "").strip() or None
        customer_id = (session.get("customer") or "").strip() or None
        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}

        if not session_id:
            return _error(400, "invalid_request", "Missing checkout session id")

        # 2) Dedup (first durable write): provider_events UNIQUE only.
        # IMPORTANT FIX:
        #   Even if provider_event is a duplicate (Stripe retry), we must continue processing,
        #   because the first attempt might have failed AFTER inserting provider_event.
        if not (hasattr(self.storage, "insert_provider_event")):
            return _error(500, "internal_error", "Storage does not support provider_events (insert_provider_event)", {})

        dedup_by_event_id = False
        try:
            inserted = bool(self.storage.insert_provider_event("stripe", event_id, raw.decode("utf-8")))  # type: ignore[attr-defined]
            if not inserted:
                dedup_by_event_id = True
        except Exception as ex:
            # Do NOT ack. Stripe must retry.
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "decision": "provider_event_write_failed",
                        "event_id": event_id,
                        "event_type": event_type,
                        "session_id": session_id,
                        "payment_status": payment_status,
                        "account_id": None,
                        "price_id": None,
                        "credited_units": None,
                        "exception": type(ex).__name__,
                    },
                    ensure_ascii=False,
                )
            )
            return _error(500, "internal_error", "Failed to persist provider_event", {"exception": type(ex).__name__})

        if dedup_by_event_id:
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "decision": "dedup_provider_event_continue",
                        "event_id": event_id,
                        "event_type": event_type,
                        "session_id": session_id,
                        "payment_status": payment_status,
                    },
                    ensure_ascii=False,
                )
            )

        # 3) paid gate
        if payment_status != "paid":
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "decision": "ignored_not_paid",
                        "event_id": event_id,
                        "event_type": event_type,
                        "session_id": session_id,
                        "payment_status": payment_status,
                        "account_id": None,
                        "price_id": None,
                        "credited_units": None,
                    },
                    ensure_ascii=False,
                )
            )
            return _json_response(200, {"ok": True, "dedup_event": dedup_by_event_id})

                # 4) Resolve account_id (PRIMARY: metadata.account_id; FALLBACK: stripe_links session_id->account_id)
        account_id_source = None
        account_id = None
        
        # Primary: metadata.account_id (contract-preferred)
        if isinstance(metadata, dict):
            v = metadata.get("account_id")
            if v is not None:
                v2 = str(v).strip()
                if v2:
                    account_id = v2
                    account_id_source = "metadata"
        
        # Fallback: stripe_links (session_id -> account_id)
        if not account_id:
            if not hasattr(self.storage, "resolve_account_by_stripe_session"):
                return _error(500, "internal_error", "Storage does not support stripe_links (resolve_account_by_stripe_session)", {})
        
            try:
                account_id = self.storage.resolve_account_by_stripe_session(session_id)  # type: ignore[attr-defined]
                account_id = str(account_id).strip() if account_id is not None else ""
                if not account_id:
                    raise ValueError("resolved_empty_account_id")
                account_id_source = "stripe_links"
            except Exception as ex:
                print(
                    json.dumps(
                        {
                            "provider": "stripe",
                            "decision": "unresolved_account",
                            "event_id": event_id,
                            "event_type": event_type,
                            "session_id": session_id,
                            "payment_status": payment_status,
                            "account_id": None,
                            "account_id_source": None,
                            "price_id": None,
                            "credited_units": None,
                            "reason": "account_id_not_resolved",
                            "exception": type(ex).__name__,
                        },
                        ensure_ascii=False,
                    )
                )
                return _error(500, "internal_error", "Unable to resolve account_id for session_id", {"session_id": session_id})
        
# 5) Determine price_id (prefer metadata; fallback to Stripe API GET with expand line_items)
        price_id = None
        if isinstance(metadata, dict):
            for k in ("price_id", "price", "stripe_price_id", "sku"):
                v = metadata.get(k)
                if isinstance(v, str) and v.strip():
                    price_id = v.strip()
                    break

        if price_id is None:
            # Try expanded line_items if present in event
            li = session.get("line_items")
            if isinstance(li, dict):
                data_list = li.get("data")
                if isinstance(data_list, list) and data_list:
                    first = data_list[0] if isinstance(data_list[0], dict) else None
                    if first and isinstance(first.get("price"), dict):
                        pid = first["price"].get("id")
                        if isinstance(pid, str) and pid.strip():
                            price_id = pid.strip()

        if price_id is None:
            # Best-effort live fetch (requires STRIPE_SECRET_KEY)
            secret_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
            if secret_key:
                obj, err = _stripe_api_get_json(
                    secret_key=secret_key,
                    path=f"/v1/checkout/sessions/{urllib.parse.quote(session_id)}",
                    query={"expand[]": "line_items.data.price"},
                )
                if obj and isinstance(obj.get("line_items"), dict):
                    data_list = obj["line_items"].get("data")
                    if isinstance(data_list, list) and data_list:
                        first = data_list[0] if isinstance(data_list[0], dict) else None
                        if first and isinstance(first.get("price"), dict):
                            pid = first["price"].get("id")
                            if isinstance(pid, str) and pid.strip():
                                price_id = pid.strip()

        # 6) Compute credited_units (server-controlled mapping)
        eur50_price_id = (os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip() or None

        credited_units: Optional[int] = None
        if eur50_price_id and price_id == eur50_price_id:
            credited_units = 5000

        if credited_units is None:
            # P0 invariant: unresolved reason must be explicit (durable status next step).
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "decision": "unresolved_mapping",
                        "event_id": event_id,
                        "event_type": event_type,
                        "session_id": session_id,
                        "payment_status": payment_status,
                        "account_id": str(account_id),
                        "price_id": price_id,
                        "credited_units": None,
                        "reason": "price_id_not_mapped",
                        "customer_id": customer_id,
                    },
                    ensure_ascii=False,
                )
            )
            return _error(500, "internal_error", "Unable to map Stripe price_id to credited_units", {"price_id": price_id})

        # 7) Credit topup (dedup by tx_hash at storage)
        tx_hash = f"stripe:{event_id}"

        try:
            from feesink.domain.models import TopUp  # type: ignore
        except Exception:
            return _error(500, "internal_error", "TopUp model not available")

        # amount_usdt is stored as CANON-equivalent (prepaid units mapping)
        try:
            rate = TOPUP_UNITS_PER_USDT
            amount_usdt = (Decimal(int(credited_units)) / rate)
            # Ensure it is an integer USDT amount in our canon (e.g., 5000 units -> 50 USDT)
            if amount_usdt != amount_usdt.to_integral_value():
                return _error(
                    500,
                    "internal_error",
                    "credited_units does not map to integer USDT amount",
                    {"credited_units": int(credited_units), "rate": str(rate)},
                )
            if amount_usdt < TOPUP_MIN_USDT:
                return _error(
                    500,
                    "internal_error",
                    "credited_units maps below minimal top-up",
                    {"amount_usdt": str(amount_usdt), "min_usdt": str(TOPUP_MIN_USDT)},
                )
        except Exception as ex:
            return _error(500, "internal_error", "Failed to compute amount_usdt", {"exception": type(ex).__name__})

        now = datetime.now(tz=UTC)
        try:
            topup = TopUp(
                account_id=str(account_id),
                tx_hash=tx_hash,
                amount_usdt=Decimal(str(amount_usdt)),
                credited_units=int(credited_units),
                ts=now,
            )
            # Explicit domain validation (do not rely on storage to catch shape errors)
            if hasattr(topup, "validate"):
                topup.validate()  # type: ignore[call-arg]
        except Exception as ex:
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "decision": "topup_invalid",
                        "event_id": event_id,
                        "event_type": event_type,
                        "session_id": session_id,
                        "payment_status": payment_status,
                        "account_id": str(account_id),
                        "price_id": price_id,
                        "credited_units": int(credited_units),
                        "exception": type(ex).__name__,
                    },
                    ensure_ascii=False,
                )
            )
            return _error(500, "internal_error", "TopUp validation failed", {"exception": type(ex).__name__})

        try:
            res = self.storage.credit_topup(topup)  # type: ignore[misc]
        except Exception as ex:
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "decision": "credit_failed",
                        "event_id": event_id,
                        "event_type": event_type,
                        "session_id": session_id,
                        "payment_status": payment_status,
                        "account_id": str(account_id),
                        "price_id": price_id,
                        "credited_units": int(credited_units),
                        "exception": type(ex).__name__,
                    },
                    ensure_ascii=False,
                )
            )
            return _error(500, "internal_error", "Failed to credit topup", {"exception": type(ex).__name__})

        dedup_by_tx_hash = not bool(getattr(res, "inserted", False))
        decision = "processed" if not dedup_by_tx_hash else "dedup_tx_hash"

        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "decision": decision,
                    "dedup_event": dedup_by_event_id,
                    "dedup_tx_hash": dedup_by_tx_hash,
                    "event_id": event_id,
                    "event_type": event_type,
                    "session_id": session_id,
                    "payment_status": payment_status,
                    "account_id": str(account_id),
                    "price_id": price_id,
                    "credited_units": int(credited_units),
                    "tx_hash": tx_hash,
                },
                ensure_ascii=False,
            )
        )

        return _json_response(200, {"ok": True, "dedup_event": dedup_by_event_id, "dedup_tx_hash": dedup_by_tx_hash})

    # ---------- WSGI entry ----------
    def __call__(self, environ, start_response):
        setup_testing_defaults(environ)
        t0 = time.monotonic()

        method = (environ.get("REQUEST_METHOD") or "GET").upper()
        path = environ.get("PATH_INFO") or "/"

        status: int
        headers: list
        body: bytes

        try:
            # Minimal HTML UI
            if path == "/ui/success" and method == "GET":
                status, headers, body = self.handle_get_ui_success(environ)

            # JSON API
            elif path == "/v1/me" and method == "GET":
                status, headers, body = self.handle_get_me(environ)

            elif path == "/v1/topups" and method == "POST":
                status, headers, body = self.handle_post_topups(environ)

            elif path == "/v1/endpoints" and method == "POST":
                status, headers, body = self.handle_post_endpoints(environ)

            elif path == "/v1/stripe/checkout_sessions" and method == "POST":
                status, headers, body = self.handle_post_stripe_checkout_sessions(environ)

            else:
                m = re.match(r"^/v1/endpoints/([^/]+)$", path)
                if m and method == "PATCH":
                    status, headers, body = self.handle_patch_endpoint(environ, m.group(1))
                elif m and method == "DELETE":
                    status, headers, body = self.handle_delete_endpoint(environ, m.group(1))
                elif path == "/v1/alerts/test" and method == "POST":
                    status, headers, body = self.handle_post_alerts_test(environ)
                elif path == "/v1/webhooks/stripe" and method == "POST":
                    status, headers, body = self.handle_post_webhooks_stripe(environ)
                elif path == "/healthz" and method == "GET":
                    payload = {
                        "ok": True,
                        "ts": _utc_iso(datetime.now(tz=UTC)),
                        "version": API_VERSION,
                    }
                    status, headers, body = _json_response(200, payload)
                else:
                    status, headers, body = _error(404, "not_found", "Route not found")

        except Exception as ex:
            status, headers, body = _error(500, "internal_error", "Unhandled error", {"exception": type(ex).__name__})

        # Minimal access log (stdout)
        duration_ms = int((time.monotonic() - t0) * 1000)
        print(
            json.dumps(
                {
                    "type": "api_request",
                    "ts": _utc_iso(datetime.now(tz=UTC)),
                    "api": API_VERSION,
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": duration_ms,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

        start_response(f"{status} OK", headers)
        return [body]


def main():
    _print_startup_banner()

    host = (os.getenv("FEESINK_API_HOST") or "127.0.0.1").strip()
    port = int(os.getenv("FEESINK_API_PORT") or "8789")

    app = FeeSinkApiApp()

    httpd = make_server(host, port, app)
    print(f"Listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
