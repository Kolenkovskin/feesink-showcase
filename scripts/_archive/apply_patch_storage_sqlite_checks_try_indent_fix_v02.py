"""
Fix: IndentationError in feesink/storage/_sqlite_checks.py

Current broken snippet:
    try:
    check_id = str(dedup_key)
        result_s = ...

Correct:
    try:
        check_id = str(dedup_key)
        result_s = ...

FEESINK-APPLY-PATCH-STORAGE-SQLITE-CHECKS-TRY-INDENT-FIX v2026.01.16-02
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from datetime import datetime, timezone


VERSION = "FEESINK-APPLY-PATCH-STORAGE-SQLITE-CHECKS-TRY-INDENT-FIX v2026.01.16-02"
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

    # Fix the exact broken pattern: try:\n<same-indent>check_id =
    # In this file it currently appears as:
    # "        try:\n        check_id = ..."
    # and we need:
    # "        try:\n            check_id = ..."
    patched, n = re.subn(
        r"(^[ \t]*try:\n)(^[ \t]*)(check_id\s*=\s*str\(dedup_key\).*$)",
        lambda m: m.group(1) + (m.group(2) + "    ") + m.group(3),
        src,
        flags=re.MULTILINE,
    )

    if n != 1:
        print(f"FAIL: expected 1 indentation fix, got {n}")
        print("HINT: open feesink/storage/_sqlite_checks.py around the try: block and check formatting.")
        return 3

    target.write_text(patched, encoding="utf-8")

    print(f"BACKUP= {backup}")
    print("PATCH_APPLIED= 1")
    print(f"TOUCHED= {target} sha1={sha1_file(target)}")
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
