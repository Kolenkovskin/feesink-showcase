"""
Patch: fix indentation after try: in _sqlite_checks.py
Reason: previous patch replaced line but lost indentation, causing IndentationError.

FEESINK-APPLY-PATCH-STORAGE-SQLITE-CHECKS-INDENT-FIX v2026.01.16-01
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from datetime import datetime, timezone


VERSION = "FEESINK-APPLY-PATCH-STORAGE-SQLITE-CHECKS-INDENT-FIX v2026.01.16-01"
UTC = timezone.utc


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    target = root / "feesink" / "storage" / "_sqlite_checks.py"
    backup = target.with_name(
        target.name + f".bak.{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )

    print("=" * 80)
    print(VERSION)
    print("TS_UTC=", datetime.now(tz=UTC).isoformat())
    print("ROOT=", root)
    print("=" * 80)

    if not target.exists():
        print(f"FAIL: target not found: {target}")
        return 2

    src = target.read_text(encoding="utf-8")
    backup.write_text(src, encoding="utf-8")

    # Fix indentation of check_id assignment inside try block
    src = src.replace(
        "try:\ncheck_id = str(dedup_key)",
        "try:\n        check_id = str(dedup_key)",
    )

    target.write_text(src, encoding="utf-8")

    print(f"BACKUP= {backup}")
    print("PATCH_APPLIED= 1")
    print(f"TOUCHED= {target} sha1={sha1_file(target)}")
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
