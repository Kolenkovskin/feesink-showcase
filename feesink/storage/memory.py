"""
In-memory storage for FeeSink (MVP: HTTP Endpoint Watchdog)

Purpose:
- Run worker ticks manually without any database.
- Validate orchestration: leasing, idempotent crediting, atomic charge+event.

Source of truth:
- SPEC.md (CANON v1)
- feesink.storage.interfaces.Storage (contract)

Notes:
- Not thread-safe (MVP manual runs).
- No persistence across process restarts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence
from uuid import uuid4

from feesink.domain.models import (
    Account,
    AccountId,
    AccountStatus,
    CheckEvent,
    Endpoint,
    EndpointId,
    TopUp,
    TxHash,
    ensure_utc,
)
from feesink.storage.interfaces import (
    ChargeResult,
    Conflict,
    CreditResult,
    Lease,
    NotFound,
    Storage,
    StorageError,
    ValidationError,
)

UTC = timezone.utc


@dataclass
class _LeaseState:
    token: str
    until: datetime  # UTC


class InMemoryStorage(Storage):
    """
    Minimal in-memory implementation of Storage.

    Enforces:
    - tx_hash idempotency
    - dedup_key idempotency for charge+event
    - lease exclusivity per endpoint
    - no negative balance
    """

    def __init__(self) -> None:
        # Accounts
        self._accounts_balance: Dict[AccountId, int] = {}
        self._accounts_status: Dict[AccountId, str] = {}

        # Endpoints
        self._endpoints: Dict[EndpointId, Endpoint] = {}

        # Top-ups by tx_hash
        self._topups_by_tx: Dict[TxHash, TopUp] = {}

        # Check events per endpoint (append-only)
        self._check_events: Dict[EndpointId, List[CheckEvent]] = {}

        # Dedup keys seen per account_id
        self._dedup_keys: Dict[AccountId, Dict[str, CheckEvent]] = {}

        # Leases per endpoint
        self._leases: Dict[EndpointId, _LeaseState] = {}

    # ----------------------------
    # Accounts
    # ----------------------------

    def ensure_account(self, account_id: AccountId) -> Account:
        if not account_id or not account_id.strip():
            raise ValidationError("account_id must be non-empty")

        if account_id not in self._accounts_balance:
            self._accounts_balance[account_id] = 0
            self._accounts_status[account_id] = AccountStatus.DEPLETED.value

        return self.get_account(account_id)

    def get_account(self, account_id: AccountId) -> Account:
        if account_id not in self._accounts_balance:
            raise NotFound(f"Account not found: {account_id}")

        balance = self._accounts_balance[account_id]
        status_str = self._accounts_status.get(account_id, AccountStatus.DEPLETED.value)

        # Map to enum for domain model validation
        try:
            status = AccountStatus(status_str)
        except Exception:
            raise ValidationError(f"Invalid stored account status: {status_str}")

        acc = Account(account_id=account_id, balance_units=balance, status=status)
        acc.validate()
        return acc

    def set_account_status(self, account_id: AccountId, status: str) -> None:
        if account_id not in self._accounts_balance:
            raise NotFound(f"Account not found: {account_id}")
        if status not in (AccountStatus.ACTIVE.value, AccountStatus.DEPLETED.value):
            raise ValidationError("status must be 'active' or 'depleted'")
        self._accounts_status[account_id] = status

    # ----------------------------
    # Endpoints
    # ----------------------------

    def add_endpoint(self, endpoint: Endpoint) -> Endpoint:
        endpoint.validate()
        self.ensure_account(endpoint.account_id)

        if endpoint.endpoint_id in self._endpoints:
            raise Conflict(f"Endpoint already exists: {endpoint.endpoint_id}")

        self._endpoints[endpoint.endpoint_id] = endpoint
        self._check_events.setdefault(endpoint.endpoint_id, [])
        return endpoint

    def update_endpoint(self, endpoint: Endpoint) -> Endpoint:
        endpoint.validate()
        if endpoint.endpoint_id not in self._endpoints:
            raise NotFound(f"Endpoint not found: {endpoint.endpoint_id}")

        self._endpoints[endpoint.endpoint_id] = endpoint
        self._check_events.setdefault(endpoint.endpoint_id, [])
        return endpoint

    def get_endpoint(self, endpoint_id: EndpointId) -> Endpoint:
        if endpoint_id not in self._endpoints:
            raise NotFound(f"Endpoint not found: {endpoint_id}")
        return self._endpoints[endpoint_id]

    def list_endpoints(self, account_id: AccountId) -> Sequence[Endpoint]:
        return [ep for ep in self._endpoints.values() if ep.account_id == account_id]

    def due_endpoints(self, now_utc: datetime, limit: int) -> Sequence[Endpoint]:
        now_u = ensure_utc(now_utc)
        if limit <= 0:
            return []

        due: List[Endpoint] = []
        for ep in self._endpoints.values():
            if not ep.enabled:
                continue
            # next_check_at must be UTC; domain model enforces it, but re-ensure
            if ensure_utc(ep.next_check_at) <= now_u:
                due.append(ep)

        # Stable deterministic order: by next_check_at then endpoint_id
        due.sort(key=lambda e: (e.next_check_at, e.endpoint_id))
        return due[:limit]

    # ----------------------------
    # Leasing
    # ----------------------------

    def acquire_endpoint_lease(
        self,
        endpoint_id: EndpointId,
        lease_for: timedelta,
        now_utc: datetime,
    ) -> Optional[Lease]:
        now_u = ensure_utc(now_utc)
        if lease_for.total_seconds() <= 0:
            raise ValidationError("lease_for must be > 0 seconds")

        # If endpoint does not exist, treat as not found (strong contract)
        if endpoint_id not in self._endpoints:
            raise NotFound(f"Endpoint not found: {endpoint_id}")

        st = self._leases.get(endpoint_id)
        if st is not None:
            # Existing lease still valid?
            if st.until > now_u:
                return None
            # Expired lease, allow override
            self._leases.pop(endpoint_id, None)

        token = uuid4().hex
        until = now_u + lease_for
        self._leases[endpoint_id] = _LeaseState(token=token, until=until)

        lease = Lease(endpoint_id=endpoint_id, lease_token=token, lease_until=until)
        # No domain validate here; Lease is storage-level object
        return lease

    def release_endpoint_lease(self, lease: Lease) -> None:
        st = self._leases.get(lease.endpoint_id)
        if st is None:
            return
        # Only release if token matches; else ignore
        if st.token == lease.lease_token:
            self._leases.pop(lease.endpoint_id, None)

    # ----------------------------
    # Payments (idempotent)
    # ----------------------------

    def has_tx_hash(self, tx_hash: TxHash) -> bool:
        return tx_hash in self._topups_by_tx

    def credit_topup(self, topup: TopUp) -> CreditResult:
        topup.validate()
        self.ensure_account(topup.account_id)

        existing = self._topups_by_tx.get(topup.tx_hash)
        if existing is not None:
            return CreditResult(inserted=False, topup=existing)

        # Insert topup
        self._topups_by_tx[topup.tx_hash] = topup

        # Credit balance
        bal = self._accounts_balance[topup.account_id]
        new_bal = bal + topup.credited_units
        self._accounts_balance[topup.account_id] = new_bal

        # Mark active if balance > 0
        self._accounts_status[topup.account_id] = (
            AccountStatus.ACTIVE.value if new_bal > 0 else AccountStatus.DEPLETED.value
        )

        return CreditResult(inserted=True, topup=topup)

    # ----------------------------
    # Checks (atomic charge + event) with dedup
    # ----------------------------

    def record_check_and_charge(
        self,
        account_id: AccountId,
        event: CheckEvent,
        charge_units: int,
        dedup_key: str,
    ) -> ChargeResult:
        if not account_id or not account_id.strip():
            raise ValidationError("account_id must be non-empty")
        if charge_units <= 0:
            raise ValidationError("charge_units must be > 0")
        if not dedup_key or not dedup_key.strip():
            raise ValidationError("dedup_key must be non-empty")

        if account_id not in self._accounts_balance:
            raise NotFound(f"Account not found: {account_id}")

        event.validate()

        # Dedup check
        acc_map = self._dedup_keys.setdefault(account_id, {})
        existing_event = acc_map.get(dedup_key)
        if existing_event is not None:
            # No additional charge, no new event append
            bal = self._accounts_balance[account_id]
            return ChargeResult(inserted=False, event=existing_event, new_balance_units=bal)

        # Ensure sufficient balance (no negative)
        bal = self._accounts_balance[account_id]
        if bal < charge_units:
            # Contract allows Conflict or ValidationError; choose Conflict.
            raise Conflict("Insufficient balance")

        # Atomic "commit" for in-memory: perform all updates together
        new_bal = bal - charge_units
        self._accounts_balance[account_id] = new_bal
        self._accounts_status[account_id] = (
            AccountStatus.ACTIVE.value if new_bal > 0 else AccountStatus.DEPLETED.value
        )

        # Persist event
        self._check_events.setdefault(event.endpoint_id, []).append(event)

        # Persist dedup key -> event
        acc_map[dedup_key] = event

        return ChargeResult(inserted=True, event=event, new_balance_units=new_bal)

    # ----------------------------
    # Housekeeping
    # ----------------------------

    def trim_check_events(self, older_than_utc: datetime) -> int:
        """
        Removes check events older than threshold (UTC).
        """
        threshold = ensure_utc(older_than_utc)
        removed = 0

        for endpoint_id, events in list(self._check_events.items()):
            kept: List[CheckEvent] = []
            for ev in events:
                if ensure_utc(ev.ts) < threshold:
                    removed += 1
                else:
                    kept.append(ev)
            self._check_events[endpoint_id] = kept

        return removed
