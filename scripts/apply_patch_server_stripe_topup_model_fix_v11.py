import hashlib
import os
import sys
from datetime import datetime, timezone

BANNER = "FEESINK-APPLY-PATCH-SERVER-STRIPE-TOPUP-MODEL-FIX v2026.01.05-11"


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def backup_file(path: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{path}.bak.{ts}"
    with open(path, "rb") as fsrc, open(backup_path, "wb") as fdst:
        fdst.write(fsrc.read())
    return backup_path


def print_context(text: str, needle: str, label: str, before_after: str) -> None:
    lines = text.splitlines()
    # find first line index containing the first line of needle
    first = needle.splitlines()[0].rstrip()
    idx_line = None
    for i, l in enumerate(lines):
        if l.rstrip() == first:
            # crude: choose the one whose following text matches needle start
            joined = "\n".join(lines[i:i + min(20, len(needle.splitlines()))])
            if needle.splitlines()[0].strip() in joined:
                idx_line = i
                break

    print(f"\n--- {before_after} ({label}) ---")
    if idx_line is None:
        print("(context not found by line scan; printing raw needle)")
        for n, l in enumerate(needle.splitlines(), 1):
            print(f"{n:04d}: {l}")
        return

    a = max(0, idx_line - 6)
    b = min(len(lines), idx_line + 20)
    for j in range(a, b):
        prefix = ">>" if j == idx_line else "  "
        print(f"{prefix} {j+1:04d}: {lines[j]}")


def main() -> int:
    print("=" * 80)
    print(BANNER)
    print("TS_UTC=", now_utc_iso())
    print("=" * 80)

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    target = os.path.join(project_root, "feesink", "api", "server.py")

    print("TARGET=", target)
    print("CWD=", os.getcwd())

    if not os.path.exists(target):
        print("ERROR: target not found")
        return 2

    text = open(target, "r", encoding="utf-8").read()
    sha_before = sha1_text(text)

    # Patch the *actual* block shown in your log:
    before_block = (
        "topup = TopUp(\n"
        "                account_id=account_id,\n"
        "                tx_hash=tx_hash,\n"
        "                amount_usdt=amount_usdt,\n"
        "                credited_units=credited_units,\n"
        "                ts=now,\n"
        "            )"
    )

    after_block = (
        "topup = TopUp(\n"
        "                topup_id=tx_hash,\n"
        "                account_id=account_id,\n"
        "                tx_hash=tx_hash,\n"
        "                amount_usdt=amount_usdt,\n"
        "                credited_units=credited_units,\n"
        "                created_at_utc=now,\n"
        "            )"
    )

    found = text.count(before_block)
    print("FOUND_MATCHES=", found)

    if found != 1:
        print("ERROR: expected exactly 1 match. Refusing to patch.")
        print("HINT: open feesink/api/server.py and find the Stripe credit path TopUp(...) block.")
        return 3

    print_context(text, before_block, "TopUp creation", "BEFORE")

    backup = backup_file(target)
    print("\nBACKUP=", backup)

    patched = text.replace(before_block, after_block, 1)
    sha_after = sha1_text(patched)

    open(target, "w", encoding="utf-8", newline="\n").write(patched)

    print("SHA1_BEFORE=", sha_before)
    print("SHA1_AFTER =", sha_after)

    print_context(patched, after_block, "TopUp creation", "AFTER")

    # Compile check
    try:
        import py_compile
        py_compile.compile(target, doraise=True)
        print("\nPY_COMPILE=OK")
    except Exception as ex:
        print("\nPY_COMPILE=FAIL", type(ex).__name__, str(ex))
        return 4

    print("PATCH_APPLIED=1")
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
