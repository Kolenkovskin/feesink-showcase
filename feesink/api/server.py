"""
FeeSink — API server + minimal HTML landing page
API_CONTRACT: v2026.01.19-02 (API_CONTRACT_v1.md)

Run (PowerShell, from repo root):
  .\\.venv\\Scripts\\python.exe -m feesink.api.server
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

API_VERSION = "FEESINK-API-APP v2026.01.22-01"


def _safe_getattr(mod, name: str, default: str) -> str:
    try:
        return getattr(mod, name)
    except Exception:
        return default


def _sha256_hex_prefix(s: str, n: int = 8) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def _get_listen_host() -> str:
    # Render must bind on 0.0.0.0 to expose the service publicly.
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
    body {{
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      margin: 40px;
      color: #111;
      background: #fff;
    }}
    .box {{ max-width: 760px; }}
    code {{ background:#f3f3f3; padding:2px 6px; border-radius:6px; }}
    .muted {{ color:#666; }}
    .row {{ margin-top: 14px; }}
    input {{
      width: 100%;
      max-width: 560px;
      padding: 12px 12px;
      border: 1px solid #ddd;
      border-radius: 8px;
      font-size: 14px;
    }}
    .btn {{
      display: inline-block;
      margin-top: 10px;
      padding: 12px 18px;
      background: #111;
      color: #fff;
      border: 0;
      border-radius: 8px;
      font-weight: 700;
      cursor: pointer;
    }}
    .btn2 {{
      display: inline-block;
      margin-top: 10px;
      margin-left: 8px;
      padding: 12px 18px;
      background: #f3f3f3;
      color: #111;
      border: 1px solid #ddd;
      border-radius: 8px;
      font-weight: 700;
      cursor: pointer;
    }}
    .err {{
      margin-top: 10px;
      color: #b00020;
      white-space: pre-wrap;
    }}
    .ok {{
      margin-top: 10px;
      color: #0b6b0b;
      white-space: pre-wrap;
    }}
    .small {{ font-size: 13px; line-height: 1.35; }}
    ul {{ margin-top: 6px; }}
    li {{ margin: 4px 0; }}
  </style>
</head>
<body>
  <div class="box">
    <h1>FeeSink</h1>
    <p class="muted">Prepaid endpoint monitoring API.</p>

    <p class="small">
      Invariants: <b>1 check = 1 unit</b> · prepaid only · no subscriptions.
    </p>

    <div class="row">
      <div class="small"><b>Step 1 — Generate a token (API key)</b></div>
      <div class="small muted">
        The token identifies your account. You create it yourself (self-issued).
      </div>
      <ul class="small muted">
        <li>Use any long random string (recommended).</li>
        <li>Keep it secret. Anyone with the token can spend your units.</li>
        <li>If you lose it, funds tied to that token cannot be recovered.</li>
      </ul>
    </div>

    <div class="row">
      <label for="token" class="small"><b>Token (Bearer)</b></label><br/>
      <input id="token" placeholder="paste or generate your token here" autocomplete="off" />
      <div>
        <button class="btn2" id="genBtn">Generate token</button>
        <button class="btn2" id="copyBtn">Copy</button>
      </div>
    </div>

    <div class="row">
      <div class="small"><b>Step 2 — Pay</b></div>
      <div class="small muted">
        Paste your token above, then pay. The payment credits units to that token/account.
      </div>
      <button class="btn" id="payBtn">Pay €50 → Get 5000 units</button>
    </div>

    <div id="msg" class="err" style="display:none;"></div>

    <p class="muted" style="margin-top:18px;">
      API version: <code>{api_version}</code><br/>
      Contract: <code>API_CONTRACT_v1.md</code>
    </p>
  </div>

<script>
(function() {{
  function show(kind, text) {{
    var el = document.getElementById("msg");
    el.style.display = "block";
    el.className = kind;
    el.textContent = text;
  }}

  function base64Url(bytes) {{
    var bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    var b64 = btoa(bin);
    return b64.replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/g, "");
  }}

  function newRequestId() {{
    // Best-effort, browser-side correlation id
    try {{
      if (window.crypto && crypto.randomUUID) return "rid_" + crypto.randomUUID();
    }} catch (e) {{}}
    return "rid_" + String(Date.now()) + "_" + String(Math.floor(Math.random()*1e9));
  }}

  function generateToken() {{
    if (!window.crypto || !crypto.getRandomValues) {{
      show("err", "Crypto RNG is not available in this browser.");
      return;
    }}
    var bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    var t = "t_" + base64Url(bytes);
    document.getElementById("token").value = t;
    show("ok", "Token generated. Save it in your password manager.");
  }}

  async function copyToken() {{
    var token = (document.getElementById("token").value || "").trim();
    if (!token) {{
      show("err", "Nothing to copy: token is empty.");
      return;
    }}
    try {{
      await navigator.clipboard.writeText(token);
      show("ok", "Copied to clipboard.");
    }} catch (e) {{
      show("err", "Clipboard copy failed. Select the token and copy manually.");
    }}
  }}

  async function createCheckout() {{
    var token = (document.getElementById("token").value || "").trim();
    if (!token) {{
      show("err", "Token is required.");
      return;
    }}

    var requestId = newRequestId();
    show("ok", "Creating Stripe Checkout Session...\\nrequest_id: " + requestId);

    try {{
      var resp = await fetch("/v1/stripe/checkout_sessions", {{
        method: "POST",
        headers: {{
          "Authorization": "Bearer " + token,
          "X-Feesink-Token": token,
          "Content-Type": "application/json",
          "X-Feesink-Request-Id": requestId
        }},
        body: "{{}}"
      }});

      var text = await resp.text();
      var data = null;
      try {{ data = JSON.parse(text); }} catch (e) {{ data = null; }}

      if (!resp.ok) {{
        var msg = "Error: HTTP " + resp.status + "\\n";
        if (data && data.error) {{
          msg += (data.error.code || "unknown") + "\\n" + (data.error.message || "");
          if (data.error.details && data.error.details.request_id) {{
            msg += "\\nrequest_id: " + data.error.details.request_id;
          }}
        }} else {{
          msg += text;
        }}
        show("err", msg);
        return;
      }}

      var url = data && data.checkout_session && data.checkout_session.url;
      if (!url) {{
        show("err", "Error: missing checkout_session.url\\nrequest_id: " + requestId);
        return;
      }}

      window.location.href = url;
    }} catch (e) {{
      show("err", "Network error: " + (e && e.message ? e.message : String(e)) + "\\nrequest_id: " + requestId);
    }}
  }}

  document.getElementById("genBtn").addEventListener("click", function() {{
    generateToken();
  }});

  document.getElementById("copyBtn").addEventListener("click", function() {{
    copyToken();
  }});

  document.getElementById("payBtn").addEventListener("click", function() {{
    createCheckout();
  }});
}})();
</script>
</body>
</html>"""
    return html.encode("utf-8")


def _wsgi_app(environ, start_response, inner_app):
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
