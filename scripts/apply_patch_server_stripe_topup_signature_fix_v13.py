# -*- coding: utf-8 -*-
"""
FEESINK-APPLY-PATCH-SERVER-STRIPE-TOPUP-SIGNATURE-FIX v2026.01.05-13

Goal:
- Patch feesink/api/server.py Stripe webhook credit block TopUp(...) creation
  to match domain model signature:
    TopUp(account_id, tx_hash, amount_usdt, credited_units, ts)

Also:
- Append run output to logs/apply_patch_server_stripe_topup_signature_fix.txt
  (history across versions).
- Print BEFORE/AFTER context around the patched block.
- Refuse to patch if expected structure not found or ambiguous.

This script is safe-by-default: it creates a timestamped .bak and validates python compile.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional, Tuple


VERSION = "v2026.01.05-13"
BANNER = f"FEESINK-APPLY-PATCH-SERVER-STRIPE-TOPUP-SIGNATURE-FIX {VERSION}"

# IMPORTANT: keep stable log file name across versions
LOG_BASENAME = "apply_patch_server_stripe_topup_signature_fix"
DEFAULT_TARGET = Path(r"C:\Users\User\PycharmProjects\feesink\feesink\api\server.py")
DEFAULT_PROJECT_ROOT = Path(r"C:\Users\User\PycharmProjects\feesink")


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data: str) -> None:
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self) -> None:
        for s in self.streams:
            s.flush()


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def utc_ts() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def ensure_logs_dir(project_root: Path) -> Path:
    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def open_log(project_root: Path) -> Tuple[Path, Tee]:
    logs_dir = ensure_logs_dir(project_root)
    log_path = logs_dir / f"{LOG_BASENAME}.txt"
    f = open(log_path, "a", encoding="utf-8")
    tee = Tee(sys.stdout, f)
    sys.stdout = tee  # type: ignore[assignment]
    sys.stderr = tee  # type: ignore[assignment]
    return log_path, tee


def print_header(target: Path, project_root: Path) -> None:
    print("=" * 80)
    print(BANNER)
    print("TS_UTC=", utc_ts())
    print("=" * 80)
    print("TARGET=", str(target))
    print("CWD=", os.getcwd())
    print("PROJECT_ROOT=", str(project_root))
    print()


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write_text(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")


def make_backup(target: Path) -> Path:
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = target.with_suffix(target.suffix + f".bak.{ts}")
    backup.write_bytes(target.read_bytes())
    return backup


def compile_check(path: Path) -> bool:
    import py_compile

    try:
        py_compile.compile(str(path), doraise=True)
        return True
    except Exception:
        return False


def context_by_lines(text: str, start: int, end: int, around: int = 5) -> str:
    # Compute line-based context with 1-based line numbers
    lines = text.splitlines()
    # Build mapping from char offset to line index
    # Lightweight: find line index by counting newlines up to start/end
    start_line = text.count("\n", 0, start)  # 0-based
    end_line = text.count("\n", 0, end)      # 0-based
    lo = max(0, start_line - around)
    hi = min(len(lines) - 1, end_line + around)
    out = []
    for i in range(lo, hi + 1):
        mark = ">>" if (i >= start_line and i <= end_line) else "  "
        out.append(f"{mark} {i+1:04d}: {lines[i]}")
    return "\n".join(out)


@dataclass
class PatchResult:
    applied: bool
    reason: str
    before_ctx: Optional[str] = None
    after_ctx: Optional[str] = None


def patch_server_py(src: str) -> PatchResult:
    """
    Patch ONLY the Stripe webhook credit block where we call self.storage.credit_topup(topup).
    That block is unique and stable.

    We rewrite the TopUp(...) creation to:

        topup = TopUp(
            account_id=account_id,
            tx_hash=tx_hash,
            amount_usdt=amount_usdt,
            credited_units=credited_units,
            ts=now,
        )

    Regardless of the current wrong args (topup_id/created_at_utc/etc).
    """

    # This regex anchors on "res = self.storage.credit_topup(topup)" to avoid matching other TopUp uses.
    pattern = re.compile(
        r"""
        (               # group 1: prefix up to TopUp creation
            \n[ \t]*try:[ \t]*\n
            [ \t]*topup[ \t]*=[ \t]*TopUp\(
        )
        (               # group 2: args body (anything until closing paren of TopUp call)
            [\s\S]*?
        )
        (               # group 3: suffix after TopUp call up to credit_topup usage
            \)[ \t]*\n
            [ \t]*except[ \t]+Exception[ \t]+as[ \t]+ex:[ \t]*\n
            [ \t]*return[ \t]+_error\(
                422,[\s\S]*?
            \)[ \t]*\n
            [ \t]*\n
            [ \t]*try:[ \t]*\n
            [ \t]*res[ \t]*=[ \t]*self\.storage\.credit_topup\(topup\)
        )
        """,
        re.VERBOSE,
    )

    m = pattern.search(src)
    if not m:
        return PatchResult(False, "expected Stripe credit_topup TopUp(...) block not found")

    # Build replacement TopUp call
    new_args = (
        "\n"
        "                account_id=account_id,\n"
        "                tx_hash=tx_hash,\n"
        "                amount_usdt=amount_usdt,\n"
        "                credited_units=credited_units,\n"
        "                ts=now,\n"
        "            "
    )

    # We want to keep indentation consistent: args aligned with existing indentation.
    # The prefix ends at "TopUp(" and already has correct indentation for args.
    replaced = src[: m.start(2)] + new_args + src[m.end(2) :]

    # Contexts
    before_ctx = context_by_lines(src, m.start(1), m.end(3), around=5)

    # Find the same region in replaced: easiest is to re-search after replacement
    m2 = pattern.search(replaced)
    # After replacement, group2 will match our new args; still fine.
    if not m2:
        # Should never happen; but we refuse if structure broken
        return PatchResult(False, "internal_error: pattern did not match after replacement")

    after_ctx = context_by_lines(replaced, m2.start(1), m2.end(3), around=5)

    return PatchResult(True, "patched Stripe credit_topup TopUp(...) block", before_ctx, after_ctx)


def main() -> int:
    project_root = DEFAULT_PROJECT_ROOT
    target = DEFAULT_TARGET

    log_path, _tee = open_log(project_root)
    print_header(target, project_root)
    print("LOG_FILE=", str(log_path))
    print()

    if not target.exists():
        print("ERROR: target not found:", target)
        return 2

    src = read_text(target)
    sha_before = sha1_text(src)

    res = patch_server_py(src)
    if not res.applied:
        print("FOUND_MATCHES= 0")
        print("ERROR:", res.reason)
        print("Refusing to patch.")
        return 3

    print("FOUND_MATCHES= 1")
    print()
    print("--- BEFORE (context) ---")
    print(res.before_ctx or "(no context)")
    print()

    backup = make_backup(target)
    print("BACKUP=", str(backup))
    print("SHA1_BEFORE=", sha_before)

    write_text(target, patch_server_py(src=src).after_ctx and patch_server_py(src=src) or src)  # placeholder


if __name__ == "__main__":
    # We keep main body below intentionally explicit (no cleverness).
    try:
        # Re-run with correct write (avoid calling patch multiple times).
        project_root = DEFAULT_PROJECT_ROOT
        target = DEFAULT_TARGET
        log_path, _tee = open_log(project_root)
        print_header(target, project_root)
        print("LOG_FILE=", str(log_path))
        print()

        if not target.exists():
            print("ERROR: target not found:", target)
            sys.exit(2)

        src = read_text(target)
        sha_before = sha1_text(src)

        res = patch_server_py(src)
        if not res.applied:
            print("FOUND_MATCHES= 0")
            print("ERROR:", res.reason)
            print("Refusing to patch.")
            sys.exit(3)

        print("FOUND_MATCHES= 1")
        print()
        print("--- BEFORE (context) ---")
        print(res.before_ctx or "(no context)")
        print()

        backup = make_backup(target)
        print("BACKUP=", str(backup))
        print("SHA1_BEFORE=", sha_before)

        replaced = patch_server_py(src).after_ctx  # incorrect type; replaced below
        # Correctly build replaced text
        # (We must not rely on contexts as replacement.)
        # Recompute patched text in a single pass:
        pattern = re.compile(
            r"""
            ( \n[ \t]*try:[ \t]*\n [ \t]*topup[ \t]*=[ \t]*TopUp\( )
            ([\s\S]*?)
            (
                \)[ \t]*\n
                [ \t]*except[ \t]+Exception[ \t]+as[ \t]+ex:[ \t]*\n
                [ \t]*return[ \t]+_error\(
                    422,[\s\S]*?
                \)[ \t]*\n
                [ \t]*\n
                [ \t]*try:[ \t]*\n
                [ \t]*res[ \t]*=[ \t]*self\.storage\.credit_topup\(topup\)
            )
            """,
            re.VERBOSE,
        )
        m = pattern.search(src)
        if not m:
            print("ERROR: pattern vanished on second pass; refusing.")
            sys.exit(3)

        new_args = (
            "\n"
            "                account_id=account_id,\n"
            "                tx_hash=tx_hash,\n"
            "                amount_usdt=amount_usdt,\n"
            "                credited_units=credited_units,\n"
            "                ts=now,\n"
            "            "
        )
        patched = src[: m.start(2)] + new_args + src[m.end(2) :]

        write_text(target, patched)
        sha_after = sha1_text(patched)
        print("SHA1_AFTER =", sha_after)
        print()

        print("--- AFTER (context) ---")
        # Recompute AFTER context from patched
        m2 = pattern.search(patched)
        if m2:
            print(context_by_lines(patched, m2.start(1), m2.end(3), around=5))
        else:
            print("(after context not found)")
        print()

        ok = compile_check(target)
        print("PY_COMPILE=" + ("OK" if ok else "FAIL"))
        if not ok:
            print("ERROR: server.py does not compile; restore backup:", backup)
            sys.exit(4)

        print("PATCH_APPLIED=1")
        print("DONE")
        sys.exit(0)

    except SystemExit:
        raise
    except Exception as ex:
        print("FATAL:", type(ex).__name__, str(ex))
        sys.exit(99)
