# Path: scripts/apply_patch_server_stripe_webhook_accountid_topup_fix_v18.py
"""
FeeSink patch script — Stripe webhook (anchor-based, no exact-line templates).

Fixes in feesink/api/server.py:
1) Ensure top comment exists:
   # DO NOT PATCH BY GUESSING REGEX; ALWAYS EXTRACT CONTEXT FIRST

2) Replace account_id resolution block between anchors:
   "# 4) Resolve account_id via stripe_links (session_id -> account_id)"
   and
   "# 5) Determine price_id"
   with:
   - Primary: metadata.account_id
   - Fallback: storage.resolve_account_by_stripe_session(session_id)
   - Non-2xx on failure (force Stripe retry)

3) Fix TopUp ctor inside Stripe webhook credit path:
   Replace any multiline block:
       topup = TopUp(
           ... various args ...
       )
   (the one that contains topup_id and/or created_at_utc)
   with canonical:
       topup = TopUp(
           account_id=str(account_id),
           tx_hash=tx_hash,
           amount_usdt=Decimal(str(amount_usdt)),
           credited_units=int(credited_units),
           ts=now,
       )

Safety:
- timestamped backup
- deterministic match checks (must find exactly 1 TopUp block with "topup = TopUp(" after "credited_units maps below minimal top-up")
- restore on failure
- append-only log:
  C:\\Users\\User\\PycharmProjects\\feesink\\logs\\apply_patch_server_stripe_webhook_accountid_topup_fix_v18.txt
"""

from __future__ import annotations

import os
import shutil
import hashlib
from datetime import datetime, timezone

PATCH_VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-WEBHOOK-ACCOUNTID-TOPUP-FIX v2026.01.05-18"
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


def _find_topup_block(text: str) -> tuple[int, int] | None:
    """
    Find the specific 'topup = TopUp(' ... ')' block in Stripe webhook credit path.

    Strategy:
    - Locate a nearby unique phrase in this code path:
      'credited_units maps below minimal top-up'
    - Search after it for 'topup = TopUp('
    - Extract from that line start to the next line containing only ')' with same indentation context.
    """
    pivot = "credited_units maps below minimal top-up"
    p = text.find(pivot)
    if p == -1:
        return None

    s = text.find("topup = TopUp(", p)
    if s == -1:
        return None

    # start at beginning of the line containing 'topup = TopUp('
    line_start = text.rfind("\n", 0, s) + 1

    # Now find the closing line that has a ')' and then optional spaces, then newline.
    # We will search forward for '\n' + <indent> + ')\n' OR ')\r\n' depending; file is normalized to \n.
    # Determine indent of the 'topup = TopUp(' line:
    indent = ""
    for ch in text[line_start:s]:
        if ch in (" ", "\t"):
            indent += ch
        else:
            break

    # Find the first occurrence of a line that equals indent + ')'
    i = s
    while True:
        nl = text.find("\n", i)
        if nl == -1:
            return None
        next_nl = text.find("\n", nl + 1)
        if next_nl == -1:
            next_nl = len(text)
        line = text[nl + 1:next_nl]
        if line.strip() == ")":
            # end includes this line + newline if present
            end = next_nl + (1 if next_nl < len(text) and text[next_nl:next_nl+1] == "\n" else 0)
            return line_start, end
        i = next_nl
        if i >= len(text):
            return None


def main() -> int:
    repo_root = r"C:\Users\User\PycharmProjects\feesink"
    target = os.path.join(repo_root, r"feesink\api\server.py")
    log_path = os.path.join(repo_root, "logs", "apply_patch_server_stripe_webhook_accountid_topup_fix_v18.txt")

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

    # (1) Top comment (non-functional)
    marker_comment = "# DO NOT PATCH BY GUESSING REGEX; ALWAYS EXTRACT CONTEXT FIRST"
    comment_applied = 0
    if marker_comment not in text:
        if text.startswith("#!"):
            nl = text.find("\n")
            if nl != -1:
                text = text[: nl + 1] + marker_comment + "\n" + text[nl + 1 :]
            else:
                text = text + "\n" + marker_comment + "\n"
        else:
            text = marker_comment + "\n" + text
        comment_applied = 1

    # (2) account_id block replace by anchors
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

    text = text[:i1] + new_block + text[i2:]
    account_block_applied = 1

    # (3) TopUp ctor fix by extracting the block and replacing it
    blk = _find_topup_block(text)
    if blk is None:
        msg = "FATAL: could not locate TopUp block for webhook credit path\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 4

    b_start, b_end = blk
    old_block = text[b_start:b_end]

    # Determine indentation of 'topup = TopUp(' line inside old_block
    s = old_block.find("topup = TopUp(")
    if s == -1:
        msg = "FATAL: internal: extracted block does not contain 'topup = TopUp('\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 4

    line_start2 = old_block.rfind("\n", 0, s) + 1
    indent2 = ""
    for ch in old_block[line_start2:s]:
        if ch in (" ", "\t"):
            indent2 += ch
        else:
            break

    new_topup_block = (
        f"{indent2}topup = TopUp(\n"
        f"{indent2}    account_id=str(account_id),\n"
        f"{indent2}    tx_hash=tx_hash,\n"
        f"{indent2}    amount_usdt=Decimal(str(amount_usdt)),\n"
        f"{indent2}    credited_units=int(credited_units),\n"
        f"{indent2}    ts=now,\n"
        f"{indent2})\n"
    )

    # Replace only the assignment block within old_block (from 'topup = TopUp(' line to closing ')')
    # Find the exact region in old_block:
    assign_start = old_block.find("topup = TopUp(")
    assign_line_start = old_block.rfind("\n", 0, assign_start) + 1
    # find closing line with ')'
    close_pos = old_block.find("\n" + indent2 + ")", assign_start)
    if close_pos == -1:
        # fallback: any line with only ')'
        close_pos = old_block.find("\n)", assign_start)
    if close_pos == -1:
        msg = "FATAL: could not find closing ')' for TopUp block\n"
        print(msg, end="")
        _append_log(log_path, msg.rstrip("\n"))
        shutil.copy2(bak, target)
        return 4
    # include that line + newline
    close_line_end = old_block.find("\n", close_pos + 1)
    if close_line_end == -1:
        close_line_end = len(old_block)
    else:
        close_line_end += 1

    replaced = old_block[:assign_line_start] + new_topup_block + old_block[close_line_end:]
    text = text[:b_start] + replaced + text[b_end:]
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
