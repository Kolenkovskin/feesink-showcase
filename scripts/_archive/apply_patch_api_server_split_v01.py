# FEESINK-APPLY-PATCH-API-SERVER-SPLIT v2026.01.16-01
#
# Goal:
# - Split feesink/api/server.py into smaller modules (<=700 lines policy)
# - Keep behavior stable (WSGI app, routes, startup banner, Stripe webhook logic)
# - Remove server.py from size allowlist (it must become <=700)
#
# Usage (PowerShell, repo root):
#   .\.venv\Scripts\python.exe .\scripts\apply_patch_api_server_split_v01.py
#
# Notes:
# - Creates new files under feesink/api/
# - Rewrites feesink/api/server.py
# - Updates scripts/lint_module_size.py allowlist (removes server.py exception)
# - Makes timestamped backups of modified files
#
# Determinism:
# - Prints version + TS_UTC + absolute ROOT + list of touched files.

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

VERSION = "FEESINK-APPLY-PATCH-API-SERVER-SPLIT v2026.01.16-01"
UTC = timezone.utc


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha1(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def _backup(path: Path) -> Path:
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    bak.write_bytes(path.read_bytes())
    return bak


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _py_compile(paths: List[Path]) -> Tuple[bool, str]:
    import py_compile

    for p in paths:
        try:
            py_compile.compile(str(p), doraise=True)
        except Exception as e:
            return False, f"{p}: {type(e).__name__}: {e}"
    return True, "OK"


@dataclass(frozen=True)
class OutFile:
    rel: str
    content: str


def main() -> int:
    root = _repo_root()

    target_server = root / "feesink" / "api" / "server.py"
    lint_file = root / "scripts" / "lint_module_size.py"

    if not target_server.exists():
        print(f"FATAL: not found: {target_server}")
        return 2
    if not lint_file.exists():
        print(f"FATAL: not found: {lint_file}")
        return 2

    print("=" * 80)
    print(VERSION)
    print("TS_UTC=", _utc_now())
    print("ROOT=", str(root))
    print("=" * 80)

    # --- Prepare new module files (kept intentionally compact & dependency-light) ---
    files: List[OutFile] = []

    files.append(
        OutFile(
            "feesink/api/_http.py",
            r'''# FeeSink API HTTP helpers
# FEESINK-API-HTTP v2026.01.16-01

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

UTC = timezone.utc


def utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")


def json_response(status: int, payload: Dict[str, Any], headers: Optional[list] = None):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hdrs = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if headers:
        hdrs.extend(headers)
    return status, hdrs, body


def html_response(status: int, html_text: str, headers: Optional[list] = None):
    body = html_text.encode("utf-8")
    hdrs = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if headers:
        hdrs.extend(headers)
    return status, hdrs, body


def error(status: int, code: str, message: str, details: Optional[Dict[str, Any]] = None):
    return json_response(
        status,
        {"error": {"code": code, "message": message, "details": details or {}}},
    )


def read_json(environ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
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


def read_raw_body(environ) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except Exception:
        length = 0
    if length <= 0:
        return b""
    return environ["wsgi.input"].read(length)


def get_bearer_token(environ) -> Optional[str]:
    auth = environ.get("HTTP_AUTHORIZATION") or ""
    m = re.match(r"^\s*Bearer\s+(.+?)\s*$", auth, re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def get_query_param(environ, name: str) -> Optional[str]:
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
''',
        )
    )

    files.append(
        OutFile(
            "feesink/api/_stripe.py",
            r'''# FeeSink Stripe helpers (no SDK)
# FEESINK-API-STRIPE v2026.01.16-01

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple


def stripe_parse_sig_header(sig_header: str) -> tuple[Optional[int], Optional[str]]:
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


def stripe_verify_signature(raw_body: bytes, sig_header: str, secret: str, tolerance_sec: int = 300) -> bool:
    if not secret or not str(secret).strip():
        return False
    t, v1 = stripe_parse_sig_header(sig_header)
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


def stripe_api_post_form(secret_key: str, path: str, form: Dict[str, str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
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
            return None, json.dumps(obj, ensure_ascii=False)[:2000]
        except Exception:
            return None, f"stripe_http_error_{getattr(e, 'code', 'unknown')}"
    except Exception as e:
        return None, f"stripe_request_failed:{type(e).__name__}"


def stripe_api_get_json(secret_key: str, path: str, query: Optional[Dict[str, str]] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
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
''',
        )
    )

    files.append(
        OutFile(
            "feesink/api/app.py",
            r'''# FeeSink API app (routing + handlers)
# FEESINK-API-APP v2026.01.16-01

from __future__ import annotations

import html
import json
import os
import re
import secrets
import time
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple
from wsgiref.util import setup_testing_defaults

from feesink.api._http import (
    UTC,
    error,
    html_response,
    json_response,
    read_json,
    read_raw_body,
    get_bearer_token,
    get_query_param,
    utc_iso,
)
from feesink.api._stripe import (
    stripe_api_get_json,
    stripe_api_post_form,
    stripe_verify_signature,
)

# Pricing policy (CANON) — do not duplicate business constants here.
try:
    from feesink.config.canon import MIN_TOPUP_USDT as TOPUP_MIN_USDT  # type: ignore
    from feesink.config.canon import USDT_TO_UNITS_RATE as _USDT_TO_UNITS_RATE  # type: ignore
except Exception:  # pragma: no cover
    TOPUP_MIN_USDT = Decimal("50")
    _USDT_TO_UNITS_RATE = 100

TOPUP_UNITS_PER_USDT = Decimal(str(_USDT_TO_UNITS_RATE))


class TokenStore:
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


def make_storage():
    storage_kind = (os.getenv("FEESINK_STORAGE") or "memory").strip().lower()
    if storage_kind == "sqlite":
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        db_path = os.path.join(repo_root, os.getenv("FEESINK_SQLITE_DB", "feesink.db"))
        schema_path = os.path.join(repo_root, os.getenv("FEESINK_SCHEMA_SQL", "schema.sql"))
        from feesink.storage.sqlite import SQLiteStorage, SQLiteStorageConfig  # type: ignore

        return SQLiteStorage(SQLiteStorageConfig(db_path=db_path, schema_sql_path=schema_path))

    from feesink.storage.memory import InMemoryStorage  # type: ignore

    return InMemoryStorage()


# Domain helpers (best-effort)
def ensure_account(storage, account_id: str) -> None:
    storage.ensure_account(account_id)


def get_account(storage, account_id: str):
    return storage.get_account(account_id)


def list_endpoints(storage, account_id: str):
    return storage.list_endpoints(account_id)


def add_endpoint(storage, endpoint_obj) -> None:
    storage.add_endpoint(endpoint_obj)


def update_endpoint(storage, endpoint_obj) -> None:
    storage.update_endpoint(endpoint_obj)


def delete_endpoint(storage, account_id: str, endpoint_id: str) -> bool:
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


def parse_amount_usdt(value: Any) -> Tuple[Optional[Decimal], Optional[str]]:
    if value is None:
        return None, "missing_amount_usdt"

    if isinstance(value, int):
        return Decimal(value), None

    if isinstance(value, float):
        return Decimal(str(value)), None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None, "empty_amount_usdt"
        try:
            return Decimal(s), None
        except (InvalidOperation, ValueError):
            return None, "invalid_amount_usdt"

    return None, "invalid_amount_usdt_type"


def amount_to_credited_units(amount_usdt: Decimal) -> Tuple[Optional[int], Optional[str]]:
    if amount_usdt <= 0:
        return None, "amount_usdt_must_be_gt_0"

    units = amount_usdt * TOPUP_UNITS_PER_USDT
    if units != units.to_integral_value():
        return None, "amount_usdt_must_map_to_integer_units"
    credited_units = int(units)
    if credited_units <= 0:
        return None, "credited_units_must_be_gt_0"
    return credited_units, None


class FeeSinkApiApp:
    def __init__(self, api_version: str):
        self.api_version = api_version
        self.storage = make_storage()
        self.tokens = TokenStore()
        self.topup_mode = (os.getenv("FEESINK_TOPUP_MODE") or "dev").strip().lower()

        dev_token = os.getenv("FEESINK_DEV_TOKEN", "").strip()
        dev_account = os.getenv("FEESINK_DEV_ACCOUNT", "demo-user").strip()

        ensure_account(self.storage, dev_account)
        if dev_token:
            self.tokens.link_token(dev_token, dev_account)
            print(f"[DEV] Linked FEESINK_DEV_TOKEN to account_id={dev_account}")
            print(f"[DEV] Success page: http://127.0.0.1:8789/ui/success?token={dev_token}")
        else:
            token = self.tokens.issue_token(dev_account)
            print(f"[DEV] Issued token for account_id={dev_account}: {token}")
            print(f"[DEV] Success page: http://127.0.0.1:8789/ui/success?token={token}")

    # --- auth ---
    def auth_account_id(self, environ) -> Tuple[Optional[str], Optional[Tuple[int, list, bytes]]]:
        token = get_bearer_token(environ) or get_query_param(environ, "token")
        if not token:
            return None, error(401, "unauthorized", "Missing Bearer token")
        account_id = self.tokens.resolve(token)
        if not account_id:
            return None, error(401, "unauthorized", "Invalid token")
        return account_id, None

    def auth_token_and_account(self, environ) -> Tuple[Optional[str], Optional[str], Optional[Tuple[int, list, bytes]]]:
        token = get_bearer_token(environ) or get_query_param(environ, "token")
        if not token:
            return None, None, error(401, "unauthorized", "Missing Bearer token")
        account_id = self.tokens.resolve(token)
        if not account_id:
            return token, None, error(401, "unauthorized", "Invalid token")
        return token, account_id, None

    # --- UI ---
    def handle_get_ui_success(self, environ):
        token = get_bearer_token(environ) or get_query_param(environ, "token")
        if not token:
            return html_response(
                200,
                "<h1>FeeSink v1</h1><p>Missing token. Provide <code>?token=...</code> or Bearer token.</p>",
            )

        account_id = self.tokens.resolve(token)
        if not account_id:
            return html_response(401, "<h1>Unauthorized</h1><p>Invalid token.</p>")

        acc = get_account(self.storage, account_id)
        balance = int(getattr(acc, "balance_units", 0))
        status = getattr(getattr(acc, "status", None), "value", getattr(acc, "status", "active"))

        token_esc = html.escape(token)
        acc_esc = html.escape(str(account_id))
        status_esc = html.escape(str(status))

        me_link = f"/v1/me?token={token_esc}"

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
        return html_response(200, html_text)

    # --- API ---
    def handle_get_me(self, environ):
        account_id, errr = self.auth_account_id(environ)
        if errr:
            return errr

        acc = get_account(self.storage, account_id)
        status = getattr(getattr(acc, "status", None), "value", getattr(acc, "status", "active"))
        account_payload = {
            "account_id": account_id,
            "status": status,
            "balance_units": int(getattr(acc, "balance_units", 0)),
        }

        endpoints_payload = []
        for ep in list_endpoints(self.storage, account_id):
            endpoints_payload.append(
                {
                    "endpoint_id": ep.endpoint_id,
                    "url": ep.url,
                    "interval_minutes": ep.interval_minutes,
                    "enabled": bool(getattr(ep, "enabled", True)),
                    "paused_reason": getattr(getattr(ep, "paused_reason", None), "value", None),
                    "next_check_at_utc": utc_iso(ep.next_check_at) if getattr(ep, "next_check_at", None) else None,
                    "last_check_at_utc": None,
                    "last_result": None,
                    "last_http_status": None,
                    "last_error_class": None,
                }
            )

        return json_response(200, {"account": account_payload, "endpoints": endpoints_payload})

    def handle_post_topups(self, environ):
        if self.topup_mode != "dev":
            return error(404, "not_found", "Route not found")

        account_id, errr = self.auth_account_id(environ)
        if errr:
            return errr

        body, e = read_json(environ)
        if e:
            return error(400, "invalid_request", "Invalid JSON body", {"reason": e})

        amount_raw = body.get("amount_usdt")
        tx_hash = (body.get("tx_hash") or "").strip()
        if not tx_hash:
            return error(422, "unprocessable_entity", "tx_hash is required", {"field": "tx_hash"})

        amount_usdt, reason = parse_amount_usdt(amount_raw)
        if reason:
            return error(422, "unprocessable_entity", "amount_usdt is invalid", {"field": "amount_usdt", "reason": reason})
        assert amount_usdt is not None

        if amount_usdt < TOPUP_MIN_USDT:
            return error(
                422,
                "unprocessable_entity",
                "amount_usdt is below minimal top-up",
                {"field": "amount_usdt", "min_usdt": str(TOPUP_MIN_USDT)},
            )

        credited_units, reason2 = amount_to_credited_units(amount_usdt)
        if reason2:
            return error(
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
            return error(500, "internal_error", "TopUp model not available")

        try:
            topup = TopUp(
                account_id=account_id,
                tx_hash=tx_hash,
                amount_usdt=amount_usdt,
                credited_units=credited_units,
                ts=now,
            )
        except Exception as ex:
            return error(422, "unprocessable_entity", "TopUp validation failed", {"exception": type(ex).__name__})

        try:
            res = self.storage.credit_topup(topup)
        except Exception as ex:
            return error(500, "internal_error", "Failed to credit topup", {"exception": type(ex).__name__})

        acc = get_account(self.storage, account_id)
        balance_units = int(getattr(acc, "balance_units", 0))

        return json_response(
            200,
            {
                "credited": {
                    "inserted": bool(getattr(res, "inserted", False)),
                    "account_id": account_id,
                    "tx_hash": tx_hash,
                    "amount_usdt": str(amount_usdt),
                    "credited_units": int(credited_units),
                    "balance_units": balance_units,
                    "ts_utc": utc_iso(now),
                    "mode": "dev",
                }
            },
        )

    def handle_post_endpoints(self, environ):
        account_id, errr = self.auth_account_id(environ)
        if errr:
            return errr

        body, e = read_json(environ)
        if e:
            return error(400, "invalid_request", "Invalid JSON body", {"reason": e})
        url = (body.get("url") or "").strip()
        interval = body.get("interval_minutes")

        if not url:
            return error(422, "unprocessable_entity", "url is required", {"field": "url"})
        if not isinstance(interval, int):
            return error(422, "unprocessable_entity", "interval_minutes must be int", {"field": "interval_minutes"})
        if interval not in (1, 5, 15):
            return error(422, "unprocessable_entity", "interval_minutes must be one of 1,5,15", {"field": "interval_minutes"})

        endpoint_id = "ep_" + secrets.token_hex(8)

        try:
            from feesink.domain.models import Endpoint  # type: ignore
        except Exception:
            return error(500, "internal_error", "Endpoint model not available")

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
            return error(422, "unprocessable_entity", "Endpoint validation failed", {"exception": type(ex).__name__})

        try:
            add_endpoint(self.storage, ep)
        except Exception as ex:
            return error(500, "internal_error", "Failed to add endpoint", {"exception": type(ex).__name__})

        return json_response(
            200,
            {"endpoint": {"endpoint_id": endpoint_id, "url": url, "interval_minutes": interval, "enabled": True}},
        )

    def handle_patch_endpoint(self, environ, endpoint_id: str):
        account_id, errr = self.auth_account_id(environ)
        if errr:
            return errr

        body, e = read_json(environ)
        if e:
            return error(400, "invalid_request", "Invalid JSON body", {"reason": e})

        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return error(422, "unprocessable_entity", "enabled must be boolean", {"field": "enabled"})

        eps = list_endpoints(self.storage, account_id)
        found = None
        for ep in eps:
            if ep.endpoint_id == endpoint_id:
                found = ep
                break
        if not found:
            return error(404, "not_found", "Endpoint not found", {"endpoint_id": endpoint_id})

        try:
            from feesink.domain.models import Endpoint, PausedReason  # type: ignore
        except Exception:
            return error(500, "internal_error", "Endpoint model not available")

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
            return error(422, "unprocessable_entity", "Endpoint validation failed", {"exception": type(ex).__name__})

        try:
            update_endpoint(self.storage, ep2)
        except Exception as ex:
            return error(500, "internal_error", "Failed to update endpoint", {"exception": type(ex).__name__})

        return json_response(
            200,
            {"endpoint": {"endpoint_id": ep2.endpoint_id, "url": ep2.url, "interval_minutes": ep2.interval_minutes, "enabled": bool(ep2.enabled)}},
        )

    def handle_delete_endpoint(self, environ, endpoint_id: str):
        account_id, errr = self.auth_account_id(environ)
        if errr:
            return errr
        ok = delete_endpoint(self.storage, account_id, endpoint_id)
        if not ok:
            return error(404, "not_found", "Endpoint not found", {"endpoint_id": endpoint_id})
        return json_response(200, {"ok": True})

    def handle_post_alerts_test(self, environ):
        return json_response(200, {"ok": True})

    def handle_post_stripe_checkout_sessions(self, environ):
        token, account_id, errr = self.auth_token_and_account(environ)
        if errr:
            return errr
        assert token is not None and account_id is not None

        secret_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
        price_id = (os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip()
        success_url = (os.getenv("STRIPE_SUCCESS_URL") or "").strip()
        cancel_url = (os.getenv("STRIPE_CANCEL_URL") or "").strip()

        if not secret_key:
            return error(500, "internal_error", "STRIPE_SECRET_KEY is not set", {})
        if not price_id:
            return error(500, "internal_error", "STRIPE_PRICE_ID_EUR_50 is not set", {})
        if not success_url:
            return error(500, "internal_error", "STRIPE_SUCCESS_URL is not set", {})
        if not cancel_url:
            return error(500, "internal_error", "STRIPE_CANCEL_URL is not set", {})

        form = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "metadata[token]": token,
            "metadata[account_id]": str(account_id),
            "metadata[price_id]": str(price_id),
        }

        obj, err2 = stripe_api_post_form(secret_key, "/v1/checkout/sessions", form)
        if err2 or not obj:
            return error(502, "bad_gateway", "Stripe request failed", {"reason": err2})

        session_id = (obj.get("id") or "").strip()
        session_url = (obj.get("url") or "").strip()
        customer_id = obj.get("customer")
        if isinstance(customer_id, str):
            customer_id = customer_id.strip()
        else:
            customer_id = None

        if not session_id or not session_url:
            return error(502, "bad_gateway", "Stripe response missing session id/url", {"stripe_id": session_id or None})

        if not hasattr(self.storage, "upsert_stripe_link"):
            return error(500, "internal_error", "Storage does not support stripe_links", {})
        try:
            self.storage.upsert_stripe_link(account_id=str(account_id), stripe_session_id=session_id, stripe_customer_id=customer_id)  # type: ignore[attr-defined]
        except Exception as ex:
            return error(500, "internal_error", "Failed to store stripe link", {"exception": type(ex).__name__})

        return json_response(200, {"checkout_session": {"id": session_id, "url": session_url}})

    def handle_post_webhooks_stripe(self, environ):
        whsec = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
        sig_header = (environ.get("HTTP_STRIPE_SIGNATURE") or "").strip()
        raw = read_raw_body(environ)

        if not stripe_verify_signature(raw, sig_header, whsec):
            print(json.dumps({"provider": "stripe", "decision": "signature_fail"}, ensure_ascii=False))
            return error(400, "invalid_signature", "Invalid Stripe signature")

        try:
            event = json.loads(raw.decode("utf-8"))
        except Exception:
            return error(400, "invalid_request", "Invalid JSON body")

        event_id = (event.get("id") or "").strip() or None
        event_type = (event.get("type") or "").strip() or None
        if not event_id:
            return error(400, "invalid_request", "Missing Stripe event id")

        if event_type != "checkout.session.completed":
            print(json.dumps({"provider": "stripe", "decision": "ignored", "event_id": event_id, "event_type": event_type}, ensure_ascii=False))
            return json_response(200, {"ok": True})

        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        session = (data.get("object") or {}) if isinstance(data.get("object"), dict) else {}

        session_id = (session.get("id") or "").strip() or None
        payment_status = (session.get("payment_status") or "").strip() or None
        customer_id = (session.get("customer") or "").strip() or None
        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}

        if not session_id:
            return error(400, "invalid_request", "Missing checkout session id")

        if not (hasattr(self.storage, "insert_provider_event")):
            return error(500, "internal_error", "Storage does not support provider_events (insert_provider_event)", {})

        dedup_by_event_id = False
        try:
            inserted = bool(self.storage.insert_provider_event("stripe", event_id, raw.decode("utf-8")))  # type: ignore[attr-defined]
            if not inserted:
                dedup_by_event_id = True
        except Exception as ex:
            print(json.dumps({"provider": "stripe", "decision": "provider_event_write_failed", "event_id": event_id, "exception": type(ex).__name__}, ensure_ascii=False))
            return error(500, "internal_error", "Failed to persist provider_event", {"exception": type(ex).__name__})

        if payment_status != "paid":
            print(json.dumps({"provider": "stripe", "decision": "ignored_not_paid", "event_id": event_id, "session_id": session_id, "payment_status": payment_status}, ensure_ascii=False))
            return json_response(200, {"ok": True, "dedup_event": dedup_by_event_id})

        # Resolve account_id: metadata.account_id preferred, fallback stripe_links
        account_id_source = None
        account_id = None
        if isinstance(metadata, dict):
            v = metadata.get("account_id")
            if v is not None:
                v2 = str(v).strip()
                if v2:
                    account_id = v2
                    account_id_source = "metadata"

        if not account_id:
            if not hasattr(self.storage, "resolve_account_by_stripe_session"):
                return error(500, "internal_error", "Storage does not support stripe_links (resolve_account_by_stripe_session)", {})
            try:
                account_id = self.storage.resolve_account_by_stripe_session(session_id)  # type: ignore[attr-defined]
                account_id = str(account_id).strip() if account_id is not None else ""
                if not account_id:
                    raise ValueError("resolved_empty_account_id")
                account_id_source = "stripe_links"
            except Exception as ex:
                print(json.dumps({"provider": "stripe", "decision": "unresolved_account", "event_id": event_id, "session_id": session_id, "exception": type(ex).__name__}, ensure_ascii=False))
                return error(500, "internal_error", "Unable to resolve account_id for session_id", {"session_id": session_id})

        # Determine price_id (metadata first; then expand line_items; then API fetch)
        price_id = None
        if isinstance(metadata, dict):
            for k in ("price_id", "price", "stripe_price_id", "sku"):
                v = metadata.get(k)
                if isinstance(v, str) and v.strip():
                    price_id = v.strip()
                    break

        if price_id is None:
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
            secret_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
            if secret_key:
                obj, errx = stripe_api_get_json(
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

        eur50_price_id = (os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip() or None
        credited_units: Optional[int] = None
        if eur50_price_id and price_id == eur50_price_id:
            credited_units = 5000

        if credited_units is None:
            print(json.dumps({"provider": "stripe", "decision": "unresolved_mapping", "event_id": event_id, "account_id": str(account_id), "price_id": price_id}, ensure_ascii=False))
            return error(500, "internal_error", "Unable to map Stripe price_id to credited_units", {"price_id": price_id})

        tx_hash = f"stripe:{event_id}"

        try:
            from feesink.domain.models import TopUp  # type: ignore
        except Exception:
            return error(500, "internal_error", "TopUp model not available")

        try:
            rate = TOPUP_UNITS_PER_USDT
            amount_usdt = (Decimal(int(credited_units)) / rate)
            if amount_usdt != amount_usdt.to_integral_value():
                return error(500, "internal_error", "credited_units does not map to integer USDT amount", {"credited_units": int(credited_units), "rate": str(rate)})
            if amount_usdt < TOPUP_MIN_USDT:
                return error(500, "internal_error", "credited_units maps below minimal top-up", {"amount_usdt": str(amount_usdt), "min_usdt": str(TOPUP_MIN_USDT)})
        except Exception as ex:
            return error(500, "internal_error", "Failed to compute amount_usdt", {"exception": type(ex).__name__})

        now = datetime.now(tz=UTC)
        try:
            topup = TopUp(
                account_id=str(account_id),
                tx_hash=tx_hash,
                amount_usdt=Decimal(str(amount_usdt)),
                credited_units=int(credited_units),
                ts=now,
            )
            if hasattr(topup, "validate"):
                topup.validate()  # type: ignore[call-arg]
        except Exception as ex:
            print(json.dumps({"provider": "stripe", "decision": "topup_invalid", "event_id": event_id, "exception": type(ex).__name__}, ensure_ascii=False))
            return error(500, "internal_error", "TopUp validation failed", {"exception": type(ex).__name__})

        try:
            res = self.storage.credit_topup(topup)  # type: ignore[misc]
        except Exception as ex:
            print(json.dumps({"provider": "stripe", "decision": "credit_failed", "event_id": event_id, "exception": type(ex).__name__}, ensure_ascii=False))
            return error(500, "internal_error", "Failed to credit topup", {"exception": type(ex).__name__})

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
                    "account_id_source": account_id_source,
                    "price_id": price_id,
                    "credited_units": int(credited_units),
                    "tx_hash": tx_hash,
                    "customer_id": customer_id,
                },
                ensure_ascii=False,
            )
        )

        return json_response(200, {"ok": True, "dedup_event": dedup_by_event_id, "dedup_tx_hash": dedup_by_tx_hash})

    # --- WSGI entry ---
    def __call__(self, environ, start_response):
        setup_testing_defaults(environ)
        t0 = time.monotonic()

        method = (environ.get("REQUEST_METHOD") or "GET").upper()
        path = environ.get("PATH_INFO") or "/"

        status: int
        headers: list
        body: bytes

        try:
            if path == "/ui/success" and method == "GET":
                status, headers, body = self.handle_get_ui_success(environ)
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
                    payload = {"ok": True, "ts": utc_iso(datetime.now(tz=UTC)), "version": self.api_version}
                    status, headers, body = json_response(200, payload)
                else:
                    status, headers, body = error(404, "not_found", "Route not found")
        except Exception as ex:
            status, headers, body = error(500, "internal_error", "Unhandled error", {"exception": type(ex).__name__})

        duration_ms = int((time.monotonic() - t0) * 1000)
        print(
            json.dumps(
                {
                    "type": "api_request",
                    "ts": utc_iso(datetime.now(tz=UTC)),
                    "api": self.api_version,
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
''',
        )
    )

    # New server.py (thin entrypoint + banner)
    files.append(
        OutFile(
            "feesink/api/server.py",
            r'''"""
FeeSink — API skeleton (Self-Service v1) + minimal HTML success page
API_CONTRACT: v2026.01.01-API-01 (docs/API_CONTRACT_v1.md)

Run (PowerShell, from repo root):
  .\.venv\Scripts\python.exe -m feesink.api.server
"""

from __future__ import annotations

import hashlib
import os
from datetime import timezone
from typing import Optional
from wsgiref.simple_server import make_server

from feesink.api.app import FeeSinkApiApp

# ----------------------------
# Version banner (must print at startup)
# ----------------------------

API_VERSION = "FEESINK-API-SKELETON v2026.01.16-API-SPLIT-01"


def _safe_getattr(mod, name: str, default: str) -> str:
    try:
        return getattr(mod, name)
    except Exception:
        return default


def _sha256_hex_prefix(s: str, n: int = 8) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def _print_startup_banner() -> None:
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

    host = (os.getenv("FEESINK_API_HOST") or "127.0.0.1").strip()
    port_raw = (os.getenv("FEESINK_API_PORT") or "8789").strip()
    try:
        port = int(port_raw)
    except Exception:
        print(f"FATAL: FEESINK_API_PORT must be int, got: {port_raw!r}")
        raise SystemExit(2)

    storage_kind = (os.getenv("FEESINK_STORAGE") or "memory").strip().lower()
    db_abs_path: Optional[str] = None
    db_basename: Optional[str] = None
    if storage_kind == "sqlite":
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        db_rel = os.getenv("FEESINK_SQLITE_DB", "feesink.db")
        db_abs_path = os.path.join(repo_root, db_rel)
        db_basename = os.path.basename(db_abs_path)

    stripe_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    whsec = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    stripe_price = (os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip()
    stripe_success = (os.getenv("STRIPE_SUCCESS_URL") or "").strip()
    stripe_cancel = (os.getenv("STRIPE_CANCEL_URL") or "").strip()
    stripe_intent = any([stripe_key, whsec, stripe_price, stripe_success, stripe_cancel])

    stripe_mode = (os.getenv("FEESINK_STRIPE_MODE") or "test").strip().lower()
    if stripe_mode not in ("test", "live"):
        print(f"FATAL: FEESINK_STRIPE_MODE must be 'test' or 'live' (got {stripe_mode!r})")
        raise SystemExit(2)

    print("=" * 80)
    print(f"MODE: STRIPE_{stripe_mode.upper()}")
    print(f"LISTEN: http://{host}:{port}")

    if storage_kind == "sqlite":
        print("STORAGE: sqlite")
        print(f"SQLITE_DB: {db_abs_path} (basename={db_basename})")
    else:
        print(f"STORAGE: {storage_kind}")

    if stripe_intent:
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

    print(API_VERSION)
    print(f"WORKER: {worker_v}")
    print(f"SQLITE:  {sqlite_v}")
    print("=" * 80)


def main() -> None:
    _print_startup_banner()

    host = (os.getenv("FEESINK_API_HOST") or "127.0.0.1").strip()
    port = int(os.getenv("FEESINK_API_PORT") or "8789")

    app = FeeSinkApiApp(api_version=API_VERSION)

    httpd = make_server(host, port, app)
    print(f"Listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
''',
        )
    )

    # Update lint allowlist: remove server.py exception
    lint_text = lint_file.read_text(encoding="utf-8")
    if "feesink/api/server.py" in lint_text:
        lint_text2 = lint_text.replace('"feesink/api/server.py": 1491,\n', "")
        lint_text2 = lint_text2.replace("'feesink/api/server.py': 1491,\n", "")
    else:
        lint_text2 = lint_text

    touched: List[Path] = []

    # Backup + write server.py and lint file
    bak_server = _backup(target_server)
    print("BACKUP=", str(bak_server))

    bak_lint = _backup(lint_file)
    print("BACKUP=", str(bak_lint))

    # Write all new/updated files
    for of in files:
        p = root / Path(of.rel)
        if p.exists() and of.rel != "feesink/api/server.py":
            # if file existed, back it up (unlikely for new modules)
            _backup(p)
        _write_text(p, of.content)
        touched.append(p)

    _write_text(lint_file, lint_text2)
    touched.append(lint_file)

    ok, msg = _py_compile(touched)
    print("PY_COMPILE=", msg)
    if not ok:
        print("FAIL: compile failed")
        return 3

    print("TOUCHED_FILES:")
    for p in touched:
        rel = p.relative_to(root).as_posix()
        print(f"  - {rel} sha1={_sha1(p)}")

    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
