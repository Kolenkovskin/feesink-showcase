# FEESINK-APPLY-PATCH-STORAGE-SQLITE-CHECKS-EVENT-TS-FIX v2026.01.16-01
from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime, timezone

PATCH_VERSION = "FEESINK-APPLY-PATCH-STORAGE-SQLITE-CHECKS-EVENT-TS-FIX v2026.01.16-01"
UTC = timezone.utc


def _utc_ts() -> str:
    return datetime.now(tz=UTC).isoformat()


def _stamp() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _backup(path: str, stamp: str) -> str:
    bak = f"{path}.bak.{stamp}"
    shutil.copy2(path, bak)
    return bak


def _py_compile(path: str) -> tuple[bool, str]:
    import py_compile

    try:
        py_compile.compile(path, doraise=True)
        return True, "OK"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    repo_root = r"C:\Users\User\PycharmProjects\feesink"
    target = os.path.join(repo_root, r"feesink\storage\_sqlite_checks.py")

    print("=" * 80)
    print(PATCH_VERSION)
    print("TS_UTC=", _utc_ts())
    print("ROOT=", repo_root)
    print("=" * 80)

    if not os.path.isfile(target):
        print("FATAL: target not found:", target)
        return 2

    before = _read(target)
    before_sha = _sha1_text(before)
    stamp = _stamp()
    bak = _backup(target, stamp)
    print("BACKUP=", bak)
    print("BEFORE_SHA1=", before_sha)

    anchor = "def record_check_and_charge("
    i0 = before.find(anchor)
    if i0 == -1:
        print("FATAL: record_check_and_charge() not found")
        return 3

    # Replace the "try: ... except" block that validates/derives check_id + timestamps.
    # We anchor on the first "try:" after the function start, and the following "except Exception as e:".
    try_pos = before.find("\n        try:\n", i0)
    exc_pos = before.find("\n        except Exception as e:\n", i0)
    if try_pos == -1 or exc_pos == -1 or exc_pos < try_pos:
        print("FATAL: could not locate try/except validation block")
        return 4

    # Find end of except block line: the "raise ValidationError(...)" line and its newline.
    raise_pos = before.find("raise ValidationError(", exc_pos)
    if raise_pos == -1:
        print("FATAL: could not locate ValidationError raise")
        return 5
    raise_line_end = before.find("\n", raise_pos)
    if raise_line_end == -1:
        raise_line_end = raise_pos

    replacement = (
        "\n        try:\n"
        "            # CANON: dedup_key is the idempotency key; domain CheckEvent has no check_id\n"
        "            check_id = str(dedup_key)\n"
        "            result_s = str(event.result.value)\n"
        "\n"
        "            # Domain drift guard:\n"
        "            # - Newer domain model: event.ts\n"
        "            # - Legacy: event.ts_utc\n"
        "            ev_ts = getattr(event, \"ts\", None)\n"
        "            if ev_ts is None:\n"
        "                ev_ts = getattr(event, \"ts_utc\", None)\n"
        "            if ev_ts is None:\n"
        "                raise AttributeError(\"CheckEvent.ts (or ts_utc) is required\")\n"
        "\n"
        "            ts_s = dt_to_str_utc(ensure_utc(ev_ts))\n"
        "            # Until domain carries scheduled_at explicitly, store the same value.\n"
        "            scheduled_at_s = ts_s\n"
        "        except Exception as e:\n"
        "            raise ValidationError(f\"invalid CheckEvent: {e}\") from e\n"
    )

    after = before[:try_pos] + replacement + before[raise_line_end + 1 :]

    _write(target, after)
    ok, msg = _py_compile(target)
    print("PY_COMPILE=", msg)
    if not ok:
        print("FATAL: compile failed; restoring backup")
        shutil.copy2(bak, target)
        return 6

    print("PATCH_APPLIED= 1")
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
