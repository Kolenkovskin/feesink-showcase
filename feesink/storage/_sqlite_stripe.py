"""
FeeSink SQLite (Stripe-related persistence).

Version:
- FEESINK-SQLITE-STRIPE v2026.01.25-01 (update accounts.last_provider_event_at_utc on provider_event insert)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, overload, Any

from feesink.domain.models import AccountId, ProviderEvent
from feesink.storage.interfaces import StorageError, ValidationError
from feesink.storage._sqlite_utils import UTC, dt_to_str_utc, ensure_utc


@dataclass(frozen=True, slots=True)
class StripeLink:
    stripe_session_id: str
    stripe_customer_id: Optional[str]
    account_id: str


class SQLiteStripeMixin:
    """
    This mixin is used by feesink/storage/sqlite.py facade.

    Expectations:
    - self._conn: sqlite3.Connection (attribute) OR self.conn: sqlite3.Connection
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """
        Prefer facade-style attribute `self._conn`, fallback to `self.conn`.
        """
        conn = getattr(self, "_conn", None)
        if isinstance(conn, sqlite3.Connection):
            return conn
        conn = getattr(self, "conn", None)
        if isinstance(conn, sqlite3.Connection):
            return conn
        raise StorageError("SQLiteStripeMixin: no sqlite3.Connection found on self (_conn/conn)")

    def _touch_last_provider_event(self, *, conn: sqlite3.Connection, account_id: str, received_at_utc_s: str) -> None:
        """
        Ops-only: update accounts.last_provider_event_at_utc to max(existing, received_at).
        Must not affect billing fields.
        """
        now_s = dt_to_str_utc(datetime.now(tz=UTC))
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE accounts
                SET
                  last_provider_event_at_utc =
                    CASE
                      WHEN last_provider_event_at_utc IS NULL THEN ?
                      WHEN last_provider_event_at_utc < ? THEN ?
                      ELSE last_provider_event_at_utc
                    END,
                  updated_at_utc = ?
                WHERE account_id = ?
                """,
                (received_at_utc_s, received_at_utc_s, received_at_utc_s, now_s, str(account_id)),
            )
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Aliases expected by feesink/api/server.py (keep stable)
    # ------------------------------------------------------------------

    def upsert_stripe_link(
        self,
        *,
        account_id: AccountId,
        stripe_session_id: str,
        stripe_customer_id: Optional[str] = None,
    ) -> None:
        created_at_utc = datetime.now(tz=UTC)
        self.put_stripe_link(
            stripe_session_id=stripe_session_id,
            account_id=account_id,
            created_at_utc=created_at_utc,
            stripe_customer_id=stripe_customer_id,
        )

    def resolve_account_by_stripe_session(self, stripe_session_id: str) -> Optional[AccountId]:
        return self.resolve_stripe_link(stripe_session_id)

    def resolve_account_by_stripe_customer(self, stripe_customer_id: str) -> Optional[AccountId]:
        return self.resolve_stripe_customer(stripe_customer_id)

    # ------------------------------------------------------------------
    # Tokens (legacy support; keep if API still imports/uses it)
    # ------------------------------------------------------------------

    def put_token(self, token: str, account_id: AccountId) -> None:
        if not token or not str(token).strip():
            raise ValidationError("token must be non-empty")
        if not account_id or not str(account_id).strip():
            raise ValidationError("account_id must be non-empty")

        conn = self._get_conn()
        now_s = dt_to_str_utc(datetime.now(tz=UTC))

        conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO tokens(token, account_id, created_at_utc)
                VALUES(?,?,?)
                ON CONFLICT(token) DO UPDATE SET
                    account_id = excluded.account_id
                """,
                (str(token), str(account_id), now_s),
            )
            cur.close()
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise StorageError(str(e)) from e

    def resolve_token(self, token: str) -> Optional[AccountId]:
        if not token or not str(token).strip():
            raise ValidationError("token must be non-empty")
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT account_id FROM tokens WHERE token = ? LIMIT 1", (str(token),))
            row = cur.fetchone()
            if row is None:
                return None
            return str(row["account_id"])
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Provider events (support BOTH old and new call styles)
    # ------------------------------------------------------------------

    @overload
    def insert_provider_event(self, provider: str, provider_event_id: str, raw_json: str) -> bool: ...
    @overload
    def insert_provider_event(self, event: ProviderEvent) -> bool: ...

    def insert_provider_event(self, *args: Any, **kwargs: Any) -> bool:
        """
        Insert provider event row (idempotent by UNIQUE(provider, provider_event_id)).

        Supported call styles:
        1) legacy: insert_provider_event(provider, provider_event_id, raw_json) -> bool
        2) new:    insert_provider_event(event: ProviderEvent) -> bool

        P1 audit fields (nullable):
        - raw_body_sha256 (hex sha256 of raw bytes)
        - signature_verified_at_utc (UTC ISO8601)

        P2 ops-only:
        - updates accounts.last_provider_event_at_utc (if event.account_id is present)
        """
        if len(args) == 1 and isinstance(args[0], ProviderEvent):
            return self._insert_provider_event_v2(args[0])

        if len(args) == 3 and not kwargs:
            provider, provider_event_id, raw_json = args
            if raw_json is None:
                raise ValidationError("raw_json must be provided")
            event = ProviderEvent(
                provider=str(provider),
                provider_event_id=str(provider_event_id),
                event_type="unknown",
                status="received",
                received_at=datetime.now(tz=UTC),
                processed_at=None,
                account_id=None,
                credited_units=None,
                raw_event_json=str(raw_json),
                raw_body_sha256=None,
                signature_verified_at=None,
            )
            return self._insert_provider_event_v2(event)

        raise TypeError("insert_provider_event expects (ProviderEvent) OR (provider, provider_event_id, raw_json)")

    def _insert_provider_event_v2(self, event: ProviderEvent) -> bool:
        provider = (event.provider or "").strip()
        provider_event_id = (event.provider_event_id or "").strip()
        if not provider or not provider_event_id:
            raise ValidationError("provider/provider_event_id must be non-empty")

        conn = self._get_conn()

        received_at_utc_s = dt_to_str_utc(ensure_utc(event.received_at))
        processed_at_utc_s = dt_to_str_utc(ensure_utc(event.processed_at)) if event.processed_at else None
        signature_verified_at_utc_s = (
            dt_to_str_utc(ensure_utc(event.signature_verified_at)) if event.signature_verified_at else None
        )

        conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO provider_events(
                  provider,
                  provider_event_id,
                  event_type,
                  status,
                  received_at_utc,
                  processed_at_utc,
                  account_id,
                  credited_units,
                  raw_event_json,
                  raw_body_sha256,
                  signature_verified_at_utc
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?);
                """,
                (
                    provider,
                    provider_event_id,
                    event.event_type,
                    event.status,
                    received_at_utc_s,
                    processed_at_utc_s,
                    event.account_id,
                    event.credited_units,
                    event.raw_event_json,
                    event.raw_body_sha256,
                    signature_verified_at_utc_s,
                ),
            )
            cur.close()

            # Ops-only touch (only when inserted; no effect on billing)
            if event.account_id:
                self._touch_last_provider_event(
                    conn=conn,
                    account_id=str(event.account_id),
                    received_at_utc_s=received_at_utc_s,
                )

            conn.commit()
            return True

        except sqlite3.IntegrityError:
            conn.rollback()
            return False
        except sqlite3.Error as e:
            conn.rollback()
            raise StorageError(f"failed to insert provider_event: {e}") from e

    # ------------------------------------------------------------------
    # Stripe links (keep strict validation + previous semantics)
    # ------------------------------------------------------------------

    def put_stripe_link(
        self,
        stripe_session_id: str,
        account_id: AccountId,
        created_at_utc: datetime,
        stripe_customer_id: Optional[str] = None,
    ) -> None:
        if not stripe_session_id or not str(stripe_session_id).strip():
            raise ValidationError("stripe_session_id must be non-empty")
        if not account_id or not str(account_id).strip():
            raise ValidationError("account_id must be non-empty")

        # Keep self-issued token invariant: ensure account exists before FK insert (if method exists).
        try:
            ensure = getattr(self, "ensure_account", None)
            if callable(ensure):
                ensure(account_id)  # type: ignore[misc]
        except Exception as e:
            raise StorageError(f"ensure_account failed: {e}") from e

        conn = self._get_conn()

        created_at_utc = ensure_utc(created_at_utc)
        created_s = dt_to_str_utc(created_at_utc)
        cust = str(stripe_customer_id).strip() if stripe_customer_id else None

        conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO stripe_links(
                        stripe_session_id, stripe_customer_id, account_id, created_at_utc
                    ) VALUES(?,?,?,?)
                    ON CONFLICT(stripe_session_id) DO UPDATE SET
                        stripe_customer_id = excluded.stripe_customer_id,
                        account_id = excluded.account_id
                    """,
                    (str(stripe_session_id), cust, str(account_id), created_s),
                )
            except sqlite3.IntegrityError as e:
                # If UNIQUE(stripe_customer_id) triggers, update existing row by customer id.
                if cust:
                    cur.execute(
                        """
                        UPDATE stripe_links
                        SET
                            stripe_session_id = ?,
                            account_id = ?,
                            created_at_utc = ?
                        WHERE stripe_customer_id = ?
                        """,
                        (str(stripe_session_id), str(account_id), created_s, cust),
                    )
                    updated = int(cur.rowcount or 0)
                    if updated != 1:
                        raise StorageError(
                            f"stripe_links: UNIQUE(stripe_customer_id) conflict but UPDATE affected {updated} rows"
                        ) from e
                else:
                    raise
            finally:
                cur.close()

            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise StorageError(str(e)) from e

    def resolve_stripe_link(self, stripe_session_id: str) -> Optional[AccountId]:
        if not stripe_session_id or not str(stripe_session_id).strip():
            raise ValidationError("stripe_session_id must be non-empty")

        conn = self._get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT account_id FROM stripe_links WHERE stripe_session_id = ? LIMIT 1",
                (str(stripe_session_id),),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return str(row["account_id"])
        finally:
            cur.close()

    def resolve_stripe_customer(self, stripe_customer_id: str) -> Optional[AccountId]:
        if not stripe_customer_id or not str(stripe_customer_id).strip():
            raise ValidationError("stripe_customer_id must be non-empty")

        conn = self._get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT account_id FROM stripe_links WHERE stripe_customer_id = ? LIMIT 1",
                (str(stripe_customer_id),),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return str(row["account_id"])
        finally:
            cur.close()
