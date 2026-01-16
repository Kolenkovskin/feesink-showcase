# Path: scripts/apply_patch_server_stripe_webhook_accountid_topup_fix_v15.py
"""
FeeSink patch script — Stripe webhook: account_id source + TopUp signature fix (v15)

Target:
  C:\\Users\\User\\PycharmProjects\\feesink\\feesink\\api\\server.py

What it does (P0):
  1) Replace the exact block:
       # 4) Resolve account_id via stripe_links (session_id -> account_id)
       ...
       # 5) Determine price_id ...
     with:
       - primary: metadata["account_id"]
       - fallback: storage.resolve_account_by_stripe_session(session_id)
       - explicit log + 5xx on failure

  2) Replace the exact TopUp(...) ctor using topup_id/created_at_utc
     with canonical:
       TopUp(account_id=..., tx_hash=..., amount_usdt=..., credited_units=..., ts=now)

Safety:
  - creates timestamped .bak
  - requires EXACTLY 1 match for each patch
  - restores backup on mismatch/compile failure
  - append-only log file in:
      C:\\Users\\User\\PycharmProjects\\feesink\\logs\\apply_patch_server_stripe_webhook_accountid_topup_fix_v15.txt
"""

from __future__ import annotations

import os
import re
import shutil
import hashlib
from datetime import datetime, timezone


PATCH_VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-WEBHOOK-ACCOUNTID-TOPUP-FIX v2026.01.05-15"
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


def _apply_one(content: str, pattern: str, replacement: str) -> tuple[str, int]:
    ms = list(re.finditer(pattern, content, flags=re.DOTALL))
    if len(ms) != 1:
        return content, len(ms)
    out = re.sub(pattern, replacement, content, count=1, flags=re.DOTALL)
    return out, 1 if out != content else 0


def main() -> int:
    repo_root = r"C:\Users\User\PycharmProjects\feesink"
    target = os.path.join(repo_root, r"feesink\api\server.py")
    log_path = os.path.join(repo_root, "logs", "apply_patch_server_stripe_webhook_accountid_topup_fix_v15.txt")

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

    patched = before

    # ------------------------------------------------------------------
    # Patch 1: account_id resolution block (EXACT markers)
    # ------------------------------------------------------------------
    account_pat = r"""
\#\ 4\)\ Resolve\ account_id\ via\ stripe_links\ \(session_id\ \-\>\ account_id\)\n
.*?
\#\ 5\)\ Determine\ price_id\ \(prefer\ metadata;\ fallback\ to\ Stripe\ API\ GET\ with\ expand\ line_items\)\n
"""
    account_repl = r"""# 4) Resolve account_id (PRIMARY: metadata.account_id; FALLBACK: stripe_links session_id->account_id)
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

    patched, c1 = _apply_one(patched, account_pat, account_repl)

    # ------------------------------------------------------------------
    # Patch 2: TopUp ctor block (EXACT field form)
    # ------------------------------------------------------------------
    topup_pat = r"""
topup\s*=\s*TopUp\(\s*
\s*topup_id\s*=\s*tx_hash\s*,\s*
\s*account_id\s*=\s*str\(account_id\)\s*,\s*
\s*tx_hash\s*=\s*tx_hash\s*,\s*
\s*amount_usdt\s*=\s*Decimal\(str\(amount_usdt\)\)\s*,\s*
\s*credited_units\s*=\s*int\(credited_units\)\s*,\s*
\s*created_at_utc\s*=\s*now\s*,\s*
\s*\)\s*
"""
    topup_repl = """topup = TopUp(
                account_id=str(account_id),
                tx_hash=tx_hash,
                amount_usdt=Decimal(str(amount_usdt)),
                credited_units=int(credited_units),
                ts=now,
            )
"""

    patched, c2 = _apply_one(patched, topup_pat, topup_repl)

    counts = f"PATCH_ACCOUNT_ID_MATCHES={c1}\nPATCH_TOPUP_CTOR_MATCHES={c2}\n"
    print(counts, end="")
    _append_log(log_path, counts.rstrip("\n"))

    if c1 != 1 or c2 != 1:
        msg = (
            "FATAL: patch match counts are not exactly 1 each.\n"
            f"  account_id block matches: {c1}\n"
            f"  topup ctor matches:       {c2}\n"
            "NO CHANGES WRITTEN (restoring original from backup)\n"
        )
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 3

    after_sha = _sha1_text(patched)
    _write(target, patched)

    ok, comp = _py_compile(target)
    msg2 = f"AFTER_SHA1= {after_sha}\nPY_COMPILE= {comp}\n"
    print(msg2, end="")
    _append_log(log_path, msg2.rstrip("\n"))

    if not ok:
        msg = "FATAL: python compile failed. Restoring backup.\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 4

    done = "DONE\n"
    print(done, end="")
    _append_log(log_path, done.rstrip("\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
