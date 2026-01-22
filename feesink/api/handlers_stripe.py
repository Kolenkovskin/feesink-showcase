# file: feesink/api/handlers_stripe.py
# FEESINK-API-HANDLERS-STRIPE v2026.01.22-03

from __future__ import annotations

import json
import os
import traceback
import urllib.parse
from datetime import datetime
from decimal import Decimal

from feesink.api._http import UTC, error, get_bearer_token, json_response, read_json, read_raw_body, utc_iso
from feesink.api._stripe import (
    stripe_api_get_json,
    stripe_api_post_form,
    stripe_verify_signature,
)
from feesink.domain.models import TopUp


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _new_request_id(now: datetime) -> str:
    return f"req_{utc_iso(now)}_pid{os.getpid()}"


def _require_self_issued_token(environ) -> str:
    token = (get_bearer_token(environ) or "").strip()
    if not token:
        raise ValueError("missing bearer token")
    return token


def _token_to_account_id(token: str) -> str:
    return token.strip()


def handle_post_stripe_checkout_sessions(environ, app) -> dict:
    """
    POST /v1/stripe/checkout_sessions

    P0 note (v2026.01.22-03):
    - Some edge proxies drop Authorization (and even custom headers) before WSGI environ.
    - For checkout_sessions ONLY (UI bootstrap), we accept JSON body {"token": "..."} as fallback.
    - Checks/usage still require Bearer token canon.
    """
    now = _now_utc()
    request_id = _new_request_id(now)

    token = None
    token_source = None

    # Primary: API canon (Authorization: Bearer ...)
    try:
        token = _require_self_issued_token(environ)
        token_source = "header"
    except Exception:
        token = None

    # UI bootstrap fallback: accept {"token": "..."} JSON body for checkout_sessions only.
    if not token:
        body, err = read_json(environ)
        if isinstance(body, dict):
            token = (body.get("token") or "").strip() or None
            if token:
                token_source = "body"

    if not token:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "missing_token",
                    "has_http_authorization": bool(environ.get("HTTP_AUTHORIZATION")),
                    "has_redirect_http_authorization": bool(environ.get("REDIRECT_HTTP_AUTHORIZATION")),
                    "has_x_feesink_token": bool(environ.get("HTTP_X_FEESINK_TOKEN")),
                    "content_type": environ.get("CONTENT_TYPE"),
                    "content_length": environ.get("CONTENT_LENGTH"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return error(401, "unauthorized", "Missing Bearer token", {"request_id": request_id})

    account_id = _token_to_account_id(token)

    if not account_id:
        return error(400, "bad_request", "Empty account_id", {"request_id": request_id})

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
        "metadata[account_id]": account_id,
        "metadata[price_id]": price_id,
        "metadata[request_id]": request_id,
        "metadata[token_source]": token_source or "unknown",
    }

    try:
        obj, err = stripe_api_post_form(secret_key, "/v1/checkout/sessions", form)
    except Exception as ex:
        return error(502, "bad_gateway", "Stripe API call failed", {"request_id": request_id, "exc": str(ex)})

    if err or not isinstance(obj, dict):
        return error(502, "bad_gateway", "Stripe API call failed", {"request_id": request_id, "err": err})

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
            {"request_id": request_id},
        )

    stripe_link_persisted = False
    try:
        app.storage.upsert_stripe_link(
            stripe_session_id=session_id,
            stripe_customer_id=customer_id,
            account_id=account_id,
            created_ts=now,
        )
        stripe_link_persisted = True
    except Exception as ex:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "stripe_link_store_fail",
                    "session_id": session_id,
                    "account_id": account_id,
                    "exc": type(ex).__name__,
                    "traceback": traceback.format_exc(limit=60),
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
            "token_source": token_source,
            "stripe_link_persisted": stripe_link_persisted,
            "checkout_session": {"id": session_id, "url": session_url},
        },
    )


def handle_post_webhooks_stripe(environ, app) -> dict:
    """
    POST /v1/webhooks/stripe

    Handles Stripe webhooks:
    - checkout.session.completed (paid)

    P0: fail-hard on any credit-chain failure so Stripe retries.
    """
    now = _now_utc()
    request_id = _new_request_id(now)
    db_path = getattr(getattr(getattr(app.storage, "_schema", None), "_config", None), "db_path", None)
    stripe_mode = (os.getenv("FEESINK_STRIPE_MODE") or "").strip() or None

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
                    "traceback": traceback.format_exc(limit=40),
                    "db_path": db_path,
                    "stripe_mode": stripe_mode,
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

    data_obj = (((evt.get("data") or {}).get("object") or {}) if isinstance(evt.get("data"), dict) else {})
    session_id = (data_obj.get("id") or "").strip()
    payment_status = (data_obj.get("payment_status") or "").strip()
    metadata = data_obj.get("metadata") or {}
    meta_account_id = None
    meta_price_id = None
    if isinstance(metadata, dict):
        meta_account_id = (metadata.get("account_id") or "").strip() or None
        meta_price_id = (metadata.get("price_id") or "").strip() or None

    # Proof point #1: receipt
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
                "db_path": db_path,
                "stripe_mode": stripe_mode,
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
                    "db_path": db_path,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return error(500, "internal_error", "Missing session_id in checkout.session.completed", {"request_id": request_id})

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

    # Proof point #2: provider_event store MUST succeed (fail-hard)
    try:
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
                    "traceback": traceback.format_exc(limit=60),
                    "db_path": db_path,
                    "stripe_mode": stripe_mode,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return error(
            500,
            "internal_error",
            "Failed to store provider event",
            {"request_id": request_id, "event_id": event_id, "decision": "provider_event_store_fail"},
        )

    # Resolve account_id
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
                        "traceback": traceback.format_exc(limit=60),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            return error(
                500,
                "internal_error",
                "Failed to resolve account_id",
                {"request_id": request_id, "event_id": event_id, "decision": "resolve_account_fail"},
            )

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
                    "db_path": db_path,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return error(
            500,
            "internal_error",
            "No account_id resolved for paid session",
            {"request_id": request_id, "event_id": event_id, "decision": "no_account_resolved"},
        )

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
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return error(500, "internal_error", "Storage credit_topup not implemented", {"request_id": request_id})

    # Proof point #3: credit must succeed (fail-hard)
    try:
        topup = TopUp(
            account_id=account_id,
            tx_hash=tx_hash,
            amount_usdt=Decimal("0"),
            credited_units=credited_units,
            ts=now,
        )
        res = app.storage.credit_topup(topup)

        ok = bool(getattr(res, "ok", False))
        if not ok:
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "request_id": request_id,
                        "decision": "credit_not_ok",
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
                        "db_path": db_path,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            return error(
                500,
                "internal_error",
                "Credit did not succeed",
                {"request_id": request_id, "event_id": event_id, "tx_hash": tx_hash, "decision": "credit_not_ok"},
            )

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
        return json_response(200, {"ok": True, "request_id": request_id, "tx_hash": tx_hash})

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
                    "traceback": traceback.format_exc(limit=80),
                    "db_path": db_path,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return error(
            500,
            "internal_error",
            "Credit failed",
            {"request_id": request_id, "event_id": event_id, "tx_hash": tx_hash, "decision": "credit_fail"},
        )


def handle_get_stripe_success(environ, app) -> dict:
    qs = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
    account_id = (qs.get("account_id", [""])[0] or "").strip()
    request_id = (qs.get("request_id", [""])[0] or "").strip()

    if not account_id:
        return error(400, "bad_request", "Missing account_id", {"request_id": request_id})

    return json_response(
        200,
        {
            "ok": True,
            "account_id": account_id,
            "request_id": request_id,
            "note": "Payment received. Webhook will credit units. Check /v1/accounts/balance.",
        },
    )


def handle_get_stripe_cancel(environ, app) -> dict:
    return json_response(200, {"ok": True})
