r"""
FeeSink demo tick runner (MVP: HTTP Endpoint Watchdog)

P0 Traceability requirement:
- Print deterministic version+hash block at the very beginning of stdout,
  BEFORE any project imports.

This demo is intentionally tolerant to minor API drift in runtime.worker.run_tick
(by adapting to parameter names via inspect.signature). This preserves behavior
and reduces "broken demo" regressions across phases.

Env switches:
- FEESINK_STORAGE=memory|sqlite
- FEESINK_HTTP=stub|real
- FEESINK_SQLITE_DB=feesink.db (default)
- FEESINK_DEMO_RESET=1|0 (default 1 for sqlite)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc

DEMO_VERSION = "FEESINK-DEMO-TICK v2026.01.05-TRACE-FIX-07"


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _trace_block() -> str:
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "feesink" / "adapters" / "http_checker_real.py",
        root / "feesink" / "adapters" / "http_checker_stub.py",
        root / "feesink" / "config" / "canon.py",
        root / "feesink" / "domain" / "models.py",
        root / "feesink" / "runtime" / "worker.py",
        root / "feesink" / "storage" / "interfaces.py",
        root / "feesink" / "storage" / "memory.py",
        root / "feesink" / "storage" / "sqlite.py",
        root / "schema.sql",
        Path(__file__).resolve(),
    ]
    lines = []
    lines.append("=" * 80)
    lines.append(DEMO_VERSION)
    lines.append(f"PYTHON={sys.version.split()[0]}")
    lines.append(f"ROOT={root.as_posix()}")
    lines.append("FILES_SHA1:")
    for p in files:
        try:
            rel = p.relative_to(root)
        except Exception:
            rel = p
        lines.append(f"  - {str(rel)} sha1={_sha1_file(p)}")
    lines.append("=" * 80)
    return "\n".join(lines)


print(_trace_block())

# --------------------------------------------------------------------------------------
# Imports AFTER trace block
# --------------------------------------------------------------------------------------

from feesink.config.canon import canon_label, credited_units, HttpCheckPolicy, PricingPolicy
from feesink.domain.models import AccountId, Endpoint, EndpointId, TxHash, TopUp
from feesink.runtime.worker import run_tick, WorkerConfig
from feesink.storage.memory import InMemoryStorage
from feesink.storage.sqlite import SQLiteStorage, SQLiteStorageConfig

# Adapter versions (stable strings used in logs)
HTTP_REAL_VERSION = "FEESINK-HTTP-REAL v2026.01.01-01"
HTTP_STUB_VERSION = "FEESINK-HTTP-STUB v2026.01.01-01"

# Storage version MUST be sourced from the actual module used at runtime.
try:
    from feesink.storage.sqlite import STORAGE_VERSION as SQLITE_STORAGE_VERSION  # type: ignore
except Exception:
    SQLITE_STORAGE_VERSION = "FEESINK-SQLITE-STORAGE (unknown)"

try:
    MEMORY_STORAGE_VERSION = getattr(InMemoryStorage, "STORAGE_VERSION", "FEESINK-MEMORY-STORAGE (unknown)")
except Exception:
    MEMORY_STORAGE_VERSION = "FEESINK-MEMORY-STORAGE (unknown)"

FEESINK_TELEMETRY_VERSION = "FEESINK-TELEMETRY v2026.01.01-01"
FEESINK_WORKER_VERSION = "FEESINK-WORKER v2026.01.01-03-OPS-02"


# --------------------------------------------------------------------------------------
# Env
# --------------------------------------------------------------------------------------

FEESINK_STORAGE = os.getenv("FEESINK_STORAGE", "sqlite").strip().lower()
FEESINK_HTTP = os.getenv("FEESINK_HTTP", "stub").strip().lower()

USE_SQLITE = FEESINK_STORAGE == "sqlite"
USE_HTTP_REAL = FEESINK_HTTP == "real"

DB_PATH = os.getenv("FEESINK_SQLITE_DB", "feesink.db")
DEMO_RESET = os.getenv("FEESINK_DEMO_RESET", "1").strip() == "1"


# --------------------------------------------------------------------------------------
# Telemetry (JSONL)
# --------------------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def emit_event(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def health_snapshot() -> dict:
    return {
        "type": "health",
        "ts": _utc_now().isoformat().replace("+00:00", "Z"),
        "worker": FEESINK_WORKER_VERSION,
        "telemetry": FEESINK_TELEMETRY_VERSION,
        "ok": True,
    }


# --------------------------------------------------------------------------------------
# SQLite helpers
# --------------------------------------------------------------------------------------

def _sqlite_db_path() -> str:
    return DB_PATH


def _apply_schema_sql(db_path: str) -> None:
    # SQLiteStorageConfig(schema_sql_path=...) already ensures schema in canonical setup.
    # Keeping this is safe due to IF NOT EXISTS DDL.
    root = Path(__file__).resolve().parents[1]
    schema_path = root / "schema.sql"
    if not schema_path.exists():
        raise RuntimeError(f"schema.sql not found at {schema_path}")
    cfg = SQLiteStorageConfig(db_path=db_path, schema_sql_path=str(schema_path))
    st = SQLiteStorage(cfg)
    st.close()


def _maybe_reset_sqlite() -> None:
    if not USE_SQLITE:
        return
    if not DEMO_RESET:
        return
    db_path = _sqlite_db_path()
    if os.path.exists(db_path):
        os.remove(db_path)


# --------------------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------------------

def _make_storage():
    if USE_SQLITE:
        root = Path(__file__).resolve().parents[1]
        return SQLiteStorage(
            SQLiteStorageConfig(
                db_path=_sqlite_db_path(),
                schema_sql_path=str(root / "schema.sql"),
            )
        )
    return InMemoryStorage()


def _make_http_adapter():
    if USE_HTTP_REAL:
        from feesink.adapters.http_checker_real import real_checker
        return real_checker()

    from feesink.adapters.http_checker_stub import preset_checker
    return preset_checker()


# --------------------------------------------------------------------------------------
# Demo setup data
# --------------------------------------------------------------------------------------

def _demo_topup(account_id: AccountId) -> TopUp:
    pricing = PricingPolicy()
    amount = pricing.min_topup_usdt
    credited = credited_units(amount)
    return TopUp(
        account_id=account_id,
        tx_hash=TxHash("tx-demo-001"),
        amount_usdt=amount,
        credited_units=credited,
        ts=_utc_now(),
    )


# --------------------------------------------------------------------------------------
# run_tick adapter (tolerate signature drift)
# --------------------------------------------------------------------------------------

def _call_run_tick(storage, http, now: datetime):
    """
    Adapts to run_tick signature drift without changing runtime behavior.

    Current canonical signature (worker.py):
      run_tick(*, storage, http, config, http_policy, ..., now=None)

    Older demos might have used:
      run_tick(storage=..., http=..., now_utc=..., tick_limit=..., lease_for=...)
    """
    sig = inspect.signature(run_tick)
    params = sig.parameters

    http_policy = HttpCheckPolicy()
    config = WorkerConfig(tick_limit=10, lease_for=timedelta(seconds=30))

    kwargs = {}

    # required in current canon
    if "http_policy" in params:
        kwargs["http_policy"] = http_policy
    if "config" in params:
        kwargs["config"] = config

    # now naming drift
    if "now" in params:
        kwargs["now"] = now
    elif "now_utc" in params:
        kwargs["now_utc"] = now

    # legacy support (only if those params exist)
    if "tick_limit" in params:
        kwargs["tick_limit"] = 10
    if "lease_for" in params:
        kwargs["lease_for"] = timedelta(seconds=30)

    return run_tick(storage=storage, http=http, **kwargs)


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def main() -> int:
    print("=" * 80)
    print(f"DEMO RUN: {canon_label()}")

    if USE_SQLITE:
        print(f"STORAGE: sqlite | {SQLITE_STORAGE_VERSION}")
        print(f"DEMO: reset {'enabled' if DEMO_RESET else 'disabled'}")
    else:
        print(f"STORAGE: memory | {MEMORY_STORAGE_VERSION}")
        print("DEMO: reset disabled (memory)")

    if USE_HTTP_REAL:
        print(f"HTTP: real | {HTTP_REAL_VERSION}")
    else:
        print(f"HTTP: stub | {HTTP_STUB_VERSION}")

    print("=" * 80)

    emit_event(health_snapshot())

    _maybe_reset_sqlite()
    storage = _make_storage()
    http = _make_http_adapter()

    if USE_SQLITE:
        _apply_schema_sql(_sqlite_db_path())

    account_id = AccountId("acc-demo")
    storage.ensure_account(account_id)

    tr = storage.credit_topup(_demo_topup(account_id))
    acc = storage.get_account(account_id)
    print(f"TopUp inserted={tr.inserted} amount_usdt={tr.topup.amount_usdt} credited_units={tr.topup.credited_units}")
    print(f"Account balance_units={acc.balance_units} status={acc.status}")

    print("-" * 80)

    # IMPORTANT: When DEMO_RESET=0 we may re-run against existing DB.
    # Endpoints creation is NOT a canonical idempotent operation in Storage,
    # so the demo must skip add_endpoint if endpoint_id already exists.
    existing_eps = {e.endpoint_id: e for e in storage.list_endpoints(account_id)}

    desired = [
        Endpoint(
            endpoint_id=EndpointId("ep-fail"),
            account_id=account_id,
            url="fail://health",
            interval_minutes=5,
            enabled=True,
            paused_reason=None,
            next_check_at=_utc_now(),
        ),
        Endpoint(
            endpoint_id=EndpointId("ep-ok"),
            account_id=account_id,
            url="ok://health",
            interval_minutes=5,
            enabled=True,
            paused_reason=None,
            next_check_at=_utc_now(),
        ),
    ]

    added = []
    skipped = []
    for ep in desired:
        if ep.endpoint_id in existing_eps:
            skipped.append(ep)
            continue
        storage.add_endpoint(ep)
        added.append(ep)

    if added:
        print("Endpoints added:")
        for ep in added:
            print(f"  - {ep.endpoint_id} url={ep.url} interval=5m enabled={ep.enabled}")
    if skipped:
        print("Endpoints skipped (already exist):")
        for ep in skipped:
            # show current url from DB to make drift visible
            cur = existing_eps.get(ep.endpoint_id)
            cur_url = getattr(cur, "url", ep.url)
            print(f"  - {ep.endpoint_id} url={cur_url} interval=5m")

    print("-" * 80)

    now = _utc_now()
    res = _call_run_tick(storage=storage, http=http, now=now)

    print(res)
    print("-" * 80)

    acc2 = storage.get_account(account_id)
    print(f"Account after tick: balance_units={acc2.balance_units} status={acc2.status}")

    eps = storage.list_endpoints(account_id)
    print("Endpoints after tick:")
    for e in eps:
        print(f"  - {e.endpoint_id} enabled={e.enabled} next_check_at={e.next_check_at}")

    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
