"""
FeeSink demo: run two ticks with the SAME scheduled time to verify dedup.

Goal:
- Prove that retrying the SAME scheduled check does NOT double-charge.
- We simulate retry by forcing endpoints back to due state (next_check_at=fixed_now)
  after the first tick.

Expected:
- First tick: charged_events == 2, balance decreases by 2
- Second tick (same now, forced due): charged_events == 0, deduped_events == 2,
  balance unchanged

How to run (from project root):
  python scripts/run_demo_tick_twice.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from feesink.config.canon import canon_label, credited_units, canon_policies
from feesink.domain.models import (
    AccountId,
    AccountStatus,
    Endpoint,
    TopUp,
    now_utc,
)
from feesink.adapters.http_checker_stub import preset_checker
from feesink.runtime.worker import WorkerConfig, run_tick
from feesink.storage.memory import InMemoryStorage


def main() -> int:
    print("=" * 80)
    print(f"DEMO RUN (DEDUP TEST): {canon_label()}")
    print("=" * 80)

    storage = InMemoryStorage()
    http = preset_checker()
    http_policy, pricing_policy = canon_policies()

    fixed_now = now_utc()

    # 1) Account
    account_id: AccountId = "demo-user"
    storage.ensure_account(account_id)
    storage.set_account_status(account_id, AccountStatus.DEPLETED.value)

    # 2) Top-up (CANON min 50 USDT)
    amount = Decimal("50")
    units = credited_units(amount)
    topup = TopUp(
        account_id=account_id,
        tx_hash=f"0x{uuid4().hex}",
        amount_usdt=amount,
        credited_units=units,
        ts=fixed_now,
    )
    storage.credit_topup(topup)

    acc = storage.get_account(account_id)
    print(f"Initial balance_units={acc.balance_units}")
    print("-" * 80)

    # 3) Two endpoints due immediately
    storage.add_endpoint(
        Endpoint(
            endpoint_id="ep-ok",
            account_id=account_id,
            url="ok://health",
            interval_minutes=5,
            enabled=True,
            next_check_at=fixed_now,
            paused_reason=None,
        )
    )
    storage.add_endpoint(
        Endpoint(
            endpoint_id="ep-fail",
            account_id=account_id,
            url="fail://health",
            interval_minutes=5,
            enabled=True,
            next_check_at=fixed_now,
            paused_reason=None,
        )
    )

    cfg = WorkerConfig(tick_limit=10, lease_for=timedelta(seconds=30))

    # ----------------------------
    # FIRST TICK
    # ----------------------------
    print("FIRST TICK")
    result_1 = run_tick(
        storage=storage,
        http=http,
        http_policy=http_policy,
        pricing_policy=pricing_policy,
        config=cfg,
        now=fixed_now,
    )
    for k, v in asdict(result_1).items():
        print(f"  {k} = {v}")

    acc_1 = storage.get_account(account_id)
    print(f"Balance after first tick: {acc_1.balance_units}")
    print("-" * 80)

    # ----------------------------
    # FORCE RETRY CONDITION (simulate same scheduled check)
    # ----------------------------
    print("FORCE RETRY: set endpoints back to due at the same now")
    for ep in storage.list_endpoints(account_id):
        forced_due = Endpoint(
            endpoint_id=ep.endpoint_id,
            account_id=ep.account_id,
            url=ep.url,
            interval_minutes=ep.interval_minutes,
            enabled=True,
            next_check_at=fixed_now,  # force due again
            paused_reason=None,
        )
        storage.update_endpoint(forced_due)

    # ----------------------------
    # SECOND TICK (same now, forced due -> must dedup)
    # ----------------------------
    print("SECOND TICK (same now + forced due → must dedup)")
    result_2 = run_tick(
        storage=storage,
        http=http,
        http_policy=http_policy,
        pricing_policy=pricing_policy,
        config=cfg,
        now=fixed_now,
    )
    for k, v in asdict(result_2).items():
        print(f"  {k} = {v}")

    acc_2 = storage.get_account(account_id)
    print(f"Balance after second tick: {acc_2.balance_units}")
    print("-" * 80)

    print("EXPECTED (DEDUP):")
    print("- First tick: charged_events == 2")
    print("- Second tick: charged_events == 0 AND deduped_events == 2")
    print("- Balance unchanged after second tick")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
