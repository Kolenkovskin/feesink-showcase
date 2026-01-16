# Path: scripts/apply_patch_server_stripe_webhook_accountid_topup_fix_v17.py
"""
FeeSink patch script — Stripe webhook (NO REGEX GUESSING).

What it changes in feesink/api/server.py:
1) Inserts a non-functional comment at file top:
   # DO NOT PATCH BY GUESSING REGEX; ALWAYS EXTRACT CONTEXT FIRST

2) Replaces the whole block between:
   "# 4) Resolve account_id via stripe_links (session_id -> account_id)"
   and
   "# 5) Determine price_id ..."
   with logic:
   - PRIMARY: metadata.account_id
   - FALLBACK: storage.resolve_account_by_stripe_session(session_id)

3) Replaces webhook TopUp ctor that uses topup_id/created_at_utc
   with canonical:
     TopUp(account_id=..., tx_hash=..., amount_usdt=..., credited_units=..., ts=now)

Safety:
- timestamped backup
- deterministic match checks
- restore on failure
- append-only log:
  C:\\Users\\User\\PycharmProjects\\feesink\\logs\\apply_patch_server_stripe_webhook_accountid_topup_fix_v17.txt
"""

from __future__ import annotations

import os
import shutil
import hashlib
from datetime import datetime, timezone

PATCH_VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-WEBHOOK-ACCOUNTID-TOPUP-FIX v2026.01.05-17"
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
    target = os.path.join(repo_root, r"feesink\api\server.py")
    log_path = os.path.join(repo_root, "logs", "apply_patch_server_stripe_webhook_accountid_topup_fix_v17.txt")

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

    text = before

    # (1) Insert comment at top if missing (non-functional)
    marker_comment = "# DO NOT PATCH BY GUESSING REGEX; ALWAYS EXTRACT CONTEXT FIRST"
    if marker_comment not in text:
        # Insert after shebang (if any) or at very start
        if text.startswith("#!"):
            nl = text.find("\n")
            if nl != -1:
                text = text[: nl + 1] + marker_comment + "\n" + text[nl + 1 :]
            else:
                text = text + "\n" + marker_comment + "\n"
        else:
            text = marker_comment + "\n" + text
        comment_applied = 1
    else:
        comment_applied = 0

    # (2) Replace account_id block by anchors
    a1 = "# 4) Resolve account_id via stripe_links (session_id -> account_id)"
    a2 = "# 5) Determine price_id"
    i1 = text.find(a1)
    if i1 == -1:
        msg = "FATAL: anchor a1 not found for account_id block\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 3

    i2 = text.find(a2, i1)
    if i2 == -1:
        msg = "FATAL: anchor a2 not found for account_id block\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 3

    # Keep indentation of the anchor line
    line_start = text.rfind("\n", 0, i1) + 1
    indent = ""
    for ch in text[line_start:i1]:
        if ch in (" ", "\t"):
            indent += ch
        else:
            break

    new_block = (
        f"{indent}# 4) Resolve account_id (PRIMARY: metadata.account_id; FALLBACK: stripe_links session_id->account_id)\n"
        f"{indent}account_id_source = None\n"
        f"{indent}account_id = None\n"
        f"{indent}\n"
        f"{indent}# Primary: metadata.account_id (contract-preferred)\n"
        f"{indent}if isinstance(metadata, dict):\n"
        f"{indent}    v = metadata.get(\"account_id\")\n"
        f"{indent}    if v is not None:\n"
        f"{indent}        v2 = str(v).strip()\n"
        f"{indent}        if v2:\n"
        f"{indent}            account_id = v2\n"
        f"{indent}            account_id_source = \"metadata\"\n"
        f"{indent}\n"
        f"{indent}# Fallback: stripe_links (session_id -> account_id)\n"
        f"{indent}if not account_id:\n"
        f"{indent}    if not hasattr(self.storage, \"resolve_account_by_stripe_session\"):\n"
        f"{indent}        return _error(500, \"internal_error\", \"Storage does not support stripe_links (resolve_account_by_stripe_session)\", {{}})\n"
        f"{indent}\n"
        f"{indent}    try:\n"
        f"{indent}        account_id = self.storage.resolve_account_by_stripe_session(session_id)  # type: ignore[attr-defined]\n"
        f"{indent}        account_id = str(account_id).strip() if account_id is not None else \"\"\n"
        f"{indent}        if not account_id:\n"
        f"{indent}            raise ValueError(\"resolved_empty_account_id\")\n"
        f"{indent}        account_id_source = \"stripe_links\"\n"
        f"{indent}    except Exception as ex:\n"
        f"{indent}        # P0 invariant: unresolved reason must be explicit; return non-2xx to force Stripe retry.\n"
        f"{indent}        print(\n"
        f"{indent}            json.dumps(\n"
        f"{indent}                {{\n"
        f"{indent}                    \"provider\": \"stripe\",\n"
        f"{indent}                    \"decision\": \"unresolved_account\",\n"
        f"{indent}                    \"event_id\": event_id,\n"
        f"{indent}                    \"event_type\": event_type,\n"
        f"{indent}                    \"session_id\": session_id,\n"
        f"{indent}                    \"payment_status\": payment_status,\n"
        f"{indent}                    \"account_id\": None,\n"
        f"{indent}                    \"account_id_source\": None,\n"
        f"{indent}                    \"price_id\": None,\n"
        f"{indent}                    \"credited_units\": None,\n"
        f"{indent}                    \"reason\": \"account_id_not_resolved\",\n"
        f"{indent}                    \"exception\": type(ex).__name__,\n"
        f"{indent}                }},\n"
        f"{indent}                ensure_ascii=False,\n"
        f"{indent}            )\n"
        f"{indent}        )\n"
        f"{indent}        return _error(500, \"internal_error\", \"Unable to resolve account_id for session_id\", {{\"session_id\": session_id}})\n"
        f"{indent}\n"
    )

    # Replace inclusive old block: from a1 line start to just before a2
    old_part = text[i1:i2]
    text = text[:i1] + new_block + text[i2:]
    account_block_applied = 1 if old_part not in text else 1  # deterministic: anchors found => applied

    # (3) Replace webhook TopUp ctor (the one with topup_id / created_at_utc)
    old_topup = (
        "topup = TopUp(\n"
        "                  topup_id=tx_hash,\n"
        "                  account_id=str(account_id),\n"
        "                  tx_hash=tx_hash,\n"
        "                  amount_usdt=Decimal(str(amount_usdt)),\n"
        "                  credited_units=int(credited_units),\n"
        "                  created_at_utc=now,\n"
        "              )"
    )

    count_topup = text.count(old_topup)
    if count_topup != 1:
        msg = (
            "FATAL: TopUp webhook ctor template count is not exactly 1.\n"
            f"TOPUP_TEMPLATE_COUNT={count_topup}\n"
        )
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 4

    new_topup = (
        "topup = TopUp(\n"
        "                  account_id=str(account_id),\n"
        "                  tx_hash=tx_hash,\n"
        "                  amount_usdt=Decimal(str(amount_usdt)),\n"
        "                  credited_units=int(credited_units),\n"
        "                  ts=now,\n"
        "              )"
    )

    text = text.replace(old_topup, new_topup)
    topup_ctor_applied = 1

    after_sha = _sha1_text(text)

    counts = (
        f"PATCH_COMMENT_APPLIED={comment_applied}\n"
        f"PATCH_ACCOUNT_BLOCK_APPLIED={account_block_applied}\n"
        f"PATCH_TOPUP_CTOR_APPLIED={topup_ctor_applied}\n"
        f"AFTER_SHA1= {after_sha}\n"
    )
    print(counts, end="")
    _append_log(log_path, counts.rstrip("\n"))

    _write(target, text)
    ok, comp = _py_compile(target)
    msg2 = f"PY_COMPILE= {comp}\n"
    print(msg2, end="")
    _append_log(log_path, msg2.rstrip("\n"))

    if not ok:
        msg = "FATAL: python compile failed. Restoring backup.\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 5

    done = "DONE\n"
    print(done, end="")
    _append_log(log_path, done.rstrip("\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
