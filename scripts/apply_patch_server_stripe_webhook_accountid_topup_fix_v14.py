# Path: scripts/apply_patch_server_stripe_webhook_accountid_topup_fix_v14.py
"""
FeeSink patch script — Stripe webhook: account_id source + TopUp signature fix

Applies to:
  C:\\Users\\User\\PycharmProjects\\feesink\\feesink\\api\\server.py

Fixes (P0):
  1) account_id resolution:
       primary = event.data.object.metadata.account_id
       fallback = storage.resolve_account_by_stripe_session(session_id)
  2) TopUp construction in webhook:
       TopUp(account_id, tx_hash, amount_usdt, credited_units, ts=now)

Safety:
  - creates .bak backup with UTC timestamp
  - checks match counts (must be exactly 1 per patch)
  - compiles target file after patch
  - writes append-only log to:
      C:\\Users\\User\\PycharmProjects\\feesink\\logs\\apply_patch_server_stripe_webhook_accountid_topup_fix_v14.txt
"""

from __future__ import annotations

import io
import os
import re
import sys
import shutil
import hashlib
from datetime import datetime, timezone

PATCH_VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-WEBHOOK-ACCOUNTID-TOPUP-FIX v2026.01.05-14"
UTC = timezone.utc


def _utc_ts() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _utc_stamp() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _append_log(log_path: str, text: str) -> None:
    _ensure_dir(os.path.dirname(log_path))
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _backup_file(path: str, stamp: str) -> str:
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


def _apply_one_patch(content: str, pattern: str, replacement: str, label: str) -> tuple[str, int]:
    matches = list(re.finditer(pattern, content, flags=re.DOTALL))
    if len(matches) != 1:
        return content, len(matches)
    new_content = re.sub(pattern, replacement, content, count=1, flags=re.DOTALL)
    if new_content == content:
        # Should not happen if matched, but keep deterministic
        return content, 0
    return new_content, 1


def main() -> int:
    repo_root = r"C:\Users\User\PycharmProjects\feesink"
    target = os.path.join(repo_root, r"feesink\api\server.py")
    logs_dir = os.path.join(repo_root, "logs")
    log_path = os.path.join(logs_dir, "apply_patch_server_stripe_webhook_accountid_topup_fix_v14.txt")

    banner = (
        "=" * 80
        + "\n"
        + f"{PATCH_VERSION}\n"
        + f"TS_UTC= {_utc_ts()}\n"
        + "=" * 80
        + "\n"
        + f"TARGET= {target}\n"
    )

    # stdout + file
    print(banner, end="")
    _append_log(log_path, banner.rstrip("\n"))

    if not os.path.isfile(target):
        msg = f"FATAL: target not found: {target}\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        return 2

    before = _read_file(target)
    before_sha1 = _sha1_text(before)
    stamp = _utc_stamp()
    backup = _backup_file(target, stamp)

    info = f"BACKUP= {backup}\nBEFORE_SHA1= {before_sha1}\n"
    print(info, end="")
    _append_log(log_path, info.rstrip("\n"))

    patched = before

    # ------------------------------------------------------------------
    # Patch 1: account_id resolution (metadata primary, stripe_links fallback)
    # ------------------------------------------------------------------
    # We replace the entire block:
    #   # 4) Resolve account_id via stripe_links ... try/except ...
    #
    # with a new block that:
    #   - tries metadata["account_id"] first
    #   - falls back to resolve_account_by_stripe_session(session_id)
    #   - keeps error logging + 5xx behavior on failure
    #
    account_block_pattern = r"""
        \#\s*4\)\s*Resolve\ account_id\ via\ stripe_links.*?
        (?:
            \#\s*5\)\s*Determine\ price_id
        )
    """

    account_block_replacement = r"""
        # 4) Resolve account_id (PRIMARY: metadata.account_id; FALLBACK: stripe_links session_id->account_id)
        account_id_source = None
        account_id = None

        # Primary: metadata.account_id (contract-preferred)
        if isinstance(metadata, dict):
            v = metadata.get("account_id")
            if v is not None:
                v2 = str(v).strip()
                if v2:
                    account_id = v2
                    account_id_source = "metadata"

        # Fallback: stripe_links (session_id -> account_id)
        if not account_id:
            if not hasattr(self.storage, "resolve_account_by_stripe_session"):
                return _error(500, "internal_error", "Storage does not support stripe_links (resolve_account_by_stripe_session)", {})

            try:
                account_id = self.storage.resolve_account_by_stripe_session(session_id)  # type: ignore[attr-defined]
                account_id = str(account_id).strip() if account_id is not None else ""
                if not account_id:
                    raise ValueError("resolved_empty_account_id")
                account_id_source = "stripe_links"
            except Exception as ex:
                # P0 invariant: unresolved reason must be explicit; return non-2xx to force Stripe retry.
                print(
                    json.dumps(
                        {
                            "provider": "stripe",
                            "decision": "unresolved_account",
                            "event_id": event_id,
                            "event_type": event_type,
                            "session_id": session_id,
                            "payment_status": payment_status,
                            "account_id": None,
                            "account_id_source": None,
                            "price_id": None,
                            "credited_units": None,
                            "reason": "account_id_not_resolved",
                            "exception": type(ex).__name__,
                        },
                        ensure_ascii=False,
                    )
                )
                return _error(500, "internal_error", "Unable to resolve account_id", {"session_id": session_id})

        # 5) Determine price_id (prefer metadata; fallback to Stripe API GET with expand line_items)
"""

    patched, applied_1 = _apply_one_patch(
        patched,
        account_block_pattern,
        account_block_replacement,
        label="account_id_resolution",
    )

    # ------------------------------------------------------------------
    # Patch 2: TopUp construction signature in webhook
    # ------------------------------------------------------------------
    # Replace TopUp(...) in webhook path that uses topup_id/created_at_utc
    # with canonical signature: TopUp(account_id, tx_hash, amount_usdt, credited_units, ts)
    #
    topup_ctor_pattern = r"""
        topup\s*=\s*TopUp\(\s*
            topup_id\s*=\s*tx_hash\s*,\s*
            account_id\s*=\s*str\(account_id\)\s*,\s*
            tx_hash\s*=\s*tx_hash\s*,\s*
            amount_usdt\s*=\s*Decimal\(str\(amount_usdt\)\)\s*,\s*
            credited_units\s*=\s*int\(credited_units\)\s*,\s*
            created_at_utc\s*=\s*now\s*,\s*
        \)\s*
    """

    topup_ctor_replacement = r"""
            topup = TopUp(
                account_id=str(account_id),
                tx_hash=tx_hash,
                amount_usdt=Decimal(str(amount_usdt)),
                credited_units=int(credited_units),
                ts=now,
            )
"""

    patched, applied_2 = _apply_one_patch(
        patched,
        topup_ctor_pattern,
        topup_ctor_replacement,
        label="topup_ctor_signature",
    )

    # ------------------------------------------------------------------
    # Validate patch application counts
    # ------------------------------------------------------------------
    counts = f"PATCH_ACCOUNT_ID_APPLIED={applied_1}\nPATCH_TOPUP_CTOR_APPLIED={applied_2}\n"
    print(counts, end="")
    _append_log(log_path, counts.rstrip("\n"))

    if applied_1 != 1 or applied_2 != 1:
        msg = (
            "FATAL: patch match counts are not exactly 1 each.\n"
            f"  account_id block applied: {applied_1}\n"
            f"  topup ctor applied:      {applied_2}\n"
            "NO CHANGES WRITTEN (restoring original from backup)\n"
        )
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))

        # Restore original (deterministic)
        shutil.copy2(backup, target)
        return 3

    after_sha1 = _sha1_text(patched)
    _write_file(target, patched)

    ok, comp = _py_compile(target)
    comp_msg = f"AFTER_SHA1= {after_sha1}\nPY_COMPILE= {comp}\n"
    print(comp_msg, end="")
    _append_log(log_path, comp_msg.rstrip("\n"))

    if not ok:
        msg = "FATAL: python compile failed. Restoring backup.\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(backup, target)
        return 4

    done = "DONE\n"
    print(done, end="")
    _append_log(log_path, done.rstrip("\n"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
