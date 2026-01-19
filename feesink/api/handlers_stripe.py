# FeeSink API Stripe handlers
# FEESINK-API-HANDLERS-STRIPE v2026.01.19-02

from __future__ import annotations

import json
import os
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


def _require_self_issued_token(environ) -> str:
    """
    Self-issued token canon:
    - user creates token themselves
    - token identifies account_id
    - token MUST NOT require pre-registration
    """
    token = (get_bearer_token(environ) or "").strip()
    if not token:
        raise ValueError("missing_token")

    # Minimal hygiene: prevent accidental empty/very short tokens.
    # Do NOT over-validate format: storage must not dictate token format.
    if len(token) < 12:
        raise ValueError("token_too_short")
    if len(token) > 512:
        raise ValueError("token_too_long")

    return token


def handle_post_stripe_checkout_sessions(app, environ):
    # IMPORTANT: self-issued token
    # Checkout session must NOT require account/token pre-registration.
    try:
        token = _require_self_issued_token(environ)
    except ValueError as ex:
        reason = str(ex)
        if reason == "missing_token":
            return error(401, "unauthorized", "Missing Bearer token", {})
        return error(401, "unauthorized", "Invalid token", {"reason": reason})

    # Canon: account_id == token (self-issued)
    account_id = token

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
        return error(
            502,
            "bad_gateway",
            "Stripe response missing session id/url",
            {"stripe_id": session_id or None},
        )

    if not hasattr(app.storage, "upsert_stripe_link"):
        return error(500, "internal_error", "Storage does not support stripe_links", {})
    try:
        app.storage.upsert_stripe_link(  # type: ignore[attr-defined]
            account_id=str(account_id),
            stripe_session_id=session_id,
            stripe_customer_id=customer_id,
        )
    except Exception as ex:
        return error(500, "internal_error", "Failed to store stripe link", {"exception": type(ex).__name__})

    return json_response(200, {"checkout_session": {"id": session_id, "url": session_url}})


def handle_post_webhooks_stripe(app, environ):
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
        print(
            json.dumps(
                {"provider": "stripe", "decision": "ignored", "event_id": event_id, "event_type": event_type},
                ensure_ascii=False,
            )
        )
        return json_response(200, {"ok": True})

    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    session = (data.get("object") or {}) if isinstance(data.get("object"), dict) else {}

    session_id = (session.get("id") or "").strip() or None
    payment_status = (session.get("payment_status") or "").strip() or None
    customer_id = (session.get("customer") or "").strip() or None
    metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}

    if not session_id:
        return error(400, "invalid_request", "Missing checkout session id")

    if not hasattr(app.storage, "insert_provider_event"):
        return error(500, "internal_error", "Storage does not support provider_events (insert_provider_event)", {})

    dedup_by_event_id = False
    try:
        inserted = bool(app.storage.insert_provider_event("stripe", event_id, raw.decode("utf-8")))  # type: ignore[attr-defined]
        if not inserted:
            dedup_by_event_id = True
    except Exception as ex:
        print(
            json.dumps(
                {"provider": "stripe", "decision": "provider_event_write_failed", "event_id": event_id, "exception": type(ex).__name__},
                ensure_ascii=False,
            )
        )
        return error(500, "internal_error", "Failed to persist provider_event", {"exception": type(ex).__name__})

    if payment_status != "paid":
        print(
            json.dumps(
                {
                    "provider": "stripe",
                    "decision": "ignored_not_paid",
                    "event_id": event_id,
                    "session_id": session_id,
                    "payment_status": payment_status,
                },
                ensure_ascii=False,
            )
        )
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
        if not hasattr(app.storage, "resolve_account_by_stripe_session"):
            return error(500, "internal_error", "Storage does not support stripe_links (resolve_account_by_stripe_session)", {})
        try:
            account_id = app.storage.resolve_account_by_stripe_session(session_id)  # type: ignore[attr-defined]
            account_id = str(account_id).strip() if account_id is not None else ""
            if not account_id:
                raise ValueError("resolved_empty_account_id")
            account_id_source = "stripe_links"
        except Exception as ex:
            print(
                json.dumps(
                    {"provider": "stripe", "decision": "unresolved_account", "event_id": event_id, "session_id": session_id, "exception": type(ex).__name__},
                    ensure_ascii=False,
                )
            )
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
        print(
            json.dumps(
                {"provider": "stripe", "decision": "unresolved_mapping", "event_id": event_id, "account_id": str(account_id), "price_id": price_id},
                ensure_ascii=False,
            )
        )
        return error(500, "internal_error", "Unable to map Stripe price_id to credited_units", {"price_id": price_id})

    tx_hash = f"stripe:{event_id}"

    from feesink.domain.models import TopUp  # type: ignore

    # Compute amount_usdt from credited_units deterministically
    rate = Decimal(str(USDT_TO_UNITS_RATE))
    amount_usdt = (Decimal(int(credited_units)) / rate)
    if amount_usdt != amount_usdt.to_integral_value():
        return error(500, "internal_error", "credited_units does not map to integer USDT amount", {"credited_units": int(credited_units), "rate": str(rate)})
    if amount_usdt < MIN_TOPUP_USDT:
        return error(500, "internal_error", "credited_units maps below minimal top-up", {"amount_usdt": str(amount_usdt), "min_usdt": str(MIN_TOPUP_USDT)})

    topup = TopUp(
        account_id=str(account_id),
        tx_hash=tx_hash,
        amount_usdt=Decimal(str(amount_usdt)),
        credited_units=int(credited_units),
        ts=_now_utc(),
    )
    try:
        topup.validate()
    except Exception as ex:
        print(json.dumps({"provider": "stripe", "decision": "topup_invalid", "event_id": event_id, "exception": type(ex).__name__}, ensure_ascii=False))
        return error(500, "internal_error", "TopUp validation failed", {"exception": type(ex).__name__})

    try:
        res = app.storage.credit_topup(topup)
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
                "dedup_event": False,  # kept for backward compatibility in logs
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
                "ts": utc_iso(_now_utc()),
            },
            ensure_ascii=False,
        )
    )

    return json_response(200, {"ok": True, "dedup_tx_hash": dedup_by_tx_hash})
