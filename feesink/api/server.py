"""
FeeSink — API skeleton (Self-Service v1) + minimal HTML landing page
API_CONTRACT: v2026.01.01-API-01 (docs/API_CONTRACT_v1.md)

Run (PowerShell, from repo root):
  .\.venv\Scripts\python.exe -m feesink.api.server
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional
from wsgiref.simple_server import make_server

from feesink.api.app import FeeSinkApiApp

# ----------------------------
# Version banner (must print at startup)
# ----------------------------

API_VERSION = "FEESINK-API-SKELETON v2026.01.18-RENDER-PORT-ROOT-01"


def _safe_getattr(mod, name: str, default: str) -> str:
    try:
        return getattr(mod, name)
    except Exception:
        return default


def _sha256_hex_prefix(s: str, n: int = 8) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def _get_listen_host() -> str:
    # Render must bind on 0.0.0.0 to expose the service publicly.
    # Allow override, but default to 0.0.0.0.
    return (os.getenv("FEESINK_API_HOST") or "0.0.0.0").strip()


def _get_listen_port() -> int:
    # Render provides PORT env var. Use it first.
    port_raw = (os.getenv("PORT") or os.getenv("FEESINK_API_PORT") or "8789").strip()
    try:
        return int(port_raw)
    except Exception:
        print(f"FATAL: PORT/FEESINK_API_PORT must be int, got: {port_raw!r}")
        raise SystemExit(2)


def _print_startup_banner() -> None:
    worker_v = "unknown"
    sqlite_v = "unknown"
    try:
        from feesink.runtime import worker as worker_mod  # type: ignore

        worker_v = _safe_getattr(worker_mod, "FEESINK_WORKER_VERSION", "unknown")
    except Exception:
        pass
    try:
        from feesink.storage import sqlite as sqlite_mod  # type: ignore

        sqlite_v = _safe_getattr(sqlite_mod, "STORAGE_VERSION", "unknown")
    except Exception:
        pass

    host = _get_listen_host()
    port = _get_listen_port()

    storage_kind = (os.getenv("FEESINK_STORAGE") or "memory").strip().lower()
    db_abs_path: Optional[str] = None
    db_basename: Optional[str] = None
    if storage_kind == "sqlite":
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        db_rel = os.getenv("FEESINK_SQLITE_DB", "feesink.db")
        db_abs_path = os.path.join(repo_root, db_rel)
        db_basename = os.path.basename(db_abs_path)

    stripe_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    whsec = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    stripe_price = (os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip()
    stripe_success = (os.getenv("STRIPE_SUCCESS_URL") or "").strip()
    stripe_cancel = (os.getenv("STRIPE_CANCEL_URL") or "").strip()
    stripe_intent = any([stripe_key, whsec, stripe_price, stripe_success, stripe_cancel])

    stripe_mode = (os.getenv("FEESINK_STRIPE_MODE") or "test").strip().lower()
    if stripe_mode not in ("test", "live"):
        print(f"FATAL: FEESINK_STRIPE_MODE must be 'test' or 'live' (got {stripe_mode!r})")
        raise SystemExit(2)

    print("=" * 80)
    print(f"MODE: STRIPE_{stripe_mode.upper()}")
    print(f"LISTEN: http://{host}:{port}")
    print(f"ENV: PORT={os.getenv('PORT')!r} FEESINK_API_PORT={os.getenv('FEESINK_API_PORT')!r}")

    if storage_kind == "sqlite":
        print("STORAGE: sqlite")
        print(f"SQLITE_DB: {db_abs_path} (basename={db_basename})")
    else:
        print(f"STORAGE: {storage_kind}")

    if stripe_intent:
        if not stripe_key:
            print("FATAL: STRIPE intent detected but STRIPE_SECRET_KEY is missing")
            raise SystemExit(2)
        expected_prefix = "sk_test_" if stripe_mode == "test" else "sk_live_"
        if not stripe_key.startswith(expected_prefix):
            print(
                f"FATAL: STRIPE_SECRET_KEY must start with {expected_prefix} "
                f"for FEESINK_STRIPE_MODE={stripe_mode!r} (got prefix={stripe_key[:7]!r})"
            )
            raise SystemExit(2)
        if not whsec:
            print("FATAL: STRIPE intent detected but STRIPE_WEBHOOK_SECRET is missing")
            raise SystemExit(2)

        print(f"STRIPE_SECRET_KEY prefix: {stripe_key[:7]}")
        print(f"STRIPE_WEBHOOK_SECRET hash-prefix: {_sha256_hex_prefix(whsec, 8)}")
    else:
        print("STRIPE: not configured (no STRIPE_* envs)")

    print(API_VERSION)
    print(f"WORKER: {worker_v}")
    print(f"SQLITE:  {sqlite_v}")
    print("=" * 80)


def _landing_html(api_version: str) -> bytes:
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>FeeSink</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 40px; }}
    code {{ background:#f3f3f3; padding:2px 6px; border-radius:6px; }}
    .box {{ max-width: 720px; }}
    .muted {{ color:#666; }}
  </style>
</head>
<body>
  <div class="box">
    <h1>FeeSink</h1>
    <p class="muted">Status: <b>OK</b></p>
    <p>API version: <code>{api_version}</code></p>
    <p class="muted">This is a minimal landing page. API endpoints are documented in <code>API_CONTRACT_v1.md</code>.</p>
  </div>
</body>
</html>"""
    return html.encode("utf-8")


def _wsgi_app(environ, start_response, inner_app):
    # Serve landing at "/" to avoid confusing 404 on the product URL.
    path = (environ.get("PATH_INFO") or "").strip()
    if path == "" or path == "/":
        body = _landing_html(API_VERSION)
        start_response(
            "200 OK",
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    return inner_app(environ, start_response)


def main() -> None:
    _print_startup_banner()

    host = _get_listen_host()
    port = _get_listen_port()

    inner = FeeSinkApiApp(api_version=API_VERSION)

    httpd = make_server(host, port, lambda e, s: _wsgi_app(e, s, inner))
    print(f"Listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
