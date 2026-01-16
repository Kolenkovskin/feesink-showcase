# Path: scripts/apply_patch_server_stripe_topup_frozen_fix_v09.py
r"""
FeeSink — apply patch: Stripe webhook credit path TopUp frozen dataclass fix (robust matcher)

WHY:
- TopUp is frozen/slots in domain => TopUp() + setattr raises TypeError/FrozenInstanceError.
- This breaks credit_topup => topups row missing => balance_units stays 0.

WHAT:
- Find the block:
    topup = TopUp()
    topup.account_id = ...
    topup.tx_hash = ...
    topup.amount_usdt = ...
    topup.credited_units = ...
    topup.ts = ...
  (with flexible spacing/indent)
- Replace with:
    topup = TopUp(account_id=..., tx_hash=..., amount_usdt=..., credited_units=..., ts=now)

SAFETY:
- Exactly 1 match required.
- Backup .bak.<ts> is created.
- Prints BEFORE/AFTER context (±5 lines).
- Verifies python compile.

Run (PowerShell):
  C:\Users\User\PycharmProjects\feesink\.venv\Scripts\python.exe C:\Users\User\PycharmProjects\feesink\scripts\apply_patch_server_stripe_topup_frozen_fix_v09.py
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-TOPUP-FROZEN-FIX v2026.01.05-09"


def _ts_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _write_text(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8", newline="\n")


def _compile_check(py_file: Path) -> None:
    import py_compile

    py_compile.compile(str(py_file), doraise=True)


def _print_banner(target: Path) -> None:
    print("=" * 80)
    print(SCRIPT_VERSION)
    print("TS_UTC=", _ts_utc())
    print("=" * 80)
    print("TARGET=", str(target))
    print("CWD=", os.getcwd())


def _context_lines(lines: list[str], start_1: int, end_1: int, radius: int = 5) -> str:
    n = len(lines)
    a = max(1, start_1 - radius)
    b = min(n, end_1 + radius)
    out = []
    for ln in range(a, b + 1):
        prefix = ">>" if start_1 <= ln <= end_1 else "  "
        out.append(f"{prefix} {ln:04d}: {lines[ln - 1]}")
    return "\n".join(out)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    default_target = repo_root / "feesink" / "api" / "server.py"

    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=str, default=str(default_target))
    args = ap.parse_args()

    target = Path(args.target).resolve()
    _print_banner(target)

    if not target.exists():
        print("ERROR: target file not found")
        return 2

    before = _read_text(target)
    before_sha1 = _sha1_text(before)

    # Robust matcher: only the TopUp() + 5 assignments block (flexible whitespace).
    # Captures indent for "topup = TopUp()" line.
    pattern = re.compile(
        r"""
(?P<indent>^[ \t]*)topup[ \t]*=[ \t]*TopUp\(\)[ \t]*\r?\n
(?P=indent)topup\.account_id[ \t]*=[ \t]*(?P<account_expr>.+?)\r?\n
(?P=indent)topup\.tx_hash[ \t]*=[ \t]*(?P<tx_expr>.+?)\r?\n
(?P=indent)topup\.amount_usdt[ \t]*=[ \t]*(?P<amount_expr>.+?)\r?\n
(?P=indent)topup\.credited_units[ \t]*=[ \t]*(?P<credited_expr>.+?)\r?\n
(?P=indent)topup\.ts[ \t]*=[ \t]*(?P<ts_expr>.+?)\r?\n
""",
        re.VERBOSE | re.MULTILINE,
    )

    matches = list(pattern.finditer(before))
    print("FOUND_MATCHES=", len(matches))
    if len(matches) != 1:
        print("ERROR: expected exactly 1 match. Refusing to patch.")
        if len(matches) == 0:
            print("HINT: server.py no longer contains TopUp() + setattr block, or it differs materially.")
        else:
            print("HINT: multiple TopUp() setattr blocks found; need to disambiguate safely.")
        return 3

    m = matches[0]
    indent = m.group("indent")

    account_expr = m.group("account_expr").strip()
    tx_expr = m.group("tx_expr").strip()
    amount_expr = m.group("amount_expr").strip()
    credited_expr = m.group("credited_expr").strip()
    ts_expr = m.group("ts_expr").strip()

    replacement = (
        f"{indent}topup = TopUp(\n"
        f"{indent}    account_id={account_expr},\n"
        f"{indent}    tx_hash={tx_expr},\n"
        f"{indent}    amount_usdt={amount_expr},\n"
        f"{indent}    credited_units={credited_expr},\n"
        f"{indent}    ts={ts_expr},\n"
        f"{indent})\n"
    )

    before_lines = before.splitlines()
    start_line = before[: m.start()].count("\n") + 1
    end_line = before[: m.end()].count("\n") + 1

    print("\n--- BEFORE (context) ---")
    print(_context_lines(before_lines, start_line, end_line, radius=5))

    after = before[: m.start()] + replacement + before[m.end() :]

    # Backup
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = target.with_suffix(target.suffix + f".bak.{ts}")
    shutil.copy2(target, backup)
    print("\nBACKUP=", str(backup))

    _write_text(target, after)
    after_sha1 = _sha1_text(after)

    print("SHA1_BEFORE=", before_sha1)
    print("SHA1_AFTER =", after_sha1)

    after_lines = after.splitlines()
    new_end_line = start_line + replacement.count("\n")  # replacement ends with \n
    print("\n--- AFTER (context) ---")
    print(_context_lines(after_lines, start_line, new_end_line, radius=5))

    try:
        _compile_check(target)
        print("\nPY_COMPILE=OK")
    except Exception as ex:
        print("\nPY_COMPILE=FAIL", type(ex).__name__, str(ex))
        return 5

    print("PATCH_APPLIED=1")
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
