"""
FeeSink SQLite helpers (split from feesink/storage/sqlite.py)

Version:
- FEESINK-SQLITE-UTILS v2026.01.16-01
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from feesink.domain.models import AccountStatus, PausedReason, ensure_utc
from feesink.storage.interfaces import StorageError

UTC = timezone.utc

STORAGE_VERSION = "FEESINK-SQLITE-STORAGE v2026.01.05-ACCOUNT-MAPPING-FIX-01"


def dt_to_str_utc(dt: datetime) -> str:
    dt = ensure_utc(dt)
    return dt.isoformat()


def str_to_dt_utc(s: str) -> datetime:
    if not s or not str(s).strip():
        raise ValueError("datetime string must be non-empty")
    dt = datetime.fromisoformat(str(s))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_int_bool(b: bool) -> int:
    return 1 if bool(b) else 0


def parse_paused_reason(value: object, endpoint_id: str) -> Optional[PausedReason]:
    if value is None:
        return None
    s = str(value)
    if not s:
        return None
    try:
        return PausedReason(s)
    except Exception as e:
        raise StorageError(f"invalid paused_reason in DB for endpoint {endpoint_id}: {s!r}") from e


def parse_account_status(value: object, account_id: str) -> AccountStatus:
    s = "active" if value is None else str(value)
    try:
        return AccountStatus(s)
    except Exception as e:
        raise StorageError(f"invalid account.status in DB for account {account_id}: {s!r}") from e
