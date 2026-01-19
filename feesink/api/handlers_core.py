# FeeSink API core handlers (HTTP endpoints + dev topups)
# FEESINK-API-HANDLERS-CORE v2026.01.19-01

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple

from feesink.api._http import (
    UTC,
    error,
    get_query_param,
    json_response,
    read_json,
)
from feesink.config.canon import MIN_TOPUP_USDT, credited_units


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _auth_account(app, environ) -> Tuple[Optional[str], Optional[Tuple[int, list, bytes]]]:
    account_id, err = app.auth_account_id(environ)
    return account_id, err


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
    Returns: account_id, balance_units, status
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
                "status": str(acc.status),
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

    # Minimal MVP fields: url only
    try:
        endpoint_id = app.storage.add_endpoint(account_id=account_id, url=url)
    except Exception as ex:
        return error(500, "internal_error", "Failed to add endpoint", {"exception": type(ex).__name__})

    return json_response(201, {"endpoint": {"endpoint_id": endpoint_id, "url": url}})


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

    try:
        ok = app.storage.update_endpoint(account_id=account_id, endpoint_id=endpoint_id, url=url)
    except Exception as ex:
        return error(500, "internal_error", "Failed to update endpoint", {"exception": type(ex).__name__})

    if not ok:
        return error(404, "not_found", "Endpoint not found")

    return json_response(200, {"ok": True})


def handle_delete_endpoint(app, environ, endpoint_id: str):
    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None

    try:
        ok = app.storage.delete_endpoint(account_id=account_id, endpoint_id=endpoint_id)
    except Exception as ex:
        return error(500, "internal_error", "Failed to delete endpoint", {"exception": type(ex).__name__})

    if not ok:
        return error(404, "not_found", "Endpoint not found")

    return json_response(200, {"ok": True})


def handle_post_alerts_test(app, environ):
    # MVP stub: accept call and return ok (no external integrations here)
    return json_response(200, {"ok": True})


def handle_post_topups_dev(app, environ):
    # DEV only: allows manual topup via API (kept because project is prepaid-only)
    if (app.topup_mode or "dev").lower() != "dev":
        return error(403, "forbidden", "Topups are disabled in this mode")

    account_id, err = _auth_account(app, environ)
    if err:
        return err
    assert account_id is not None

    data, err2 = read_json(environ)
    if err2:
        return err2

    amount_raw = data.get("amount_usdt")
    if amount_raw is None:
        return error(400, "invalid_request", "Missing 'amount_usdt'")

    try:
        amount = Decimal(str(amount_raw)).quantize(Decimal("1"))
    except Exception:
        return error(400, "invalid_request", "Invalid 'amount_usdt'")

    if amount < MIN_TOPUP_USDT:
        return error(400, "invalid_request", "Topup amount below minimum", {"min_usdt": str(MIN_TOPUP_USDT)})

    try:
        cu = int(credited_units(amount))
    except Exception as ex:
        return error(400, "invalid_request", "Unable to convert amount_usdt to units", {"exception": type(ex).__name__})

    # Create TopUp model
    from feesink.domain.models import TopUp  # type: ignore

    tx_hash = f"dev:{account_id}:{int(_now_utc().timestamp())}"
    topup = TopUp(
        account_id=account_id,
        tx_hash=tx_hash,
        amount_usdt=amount,
        credited_units=cu,
        ts=_now_utc(),
    )
    try:
        topup.validate()
    except Exception as ex:
        return error(400, "invalid_request", "TopUp validation failed", {"exception": type(ex).__name__})

    try:
        res = app.storage.credit_topup(topup)
    except Exception as ex:
        return error(500, "internal_error", "Failed to credit topup", {"exception": type(ex).__name__})

    return json_response(
        200,
        {
            "ok": True,
            "topup": {
                "account_id": account_id,
                "tx_hash": tx_hash,
                "amount_usdt": str(amount),
                "credited_units": cu,
                "inserted": bool(getattr(res, "inserted", False)),
            },
        },
    )
