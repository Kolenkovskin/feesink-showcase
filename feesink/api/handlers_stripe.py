# feesink/api/handlers_stripe.py
# FEESINK-API-HANDLERS-STRIPE v2026.01.20-03

from __future__ import annotations

import json
import os
import traceback
import urllib.parse
from datetime import datetime
from decimal import Decimal
from typing import Optional

from feesink.api._http import UTC, error, get_bearer_token, json_response, read_raw_body, utc_iso
from feesink.api._stripe import (
    stripe_api_get_json,
    stripe_api_post_form,
    stripe_verify_signature,
)
from feesink.config.canon import MIN_TOPUP_USDT, USDT_TO_UNITS_RATE


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _new_request_id(now: datetime) -> str:
    # stdlib-only, sufficiently unique per process + timestamp
    # Example: req_2026-01-19T09:00:00.123456Z_pid12345
    return f"req_{utc_iso(now)}_pid{os.getpid()}"


def _require_self_issued_token(environ) -> str:
    """
    Self-issued token canon:
    - user creates token themselves
    - token identifies account_id
    - token is used as Bearer token for API calls
    """
    token = (get_bearer_token(environ) or "").strip()
    if not token:
        raise ValueError("missing_token")
    return token


def _token_to_account_id(token: str) -> str:
    # canon: token == account_id
    return token


def _to_int_units(amount_usdt: Decimal) -> int:
    # 1 USDT -> USDT_TO_UNITS_RATE units
    # Use quantize via int() after multiplication.
    return int((amount_usdt * USDT_TO_UNITS_RATE).to_integral_value())


def _safe_storage_db_path(app) -> Optional[str]:
    """
    Best-effort introspection to prove which DB the service is writing to.
    We do NOT enforce any structure here (no brittle invariants).
    """
    try:
        st = getattr(app, "storage", None)
        if st is None:
            return None
        schema = getattr(st, "_schema", None)
        if schema is None:
            return None
        cfg = getattr(schema, "_config", None)
        if cfg is None:
            return None
        v = getattr(cfg, "db_path", None)
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return None
    except Exception:
        return None


def _jlog(payload: dict) -> None:
    """
    Deterministic JSON log with flush=True.
    Render may buffer stdout; flush reduces "invisible logs" risk.
    """
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def handle_post_stripe_checkout_sessions(app, environ):
    now = _now_utc()
    request_id = _new_request_id(now)
    db_path = _safe_storage_db_path(app)

    # Requires Authorization: Bearer <token>
    try:
        token = _require_self_issued_token(environ)
    except ValueError:
        _jlog(
            {
                "provider": "stripe",
                "decision": "reject_missing_bearer",
                "request_id": request_id,
                "db_path": db_path,
            }
        )
        return error(401, "unauthorized", "Missing Bearer token", {"request_id": request_id})

    account_id = _token_to_account_id(token)

    # P0 guard: checkout_session without account_id is forbidden
    assert account_id, "checkout_session without account_id is forbidden"

    secret_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    price_id = (os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip()
    success_url = (os.getenv("STRIPE_SUCCESS_URL") or "").strip()
    cancel_url = (os.getenv("STRIPE_CANCEL_URL") or "").strip()

    if not secret_key:
        return error(500, "internal_error", "STRIPE_SECRET_KEY is not set", {"request_id": request_id})
    if not price_id:
        return error(500, "internal_error", "STRIPE_PRICE_ID_EUR_50 is not set", {"request_id": request_id})
    if not success_url:
        return error(500, "internal_error", "STRIPE_SUCCESS_URL is not set", {"request_id": request_id})
    if not cancel_url:
        return error(500, "internal_error", "STRIPE_CANCEL_URL is not set", {"request_id": request_id})

    form = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        # Backup channel: Stripe exposes client_reference_id directly in session
        "client_reference_id": str(account_id),
        # Primary channel: metadata for our webhook
        "metadata[token]": token,
        "metadata[account_id]": str(account_id),
        "metadata[price_id]": str(price_id),
    }

    obj, err2 = stripe_api_post_form(secret_key, "/v1/checkout/sessions", form)
    if err2 or not obj:
        _jlog(
            {
                "provider": "stripe",
                "decision": "stripe_create_failed",
                "request_id": request_id,
                "db_path": db_path,
                "reason": err2,
                "account_id": str(account_id),
            }
        )
        return error(
            502,
            "bad_gateway",
            "Stripe request failed",
            {"request_id": request_id, "reason": err2},
        )

    session_id = (obj.get("id") or "").strip()
    session_url = (obj.get("url") or "").strip()
    customer_id = obj.get("customer")
    if isinstance(customer_id, str):
        customer_id = customer_id.strip()
    else:
        customer_id = None

    if not session_id or not session_url:
        _jlog(
            {
                "provider": "stripe",
                "decision": "stripe_response_missing_id_or_url",
                "request_id": request_id,
                "db_path": db_path,
                "stripe_id": session_id or None,
                "account_id": str(account_id),
            }
        )
        return error(
            502,
            "bad_gateway",
            "Stripe response missing session id/url",
            {"request_id": request_id, "stripe_id": session_id or None},
        )

    if not hasattr(app.storage, "upsert_stripe_link"):
        _jlog(
            {
                "provider": "stripe",
                "decision": "stripe_link_persisted",
                "stripe_link_persisted": False,
                "reason": "storage_missing_method",
                "request_id": request_id,
                "db_path": db_path,
                "session_id": session_id,
                "account_id": str(account_id),
            }
        )
        return error(
            500,
            "internal_error",
            "Storage does not support stripe_links",
            {"request_id": request_id},
        )

    try:
        # Note: customer_id may be None at this stage. It is still useful to persist session->account mapping.
        app.storage.upsert_stripe_link(  # type: ignore[attr-defined]
            account_id=str(account_id),
            stripe_session_id=session_id,
            stripe_customer_id=customer_id,
        )
        _jlog(
            {
                "provider": "stripe",
                "decision": "stripe_link_persisted",
                "stripe_link_persisted": True,
                "request_id": request_id,
                "db_path": db_path,
                "session_id": session_id,
                "account_id": str(account_id),
                "customer_id": customer_id,
            }
        )
    except Exception as ex:
        # Critical: log the real cause (one-line JSON + traceback)
        msg = str(ex).replace("\n", "\\n")
        _jlog(
            {
                "provider": "stripe",
                "decision": "stripe_link_persisted",
                "stripe_link_persisted": False,
                "request_id": request_id,
                "db_path": db_path,
                "session_id": session_id,
                "account_id": str(account_id),
                "customer_id": customer_id,
                "exc_type": type(ex).__name__,
                "exc_msg": msg,
            }
        )
        print(traceback.format_exc(), flush=True)
        return error(
            500,
            "internal_error",
            "Failed to store stripe link",
            {"request_id": request_id, "exception": type(ex).__name__, "cause": msg},
        )

    return json_response(
        200,
        {"request_id": request_id, "checkout_session": {"id": session_id, "url": session_url}},
    )


def _extract_account_id_from_metadata(md: object) -> Optional[str]:
    if not isinstance(md, dict):
        return None
    v = md.get("account_id")
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return None


def _extract_price_id_from_metadata(md: object) -> Optional[str]:
    if not isinstance(md, dict):
        return None
    v = md.get("price_id")
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return None


def handle_post_webhooks_stripe(app, environ):
    now = _now_utc()
    request_id = _new_request_id(now)
    db_path = _safe_storage_db_path(app)

    whsec = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    sig_header = (environ.get("HTTP_STRIPE_SIGNATURE") or "").strip()
    raw = read_raw_body(environ)

    if not stripe_verify_signature(raw, sig_header, whsec):
        _jlog(
            {
                "provider": "stripe",
                "decision": "signature_fail",
                "request_id": request_id,
                "db_path": db_path,
            }
        )
        return error(400, "invalid_signature", "Invalid Stripe signature")

    try:
        event = json.loads(raw.decode("utf-8"))
    except Exception:
        _jlog(
            {
                "provider": "stripe",
                "decision": "invalid_json",
                "request_id": request_id,
                "db_path": db_path,
            }
        )
        return error(400, "invalid_request", "Invalid JSON body")

    event_id = (event.get("id") or "").strip() or None
    event_type = (event.get("type") or "").strip() or None
    if not event_id:
        _jlog(
            {
                "provider": "stripe",
                "decision": "missing_event_id",
                "request_id": request_id,
                "db_path": db_path,
            }
        )
        return error(400, "invalid_request", "Missing Stripe event id")

    if event_type != "checkout.session.completed":
        _jlog(
            {
                "provider": "stripe",
                "decision": "ignored_event",
                "request_id": request_id,
                "db_path": db_path,
                "event_id": event_id,
                "event_type": event_type,
            }
        )
        return json_response(200, {"ok": True, "ignored": True})

    # Store provider event payload (idempotent)
    try:
        if hasattr(app.storage, "insert_provider_event"):
            app.storage.insert_provider_event(  # type: ignore[attr-defined]
                provider="stripe",
                provider_event_id=event_id,
                event_type=event_type or "unknown",
                raw_json=raw.decode("utf-8"),
                received_at_utc=utc_iso(_now_utc()),
            )
    except Exception as ex:
        _jlog(
            {
                "provider": "stripe",
                "decision": "provider_event_store_fail",
                "request_id": request_id,
                "db_path": db_path,
                "event_id": event_id,
                "exc": type(ex).__name__,
            }
        )

    data_obj = (((event.get("data") or {}).get("object")) or {})
    session_id = (data_obj.get("id") or "").strip() or None
    payment_status = (data_obj.get("payment_status") or "").strip() or None
    customer_id = (data_obj.get("customer") or "").strip() if isinstance(data_obj.get("customer"), str) else None

    if not session_id:
        _jlog(
            {
                "provider": "stripe",
                "decision": "missing_session_id",
                "request_id": request_id,
                "db_path": db_path,
                "event_id": event_id,
            }
        )
        return error(400, "invalid_request", "Missing session id")

    metadata = data_obj.get("metadata") or {}
    account_id = _extract_account_id_from_metadata(metadata)

    # 2) fallback to stripe_links lookup
    if not account_id:
        if hasattr(app.storage, "resolve_account_by_stripe_session"):
            try:
                account_id = app.storage.resolve_account_by_stripe_session(session_id)  # type: ignore[attr-defined]
            except Exception as ex:
                msg = str(ex).replace("\n", "\\n")
                _jlog(
                    {
                        "provider": "stripe",
                        "decision": "resolve_account_by_stripe_session_fail",
                        "request_id": request_id,
                        "db_path": db_path,
                        "event_id": event_id,
                        "session_id": session_id,
                        "exc_type": type(ex).__name__,
                        "exc_msg": msg,
                    }
                )
                print(traceback.format_exc(), flush=True)
                account_id = None

    # 3) final fallback: Stripe API GET session and read metadata.account_id OR client_reference_id
    fetched_meta_price_id = None
    fetched_client_ref = None
    if not account_id:
        try:
            secret_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
            if secret_key:
                url = f"/v1/checkout/sessions/{urllib.parse.quote(session_id)}"
                obj2, err3 = stripe_api_get_json(secret_key, url)
                if not err3 and obj2:
                    md2 = obj2.get("metadata") or {}
                    account_id = _extract_account_id_from_metadata(md2) or None
                    fetched_meta_price_id = _extract_price_id_from_metadata(md2) or None
                    cr = obj2.get("client_reference_id")
                    if isinstance(cr, str):
                        fetched_client_ref = cr.strip() or None
                    if not account_id and fetched_client_ref:
                        # backup channel: client_reference_id is our token/account_id
                        account_id = fetched_client_ref
        except Exception:
            account_id = None

    if not account_id:
        _jlog(
            {
                "provider": "stripe",
                "decision": "no_account_resolved",
                "request_id": request_id,
                "db_path": db_path,
                "event_id": event_id,
                "session_id": session_id,
                "hint": "missing_metadata_and_stripe_links",
            }
        )
        return json_response(200, {"ok": True})

    # Best-effort: persist stripe_links from webhook once we have session_id + account_id (+ customer_id if present)
    if hasattr(app.storage, "upsert_stripe_link"):
        try:
            app.storage.upsert_stripe_link(  # type: ignore[attr-defined]
                account_id=str(account_id),
                stripe_session_id=session_id,
                stripe_customer_id=customer_id,
            )
        except Exception:
            # Do not fail webhook on persistence problems.
            pass

    # Payment must be paid
    if payment_status != "paid":
        _jlog(
            {
                "provider": "stripe",
                "decision": "not_paid",
                "request_id": request_id,
                "db_path": db_path,
                "event_id": event_id,
                "session_id": session_id,
                "payment_status": payment_status,
                "account_id": str(account_id),
            }
        )
        return json_response(200, {"ok": True})

    # Determine credited units from price mapping
    env_price_id = (os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip()
    meta_price_id = _extract_price_id_from_metadata(metadata) or fetched_meta_price_id

    # Canon mapping: use env price id if meta is missing (and treat mismatch as warning)
    price_id_effective = meta_price_id or env_price_id

    # Default: EUR 50 => 5000 units (canonical)
    credited_units = 5000

    # Keep for future compatibility (USDT mode placeholder)
    if isinstance(MIN_TOPUP_USDT, Decimal) and MIN_TOPUP_USDT > 0:
        _ = _to_int_units(MIN_TOPUP_USDT)

    # Idempotency key based on provider event id
    tx_hash = f"stripe:{event_id}"

    # Credit account
    try:
        if hasattr(app.storage, "credit_account"):
            res = app.storage.credit_account(  # type: ignore[attr-defined]
                account_id=str(account_id),
                credited_units=int(credited_units),
                provider="stripe",
                provider_event_id=event_id,
                tx_hash=tx_hash,
                meta_json=json.dumps(
                    {
                        "event_type": event_type,
                        "session_id": session_id,
                        "customer_id": customer_id,
                        "meta_price_id": meta_price_id,
                        "env_price_id": env_price_id,
                        "price_id_effective": price_id_effective,
                        "account_id_source": (
                            "metadata"
                            if _extract_account_id_from_metadata(metadata)
                            else ("stripe_links" if hasattr(app.storage, "resolve_account_by_stripe_session") else "stripe_api_get")
                        ),
                        "fetched_client_reference_id": fetched_client_ref,
                    },
                    ensure_ascii=False,
                ),
            )
            _jlog(
                {
                    "provider": "stripe",
                    "decision": "credited",
                    "request_id": request_id,
                    "db_path": db_path,
                    "event_id": event_id,
                    "session_id": session_id,
                    "account_id": str(account_id),
                    "credited_units": int(credited_units),
                    "tx_hash": tx_hash,
                    "inserted": bool(getattr(res, "inserted", True)),
                }
            )
    except Exception as ex:
        msg = str(ex).replace("\n", "\\n")
        _jlog(
            {
                "provider": "stripe",
                "decision": "credit_fail",
                "request_id": request_id,
                "db_path": db_path,
                "event_id": event_id,
                "session_id": session_id,
                "account_id": str(account_id),
                "tx_hash": tx_hash,
                "exc_type": type(ex).__name__,
                "exc_msg": msg,
            }
        )
        print(traceback.format_exc(), flush=True)

    return json_response(200, {"ok": True})
