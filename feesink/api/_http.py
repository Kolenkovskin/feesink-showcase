# FeeSink API HTTP helpers
# FEESINK-API-HTTP v2026.01.22-01

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

UTC = timezone.utc


def utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")


def json_response(status: int, payload: Dict[str, Any], headers: Optional[list] = None):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hdrs = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if headers:
        hdrs.extend(headers)
    return status, hdrs, body


def html_response(status: int, html_text: str, headers: Optional[list] = None):
    body = html_text.encode("utf-8")
    hdrs = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if headers:
        hdrs.extend(headers)
    return status, hdrs, body


def error(status: int, code: str, message: str, details: Optional[Dict[str, Any]] = None):
    return json_response(
        status,
        {"error": {"code": code, "message": message, "details": details or {}}},
    )


def read_json(environ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except Exception:
        length = 0
    if length <= 0:
        return None, "empty_body"
    raw = environ["wsgi.input"].read(length)
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, "invalid_json"
    if not isinstance(obj, dict):
        return None, "json_not_object"
    return obj, None


def read_raw_body(environ) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except Exception:
        length = 0
    if length <= 0:
        return b""
    return environ["wsgi.input"].read(length)


_BEARER_RE = re.compile(r"^\s*Bearer\s+(.+?)\s*$", re.IGNORECASE)


def _extract_bearer(s: str) -> Optional[str]:
    if not s:
        return None
    m = _BEARER_RE.match(s)
    if not m:
        return None
    return m.group(1)


def get_bearer_token(environ) -> Optional[str]:
    """
    Canon: Authorization: Bearer <token>

    Reality guard (P0):
    Some WSGI stacks / proxies may drop HTTP_AUTHORIZATION.
    We allow deterministic fallback for the landing UI:

      - X-Feesink-Token: <token>

    Also supports common WSGI forward var:
      - REDIRECT_HTTP_AUTHORIZATION
    """
    # Primary: WSGI-standard
    token = _extract_bearer(environ.get("HTTP_AUTHORIZATION") or "")
    if token:
        return token

    # Common server/proxy forwarding var (Apache-style)
    token = _extract_bearer(environ.get("REDIRECT_HTTP_AUTHORIZATION") or "")
    if token:
        return token

    # Landing/UI fallback: direct token header (no "Bearer " wrapper)
    # WSGI turns "X-Feesink-Token" into "HTTP_X_FEESINK_TOKEN"
    x = (environ.get("HTTP_X_FEESINK_TOKEN") or "").strip()
    if x:
        return x

    return None


def get_query_param(environ, name: str) -> Optional[str]:
    qs = environ.get("QUERY_STRING") or ""
    for part in qs.split("&"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
        else:
            k, v = part, ""
        if k == name:
            return v
    return None
