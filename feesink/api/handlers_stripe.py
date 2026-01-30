# file: feesink/api/handlers_stripe.py
# FEESINK-API-HANDLERS-STRIPE v2026.01.24-02

from __future__ import annotations

import hashlib
import json
import os
import traceback
import urllib.parse
from datetime import datetime
from decimal import Decimal

from feesink.api._http import UTC, error, get_bearer_token, json_response, read_raw_body, utc_iso
from feesink.api._stripe import stripe_api_post_form, stripe_verify_signature
from feesink.domain.models import ProviderEvent, TopUp


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


def handle_post_stripe_checkout_sessions(app, environ) -> dict:
    """
    POST /v1/stripe/checkout_sessions

    Creates a Stripe Checkout Session for a self-issued token (token == account_id).
    Price is taken ONLY from ENV STRIPE_PRICE_ID_EUR_50.
    """
    now = _now_utc()
    request_id = _new_request_id(now)

    try:
        token = _require_self_issued_token(environ)
    except Exception:
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

    # Always ensure account exists (token == account_id)
    try:
        app.storage.ensure_account(account_id)
    except Exception:
        return error(
            500,
            "internal_error",
            "Failed to ensure account",
            {"request_id": request_id, "account_id": account_id, "traceback": traceback.format_exc(limit=30)},
        )

    metadata = {"feesink_account_id": account_id}

    form = {
        "mode": "payment",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata[feesink_account_id]": account_id,
    }

    try:
        resp = stripe_api_post_form(
            secret_key=secret_key,
            url_path="/v1/checkout/sessions",
            form=form,
        )
    except Exception:
        return error(
            500,
            "internal_error",
            "Stripe API request failed",
            {"request_id": request_id, "traceback": traceback.format_exc(limit=30)},
        )

    if not isinstance(resp, dict):
        return error(500, "internal_error", "Invalid Stripe response", {"request_id": request_id})

    session_id = str(resp.get("id") or "").strip()
    url = str(resp.get("url") or "").strip()
    customer_id = str(resp.get("customer") or "").strip() or None

    if not session_id or not url:
        return error(500, "internal_error", "Stripe response missing id/url", {"request_id": request_id, "resp": resp})

    # Persist stripe link mapping (best-effort but fail if cannot store to avoid losing attribution)
    try:
        app.storage.upsert_stripe_link(
            account_id=account_id,
            stripe_session_id=session_id,
            stripe_customer_id=customer_id,
        )
    except Exception:
        return error(
            500,
            "internal_error",
            "Failed to store stripe link",
            {"request_id": request_id, "traceback": traceback.format_exc(limit=60)},
        )

    return json_response(
        200,
        {
            "ok": True,
            "checkout_session_id": session_id,
            "url": url,
            "account_id": account_id,
            "metadata": metadata,
            "request_id": request_id,
        },
    )


def handle_post_stripe_webhook(app, environ) -> dict:
    """
    POST /v1/webhooks/stripe

    P0 rules:
    - signature must be verified before trusting payload
    - paid session must credit exactly once (idempotent by tx_hash)
    - duplicates must return 200 OK (Stripe retries must not cause 500 loops)
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

    # P1 audit: sha256 of raw bytes (what was signed)
    raw_body_sha256 = hashlib.sha256(raw_body).hexdigest()

    sig = environ.get("HTTP_STRIPE_SIGNATURE")
    if not sig:
        return error(400, "bad_request", "Missing Stripe-Signature header", {"request_id": request_id})

    if isinstance(sig, (bytes, bytearray)):
        sig = sig.decode("utf-8", "replace")

    # signature_verified timestamp (UTC) must be captured at the moment we accept signature
    signature_verified_at = None

    try:
        ok = stripe_verify_signature(raw_body=raw_body, sig_header=sig, secret=secret)
        if not ok:
            raise ValueError("signature_mismatch")

        signature_verified_at = _now_utc()
        signature_verified_at_utc = utc_iso(signature_verified_at)

        # P0: explicit log to separate "signature ok" from "payload parse ok"
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "signature_verified",
                    "db_path": db_path,
                    "stripe_mode": stripe_mode,
                    "raw_body_sha256": raw_body_sha256,
                    "signature_verified_at_utc": signature_verified_at_utc,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

        evt = json.loads(raw_body.decode("utf-8"))
    except Exception:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "signature_verify_fail",
                    "traceback": traceback.format_exc(limit=40),
                    "db_path": db_path,
                    "stripe_mode": stripe_mode,
                    "sig_header_type": type(sig).__name__,
                    "raw_body_type": type(raw_body).__name__,
                    "raw_body_sha256": raw_body_sha256,
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

    meta = (data_obj.get("metadata") or {}) if isinstance(data_obj.get("metadata"), dict) else {}
    meta_account_id = (meta.get("feesink_account_id") or "").strip()

    print(
        json.dumps(
            {
                "provider": "stripe",
                "request_id": request_id,
                "decision": "webhook_received",
                "event_id": event_id,
                "event_type": event_type,
                "payment_status": payment_status,
                "session_id": session_id,
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
        return error(500, "internal_error", "Missing session_id in checkout.session.completed", {"request_id": request_id})

    if payment_status != "paid":
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "not_paid",
                    "event_id": event_id,
                    "session_id": session_id,
                    "payment_status": payment_status,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return json_response(200, {"ok": True})

    # provider_event store (fail-hard)
    try:
        pe = ProviderEvent(
            provider="stripe",
            provider_event_id=event_id,
            event_type=event_type or None,
            status="received",
            received_at=now,
            processed_at=None,
            account_id=None,
            credited_units=None,
            raw_event_json=json.dumps(evt, ensure_ascii=False),
            raw_body_sha256=raw_body_sha256,
            signature_verified_at=signature_verified_at,
        )
        app.storage.insert_provider_event(pe)
    except Exception:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "provider_event_store_fail",
                    "event_id": event_id,
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

    # Resolve account_id (prefer metadata)
    account_id = meta_account_id
    resolved_by = "metadata"
    if not account_id:
        try:
            account_id = app.storage.resolve_account_by_stripe_session(session_id)
            if account_id:
                resolved_by = "stripe_links"
        except Exception:
            print(
                json.dumps(
                    {
                        "provider": "stripe",
                        "request_id": request_id,
                        "decision": "resolve_account_fail",
                        "event_id": event_id,
                        "session_id": session_id,
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
        return json_response(200, {"ok": True})

    # Ensure account exists BEFORE credit (P0)
    try:
        app.storage.ensure_account(account_id)
    except Exception:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "ensure_account_fail",
                    "event_id": event_id,
                    "account_id": account_id,
                    "resolved_by": resolved_by,
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
            "Failed to ensure account",
            {"request_id": request_id, "event_id": event_id, "decision": "ensure_account_fail"},
        )

    # Credit topup (idempotent by tx_hash)
    # tx_hash canonical: "stripe:<event_id>"
    tx_hash = f"stripe:{event_id}"
    credited_units = 5000  # mapping for EUR 50 (canon)

    topup = TopUp(
        account_id=account_id,
        tx_hash=tx_hash,
        amount_usdt=Decimal("0"),  # stripe path does not use USDT amount (kept for contract)
        credited_units=credited_units,
        ts=now,
    )

    try:
        credit_res = app.storage.credit_topup(topup)
    except Exception:
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "request_id": request_id,
                    "decision": "credit_fail",
                    "event_id": event_id,
                    "account_id": account_id,
                    "tx_hash": tx_hash,
                    "traceback": traceback.format_exc(limit=60),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return error(500, "internal_error", "Credit failed", {"request_id": request_id, "event_id": event_id})

    if getattr(credit_res, "inserted", False):
        decision = "credited"
    else:
        decision = "duplicate_tx_hash"

    print(
        json.dumps(
            {
                "provider": "stripe",
                "request_id": request_id,
                "decision": decision,
                "event_id": event_id,
                "account_id": account_id,
                "resolved_by": resolved_by,
                "tx_hash": tx_hash,
                "credited_units": credited_units,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return json_response(200, {"ok": True, "decision": decision, "event_id": event_id, "request_id": request_id})


# Back-compat export: older callers/import_smoke expect this name.
# P0: alias only, no behavior change.
def handle_post_webhooks_stripe(app, environ) -> dict:
    return handle_post_stripe_webhook(app, environ)
