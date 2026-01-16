import hashlib
import os
import sys
from datetime import datetime, timezone

BANNER = "FEESINK-APPLY-PATCH-SERVER-STRIPE-TOPUP-MODEL-FIX v2026.01.05-10"


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def backup_file(path: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{path}.bak.{ts}"
    with open(path, "rb") as fsrc, open(backup_path, "wb") as fdst:
        fdst.write(fsrc.read())
    return backup_path


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

    # We expect the (already patched) block that still uses ts=now (wrong for storage).
    before_block = (
        "topup = TopUp(\n"
        "                account_id=str(account_id),\n"
        "                tx_hash=tx_hash,\n"
        "                amount_usdt=Decimal(str(amount_usdt)),\n"
        "                credited_units=int(credited_units),\n"
        "                ts=now,\n"
        "            )"
    )

    after_block = (
        "topup = TopUp(\n"
        "                topup_id=tx_hash,\n"
        "                account_id=str(account_id),\n"
        "                tx_hash=tx_hash,\n"
        "                amount_usdt=Decimal(str(amount_usdt)),\n"
        "                credited_units=int(credited_units),\n"
        "                created_at_utc=now,\n"
        "            )"
    )

    idx = text.find(before_block)
    if idx < 0:
        print("FOUND_MATCHES= 0")
        print("ERROR: expected TopUp(...) block not found. Refusing to patch.")
        print("HINT: open feesink/api/server.py and locate the TopUp(...) creation in Stripe webhook credit path.")
        return 3

    print("FOUND_MATCHES= 1")

    # Print BEFORE/AFTER context (5 lines around)
    lines = text.splitlines()
    # Find line number of the first line of the block
    first_line = "topup = TopUp("
    line_no = None
    for i, l in enumerate(lines):
        if l.strip() == first_line and text.find(before_block) == text.find(first_line, text.find(before_block) - 50, text.find(before_block) + 50):
            line_no = i
            break
    # Fallback: just search for the exact sequence by scanning
    if line_no is None:
        for i, l in enumerate(lines):
            if l.strip() == first_line:
                # pick the nearest occurrence to idx
                # crude but enough for diagnostics
                line_no = i
        # still ok if None; we won't print line context by line numbers

    def print_context(label: str, content_lines: list[str], start_line: int | None):
        print(f"\n--- {label} (context) ---")
        if start_line is None:
            # Print a raw slice around idx
            s = max(0, idx - 250)
            e = min(len(text), idx + len(before_block) + 250)
            snippet = text[s:e]
            for n, l in enumerate(snippet.splitlines(), 1):
                print(f"{n:04d}: {l}")
            return

        a = max(0, start_line - 5)
        b = min(len(content_lines), start_line + 5 + 12)  # +12 lines to show full block
        for j in range(a, b):
            prefix = ">>" if j == start_line else "  "
            print(f"{prefix} {j+1:04d}: {content_lines[j]}")

    # BEFORE context
    print_context("BEFORE", lines, line_no)

    backup = backup_file(target)
    print("\nBACKUP=", backup)
    patched = text.replace(before_block, after_block, 1)

    sha_after = sha1_text(patched)
    open(target, "w", encoding="utf-8", newline="\n").write(patched)

    print("SHA1_BEFORE=", sha_before)
    print("SHA1_AFTER =", sha_after)

    # AFTER context
    lines_after = patched.splitlines()
    # Try to find where it landed now
    after_line_no = None
    for i, l in enumerate(lines_after):
        if l.strip() == first_line:
            # choose the first occurrence after original idx approx by string search
            after_line_no = i
            break
    print_context("AFTER", lines_after, after_line_no)

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
