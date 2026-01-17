"""
FEESINK-DB-SMOKE-SQLITE v2026.01.17-03

Goal (P0):
- schema applies
- credit_topup idempotent (tx_hash)
- record_check_and_charge: requires endpoint exists (FK), charges 1 unit, dedup works
- insufficient funds: deterministic refusal (Conflict) and no check_event inserted

Design:
- Contract-sensitive: constructs domain dataclasses using dataclass field introspection
  and prints fields on mismatch to speed up drift fixes.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import MISSING, dataclass, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar

from feesink.domain.models import CheckEvent, CheckResult, Endpoint, TopUp
from feesink.storage.interfaces import Conflict
from feesink.storage.sqlite import SQLiteStorage, SQLiteStorageConfig

T = TypeVar("T")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


@dataclass(frozen=True)
class _Ctx:
    root: Path
    db_path: Path
    schema_path: Path


def _print_banner(ctx: _Ctx) -> None:
    print("=" * 80)
    print("FEESINK-DB-SMOKE-SQLITE v2026.01.17-03")
    print("TS_UTC=", _iso_z(_utc_now()))
    print("ROOT=", str(ctx.root))
    print("DB=", str(ctx.db_path))
    print("SCHEMA_SQL=", str(ctx.schema_path))
    print("=" * 80)


def _make_ctx() -> _Ctx:
    root = Path(__file__).resolve().parents[1]
    schema_path = root / "schema.sql"
    fd, tmp = tempfile.mkstemp(prefix="feesink_smoke_", suffix=".db")
    os.close(fd)
    return _Ctx(root=root, db_path=Path(tmp), schema_path=schema_path)


def _read_one(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> Any:
    cur = conn.execute(sql, args)
    row = cur.fetchone()
    return row[0] if row else None


def _dc_fields(cls: type[Any]) -> list[str]:
    if not is_dataclass(cls):
        return []
    fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
    return sorted(fields.keys())


def _dc_make(cls: type[T], **candidates: Any) -> T:
    """
    Construct dataclass instance robustly:
    - takes only fields that exist in cls
    - verifies all required fields are provided
    - on mismatch: prints available fields to aid fixes
    """
    if not is_dataclass(cls):
        raise TypeError(f"{cls.__name__} is not a dataclass")

    fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
    kwargs: dict[str, Any] = {k: v for k, v in candidates.items() if k in fields}

    missing_required: list[str] = []
    for name, f in fields.items():
        required = (f.default is MISSING) and (f.default_factory is MISSING)  # type: ignore
        if required and name not in kwargs:
            missing_required.append(name)

    if missing_required:
        have = ", ".join(sorted(kwargs.keys()))
        need = ", ".join(missing_required)
        allf = ", ".join(sorted(fields.keys()))
        raise TypeError(
            f"{cls.__name__} missing required fields: {need}. "
            f"Provided: {have}. All fields: {allf}"
        )

    return cls(**kwargs)  # type: ignore[misc]


def main() -> int:
    # import-only smoke (catches syntax/import regressions early)
    _ = SQLiteStorage  # noqa: F841

    ctx = _make_ctx()
    _print_banner(ctx)

    # CANON: SQLiteStorageConfig expects db_path (NOT sqlite_db_path)
    cfg = SQLiteStorageConfig(
        db_path=str(ctx.db_path),
        schema_sql_path=str(ctx.schema_path),
    )
    st = SQLiteStorage(cfg)

    account_id = "smoke-account"
    endpoint_id = "smoke-endpoint"

    # 0) ensure account + endpoint exist (needed for FK on check_events)
    st.ensure_account(account_id)

    try:
        ep = _dc_make(
            Endpoint,
            endpoint_id=endpoint_id,
            account_id=account_id,
            url="https://example.com/healthz",
            interval_minutes=5,
            enabled=True,
            next_check_at=_utc_now(),
            paused_reason=None,
            last_check_at=None,
            last_result=None,
        )
    except TypeError as e:
        print("[SMOKE][ERROR] Endpoint fields:", _dc_fields(Endpoint))
        raise

    st.add_endpoint(ep)

    # 1) credit topup (idempotent by tx_hash)
    try:
        topup = _dc_make(
            TopUp,
            account_id=account_id,
            tx_hash="smoke-tx-001",
            amount_usdt=Decimal("50.00"),
            credited_units=3,
            ts=_utc_now(),
            created_at_utc=_utc_now(),
            # legacy/optional (ignored if absent)
            provider="smoke",
            topup_id=None,
        )
    except TypeError as e:
        print("[SMOKE][ERROR] TopUp fields:", _dc_fields(TopUp))
        raise

    r1 = st.credit_topup(topup)
    _assert(getattr(r1, "inserted") is True, "first topup must insert=True")

    r2 = st.credit_topup(topup)
    _assert(getattr(r2, "inserted") is False, "second topup must be idempotent (inserted=False)")

    acc = st.get_account(account_id)
    _assert(acc.balance_units == 3, f"balance must be 3 after idempotent credit, got {acc.balance_units}")

    # 2) first check inserts + charges 1 unit
    scheduled_at = _utc_now().replace(microsecond=0)
    scheduled_s = _iso_z(scheduled_at)
    dedup_key = f"{endpoint_id}|{scheduled_s}"

    try:
        ev = _dc_make(
            CheckEvent,
            endpoint_id=endpoint_id,
            ts=_utc_now(),
            ts_utc=_utc_now(),
            result=CheckResult.OK,
            latency_ms=123,
            http_status=200,
            error_class=None,
            units_charged=1,
            # legacy/optional
            check_id=None,
        )
    except TypeError as e:
        print("[SMOKE][ERROR] CheckEvent fields:", _dc_fields(CheckEvent))
        raise

    c1 = st.record_check_and_charge(
        account_id=account_id,
        event=ev,
        charge_units=1,
        dedup_key=dedup_key,
    )
    print(
        f"[SMOKE] first check dedup_key={dedup_key} "
        f"inserted={c1.inserted} new_balance_units={c1.new_balance_units}"
    )
    _assert(c1.inserted is True, "first check must insert")
    _assert(c1.new_balance_units == 2, f"balance must be 2 after first check, got {c1.new_balance_units}")

    acc2 = st.get_account(account_id)
    _assert(acc2.balance_units == 2, f"balance must be 2 after first check, got {acc2.balance_units}")

    # 3) second check with same dedup_key must not re-charge
    c2 = st.record_check_and_charge(
        account_id=account_id,
        event=ev,
        charge_units=1,
        dedup_key=dedup_key,
    )
    print(
        f"[SMOKE] second check dedup_key={dedup_key} "
        f"inserted={c2.inserted} new_balance_units={c2.new_balance_units}"
    )
    _assert(c2.inserted is False, "second check must be dedup (no insert)")
    _assert(c2.new_balance_units == 2, f"balance must remain 2 after dedup, got {c2.new_balance_units}")

    acc3 = st.get_account(account_id)
    _assert(acc3.balance_units == 2, f"balance must remain 2 after dedup, got {acc3.balance_units}")

    # 4) insufficient funds: drain remaining 2 units with unique dedup keys
    ev2 = ev
    dk2 = f"{endpoint_id}|{_iso_z(_utc_now())}|2"
    st.record_check_and_charge(account_id=account_id, event=ev2, charge_units=1, dedup_key=dk2)
    dk3 = f"{endpoint_id}|{_iso_z(_utc_now())}|3"
    st.record_check_and_charge(account_id=account_id, event=ev2, charge_units=1, dedup_key=dk3)

    acc4 = st.get_account(account_id)
    _assert(acc4.balance_units == 0, f"balance must be 0 after draining, got {acc4.balance_units}")

    dk4 = f"{endpoint_id}|{_iso_z(_utc_now())}|insufficient"
    try:
        st.record_check_and_charge(account_id=account_id, event=ev2, charge_units=1, dedup_key=dk4)
        raise AssertionError("expected Conflict(insufficient balance_units)")
    except Conflict:
        pass

    # DB sanity: dk4 must NOT be inserted
    con = sqlite3.connect(str(ctx.db_path))
    try:
        n_ok = _read_one(con, "SELECT COUNT(1) FROM check_events WHERE dedup_key=?", (dedup_key,))
        _assert(int(n_ok) == 1, f"must have 1 check_event row for first dedup_key, got {n_ok}")

        n_fail = _read_one(con, "SELECT COUNT(1) FROM check_events WHERE dedup_key=?", (dk4,))
        _assert(int(n_fail) == 0, f"must have 0 rows for insufficient-funds dedup_key, got {n_fail}")
    finally:
        con.close()

    print(f"[SMOKE] final_balance_units={st.get_account(account_id).balance_units}")
    print("PASS: sqlite smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
