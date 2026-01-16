# scripts/apply_patch_server_stripe_credit_fix_v05.py
# FEESINK-APPLY-PATCH-SERVER-STRIPE-CREDIT v2026.01.05-05

from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime, timezone

VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-CREDIT v2026.01.05-05"


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
        print("Tried:")
        print("  -", repo / "feesink" / "api" / "server.py")
        print("  -", repo / "feesink" / "server.py")
        return 2

    print("TARGET=", target)
    src = target.read_text(encoding="utf-8")

    changed = False

    # ------------------------------------------------------------------
    # PATCH 1: Remove early return on provider_event dedup
    # Match BOTH: {"ok": True, "dedup": True} and {'ok': True, 'dedup': True}
    # ------------------------------------------------------------------
    pat_dedup_return = re.compile(
        r"""^\s*return\s+_json_response\(\s*200\s*,\s*\{\s*['"]ok['"]\s*:\s*True\s*,\s*['"]dedup['"]\s*:\s*True\s*\}\s*\)\s*$""",
        re.MULTILINE,
    )

    def repl_dedup(_: re.Match) -> str:
        return (
            "        # NOTE: do not short-circuit on provider_event dedup.\n"
            "        # Dedup of credit is enforced by topups.tx_hash, so we can safely continue.\n"
        ).rstrip("\n")

    src2, n_dedup = pat_dedup_return.subn(repl_dedup, src, count=1)
    if n_dedup == 1:
        src = src2
        changed = True

    # ------------------------------------------------------------------
    # PATCH 2: Fix Stripe webhook TopUp constructor
    # Convert:
    #   topup = TopUp(account_id=..., tx_hash=..., amount_usdt=..., credited_units=..., ts=now)
    # into:
    #   topup = TopUp(); topup.account_id=...; ...
    #
    # Match both quote styles and flexible spacing.
    # ------------------------------------------------------------------
    pat_topup_call = re.compile(
        r"""
(^\s*tx_hash\s*=\s*f["']stripe:\{event_id\}["']\s*\n
(?:(?:.|\n)*?)
^\s*now\s*=\s*datetime\.now\(tz=UTC\)\s*\n
^\s*try:\s*\n)
(?P<indent>\s*)
topup\s*=\s*TopUp\(\s*\n
\s*account_id\s*=\s*str\(account_id\)\s*,\s*\n
\s*tx_hash\s*=\s*tx_hash\s*,\s*\n
\s*amount_usdt\s*=\s*Decimal\(str\(amount_usdt\)\)\s*,\s*\n
\s*credited_units\s*=\s*int\(credited_units\)\s*,\s*\n
\s*ts\s*=\s*now\s*,?\s*\n
^\s*\)\s*\n
""",
        re.VERBOSE | re.MULTILINE,
    )

    def repl_topup(m: re.Match) -> str:
        prefix = m.group(1)
        indent = m.group("indent")
        body = (
            f"{indent}topup = TopUp()\n"
            f"{indent}topup.account_id = str(account_id)\n"
            f"{indent}topup.tx_hash = tx_hash\n"
            f"{indent}topup.amount_usdt = Decimal(str(amount_usdt))\n"
            f"{indent}topup.credited_units = int(credited_units)\n"
            f"{indent}topup.ts = now\n"
        )
        return prefix + body

    src3, n_topup = pat_topup_call.subn(repl_topup, src, count=1)
    if n_topup == 1:
        src = src3
        changed = True

    print("PATCH_RESULTS:")
    print("  - dedup_return_removed:", n_dedup == 1)
    print("  - stripe_TopUp_constructor_fixed:", n_topup == 1)

    if not changed:
        print("ERROR: no changes applied (patterns not found). No changes written.")
        return 3

    b = backup_file(target)
    target.write_text(src, encoding="utf-8")
    print("OK: patched.")
    print("BACKUP=", b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
