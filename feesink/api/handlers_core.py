# FeeSink API core handlers (HTTP endpoints + dev topups)
# FEESINK-API-HANDLERS-CORE v2026.01.26-01

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple
from uuid import uuid4

from feesink.api._http import (
    UTC,
    error,
    get_query_param,
    json_response,
    read_json,
)
from feesink.config.canon import MIN_TOPUP_USDT, credited_units
from feesink.domain.models import Endpoint, PausedReason
from feesink.storage.interfaces import NotFound, ValidationError


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _auth_account(app, environ) -> Tuple[Optional[str], Optional[Tuple[int, list, bytes]]]:
    account_id, err = app.auth_account_id(environ)
    return account_id, err


def _status_to_public(value: object) -> str:
    """
    Normalize internal AccountStatus to public API string.

    Internal may be:
      - enum AccountStatus.ACTIVE
      - string "active"
      - other printable representation
    Public contract:
      "active" | "paused" | "inactive" | "unknown"
    """
    if value is None:
        return "unknown"
    # Enum repr often looks like "AccountStatus.ACTIVE"
    s = str(value).strip()
    if not s:
        return "unknown"
    if "." in s:
        s = s.split(".")[-1]
    s = s.lower()

    if s in ("active", "paused", "inactive"):
        return s
    return "unknown"


def handle_get_ui_success(app, environ):
    token = get_query_param(environ, "token") or ""
    token = token.strip()

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

    # Ensure account exists (idempotent)
    app.storage.ensure_account(account_id)

    return json_response(200, {"account": {"account_id": account_id}})


def handle_get_accounts_balance(app, environ):
    """
    GET /v1/accounts/balance
    Auth: Bearer token
    Returns: account_id, balance_units, status, units_per_check
    """
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

    data, err2 = read_json(environ)
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
    except TypeError as ex:
        return error(
            500,
            "storage_contract_violation",
            "Storage contract mismatch for add_endpoint",
            {"exception": type(ex).__name__, "op": "add_endpoint"},
        )
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


def handle_patch_endpoint(app, environ, endpoint_id: str):
    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None

    data, err2 = read_json(environ)
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

    # Hide existence if endpoint belongs to another account
    if str(current.account_id) != str(account_id):
        return error(404, "not_found", "Endpoint not found")

    updated = current
    if url is not None:
        updated = replace(updated, url=url)

    if interval_seconds is not None:
        updated = replace(updated, interval_minutes=int(interval_seconds) // 60)

    if enabled is not None:
        updated = replace(
            updated,
            enabled=bool(enabled),
            paused_reason=None if enabled else PausedReason.MANUAL,
        )

    try:
        _ = app.storage.update_endpoint(updated)
    except TypeError as ex:
        return error(
            500,
            "storage_contract_violation",
            "Storage contract mismatch for update_endpoint",
            {"exception": type(ex).__name__, "op": "update_endpoint"},
        )
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
    except TypeError as ex:
        return error(
            500,
            "storage_contract_violation",
            "Storage contract mismatch for delete_endpoint",
            {"exception": type(ex).__name__, "op": "delete_endpoint"},
        )
    except Exception as ex:
        return error(500, "internal_error", "Failed to delete endpoint", {"exception": type(ex).__name__})

    return json_response(200, {"ok": True})


def handle_post_alerts_test(app, environ):
    # MVP stub: accept call and return ok (no external integrations here)
    return json_response(200, {"ok": True})


def handle_post_topups_dev(app, environ):
    # DEV only: allows manual topup via API (kept because project is prepaid-only)
    if (app.topup_mode or "").strip().lower() not in ("dev", "development"):
        return error(403, "forbidden", "Dev topups are disabled")

    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None

    data, err2 = read_json(environ)
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

    # DEV tx hash: time-based but deterministic enough for dev; in live we use provider tx hash
    tx_hash = f"dev:{account_id}:{_now_utc().isoformat()}"

    try:
        # Ensure account exists, then credit
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
