# scripts/apply_patch_server_stripe_dedup_continue_v06.py
# FEESINK-APPLY-PATCH-SERVER-STRIPE-DEDUP-CONTINUE v2026.01.05-06

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-DEDUP-CONTINUE v2026.01.05-06"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def backup_file(p: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    b = p.with_suffix(p.suffix + f".bak.{stamp}")
    b.write_bytes(p.read_bytes())
    return b


def main() -> int:
    print("=" * 80)
    print(VERSION)
    print("START_UTC=", utc())
    print("=" * 80)

    repo = Path(__file__).resolve().parents[1]
    target = repo / "feesink" / "api" / "server.py"
    if not target.exists():
        target = repo / "feesink" / "server.py"
    if not target.exists():
        print("ERROR: server.py not found")
        return 2

    print("TARGET=", target)

    src = target.read_text(encoding="utf-8")

    needle = 'return _json_response(200, {"ok": True, "dedup": True})'
    count = src.count(needle)

    print("FOUND_DEDUP_RETURN_COUNT=", count)

    if count != 1:
        print("ERROR: expected exactly 1 dedup return. No changes written.")
        return 3

    repl = (
        "# NOTE: do not short-circuit on provider_event dedup.\n"
        "# Dedup of credit is enforced by topups.tx_hash, so we can safely continue.\n"
        "# (Stripe retry after transient crash must still be able to credit.)\n"
    )

    src2 = src.replace(needle, repl.rstrip("\n"), 1)

    b = backup_file(target)
    target.write_text(src2, encoding="utf-8")

    print("OK: patched.")
    print("BACKUP=", b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
