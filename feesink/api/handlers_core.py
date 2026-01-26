# FeeSink API core handlers
# FEESINK-API-HANDLERS-CORE v2026.01.26-04

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple
from uuid import uuid4

import socket
import time
import urllib.request
import urllib.error

from feesink.api._http import (
    UTC,
    error,
    get_query_param,
    json_response,
    read_json,
    utc_iso,
)
from feesink.config.canon import MIN_TOPUP_USDT, credited_units
from feesink.domain.models import CheckEvent, CheckResult, ErrorClass, Endpoint, PausedReason
from feesink.storage.interfaces import Conflict, NotFound, ValidationError


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _auth_account(app, environ) -> Tuple[Optional[str], Optional[Tuple[int, list, bytes]]]:
    account_id, err = app.auth_account_id(environ)
    return account_id, err


def _status_to_public(value: object) -> str:
    if value is None:
        return "unknown"
    s = str(value).strip()
    if not s:
        return "unknown"
    if "." in s:
        s = s.split(".")[-1]
    s = s.lower()
    if s in ("active", "paused", "inactive", "depleted"):
        return s
    return "unknown"


def _read_json_or_400(environ):
    obj, err_code = read_json(environ)
    if err_code is None:
        return obj, None
    if err_code == "empty_body":
        return None, error(400, "invalid_request", "Empty body")
    if err_code == "invalid_json":
        return None, error(400, "invalid_request", "Invalid JSON")
    return None, error(400, "invalid_request", "Invalid request body", {"reason": err_code})


def handle_get_ui_success(app, environ):
    token = (get_query_param(environ, "token") or "").strip()
    html = f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>FeeSink</title></head>
  <body style="font-family: Arial, sans-serif; padding: 24px;">
    <h2>FeeSink: Success</h2>
    <p>Token:</p>
    <pre style="background:#f6f6f6; padding:12px; border-radius:8px;">{token}</pre>
    <p>You can use it as Bearer token for API calls.</p>
  </body>
</html>
"""
    return 200, [("Content-Type", "text/html; charset=utf-8")], html.encode("utf-8")


def handle_get_me(app, environ):
    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None
    app.storage.ensure_account(account_id)
    return json_response(200, {"account": {"account_id": account_id}})


def handle_get_accounts_balance(app, environ):
    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None

    try:
        acc = app.storage.ensure_account(account_id)
    except Exception as ex:
        return error(500, "internal_error", "Failed to load account", {"exception": type(ex).__name__})

    return json_response(
        200,
        {
            "account": {
                "account_id": acc.account_id,
                "balance_units": int(acc.balance_units),
                "status": _status_to_public(getattr(acc, "status", None)),
                "units_per_check": 1,
            }
        },
    )


def handle_post_endpoints(app, environ):
    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None

    # endpoints.account_id is FK -> must exist
    try:
        app.storage.ensure_account(account_id)
    except Exception as ex:
        return error(500, "internal_error", "Failed to ensure account", {"exception": type(ex).__name__})

    data, err2 = _read_json_or_400(environ)
    if err2:
        return err2

    url = (data.get("url") or "").strip()
    if not url:
        return error(400, "invalid_request", "Missing 'url'")

    enabled = data.get("enabled", True)
    enabled = bool(enabled) if isinstance(enabled, bool) else str(enabled).strip().lower() not in ("0", "false", "no", "off")

    interval_seconds = data.get("interval_seconds", 300)
    try:
        interval_seconds = int(interval_seconds)
    except Exception:
        return error(400, "invalid_request", "interval_seconds must be int")

    if interval_seconds <= 0:
        return error(400, "invalid_request", "interval_seconds must be > 0")
    if interval_seconds % 60 != 0:
        return error(400, "invalid_request", "interval_seconds must be divisible by 60")

    interval_minutes = interval_seconds // 60

    endpoint = Endpoint(
        endpoint_id=uuid4().hex,
        account_id=account_id,
        url=url,
        interval_minutes=interval_minutes,
        enabled=enabled,
        next_check_at=_now_utc(),
        paused_reason=None if enabled else PausedReason.MANUAL,
    )

    try:
        created = app.storage.add_endpoint(endpoint)
    except Conflict as ex:
        return error(409, "conflict", "Endpoint conflict", {"reason": str(ex)})
    except ValidationError as ex:
        return error(400, "invalid_request", str(ex))
    except Exception as ex:
        return error(500, "internal_error", "Failed to add endpoint", {"exception": type(ex).__name__})

    return json_response(
        201,
        {
            "endpoint": {
                "endpoint_id": created.endpoint_id,
                "url": created.url,
                "interval_seconds": int(created.interval_minutes) * 60,
                "enabled": bool(created.enabled),
            }
        },
    )


def _run_http_check(url: str, timeout_seconds: int) -> tuple[CheckResult, Optional[int], int, Optional[ErrorClass]]:
    t0 = time.monotonic()
    http_status: Optional[int] = None
    err_class: Optional[ErrorClass] = None
    result = CheckResult.FAIL

    req = urllib.request.Request(
        url=url,
        method="GET",
        headers={"User-Agent": "FeeSink/1.0"},
    )

    try:
        with urllib.request.urlopen(req, timeout=float(timeout_seconds)) as resp:
            http_status = int(getattr(resp, "status", 0) or 0) or None
            if http_status is not None and 200 <= http_status <= 299:
                result = CheckResult.OK
            else:
                result = CheckResult.FAIL
                err_class = ErrorClass.HTTP_NON_2XX
    except urllib.error.HTTPError as e:
        http_status = int(getattr(e, "code", 0) or 0) or None
        result = CheckResult.FAIL
        err_class = ErrorClass.HTTP_NON_2XX
    except urllib.error.URLError as e:
        # DNS / connect / TLS often appear here; keep deterministic bucket.
        result = CheckResult.FAIL
        err_class = ErrorClass.CONNECT
    except socket.timeout:
        result = CheckResult.TIMEOUT
        err_class = ErrorClass.TIMEOUT
    except Exception:
        result = CheckResult.FAIL
        err_class = ErrorClass.UNKNOWN

    latency_ms = int((time.monotonic() - t0) * 1000)
    if latency_ms < 0:
        latency_ms = 0
    return result, http_status, latency_ms, err_class


def handle_post_checks(app, environ):
    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None

    data, err2 = _read_json_or_400(environ)
    if err2:
        return err2

    endpoint_id = (data.get("endpoint_id") or "").strip()
    if not endpoint_id:
        return error(400, "invalid_request", "Missing 'endpoint_id'")

    timeout_seconds = data.get("timeout_seconds", 10)
    try:
        timeout_seconds = int(timeout_seconds)
    except Exception:
        return error(400, "invalid_request", "timeout_seconds must be int")
    if timeout_seconds <= 0 or timeout_seconds > 60:
        return error(400, "invalid_request", "timeout_seconds must be in [1..60]")

    # Optional dedup_key to allow retry without double-charge
    dedup_key = (data.get("dedup_key") or "").strip()
    if not dedup_key:
        now = _now_utc()
        dedup_key = f"manual:{endpoint_id}:{utc_iso(now)}:{uuid4().hex}"

    try:
        ep = app.storage.get_endpoint(endpoint_id)
    except NotFound:
        return error(404, "not_found", "Endpoint not found")
    except Exception as ex:
        return error(500, "internal_error", "Failed to load endpoint", {"exception": type(ex).__name__})

    if str(ep.account_id) != str(account_id):
        return error(404, "not_found", "Endpoint not found")

    # FACT: execute the check first
    result, http_status, latency_ms, err_class = _run_http_check(ep.url, timeout_seconds)

    now = _now_utc()
    event = CheckEvent(
        endpoint_id=endpoint_id,
        ts=now,
        result=result,
        latency_ms=latency_ms,
        http_status=http_status,
        error_class=err_class,
        units_charged=1,
    )

    try:
        event.validate()
    except Exception as ex:
        return error(400, "invalid_request", "CheckEvent validation failed", {"exception": type(ex).__name__})

    # P0: charge strictly after the check fact exists (atomic record + charge in storage)
    try:
        ch = app.storage.record_check_and_charge(
            account_id=account_id,
            event=event,
            charge_units=1,
            dedup_key=dedup_key,
        )
    except Conflict as ex:
        # insufficient balance_units is Conflict in SQLiteChecksMixin
        return error(409, "insufficient_balance", "Not enough units", {"reason": str(ex)})
    except ValidationError as ex:
        return error(400, "invalid_request", str(ex))
    except Exception as ex:
        return error(500, "internal_error", "Failed to record check and charge", {"exception": type(ex).__name__})

    return json_response(
        201,
        {
            "check": {
                "endpoint_id": endpoint_id,
                "dedup_key": dedup_key,
                "result": str(result.value),
                "http_status": http_status,
                "latency_ms": int(latency_ms),
                "error_class": (str(err_class.value) if err_class is not None else None),
                "charged": bool(ch.inserted),
                "new_balance_units": int(ch.new_balance_units),
            }
        },
    )


def handle_patch_endpoint(app, environ, endpoint_id: str):
    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None

    data, err2 = _read_json_or_400(environ)
    if err2:
        return err2

    url = data.get("url")
    if url is not None:
        url = str(url).strip()
        if not url:
            return error(400, "invalid_request", "url must be non-empty")

    enabled = data.get("enabled")
    if enabled is not None:
        enabled = bool(enabled) if isinstance(enabled, bool) else str(enabled).strip().lower() not in ("0", "false", "no", "off")

    interval_seconds = data.get("interval_seconds")
    if interval_seconds is not None:
        try:
            interval_seconds = int(interval_seconds)
        except Exception:
            return error(400, "invalid_request", "interval_seconds must be int")
        if interval_seconds <= 0:
            return error(400, "invalid_request", "interval_seconds must be > 0")
        if interval_seconds % 60 != 0:
            return error(400, "invalid_request", "interval_seconds must be divisible by 60")

    try:
        current = app.storage.get_endpoint(endpoint_id)
    except NotFound:
        return error(404, "not_found", "Endpoint not found")
    except Exception as ex:
        return error(500, "internal_error", "Failed to load endpoint", {"exception": type(ex).__name__})

    if str(current.account_id) != str(account_id):
        return error(404, "not_found", "Endpoint not found")

    updated = current
    if url is not None:
        updated = replace(updated, url=url)
    if interval_seconds is not None:
        updated = replace(updated, interval_minutes=int(interval_seconds) // 60)
    if enabled is not None:
        updated = replace(updated, enabled=bool(enabled), paused_reason=None if enabled else PausedReason.MANUAL)

    try:
        _ = app.storage.update_endpoint(updated)
    except Conflict as ex:
        return error(409, "conflict", "Endpoint conflict", {"reason": str(ex)})
    except ValidationError as ex:
        return error(400, "invalid_request", str(ex))
    except NotFound:
        return error(404, "not_found", "Endpoint not found")
    except Exception as ex:
        return error(500, "internal_error", "Failed to update endpoint", {"exception": type(ex).__name__})

    return json_response(200, {"ok": True})


def handle_delete_endpoint(app, environ, endpoint_id: str):
    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None

    try:
        app.storage.delete_endpoint(account_id=account_id, endpoint_id=endpoint_id)
    except NotFound:
        return error(404, "not_found", "Endpoint not found")
    except Conflict as ex:
        return error(409, "conflict", "Endpoint conflict", {"reason": str(ex)})
    except Exception as ex:
        return error(500, "internal_error", "Failed to delete endpoint", {"exception": type(ex).__name__})

    return json_response(200, {"ok": True})


def handle_post_alerts_test(app, environ):
    return json_response(200, {"ok": True})


def handle_post_topups_dev(app, environ):
    if (app.topup_mode or "").strip().lower() not in ("dev", "development"):
        return error(403, "forbidden", "Dev topups are disabled")

    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None

    data, err2 = _read_json_or_400(environ)
    if err2:
        return err2

    amount_usdt_raw = data.get("amount_usdt")
    if amount_usdt_raw is None:
        return error(400, "invalid_request", "Missing 'amount_usdt'")

    try:
        amount_usdt = Decimal(str(amount_usdt_raw))
    except Exception:
        return error(400, "invalid_request", "'amount_usdt' must be numeric")

    if amount_usdt < MIN_TOPUP_USDT:
        return error(400, "invalid_request", f"amount_usdt must be >= {MIN_TOPUP_USDT}")

    units = int(credited_units(amount_usdt))
    tx_hash = f"dev:{account_id}:{_now_utc().isoformat()}"

    try:
        app.storage.ensure_account(account_id)
        res = app.storage.credit_topup(
            topup=app.make_topup(account_id=account_id, tx_hash=tx_hash, amount_usdt=amount_usdt, credited_units=units)
        )
    except Exception as ex:
        return error(500, "internal_error", "Failed to credit topup", {"exception": type(ex).__name__})

    return json_response(
        200,
        {
            "ok": True,
            "topup": {
                "account_id": account_id,
                "tx_hash": tx_hash,
                "amount_usdt": str(amount_usdt),
                "credited_units": units,
                "inserted": bool(getattr(res, "inserted", True)),
            },
        },
    )
