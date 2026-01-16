"""
FeeSink storage interfaces (MVP: HTTP Endpoint Watchdog)

Source of truth:
- Project root SPEC.md (CANON v1)

Storage layer responsibilities:
- Persistence of Accounts, Endpoints, CheckEvents, TopUps
- Idempotent payment crediting (tx_hash unique)
- Deterministic unit charging per check (no double-charge)
- Endpoint leasing to prevent concurrent processing

This module defines contracts only (no implementation, no I/O).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Protocol, Sequence

from feesink.domain.models import (
    Account,
    AccountId,
    CheckEvent,
    Endpoint,
    EndpointId,
    TopUp,
    TxHash,
)


UTC = timezone.utc


# ----------------------------
# Storage errors (contract-level)
# ----------------------------

class StorageError(Exception):
    """Base class for storage failures."""


class NotFound(StorageError):
    """Entity not found."""


class Conflict(StorageError):
    """Optimistic concurrency conflict / lock conflict."""


class ValidationError(StorageError):
    """Storage-level validation error (e.g. invariant violation)."""


# ----------------------------
# Leasing model (to prevent double-check / double-charge)
# ----------------------------

@dataclass(frozen=True, slots=True)
class Lease:
    """
    Lease token returned by storage to prove exclusive right
    to process an endpoint for a bounded time window.

    Invariant:
    - lease_until is UTC, and must be in the future at acquisition.
    """
    endpoint_id: EndpointId
    lease_token: str
    lease_until: datetime  # UTC


# ----------------------------
# Idempotency results
# ----------------------------

@dataclass(frozen=True, slots=True)
class CreditResult:
    """
    Result of attempting to credit a top-up.

    inserted=True: first time tx_hash seen and credited
    inserted=False: tx_hash already existed; no additional credit applied
    """
    inserted: bool
    topup: TopUp


@dataclass(frozen=True, slots=True)
class ChargeResult:
    """
    Result of attempting to persist a check event and charge units atomically.

    inserted=True: event persisted and units charged
    inserted=False: event considered duplicate; no additional charge applied
    """
    inserted: bool
    event: CheckEvent
    new_balance_units: int


# ----------------------------
# Storage interface (CANON v1)
# ----------------------------

class Storage(Protocol):
    """
    Minimal persistence contract for FeeSink MVP.

    MUST-HAVE invariants:
    1) Payments: credit_topup(tx_hash) is idempotent.
       - same tx_hash must never credit twice.

    2) Checks: record_check_and_charge(...) is atomic and prevents double-charge.
       - for a given execution attempt, storage must ensure a check event cannot be
         persisted and charged twice.

    3) Leasing: acquire_endpoint_lease(...) prevents two workers from processing
       the same endpoint concurrently.
    """

    # -------- Accounts --------

    def get_account(self, account_id: AccountId) -> Account:
        """Return account or raise NotFound."""

    def ensure_account(self, account_id: AccountId) -> Account:
        """
        Ensure account exists (create if absent) with balance=0.
        Returns current account.
        """

    def set_account_status(self, account_id: AccountId, status: str) -> None:
        """
        Persist status field if tracked explicitly by storage.
        status is expected to be one of: 'active' | 'depleted'
        """

    # -------- Endpoints --------

    def add_endpoint(self, endpoint: Endpoint) -> Endpoint:
        """Persist endpoint. Must validate domain invariants."""

    def update_endpoint(self, endpoint: Endpoint) -> Endpoint:
        """Persist endpoint update. Must validate invariants."""

    def get_endpoint(self, endpoint_id: EndpointId) -> Endpoint:
        """Return endpoint or raise NotFound."""

    def list_endpoints(self, account_id: AccountId) -> Sequence[Endpoint]:
        """Return all endpoints for account (enabled and paused)."""

    def due_endpoints(self, now_utc: datetime, limit: int) -> Sequence[Endpoint]:
        """
        Return endpoints due for checking (next_check_at <= now_utc)
        and currently enabled, subject to a limit.
        """

    # -------- Leasing --------

    def acquire_endpoint_lease(
        self,
        endpoint_id: EndpointId,
        lease_for: timedelta,
        now_utc: datetime,
    ) -> Optional[Lease]:
        """
        Try to acquire exclusive lease for endpoint.

        Returns:
        - Lease if acquired
        - None if already leased by someone else

        MUST:
        - lease_until = now_utc + lease_for (UTC)
        - guarantee only one active lease per endpoint at a time
        """

    def release_endpoint_lease(self, lease: Lease) -> None:
        """
        Release lease early (best-effort). If lease already expired/unknown, ignore.
        """

    # -------- Payments (idempotent) --------

    def credit_topup(self, topup: TopUp) -> CreditResult:
        """
        Idempotently credit a confirmed on-chain top-up.

        MUST:
        - enforce uniqueness by tx_hash
        - if tx_hash already exists: inserted=False and no additional credit
        - if inserted: increase account.balance_units by credited_units
        """

    def has_tx_hash(self, tx_hash: TxHash) -> bool:
        """Fast check: whether tx_hash is already recorded."""

    # -------- Checks (atomic charge) --------

    def record_check_and_charge(
        self,
        account_id: AccountId,
        event: CheckEvent,
        charge_units: int,
        dedup_key: str,
    ) -> ChargeResult:
        """
        Atomically:
        1) persist CheckEvent
        2) deduct charge_units from account.balance_units

        MUST:
        - never let balance go negative
        - if balance is insufficient: raise Conflict or ValidationError (implementation choice)
        - ensure dedup by dedup_key (idempotency for retries)
          Same dedup_key must not charge twice.

        Notes:
        - dedup_key is generated by runtime per execution attempt
          (e.g. endpoint_id + scheduled_ts + attempt_id).
        """

    # -------- Housekeeping (optional, but useful) --------

    def trim_check_events(self, older_than_utc: datetime) -> int:
        """
        Optional: delete old CheckEvent rows to keep storage bounded.
        Returns number removed.
        """


# ----------------------------
# Helper: default lease duration for MVP (runtime may override)
# ----------------------------

DEFAULT_LEASE_FOR = timedelta(seconds=30)
