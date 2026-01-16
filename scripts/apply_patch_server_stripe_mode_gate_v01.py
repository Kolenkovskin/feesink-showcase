# scripts/apply_patch_server_stripe_mode_gate_v01.py
# FEESINK-APPLY-PATCH-SERVER-STRIPE-MODE-GATE v2026.01.07-01
#
# Purpose:
# - Introduce explicit FEESINK_STRIPE_MODE=test|live (default test).
# - Startup banner prints MODE: STRIPE_TEST / STRIPE_LIVE.
# - Kill-switch validates STRIPE_SECRET_KEY prefix based on selected mode:
#     test -> sk_test_
#     live -> sk_live_
#
# Safety:
# - Creates timestamped .bak backup
# - Applies anchored, minimal string replacements (no "guessing regex")
# - Verifies that expected anchors exist exactly once
#
# Run:
#   (.venv) PS> python .\scripts\apply_patch_server_stripe_mode_gate_v01.py

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path


VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-MODE-GATE v2026.01.07-01"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def main() -> int:
    print("=" * 80)
    print(VERSION)
    print("TS_UTC=", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    print("=" * 80)

    repo_root = Path(__file__).resolve().parents[1]
    target = repo_root / "feesink" / "api" / "server.py"
    print("TARGET=", str(target))

    if not target.exists():
        print("ERROR: TARGET not found")
        return 2

    original = target.read_text(encoding="utf-8")
    print("ORIG_SHA1=", sha1_text(original))

    # -------------------------------------------------------------------------
    # Patch 1: replace hardcoded banner print line
    # -------------------------------------------------------------------------
    anchor_print_old = 'print("MODE: STRIPE_TEST_ONLY")'
    anchor_print_new = 'print(f"MODE: STRIPE_{stripe_mode.upper()}")'

    count_print = original.count(anchor_print_old)
    print("FOUND anchor_print_old=", count_print)
    if count_print != 1:
        print("ERROR: expected exactly 1 occurrence of banner MODE print anchor")
        return 2

    patched = original.replace(anchor_print_old, anchor_print_new)

    # -------------------------------------------------------------------------
    # Patch 2: inject stripe_mode resolution + validation after stripe_intent line
    # -------------------------------------------------------------------------
    inject_after = "stripe_intent = any([stripe_key, whsec, stripe_price, stripe_success, stripe_cancel])"
    count_inject_after = patched.count(inject_after)
    print("FOUND inject_after=", count_inject_after)
    if count_inject_after != 1:
        print("ERROR: expected exactly 1 occurrence of stripe_intent anchor")
        return 2

    injection = (
        inject_after
        + "\n\n"
        + "    # --- Stripe mode gate (P0): explicit test/live selection ---\n"
        + "    stripe_mode = (os.getenv('FEESINK_STRIPE_MODE') or 'test').strip().lower()\n"
        + "    if stripe_mode not in ('test', 'live'):\n"
        + "        print(f\"FATAL: FEESINK_STRIPE_MODE must be 'test' or 'live' (got {stripe_mode!r})\")\n"
        + "        raise SystemExit(2)\n"
    )
    patched = patched.replace(inject_after, injection)

    # -------------------------------------------------------------------------
    # Patch 3: change kill-switch prefix check sk_test_ -> dynamic sk_test_/sk_live_
    # -------------------------------------------------------------------------
    kill_old = (
        "        if not stripe_key.startswith(\"sk_test_\"):\n"
        "            print(f\"FATAL: STRIPE_SECRET_KEY must start with sk_test_ (got prefix={stripe_key[:7]!r})\")\n"
        "            raise SystemExit(2)\n"
    )

    kill_new = (
        "        expected_prefix = \"sk_test_\" if stripe_mode == \"test\" else \"sk_live_\"\n"
        "        if not stripe_key.startswith(expected_prefix):\n"
        "            print(\n"
        "                f\"FATAL: STRIPE_SECRET_KEY must start with {expected_prefix} \"\n"
        "                f\"for FEESINK_STRIPE_MODE={stripe_mode!r} (got prefix={stripe_key[:7]!r})\"\n"
        "            )\n"
        "            raise SystemExit(2)\n"
    )

    count_kill = patched.count(kill_old)
    print("FOUND kill_old=", count_kill)
    if count_kill != 1:
        print("ERROR: expected exactly 1 occurrence of kill-switch prefix block")
        return 2

    patched = patched.replace(kill_old, kill_new)

    # -------------------------------------------------------------------------
    # Finalize: write backup + patched
    # -------------------------------------------------------------------------
    backup = target.with_suffix(f".py.bak.{utc_stamp()}")
    backup.write_text(original, encoding="utf-8")
    print("BACKUP=", str(backup))

    target.write_text(patched, encoding="utf-8")
    print("PATCH_APPLIED=1")
    print("NEW_SHA1=", sha1_text(patched))

    print("=" * 80)
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
