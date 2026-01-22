# file: feesink/api/handlers_stripe.py
# FEESINK-API-HANDLERS-STRIPE v2026.01.21-02

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
    StripeError,
    create_checkout_session,
    parse_stripe_event,
    verify_stripe_webhook_signature,
)
from feesink.config.canon import UNITS_PER_CHECK
from feesink.domain.models import TopUp


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _new_request_id(now: datetime) -> str:
    # Deterministic-ish enough for logs; exact uniqueness not a billing invariant.
    # (We will later add X-Feesink-Request-Id header + last_credit_tx_hash in balance.)
    return f"req_{now.strftime('%Y%m%dT%H%M%S')}_{os.getpid()}"


def handle_post_stripe_checkout_sessions(environ, app) -> dict:
    """
    POST /v1/stripe/checkout_sessions

    Body:
    {
      "account_id": "self-issued-token"
    }

    Notes (P0):
    - price_id comes ONLY from ENV STRIPE_PRICE_ID_EUR_50
    - account_id is required (token == account_id)
    """
    now = _now_utc()
    request_id = _new_request_id(now)

    auth = get_bearer_token(environ) or ""
    if not auth:
        return error(401, "unauthorized", "Missing Bearer token", {"request_id": request_id})

    # Self-issued token: token == account_id
    account_id = auth.strip()
    if not account_id:
        return error(401, "unauthorized", "Empty token", {"request_id": request_id})

    # Body may contain account_id too, but we treat token as canonical.
    try:
        raw = read_raw_body(environ)
        body = json.loads(raw.decode("utf-8") or "{}")
        if isinstance(body, dict) and body.get("account_id"):
            # Assert: must match token to avoid accidental mismatch.
            if str(body.get("account_id")).strip() != account_id:
                return error(
                    400,
                    "bad_request",
                    "account_id mismatch (token != body.account_id)",
                    {"request_id": request_id},
                )
    except Exception:
        # Body parsing is not critical for canonical token flow.
        body = {}

    try:
        obj = create_checkout_session(
            account_id=account_id,
            request_id=request_id,
        )
    except StripeError as ex:
        return error(502, "bad_gateway", "Stripe API failed", {"request_id": request_id, "exc": str(ex)})
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
            {"request_id": request_id},
        )

    # Persist stripe link (best-effort). This is NOT the credit itself.
    stripe_link_persisted = False
    if hasattr(app.storage, "upsert_stripe_link"):
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
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            # Keep returning session url; user can still pay. Webhook will try metadata first.

    return json_response(
        200,
        {
            "request_id": request_id,
            "account_id": account_id,
            "stripe_link_persisted": stripe_link_persisted,
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
    db_path = (os.getenv("FEESINK_SQLITE_DB") or "").strip() or None
    stripe_mode = (os.getenv("FEESINK_STRIPE_MODE") or "").strip() or None

    secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not secret:
        return error(500, "internal_error", "STRIPE_WEBHOOK_SECRET is not set", {"request_id": request_id})

    raw = read_raw_body(environ)
    sig = (environ.get("HTTP_STRIPE_SIGNATURE") or "").strip()
    if not sig:
        return error(400, "bad_request", "Missing Stripe-Signature header", {"request_id": request_id})

    # Verify signature first (P0).
    try:
        verify_stripe_webhook_signature(payload=raw, sig_header=sig, secret=secret)
    except Exception as ex:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "bad_signature",
                    "db_path": db_path,
                    "stripe_mode": stripe_mode,
                    "exc": type(ex).__name__,
                    "traceback": traceback.format_exc(limit=40),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return error(400, "bad_request", "Invalid Stripe signature", {"request_id": request_id})

    # Parse event
    try:
        evt = parse_stripe_event(raw)
    except Exception as ex:
        return error(400, "bad_request", "Invalid Stripe event payload", {"request_id": request_id, "exc": str(ex)})

    event_id = (evt.get("id") or "").strip()
    event_type = (evt.get("type") or "").strip()
    obj = (((evt.get("data") or {}).get("object")) or {}) if isinstance(evt.get("data"), dict) else {}
    payment_status = (obj.get("payment_status") or "").strip()
    session_id = (obj.get("id") or "").strip()

    meta = obj.get("metadata") or {}
    meta_account_id = (meta.get("account_id") or "").strip() if isinstance(meta, dict) else ""
    meta_request_id = (meta.get("request_id") or "").strip() if isinstance(meta, dict) else ""

    # Log webhook receipt (proof point #1)
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
                "meta_request_id": meta_request_id,
                "db_path": db_path,
                "stripe_mode": stripe_mode,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )

    # We only care about checkout.session.completed with paid status
    if event_type != "checkout.session.completed" or payment_status != "paid":
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
        # Fail hard so Stripe retries with same payload; we must not silently lose credit opportunity.
        return error(500, "internal_error", "Missing session_id in paid event", {"request_id": request_id})

    # Store provider event payload (STRICT).
    # If this fails, we return 500 so Stripe retries (do NOT mask billing-chain failures).
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
                    "event_type": event_type,
                    "session_id": session_id,
                    "payment_status": payment_status,
                    "db_path": db_path,
                    "stripe_mode": stripe_mode,
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
            "Failed to store provider event",
            {"request_id": request_id, "event_id": event_id, "decision": "provider_event_store_fail"},
        )

    # Resolve account_id:
    account_id = meta_account_id
    resolved_by = "metadata"

    if not account_id:
        # Fallback: try resolve by stripe_links mapping (session_id -> account_id)
        if hasattr(app.storage, "get_account_id_by_stripe_session_id"):
            try:
                account_id = app.storage.get_account_id_by_stripe_session_id(session_id)
                resolved_by = "stripe_links" if account_id else "stripe_links_none"
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
                    "resolved_by": resolved_by,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        # Fail hard: without account_id we cannot credit; Stripe must retry (or we must fix mapping).
        return error(
            500,
            "internal_error",
            "No account_id resolved for paid session",
            {"request_id": request_id, "event_id": event_id, "session_id": session_id},
        )

    # Price mapping is deterministic:
    # 50 EUR => 5000 units
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
        return error(500, "internal_error", "Storage credit_topup not implemented", {"request_id": request_id})

    try:
        topup = TopUp(
            account_id=account_id,
            tx_hash=tx_hash,
            amount_usdt=Decimal("0"),
            credited_units=credited_units,
            ts=now,
        )
        res = app.storage.credit_topup(topup)

        res_ok = bool(getattr(res, "ok", False))
        if not res_ok:
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
                            "credited_units": getattr(res, "credited_units", None),
                            "balance_units": getattr(res, "balance_units", None),
                            "dedup": getattr(res, "dedup", None),
                            "reason": getattr(res, "reason", None),
                        },
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
                        "credited_units": getattr(res, "credited_units", None),
                        "balance_units": getattr(res, "balance_units", None),
                        "dedup": getattr(res, "dedup", None),
                        "reason": getattr(res, "reason", None),
                    },
                    "db_path": db_path,
                    "stripe_mode": stripe_mode,
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
                    "db_path": db_path,
                    "stripe_mode": stripe_mode,
                    "exc": type(ex).__name__,
                    "traceback": traceback.format_exc(limit=80),
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
    """
    GET /v1/stripe/success?account_id=...&request_id=...

    Landing redirect target after successful Stripe payment.
    """
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
            "units_per_check": UNITS_PER_CHECK,
            "note": "Payment received. Webhook will credit units. Check /v1/accounts/balance.",
        },
    )


def handle_get_stripe_cancel(environ, app) -> dict:
    """
    GET /v1/stripe/cancel

    Landing redirect target after canceled Stripe checkout.
    """
    return json_response(200, {"ok": True})
