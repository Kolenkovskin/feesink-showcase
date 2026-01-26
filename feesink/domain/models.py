"""
FeeSink domain models (MVP: HTTP Endpoint Watchdog)

Source of truth:
- Project root SPEC.md (CANON v1)

Domain layer rules:
- No I/O, no network, no persistence
- UTC timestamps only
- Deterministic, minimal, low-support by design
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Final, Optional


CANON_VERSION: Final[str] = "CANON v1"
UTC: Final[timezone] = timezone.utc


def ensure_utc(dt: datetime) -> datetime:
    """
    Ensure datetime is timezone-aware and UTC.
    Domain invariant: all timestamps are UTC.
    """
    if not isinstance(dt, datetime):
        raise TypeError("dt must be a datetime")
    if dt.tzinfo is None:
        raise ValueError("dt must be timezone-aware (UTC)")
    dt_utc = dt.astimezone(UTC)
    return dt_utc


def now_utc() -> datetime:
    """UTC 'now' helper for runtime/tests (still no I/O)."""
    return datetime.now(tz=UTC)


# ----------------------------
# Enums (CANON v1)
# ----------------------------

class AccountStatus(str, Enum):
    ACTIVE = "active"
    DEPLETED = "depleted"


class PausedReason(str, Enum):
    MANUAL = "manual"
    DEPLETED = "depleted"


class CheckResult(str, Enum):
    OK = "ok"
    FAIL = "fail"
    TIMEOUT = "timeout"


class ErrorClass(str, Enum):
    DNS = "dns"
    CONNECT = "connect"
    TLS = "tls"
    TIMEOUT = "timeout"
    HTTP_NON_2XX = "http_non_2xx"
    REDIRECT_LOOP = "redirect_loop"
    UNKNOWN = "unknown"


# ----------------------------
# IDs (simple aliases)
# ----------------------------

AccountId = str
EndpointId = str
TxHash = str


# ----------------------------
# Domain entities (CANON v1)
# ----------------------------

@dataclass(frozen=True, slots=True)
class Account:
    """
    Account holds prepaid balance in units.

    Invariants:
    - balance_units is integer >= 0
    - status is derived from balance or explicitly tracked by runtime/storage
    """
    account_id: AccountId
    balance_units: int
    status: AccountStatus

    def validate(self) -> None:
        if not self.account_id or not self.account_id.strip():
            raise ValueError("account_id must be non-empty")
        if not isinstance(self.balance_units, int):
            raise TypeError("balance_units must be int")
        if self.balance_units < 0:
            raise ValueError("balance_units must be >= 0")
        if not isinstance(self.status, AccountStatus):
            raise TypeError("status must be AccountStatus")


@dataclass(frozen=True, slots=True)
class Endpoint:
    """
    HTTP endpoint to monitor.

    Invariants:
    - interval_minutes > 0
    - next_check_at is UTC aware datetime
    - paused_reason set only when enabled=False
    - last_check_at (if set) is UTC aware datetime
    - last_result (if set) is limited to OK|FAIL (schema constraint)
    """
    endpoint_id: EndpointId
    account_id: AccountId
    url: str
    interval_minutes: int

    enabled: bool
    next_check_at: datetime  # UTC

    paused_reason: Optional[PausedReason] = None

    # Optional telemetry (nullable in schema)
    last_check_at: Optional[datetime] = None  # UTC
    last_result: Optional[CheckResult] = None  # OK|FAIL only

    def validate(self) -> None:
        if not self.endpoint_id or not self.endpoint_id.strip():
            raise ValueError("endpoint_id must be non-empty")
        if not self.account_id or not self.account_id.strip():
            raise ValueError("account_id must be non-empty")
        if not self.url or not self.url.strip():
            raise ValueError("url must be non-empty")
        if not isinstance(self.interval_minutes, int):
            raise TypeError("interval_minutes must be int")
        if self.interval_minutes <= 0:
            raise ValueError("interval_minutes must be > 0")

        _ = ensure_utc(self.next_check_at)

        if self.last_check_at is not None:
            _ = ensure_utc(self.last_check_at)

        if self.last_result is not None:
            if not isinstance(self.last_result, CheckResult):
                raise TypeError("last_result must be CheckResult")
            # schema.sql allows only ok|fail here
            if self.last_result not in (CheckResult.OK, CheckResult.FAIL):
                raise ValueError("last_result must be OK or FAIL (telemetry)")

        if self.enabled:
            if self.paused_reason is not None:
                raise ValueError("paused_reason must be None when enabled=True")
        else:
            if self.paused_reason is None:
                raise ValueError("paused_reason must be set when enabled=False")
            if not isinstance(self.paused_reason, PausedReason):
                raise TypeError("paused_reason must be PausedReason")


@dataclass(frozen=True, slots=True)
class CheckEvent:
    """
    Single check execution result.

    Contract:
    - Each check event charges exactly 1 unit (units_charged=1).
    - Result is recorded even if fail/timeout.
    - No response body stored.
    """
    endpoint_id: EndpointId
    ts: datetime  # UTC

    result: CheckResult
    latency_ms: int

    # Optional metadata
    http_status: Optional[int] = None
    error_class: Optional[ErrorClass] = None

    units_charged: int = 1  # CANON: 1 check = 1 unit

    def validate(self) -> None:
        if not self.endpoint_id or not self.endpoint_id.strip():
            raise ValueError("endpoint_id must be non-empty")
        _ = ensure_utc(self.ts)

        if not isinstance(self.result, CheckResult):
            raise TypeError("result must be CheckResult")

        if not isinstance(self.latency_ms, int):
            raise TypeError("latency_ms must be int")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be >= 0")

        if self.http_status is not None:
            if not isinstance(self.http_status, int):
                raise TypeError("http_status must be int")
            if self.http_status < 100 or self.http_status > 599:
                raise ValueError("http_status must be in [100..599]")

        if self.error_class is not None and not isinstance(self.error_class, ErrorClass):
            raise TypeError("error_class must be ErrorClass")

        if not isinstance(self.units_charged, int):
            raise TypeError("units_charged must be int")
        if self.units_charged != 1:
            raise ValueError("units_charged must be exactly 1 (CANON)")


@dataclass(frozen=True, slots=True)
class TopUp:
    """
    Confirmed on-chain top-up credit.

    Invariants:
    - tx_hash is unique (enforced by storage)
    - credited_units is integer > 0
    - amount_usdt is Decimal > 0
    - ts is UTC
    """
    account_id: AccountId
    tx_hash: TxHash

    amount_usdt: Decimal
    credited_units: int

    ts: datetime  # UTC

    def validate(self) -> None:
        if not self.account_id or not self.account_id.strip():
            raise ValueError("account_id must be non-empty")
        if not self.tx_hash or not self.tx_hash.strip():
            raise ValueError("tx_hash must be non-empty")

        if not isinstance(self.amount_usdt, Decimal):
            raise TypeError("amount_usdt must be Decimal")
        if self.amount_usdt <= 0:
            raise ValueError("amount_usdt must be > 0")

        if not isinstance(self.credited_units, int):
            raise TypeError("credited_units must be int")
        if self.credited_units <= 0:
            raise ValueError("credited_units must be > 0")

        _ = ensure_utc(self.ts)


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    """
    Provider webhook/event row (audit + idempotency).

    Notes:
    - received_at/signature_verified_at are UTC datetimes
    - raw_body_sha256 is SHA256 hex of raw bytes (not JSON)
    """
    provider: str
    provider_event_id: str

    event_type: Optional[str]
    status: str  # 'received' | 'processed' | 'failed' (schema constraint)

    received_at: datetime  # UTC
    processed_at: Optional[datetime] = None  # UTC

    account_id: Optional[AccountId] = None
    credited_units: Optional[int] = None

    raw_event_json: Optional[str] = None

    # P1 audit fields
    raw_body_sha256: Optional[str] = None
    signature_verified_at: Optional[datetime] = None  # UTC

    def validate(self) -> None:
        if not self.provider or not str(self.provider).strip():
            raise ValueError("provider must be non-empty")
        if not self.provider_event_id or not str(self.provider_event_id).strip():
            raise ValueError("provider_event_id must be non-empty")
        if not self.status or not str(self.status).strip():
            raise ValueError("status must be non-empty")

        _ = ensure_utc(self.received_at)
        if self.processed_at is not None:
            _ = ensure_utc(self.processed_at)
        if self.signature_verified_at is not None:
            _ = ensure_utc(self.signature_verified_at)

        if self.credited_units is not None:
            if not isinstance(self.credited_units, int):
                raise TypeError("credited_units must be int")
            if self.credited_units < 0:
                raise ValueError("credited_units must be >= 0")
