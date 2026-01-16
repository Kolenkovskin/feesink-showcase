# Path: scripts/apply_patch_sqlite_credit_topup_compat_v01.py
"""
Patch: make SQLiteStorage.credit_topup compatible with BOTH TopUp shapes:

OLD shape (legacy):
  topup.topup_id
  topup.created_at_utc

NEW shape (current canon):
  topup.tx_hash (unique)
  topup.ts (UTC)

Rules:
- idempotency remains by topups.tx_hash (already enforced by DB UNIQUE if present)
- storage will derive:
    topup_id = topup.topup_id if present else topup.tx_hash
    created_at = topup.created_at_utc if present else topup.ts
- keep existing behavior otherwise

Target:
  C:\\Users\\User\\PycharmProjects\\feesink\\feesink\\storage\\sqlite.py

Safety:
- timestamped backup
- patch only credit_topup() prelude (validation + created_s derivation)
- restore on mismatch/compile failure
- append-only log:
  C:\\Users\\User\\PycharmProjects\\feesink\\logs\\apply_patch_sqlite_credit_topup_compat_v01.txt
"""

from __future__ import annotations

import os
import shutil
import hashlib
from datetime import datetime, timezone

PATCH_VERSION = "FEESINK-APPLY-PATCH-SQLITE-CREDIT-TOPUP-COMPAT v2026.01.05-01"
UTC = timezone.utc


def _utc_ts() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _append_log(path: str, text: str) -> None:
    _ensure_dir(os.path.dirname(path))
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


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
    target = os.path.join(repo_root, r"feesink\storage\sqlite.py")
    log_path = os.path.join(repo_root, "logs", "apply_patch_sqlite_credit_topup_compat_v01.txt")

    banner = (
        "=" * 80 + "\n"
        + f"{PATCH_VERSION}\n"
        + f"TS_UTC= {_utc_ts()}\n"
        + "=" * 80 + "\n"
        + f"TARGET= {target}\n"
    )
    print(banner, end="")
    _append_log(log_path, banner.rstrip("\n"))

    if not os.path.isfile(target):
        msg = f"FATAL: target not found: {target}\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        return 2

    before = _read(target)
    before_sha = _sha1_text(before)
    stamp = _stamp()
    bak = _backup(target, stamp)

    info = f"BACKUP= {bak}\nBEFORE_SHA1= {before_sha}\n"
    print(info, end="")
    _append_log(log_path, info.rstrip("\n"))

    # Anchor inside credit_topup
    fn_anchor = "def credit_topup(self, topup: TopUp) -> CreditResult:"
    i0 = before.find(fn_anchor)
    if i0 == -1:
        msg = "FATAL: credit_topup() anchor not found\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 3

    # We replace the *prelude* from:
    #   if not topup:
    # down to:
    #   created_s = _dt_to_str_utc(ensure_utc(topup.created_at_utc))
    # (inclusive)
    pre_start = before.find("        if not topup:", i0)
    pre_end = before.find("        self._conn.execute(\"BEGIN IMMEDIATE;\")", i0)
    if pre_start == -1 or pre_end == -1 or pre_end <= pre_start:
        msg = "FATAL: could not locate credit_topup() prelude bounds\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 3

    old_prelude = before[pre_start:pre_end]

    # Safety check: ensure old_prelude contains at least one legacy field reference
    if "topup.created_at_utc" not in old_prelude and "topup.topup_id" not in old_prelude:
        msg = "FATAL: legacy prelude markers not found (unexpected credit_topup layout)\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 3

    new_prelude = """        if not topup:
            raise ValidationError("topup must be provided")

        # Compat: accept both legacy TopUp (topup_id/created_at_utc) and canon TopUp (tx_hash/ts)
        topup_id = getattr(topup, "topup_id", None)
        if not topup_id:
            topup_id = getattr(topup, "tx_hash", None)

        created_at = getattr(topup, "created_at_utc", None)
        if created_at is None:
            created_at = getattr(topup, "ts", None)

        if not topup_id or not str(topup_id).strip():
            raise ValidationError("topup_id must be non-empty (topup.topup_id or topup.tx_hash)")
        if not topup.account_id or not str(topup.account_id).strip():
            raise ValidationError("topup.account_id must be non-empty")
        if not topup.tx_hash or not str(topup.tx_hash).strip():
            raise ValidationError("topup.tx_hash must be non-empty")
        if created_at is None:
            raise ValidationError("topup timestamp must be provided (topup.created_at_utc or topup.ts)")

        now_s = _dt_to_str_utc(datetime.now(tz=UTC))
        created_s = _dt_to_str_utc(ensure_utc(created_at))

"""

    after = before[:pre_start] + new_prelude + before[pre_end:]

    # Also patch INSERT bind to use computed topup_id if code still uses topup.topup_id later.
    # Minimal-safe: replace occurrences of "str(topup.topup_id)" with "str(topup_id)" inside credit_topup.
    # We scope it by only replacing after pre_start and before the next "def " at same indent if present.
    scope_end = after.find("\n    def ", pre_start)
    if scope_end == -1:
        scope_end = len(after)

    scoped = after[pre_start:scope_end]
    replaced_count = scoped.count("str(topup.topup_id)")
    scoped2 = scoped.replace("str(topup.topup_id)", "str(topup_id)")
    after2 = after[:pre_start] + scoped2 + after[scope_end:]

    patch_info = (
        f"PATCH_PRELUDE_APPLIED=1\n"
        f"PATCH_STR_TOPUP_ID_REPLACEMENTS={replaced_count}\n"
        f"AFTER_SHA1= {_sha1_text(after2)}\n"
    )
    print(patch_info, end="")
    _append_log(log_path, patch_info.rstrip("\n"))

    _write(target, after2)
    ok, comp = _py_compile(target)
    msg2 = f"PY_COMPILE= {comp}\n"
    print(msg2, end="")
    _append_log(log_path, msg2.rstrip("\n"))

    if not ok:
        msg = "FATAL: python compile failed. Restoring backup.\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 4

    print("DONE\n", end="")
    _append_log(log_path, "DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
