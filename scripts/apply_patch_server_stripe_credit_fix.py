# scripts/apply_patch_server_stripe_credit_fix.py
# FEESINK-APPLY-PATCH-SERVER-STRIPE-CREDIT v2026.01.05-01

from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime, timezone

VERSION = "FEESINK-APPLY-PATCH-SERVER-STRIPE-CREDIT v2026.01.05-01"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _backup_file(p: Path) -> Path:
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
    candidates = [
        repo / "feesink" / "api" / "server.py",
        repo / "feesink" / "server.py",
    ]
    server_path = next((p for p in candidates if p.exists()), None)
    if not server_path:
        print("ERROR: server.py not found in expected locations:")
        for c in candidates:
            print("  -", c)
        return 2

    print("TARGET=", server_path)

    src = server_path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # PATCH 1: provider_events dedup must NOT short-circuit credit attempt
    # ------------------------------------------------------------------
    # Replace:
    #   if not inserted:
    #       print(...)
    #       return _json_response(200, {"ok": True, "dedup": True})
    #
    # With:
    #   if not inserted:
    #       print(...)
    #       # continue (topups tx_hash provides true credit dedup)
    #
    # NOTE: We keep the log print exactly, but remove the return.
    pat1 = re.compile(
        r"""
(?P<block>
^\s*if\s+not\s+inserted:\s*\n
(?P<indent>\s*)print\(\s*\n
(?:(?:.|\n)*?)
^\s*\)\s*\n
^\s*return\s+_json_response\(\s*200\s*,\s*\{"ok":\s*True\s*,\s*"dedup":\s*True\}\s*\)\s*\n
)
""",
        re.VERBOSE | re.MULTILINE,
    )

    def repl1(m: re.Match) -> str:
        block = m.group("block")
        indent = m.group("indent")
        # remove ONLY the return line; keep everything else
        block = re.sub(
            r"^\s*return\s+_json_response\(\s*200\s*,\s*\{\"ok\":\s*True\s*,\s*\"dedup\":\s*True\}\s*\)\s*\n",
            f"{indent}# NOTE: do not short-circuit; proceed to credit_topup (dedup is enforced by topups.tx_hash)\n",
            block,
            flags=re.MULTILINE,
        )
        return block

    new_src, n1 = pat1.subn(repl1, src, count=1)

    # ------------------------------------------------------------------
    # PATCH 2: TopUp() is not a dataclass; cannot call TopUp(...)
    # ------------------------------------------------------------------
    # Replace the block that does:
    #   topup = TopUp(account_id=..., tx_hash=..., amount_usdt=..., credited_units=..., ts=now)
    # with attribute assignment:
    #   topup = TopUp()
    #   topup.account_id = ...
    #   ...
    pat2 = re.compile(
        r"""
(?P<prefix>
^\s*now\s*=\s*datetime\.now\(tz=UTC\)\s*\n
^\s*try:\s*\n
)
(?P<body>
(?:(?:.|\n)*?)
^\s*topup\s*=\s*TopUp\(\s*\n
(?:(?:.|\n)*?)
^\s*\)\s*\n
(?:(?:.|\n)*?)
)
(?P<suffix>
^\s*#\s*Explicit\s+domain\s+validation.*\n
^\s*if\s+hasattr\(topup,\s*\"validate\"\):\s*\n
^\s*topup\.validate\(\)\s*#\s*type:\s*ignore\[call-arg\]\s*\n
^\s*except\s+Exception\s+as\s+ex:\s*\n
)
""",
        re.VERBOSE | re.MULTILINE,
    )

    def repl2(m: re.Match) -> str:
        prefix = m.group("prefix")
        suffix = m.group("suffix")
        # Keep try/except structure and validation as-is, but change construction.
        # Use the already computed variables: account_id, tx_hash, amount_usdt, credited_units, now
        body = (
            "            topup = TopUp()\n"
            "            topup.account_id = str(account_id)\n"
            "            topup.tx_hash = tx_hash\n"
            "            topup.amount_usdt = Decimal(str(amount_usdt))\n"
            "            topup.credited_units = int(credited_units)\n"
            "            topup.ts = now\n"
        )
        return prefix + body + suffix

    new_src2, n2 = pat2.subn(repl2, new_src, count=1)

    print("PATCH_RESULTS:")
    print("  - dedup_short_circuit_removed:", bool(n1))
    print("  - TopUp_constructor_fixed:", bool(n2))

    if n1 != 1 or n2 != 1:
        print("ERROR: patch patterns did not match exactly once. No changes written.")
        print("DETAILS:", {"n1": n1, "n2": n2})
        return 3

    backup = _backup_file(server_path)
    server_path.write_text(new_src2, encoding="utf-8")
    print("OK: patched.")
    print("BACKUP=", backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
