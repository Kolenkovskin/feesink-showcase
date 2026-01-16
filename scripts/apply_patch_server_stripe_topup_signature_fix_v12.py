# apply_patch_server_stripe_topup_signature_fix_v12.py
from __future__ import annotations

import hashlib
import re
from datetime import datetime, UTC
from pathlib import Path

VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-TOPUP-SIGNATURE-FIX v2026.01.05-12"


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def main() -> int:
    ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    root = Path(__file__).resolve().parents[1]
    target = root / "feesink" / "api" / "server.py"

    print("=" * 80)
    print(VERSION)
    print("TS_UTC=", ts)
    print("=" * 80)
    print("TARGET=", str(target))
    print("CWD=", str(Path.cwd()))

    if not target.exists():
        print("ERROR: target not found.")
        return 2

    src = target.read_text(encoding="utf-8")
    before_sha = sha1_text(src)

    # We patch the specific TopUp(...) constructor block by replacing its argument list.
    # We accept any indentation and any existing args inside TopUp(...).
    # Goal (by domain.models.TopUp signature):
    #   TopUp(account_id=account_id, tx_hash=tx_hash, amount_usdt=amount_usdt, credited_units=credited_units, ts=now)
    pattern = re.compile(
        r"""
(?P<indent>^[ \t]*)topup\s*=\s*TopUp\s*\(\s*
(?P<body>.*?)
(?P=indent)\)\s*$
""",
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    matches = list(pattern.finditer(src))
    print("FOUND_MATCHES=", len(matches))
    if len(matches) != 1:
        print("ERROR: expected exactly 1 TopUp(...) block; refusing to patch.")
        return 3

    m = matches[0]
    start, end = m.span()
    indent = m.group("indent")

    # BEFORE context (5 lines around)
    lines = src.splitlines()
    start_line = src[:start].count("\n")
    end_line = src[:end].count("\n")
    ctx_from = max(0, start_line - 5)
    ctx_to = min(len(lines), end_line + 6)

    print("\n--- BEFORE (context) ---")
    for i in range(ctx_from, ctx_to):
        prefix = ">>" if start_line <= i <= end_line else "  "
        print(f"{prefix} {i+1:04d}: {lines[i]}")

    replacement = (
        f"{indent}topup = TopUp(\n"
        f"{indent}    account_id=account_id,\n"
        f"{indent}    tx_hash=tx_hash,\n"
        f"{indent}    amount_usdt=amount_usdt,\n"
        f"{indent}    credited_units=credited_units,\n"
        f"{indent}    ts=now,\n"
        f"{indent})"
    )

    new_src = src[:start] + replacement + src[end:]

    if new_src == src:
        print("NOOP: patch would not change anything.")
        return 4

    backup = target.with_suffix(f".py.bak.{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    backup.write_text(src, encoding="utf-8")

    target.write_text(new_src, encoding="utf-8")
    after_sha = sha1_text(new_src)

    print("\nBACKUP=", str(backup))
    print("SHA1_BEFORE=", before_sha)
    print("SHA1_AFTER =", after_sha)

    # AFTER context
    new_lines = new_src.splitlines()
    # recompute approximate region for printing
    new_start_line = new_src[:start].count("\n")
    new_end_line = new_start_line + replacement.count("\n")
    ctx_from2 = max(0, new_start_line - 5)
    ctx_to2 = min(len(new_lines), new_end_line + 6)

    print("\n--- AFTER (context) ---")
    for i in range(ctx_from2, ctx_to2):
        prefix = ">>" if new_start_line <= i <= new_end_line else "  "
        print(f"{prefix} {i+1:04d}: {new_lines[i]}")

    # compile check
    import py_compile

    try:
        py_compile.compile(str(target), doraise=True)
        print("\nPY_COMPILE=OK")
    except Exception as ex:
        print("\nPY_COMPILE=FAIL:", type(ex).__name__, str(ex))
        print("NOTE: restoring backup is recommended.")
        return 5

    print("PATCH_APPLIED=1")
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
