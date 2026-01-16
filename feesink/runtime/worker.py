"""
FeeSink runtime worker (MVP: HTTP Endpoint Watchdog)

Source of truth:
- Project root SPEC.md (CANON v1)

This module implements:
- Scheduling logic (next_check_at)
- Worker algorithm (HTTP check via adapter + storage via contract)

Phase 3 adds:
- Ops telemetry (JSONL events)
- In-process health snapshot

IMPORTANT:
- Keep backward compatibility with demo scripts:
  run_tick(...) may be called with keyword arg `pricing_policy=...`
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import json
import time
from typing import Optional, Protocol, Sequence

from feesink.config.canon import HttpCheckPolicy, PricingPolicy, UNITS_PER_CHECK
from feesink.domain.models import (
    AccountStatus,
    CheckEvent,
    CheckResult,
    ErrorClass,
    Endpoint,
    PausedReason,
    ensure_utc,
    now_utc,
)
from feesink.storage.interfaces import Lease, Storage, Conflict, ValidationError


UTC = timezone.utc


# ----------------------------
# Runtime CANON (worker-level)
# ----------------------------

DEFAULT_LEASE_FOR = timedelta(seconds=30)

FEESINK_WORKER_VERSION = "FEESINK-WORKER v2026.01.01-03-OPS-02"
TELEMETRY_SCHEMA_VERSION = "FEESINK-TELEMETRY v2026.01.01-01"


def _utc_iso(dt: datetime) -> str:
    return ensure_utc(dt).isoformat().replace("+00:00", "Z")


def emit_event(event: dict) -> None:
    """Emit one structured telemetry event as a single JSON line to stdout."""
    try:
        print(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        # Telemetry must never break runtime.
        pass


def health_snapshot(now: Optional[datetime] = None) -> dict:
    """In-process health snapshot (no HTTP API)."""
    n = ensure_utc(now) if now is not None else now_utc()
    return {
        "type": "health",
        "ts": _utc_iso(n),
        "worker": FEESINK_WORKER_VERSION,
        "telemetry": TELEMETRY_SCHEMA_VERSION,
        "ok": True,
    }


# ----------------------------
# HTTP checker abstraction
# ----------------------------

@dataclass(frozen=True, slots=True)
class CheckOutcome:
    result: CheckResult
    latency_ms: int
    http_status: Optional[int] = None
    error_class: Optional[ErrorClass] = None


class HttpChecker(Protocol):
    def check(self, url: str, policy: HttpCheckPolicy) -> CheckOutcome:
        ...


# ----------------------------
# Worker configuration
# ----------------------------

@dataclass(frozen=True, slots=True)
class WorkerConfig:
    tick_limit: int = 50
    lease_for: timedelta = DEFAULT_LEASE_FOR


# ----------------------------
# Worker result (telemetry-friendly)
# ----------------------------

@dataclass(frozen=True, slots=True)
class TickResult:
    ok: bool
    now_utc: datetime

    due_found: int
    leased: int
    processed: int

    charged_events: int
    deduped_events: int

    depleted_paused: int
    errors: int


# ----------------------------
# Core runtime logic (CANON v1)
# ----------------------------

def compute_next_check_at(now: datetime, interval_minutes: int) -> datetime:
    """next_check_at = now + interval (UTC)."""
    now_u = ensure_utc(now)
    return now_u + timedelta(minutes=interval_minutes)


def make_dedup_key(endpoint_id: str, scheduled_at_utc: datetime) -> str:
    """
    Deterministic dedup key for charging idempotency.
    Canon: dedup_key = endpoint_id + "|" + scheduled_at_utc_iso_z
    """
    ts = ensure_utc(scheduled_at_utc).isoformat().replace("+00:00", "Z")
    return f"{endpoint_id}|{ts}"


def run_tick(
    *,
    storage: Storage,
    http: HttpChecker,
    config: WorkerConfig,
    http_policy: HttpCheckPolicy,
    pricing: Optional[PricingPolicy] = None,
    pricing_policy: Optional[PricingPolicy] = None,
    now: Optional[datetime] = None,
) -> TickResult:
    """
    One tick:
    - pull due endpoints
    - try to lease each
    - perform check via HttpChecker
    - record event + charge idempotently (dedup_key)
    - reschedule next_check_at
    - pause endpoints if depleted

    Backward compatibility:
    - accepts both `pricing=` and `pricing_policy=` keyword args.
    """
    # Back-compat: demo scripts used pricing_policy=...
    eff_pricing: Optional[PricingPolicy] = pricing if pricing is not None else pricing_policy
    # NOTE: current CANON uses fixed UNITS_PER_CHECK for charging; eff_pricing is kept for future evolution.
    _ = eff_pricing

    now_u = ensure_utc(now) if now is not None else now_utc()

    _t0 = time.monotonic()

    emit_event({
        "type": "tick_start",
        "ts": _utc_iso(now_u),
        "worker": FEESINK_WORKER_VERSION,
        "telemetry": TELEMETRY_SCHEMA_VERSION,
        "tick_limit": config.tick_limit,
        "lease_for_s": int(config.lease_for.total_seconds()),
    })

    due: Sequence[Endpoint] = storage.due_endpoints(now_u, limit=config.tick_limit)

    emit_event({
        "type": "due_found",
        "ts": _utc_iso(now_u),
        "worker": FEESINK_WORKER_VERSION,
        "telemetry": TELEMETRY_SCHEMA_VERSION,
        "due_found": len(due),
    })

    leased = 0
    processed = 0
    charged_events = 0
    deduped_events = 0
    depleted_paused = 0
    errors = 0

    for ep in due:
        if not ep.enabled:
            continue

        lease: Optional[Lease] = storage.acquire_endpoint_lease(
            endpoint_id=ep.endpoint_id,
            lease_for=config.lease_for,
            now_utc=now_u,
        )
        if lease is None:
            emit_event({
                "type": "lease_denied",
                "ts": _utc_iso(now_u),
                "worker": FEESINK_WORKER_VERSION,
                "telemetry": TELEMETRY_SCHEMA_VERSION,
                "endpoint_id": ep.endpoint_id,
            })
            continue

        leased += 1

        try:
            scheduled_at = now_u

            outcome = http.check(ep.url, http_policy)

            emit_event({
                "type": "http_result",
                "ts": _utc_iso(now_u),
                "worker": FEESINK_WORKER_VERSION,
                "telemetry": TELEMETRY_SCHEMA_VERSION,
                "endpoint_id": ep.endpoint_id,
                "result": outcome.result.value,
                "latency_ms": int(outcome.latency_ms),
                "http_status": outcome.http_status,
                "error_class": outcome.error_class.value if outcome.error_class else None,
            })

            event = CheckEvent(
                endpoint_id=ep.endpoint_id,
                ts=now_u,
                result=outcome.result,
                latency_ms=outcome.latency_ms,
                http_status=outcome.http_status,
                error_class=outcome.error_class,
                units_charged=UNITS_PER_CHECK,
            )
            event.validate()

            dedup_key = make_dedup_key(ep.endpoint_id, scheduled_at)

            try:
                cr = storage.record_check_and_charge(
                    account_id=ep.account_id,
                    event=event,
                    charge_units=UNITS_PER_CHECK,
                    dedup_key=dedup_key,
                )
            except (Conflict, ValidationError):
                errors += 1
                emit_event({
                    "type": "storage_error",
                    "ts": _utc_iso(now_u),
                    "worker": FEESINK_WORKER_VERSION,
                    "telemetry": TELEMETRY_SCHEMA_VERSION,
                    "endpoint_id": ep.endpoint_id,
                    "error": "conflict_or_validation",
                })

                # Conservative: if storage refuses charge, pause endpoint as depleted (degrade).
                paused = Endpoint(
                    endpoint_id=ep.endpoint_id,
                    account_id=ep.account_id,
                    url=ep.url,
                    interval_minutes=ep.interval_minutes,
                    enabled=False,
                    next_check_at=ep.next_check_at,
                    paused_reason=PausedReason.DEPLETED,
                )
                try:
                    storage.update_endpoint(paused)
                    depleted_paused += 1
                    emit_event({
                        "type": "depleted_pause",
                        "ts": _utc_iso(now_u),
                        "worker": FEESINK_WORKER_VERSION,
                        "telemetry": TELEMETRY_SCHEMA_VERSION,
                        "endpoint_id": ep.endpoint_id,
                        "reason": "charge_failed",
                    })
                except Exception:
                    errors += 1
                continue

            processed += 1

            if cr.inserted:
                charged_events += 1
                emit_event({
                    "type": "charge_applied",
                    "ts": _utc_iso(now_u),
                    "worker": FEESINK_WORKER_VERSION,
                    "telemetry": TELEMETRY_SCHEMA_VERSION,
                    "endpoint_id": ep.endpoint_id,
                    "units": UNITS_PER_CHECK,
                    "balance_units": int(cr.new_balance_units),
                })
            else:
                deduped_events += 1
                emit_event({
                    "type": "charge_dedup",
                    "ts": _utc_iso(now_u),
                    "worker": FEESINK_WORKER_VERSION,
                    "telemetry": TELEMETRY_SCHEMA_VERSION,
                    "endpoint_id": ep.endpoint_id,
                    "units": UNITS_PER_CHECK,
                    "balance_units": int(cr.new_balance_units),
                })

            if cr.new_balance_units <= 0:
                paused = Endpoint(
                    endpoint_id=ep.endpoint_id,
                    account_id=ep.account_id,
                    url=ep.url,
                    interval_minutes=ep.interval_minutes,
                    enabled=False,
                    next_check_at=ep.next_check_at,
                    paused_reason=PausedReason.DEPLETED,
                )
                storage.update_endpoint(paused)
                storage.set_account_status(ep.account_id, AccountStatus.DEPLETED.value)
                depleted_paused += 1
                emit_event({
                    "type": "depleted_pause",
                    "ts": _utc_iso(now_u),
                    "worker": FEESINK_WORKER_VERSION,
                    "telemetry": TELEMETRY_SCHEMA_VERSION,
                    "endpoint_id": ep.endpoint_id,
                    "reason": "balance_le_0",
                })
                continue

            updated = Endpoint(
                endpoint_id=ep.endpoint_id,
                account_id=ep.account_id,
                url=ep.url,
                interval_minutes=ep.interval_minutes,
                enabled=True,
                next_check_at=compute_next_check_at(now_u, ep.interval_minutes),
                paused_reason=None,
            )
            storage.update_endpoint(updated)
            storage.set_account_status(ep.account_id, AccountStatus.ACTIVE.value)

        except Exception:
            errors += 1
            emit_event({
                "type": "tick_error",
                "ts": _utc_iso(now_u),
                "worker": FEESINK_WORKER_VERSION,
                "telemetry": TELEMETRY_SCHEMA_VERSION,
                "endpoint_id": ep.endpoint_id,
            })
        finally:
            try:
                storage.release_endpoint_lease(lease)
            except Exception:
                errors += 1
                emit_event({
                    "type": "lease_release_error",
                    "ts": _utc_iso(now_u),
                    "worker": FEESINK_WORKER_VERSION,
                    "telemetry": TELEMETRY_SCHEMA_VERSION,
                    "endpoint_id": ep.endpoint_id,
                })

    duration_ms = int((time.monotonic() - _t0) * 1000)
    emit_event({
        "type": "tick_end",
        "ts": _utc_iso(now_u),
        "worker": FEESINK_WORKER_VERSION,
        "telemetry": TELEMETRY_SCHEMA_VERSION,
        "ok": (errors == 0),
        "due_found": len(due),
        "leased": leased,
        "processed": processed,
        "charged_events": charged_events,
        "deduped_events": deduped_events,
        "depleted_paused": depleted_paused,
        "errors": errors,
        "duration_ms": duration_ms,
    })

    return TickResult(
        ok=(errors == 0),
        now_utc=now_u,
        due_found=len(due),
        leased=leased,
        processed=processed,
        charged_events=charged_events,
        deduped_events=deduped_events,
        depleted_paused=depleted_paused,
        errors=errors,
    )
