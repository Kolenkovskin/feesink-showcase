"""
Patch: SQLite checks should not require event.check_id (domain model has no check_id).
Policy: use dedup_key as check_id (deterministic, idempotent).

FEESINK-APPLY-PATCH-STORAGE-SQLITE-CHECKS-CHECKID-FIX v2026.01.16-01
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


VERSION = "FEESINK-APPLY-PATCH-STORAGE-SQLITE-CHECKS-CHECKID-FIX v2026.01.16-01"
UTC = timezone.utc


def ts_utc() -> str:
    return datetime.now(tz=UTC).isoformat()


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def write_log(root: Path, text: str) -> None:
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "apply_patch_storage_sqlite_checks_checkid_fix_v01.txt"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    target = root / "feesink" / "storage" / "_sqlite_checks.py"
    backup = target.with_name(target.name + f".bak.{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}")

    out = []
    out.append("=" * 80)
    out.append(VERSION)
    out.append(f"TS_UTC= {ts_utc()}")
    out.append(f"ROOT= {root}")
    out.append("=" * 80)

    if not target.exists():
        out.append(f"FAIL: target not found: {target}")
        print("\n".join(out))
        write_log(root, "\n".join(out))
        return 2

    src = target.read_text(encoding="utf-8")

    # 1) Replace the buggy line: check_id = str(event.check_id)
    #    with deterministic derivation from dedup_key.
    #    Keep it simple and explicit.
    pattern_line = r"^\s*check_id\s*=\s*str\(event\.check_id\)\s*$"
    repl_line = "        check_id = str(dedup_key)  # CANON: dedup_key is the idempotency key; domain CheckEvent has no check_id"
    new_src, n1 = re.subn(pattern_line, repl_line, src, flags=re.MULTILINE)

    # 2) If file contains error wrapping mentioning invalid CheckEvent due to check_id,
    #    keep message but it will no longer trigger on missing attribute.
    if n1 != 1:
        out.append(f"FAIL: expected to patch 1 occurrence of event.check_id line, patched={n1}")
        print("\n".join(out))
        write_log(root, "\n".join(out))
        return 3

    # Write backup + patched file
    backup.write_text(src, encoding="utf-8")
    target.write_text(new_src, encoding="utf-8")

    out.append(f"BACKUP= {backup}")
    out.append("PATCH_APPLIED= 1")
    out.append(f"TOUCHED= {target} sha1={sha1_file(target)}")
    out.append("DONE")

    print("\n".join(out))
    write_log(root, "\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
