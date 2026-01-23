# FeeSink Stripe helpers (no SDK)
# FEESINK-API-STRIPE v2026.01.23-01

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple, Union


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


def stripe_verify_signature(
    raw_body: Union[bytes, bytearray, str],
    sig_header: Union[str, bytes, bytearray],
    secret: Union[str, bytes, bytearray],
    tolerance_sec: int = 300,
) -> bool:
    """
    Stripe webhook signature verification (HMAC SHA256), SDK-free.

    IMPORTANT (P0):
    - raw_body MUST be the exact request bytes
    - sig_header MUST be the Stripe-Signature header
    - secret MUST be STRIPE_WEBHOOK_SECRET
    """
    if not secret:
        return False

    if isinstance(secret, (bytes, bytearray)):
        secret_s = secret.decode("utf-8", "replace").strip()
    else:
        secret_s = str(secret).strip()

    if not secret_s:
        return False

    if isinstance(sig_header, (bytes, bytearray)):
        sig_s = sig_header.decode("utf-8", "replace")
    else:
        sig_s = str(sig_header)

    if isinstance(raw_body, str):
        raw_b = raw_body.encode("utf-8")
    elif isinstance(raw_body, bytearray):
        raw_b = bytes(raw_body)
    else:
        raw_b = raw_body

    t, v1 = stripe_parse_sig_header(sig_s)
    if t is None or not v1:
        return False

    now = int(time.time())
    if abs(now - int(t)) > int(tolerance_sec):
        return False

    signed_payload = (str(t) + ".").encode("utf-8") + raw_b
    expected = hmac.new(secret_s.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
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
