# file: feesink/api/handlers_stripe.py
# FEESINK-API-HANDLERS-STRIPE v2026.01.21-01

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
from feesink.domain.models import TopUp


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
        raise ValueError("missing bearer token")
    return token


def _token_to_account_id(token: str) -> str:
    # Canon: token == account_id (self-issued)
    return token.strip()


def handle_post_stripe_checkout_sessions(environ, app) -> dict:
    """
    POST /v1/stripe/checkout_sessions

    Creates a Stripe Checkout Session for a self-issued token (token == account_id)
    and persists stripe_links mapping (stripe_session_id -> account_id).

    Price is taken ONLY from ENV STRIPE_PRICE_ID_EUR_50.
    """
    now = _now_utc()
    request_id = _new_request_id(now)

    try:
        token = _require_self_issued_token(environ)
    except Exception:
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

    # Put token into success url for a user-friendly confirmation page (landing)
    success_url = success_url
    cancel_url = cancel_url

    metadata = {"account_id": account_id, "price_id": price_id}

    form = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "metadata[account_id]": account_id,
        "metadata[price_id]": price_id,
    }

    try:
        obj = stripe_api_post_form(secret_key, "/v1/checkout/sessions", form)
    except Exception as ex:
        return error(502, "bad_gateway", "Stripe API failed", {"request_id": request_id, "exc": str(ex)})

    session_id = (obj.get("id") or "").strip()
    session_url = (obj.get("url") or "").strip()
    customer_id = obj.get("customer")
    if isinstance(customer_id, str):
        customer_id = customer_id.strip()
    else:
        customer_id = None

    if not session_id or not session_url:
        return error(
            502,
            "bad_gateway",
            "Stripe returned an invalid checkout session",
            {"request_id": request_id, "stripe": {"id": session_id, "url": session_url}},
        )

    stripe_link_persisted = False
    try:
        app.storage.upsert_stripe_link(
            stripe_session_id=session_id,
            stripe_customer_id=customer_id,
            account_id=account_id,
        )
        stripe_link_persisted = True
    except Exception as ex:
        # Do not hide this; it blocks webhook->account resolution.
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "stripe_link_persist_fail",
                    "session_id": session_id,
                    "customer_id": customer_id,
                    "account_id": account_id,
                    "db_path": getattr(getattr(getattr(app.storage, "_schema", None), "_config", None), "db_path", None),
                    "exc": type(ex).__name__,
                    "traceback": traceback.format_exc(limit=30),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return error(500, "internal_error", "Failed to store stripe link", {"request_id": request_id})

    # Log for deterministic correlation
    print(
        json.dumps(
            {
                "provider": "stripe",
                "request_id": request_id,
                "decision": "stripe_link_persisted",
                "session_id": session_id,
                "customer_id": customer_id,
                "account_id": account_id,
                "stripe_link_persisted": stripe_link_persisted,
                "db_path": getattr(getattr(getattr(app.storage, "_schema", None), "_config", None), "db_path", None),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )

    return json_response(
        200,
        {
            "request_id": request_id,
            "checkout_session": {"id": session_id, "url": session_url},
        },
    )


def handle_post_webhooks_stripe(environ, app) -> dict:
    """
    POST /v1/webhooks/stripe

    Handles Stripe webhooks, currently focusing on:
    - checkout.session.completed (paid)

    Chain:
    checkout.session.completed -> resolve account_id -> credit_topup(tx_hash=stripe:evt_...) -> balance_units increases
    """
    now = _now_utc()
    request_id = _new_request_id(now)

    secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not secret:
        return error(500, "internal_error", "STRIPE_WEBHOOK_SECRET is not set", {"request_id": request_id})

    try:
        raw_body = read_raw_body(environ)
    except Exception:
        return error(400, "bad_request", "Missing request body", {"request_id": request_id})

    sig = environ.get("HTTP_STRIPE_SIGNATURE")
    if not sig:
        return error(400, "bad_request", "Missing Stripe-Signature header", {"request_id": request_id})

    try:
        evt = stripe_verify_signature(secret, raw_body, sig)
    except Exception as ex:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "signature_verify_fail",
                    "exc": type(ex).__name__,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return error(400, "bad_request", "Invalid signature", {"request_id": request_id})

    if not isinstance(evt, dict):
        return error(400, "bad_request", "Invalid event payload", {"request_id": request_id})

    event_id = (evt.get("id") or "").strip()
    event_type = (evt.get("type") or "").strip()

    # Parse object
    data_obj = (((evt.get("data") or {}).get("object") or {}) if isinstance(evt.get("data"), dict) else {})
    session_id = (data_obj.get("id") or "").strip()
    payment_status = (data_obj.get("payment_status") or "").strip()
    metadata = data_obj.get("metadata") or {}
    meta_account_id = None
    meta_price_id = None
    if isinstance(metadata, dict):
        meta_account_id = (metadata.get("account_id") or "").strip() or None
        meta_price_id = (metadata.get("price_id") or "").strip() or None

    # Always log receipt (key fields)
    print(
        json.dumps(
            {
                "provider": "stripe",
                "request_id": request_id,
                "decision": "webhook_received",
                "event_id": event_id,
                "event_type": event_type,
                "session_id": session_id,
                "payment_status": payment_status,
                "meta_account_id": meta_account_id,
                "meta_price_id": meta_price_id,
                "db_path": getattr(getattr(getattr(app.storage, "_schema", None), "_config", None), "db_path", None),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )

    if event_type != "checkout.session.completed":
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "ignored_event",
                    "event_id": event_id,
                    "event_type": event_type,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return json_response(200, {"ok": True})

    if not session_id:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "missing_session_id",
                    "event_id": event_id,
                    "event_type": event_type,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return json_response(200, {"ok": True})

    if payment_status != "paid":
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "not_paid",
                    "event_id": event_id,
                    "event_type": event_type,
                    "session_id": session_id,
                    "payment_status": payment_status,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return json_response(200, {"ok": True})

    # Store provider event payload (best-effort, idempotent by provider_event_id)
    try:
        # IMPORTANT: storage contract accepts ONLY (provider, provider_event_id, raw_json)
        app.storage.insert_provider_event(
            provider="stripe",
            provider_event_id=event_id,
            raw_json=json.dumps(evt, ensure_ascii=False),
        )
    except Exception as ex:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "provider_event_store_fail",
                    "event_id": event_id,
                    "exc": type(ex).__name__,
                    "traceback": traceback.format_exc(limit=30),
                    "db_path": getattr(getattr(getattr(app.storage, "_schema", None), "_config", None), "db_path", None),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

    # Resolve account_id:
    account_id = meta_account_id
    resolved_by = "metadata"
    if not account_id:
        try:
            account_id = app.storage.resolve_account_by_stripe_session(session_id)
            if account_id:
                resolved_by = "stripe_links"
        except Exception as ex:
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "request_id": request_id,
                        "decision": "resolve_account_fail",
                        "event_id": event_id,
                        "session_id": session_id,
                        "exc": type(ex).__name__,
                        "traceback": traceback.format_exc(limit=30),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            account_id = None

    if not account_id:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "no_account_resolved",
                    "event_id": event_id,
                    "session_id": session_id,
                    "meta_account_id": meta_account_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return json_response(200, {"ok": True})

    # Credit decision: fixed pack mapping (current canon: EUR 50 => 5000 units)
    # (We keep it deterministic and avoid extra lookups.)
    credited_units = 5000
    tx_hash = f"stripe:{event_id}"

    if not hasattr(app.storage, "credit_topup"):
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "credit_missing_impl",
                    "event_id": event_id,
                    "session_id": session_id,
                    "account_id": account_id,
                    "resolved_by": resolved_by,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return json_response(200, {"ok": True})

    try:
        topup = TopUp(
            account_id=account_id,
            tx_hash=tx_hash,
            amount_usdt=Decimal("0"),
            credited_units=credited_units,
            ts=now,
        )
        res = app.storage.credit_topup(topup)
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "credited",
                    "event_id": event_id,
                    "session_id": session_id,
                    "account_id": account_id,
                    "resolved_by": resolved_by,
                    "tx_hash": tx_hash,
                    "credited_units": credited_units,
                    "credit_result": {
                        "ok": getattr(res, "ok", None),
                        "decision": getattr(res, "decision", None),
                        "credited_units": getattr(res, "credited_units", None),
                        "balance_units": getattr(res, "balance_units", None),
                        "topup_id": getattr(res, "topup_id", None),
                        "account_id": getattr(res, "account_id", None),
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
    except Exception as ex:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "credit_fail",
                    "event_id": event_id,
                    "session_id": session_id,
                    "account_id": account_id,
                    "resolved_by": resolved_by,
                    "tx_hash": tx_hash,
                    "credited_units": credited_units,
                    "exc": type(ex).__name__,
                    "traceback": traceback.format_exc(limit=40),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

    return json_response(200, {"ok": True})
