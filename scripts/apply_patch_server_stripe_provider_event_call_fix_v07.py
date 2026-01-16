# scripts/apply_patch_server_stripe_provider_event_call_fix_v07.py
# FEESINK-APPLY-PATCH-SERVER-STRIPE-PROVIDER-EVENT-CALL v2026.01.05-07

from __future__ import annotations

import re
import sys
import py_compile
from pathlib import Path
from datetime import datetime, timezone

VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-PROVIDER-EVENT-CALL v2026.01.05-07"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def backup_file(p: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = p.with_suffix(p.suffix + f".bak.{ts}")
    bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return bak


def main() -> int:
    print("=" * 80)
    print(VERSION)
    print("TS_UTC=", utc())
    print("=" * 80)

    repo = Path(__file__).resolve().parents[1]
    target = repo / "feesink" / "api" / "server.py"

    if not target.exists():
        print("ERROR: target not found:", target)
        return 2

    print("TARGET=", target)

    src = target.read_text(encoding="utf-8")

    # We fix ANY corrupted form of the inserted=bool(self.storage.insert_provider_event...)
    # including cases like:
    #   - self.storage.insert_provider_event... event_id, ...
    #   - self.storage.insert_provider_event(..., event_id, ...
    #   - missing "stripe" provider argument
    #
    # Strategy:
    #   Replace the entire line that starts with "inserted = bool(self.storage.insert_provider_event"
    #   up to the end of that line, with the canonical call.
    canonical_line = (
        '            inserted = bool(self.storage.insert_provider_event("stripe", event_id, raw.decode("utf-8")))'
        "  # type: ignore[attr-defined]"
    )

    pattern = re.compile(
        r'^\s*inserted\s*=\s*bool\(\s*self\.storage\.insert_provider_event.*$',
        re.MULTILINE,
    )

    matches = list(pattern.finditer(src))
    print("FOUND_MATCHES=", len(matches))

    if len(matches) != 1:
        # If we cannot find exactly one match, try a narrower recovery:
        # find a line that contains "insert_provider_event" and "inserted = bool(" in vicinity.
        alt_pattern = re.compile(r'^\s*inserted\s*=.*insert_provider_event.*$', re.MULTILINE)
        alt = list(alt_pattern.finditer(src))
        print("FOUND_ALT_MATCHES=", len(alt))
        if len(alt) == 1:
            matches = alt
        else:
            print("ERROR: Could not uniquely locate the provider_event inserted line to patch.")
            return 3

    bak = backup_file(target)
    print("BACKUP=", bak)

    patched = pattern.sub(canonical_line, src, count=1)
    if patched == src:
        # if primary pattern didn't substitute (e.g., we used alt match), patch via manual replace
        line = matches[0].group(0)
        patched = src.replace(line, canonical_line, 1)

    target.write_text(patched, encoding="utf-8")
    print("PATCH_APPLIED=1")

    # Fail-fast: compile the patched file
    try:
        py_compile.compile(str(target), doraise=True)
        print("PY_COMPILE=OK")
    except Exception as ex:
        print("PY_COMPILE=FAIL", type(ex).__name__, str(ex))
        return 4

    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
