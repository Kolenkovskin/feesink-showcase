#!/usr/bin/env python3
# FEESINK-STRIPE-ENV-AUDIT v2026.01.19-03
r"""
Deterministic ENV audit for Stripe readiness (FeeSink canonical env names).

What it does (read-only):
- Prints a deterministic banner with Stripe mode + key prefix/kind + price_id (EUR_50) presence
  + webhook secret presence + canonical public webhook endpoint.
- Validates "no mixing" rule: FEESINK_STRIPE_MODE must match sk_* prefix.
- Exits non-zero on FAIL.
- Appends full stdout to:
  C:\Users\User\PycharmProjects\feesink\logs\stripe_env_audit.txt

Stdlib-only.
"""

from __future__ import annotations

import datetime as _dt
import hashlib as _hashlib
import os as _os
import sys as _sys
from pathlib import Path as _Path


VERSION = "FEESINK-STRIPE-ENV-AUDIT v2026.01.19-03"
DEFAULT_PUBLIC_BASE_URL = "https://feesink.com"
DEFAULT_WEBHOOK_PATH = "/v1/webhooks/stripe"
LOG_PATH_WIN = r"C:\Users\User\PycharmProjects\feesink\logs\stripe_env_audit.txt"


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: _Path) -> str:
    h = _hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_repo_root(start: _Path) -> _Path:
    p = start.resolve()
    for _ in range(0, 20):
        if (p / ".git").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return start.resolve().parent.parent


def _mask_secret_prefix(secret: str, keep: int = 8) -> str:
    if not secret:
        return ""
    s = secret.strip()
    if len(s) <= keep:
        return s
    return s[:keep] + "…"


def _norm_mode(mode: str) -> str:
    m = (mode or "").strip().lower()
    if m in ("live", "prod", "production"):
        return "live"
    if m in ("test", "dev", "development"):
        return "test"
    return m or ""


def _key_kind(sk: str) -> str:
    sk = (sk or "").strip()
    if sk.startswith("sk_live_"):
        return "live"
    if sk.startswith("sk_test_"):
        return "test"
    return "unknown"


def _audit() -> int:
    ts_utc = _utc_now_iso()

    here = _Path(__file__).resolve()
    root = _find_repo_root(here)

    related = [
        ("scripts/stripe_env_audit.py", here),
        ("feesink/api/app.py", root / "feesink" / "api" / "app.py"),
        ("feesink/api/handlers_stripe.py", root / "feesink" / "api" / "handlers_stripe.py"),
    ]

    stripe_mode_raw = _os.getenv("FEESINK_STRIPE_MODE", "")
    stripe_mode = _norm_mode(stripe_mode_raw)

    stripe_secret_key = _os.getenv("STRIPE_SECRET_KEY", "")
    stripe_secret_kind = _key_kind(stripe_secret_key)

    webhook_secret = _os.getenv("STRIPE_WEBHOOK_SECRET", "")
    webhook_secret_present = "yes" if webhook_secret.strip() else "no"

    price_id_eur50 = _os.getenv("STRIPE_PRICE_ID_EUR_50", "")

    public_base_url = (_os.getenv("FEESINK_PUBLIC_BASE_URL", "") or DEFAULT_PUBLIC_BASE_URL).strip()
    webhook_path = (_os.getenv("FEESINK_STRIPE_WEBHOOK_PATH", "") or DEFAULT_WEBHOOK_PATH).strip()

    print("=" * 80)
    print(VERSION)
    print(f"TS_UTC= {ts_utc}")
    print(f"ROOT= {root}")
    print("=" * 80)

    print("[ENV]")
    print(f"FEESINK_STRIPE_MODE= {stripe_mode or '<missing>'}")
    print(f"STRIPE_SECRET_KEY.prefix= {_mask_secret_prefix(stripe_secret_key, keep=12) or '<missing>'}")
    print(f"STRIPE_SECRET_KEY.kind= {stripe_secret_kind}")
    print(f"STRIPE_WEBHOOK_SECRET.present= {webhook_secret_present}")
    print(f"STRIPE_PRICE_ID_EUR_50= {price_id_eur50 or '<missing>'}")
    print(f"PUBLIC_BASE_URL= {public_base_url}")
    print(f"WEBHOOK_ENDPOINT= {public_base_url.rstrip('/')}{webhook_path}")
    print()

    print("[HASHES_SHA256]")
    for label, p in related:
        if p.exists() and p.is_file():
            try:
                print(f"{label}= {_sha256_file(p)}")
            except Exception as e:
                print(f"{label}= <error {type(e).__name__}: {e}>")
        else:
            print(f"{label}= <missing>")
    print()

    print("[CHECKS]")
    fail = False

    if stripe_mode not in ("live", "test"):
        print("FAIL: FEESINK_STRIPE_MODE must be 'live' or 'test'.")
        fail = True
    else:
        print("PASS: FEESINK_STRIPE_MODE valid.")

    if stripe_secret_kind == "unknown":
        print("FAIL: STRIPE_SECRET_KEY missing or invalid.")
        fail = True
    else:
        print("PASS: STRIPE_SECRET_KEY prefix valid.")

    if stripe_mode and stripe_secret_kind in ("live", "test") and stripe_mode != stripe_secret_kind:
        print("FAIL: Stripe mode / key mismatch (no mixing).")
        fail = True
    else:
        if stripe_mode:
            print("PASS: Stripe mode matches key.")

    if not price_id_eur50.strip():
        print("FAIL: STRIPE_PRICE_ID_EUR_50 missing (required for checkout creation).")
        fail = True
    else:
        print("PASS: STRIPE_PRICE_ID_EUR_50 present.")

    if stripe_mode == "live" and webhook_secret_present != "yes":
        print("FAIL: STRIPE_WEBHOOK_SECRET required for LIVE readiness.")
        fail = True
    else:
        if webhook_secret_present == "yes":
            print("PASS: STRIPE_WEBHOOK_SECRET present.")
        else:
            print("WARN: STRIPE_WEBHOOK_SECRET missing (ok for local-only TEST, not for LIVE).")

    print()
    print("[SUMMARY]")
    if fail:
        print("SUMMARY=FAIL")
        return 2

    print("SUMMARY=PASS")
    return 0


def main() -> int:
    from io import StringIO

    buf = StringIO()
    old = _sys.stdout
    try:
        _sys.stdout = buf
        rc = _audit()
    finally:
        _sys.stdout = old

    out = buf.getvalue()
    print(out, end="")

    try:
        p = _Path(LOG_PATH_WIN)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(out)
            if not out.endswith("\n"):
                f.write("\n")
            f.write("\n")
    except Exception:
        pass

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
