# feesink/storage/_sqlite_stripe.py
"""
FeeSink SQLite: Stripe helper methods used by API.

Split from feesink/storage/sqlite.py without behavior changes.

Version:
- FEESINK-SQLITE-STRIPE v2026.01.19-03
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from feesink.domain.models import AccountId
from feesink.storage.interfaces import StorageError, ValidationError
from feesink.storage._sqlite_utils import UTC, dt_to_str_utc, ensure_utc


class SQLiteStripeMixin:
    _conn: sqlite3.Connection

    # Aliases expected by feesink/api/server.py

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

    def put_token(self, token: str, account_id: AccountId) -> None:
        if not token or not str(token).strip():
            raise ValidationError("token must be non-empty")
        if not account_id or not str(account_id).strip():
            raise ValidationError("account_id must be non-empty")

        now_s = dt_to_str_utc(datetime.now(tz=UTC))
        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
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
            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e

    def resolve_token(self, token: str) -> Optional[AccountId]:
        if not token or not str(token).strip():
            raise ValidationError("token must be non-empty")
        cur = self._conn.cursor()
        try:
            cur.execute("SELECT account_id FROM tokens WHERE token = ? LIMIT 1", (str(token),))
            row = cur.fetchone()
            if row is None:
                return None
            return str(row["account_id"])
        finally:
            cur.close()

    def insert_provider_event(self, provider: str, provider_event_id: str, raw_json: str) -> bool:
        if not provider or not str(provider).strip():
            raise ValidationError("provider must be non-empty")
        if not provider_event_id or not str(provider_event_id).strip():
            raise ValidationError("provider_event_id must be non-empty")
        if raw_json is None:
            raise ValidationError("raw_json must be provided")

        now_s = dt_to_str_utc(datetime.now(tz=UTC))
        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO provider_events(
                    provider, provider_event_id, event_type, status,
                    received_at_utc, processed_at_utc,
                    account_id, credited_units, raw_event_json
                )
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(provider),
                    str(provider_event_id),
                    "unknown",
                    "received",
                    now_s,
                    None,
                    None,
                    None,
                    str(raw_json),
                ),
            )
            cur.close()
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            self._conn.rollback()
            return False
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e

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

        created_at_utc = ensure_utc(created_at_utc)
        created_s = dt_to_str_utc(created_at_utc)
        cust = str(stripe_customer_id).strip() if stripe_customer_id else None

        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
            try:
                # Primary path: upsert by stripe_session_id (PK)
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
                # Secondary path: schema.sql enforces UNIQUE(stripe_customer_id) when present.
                # If a customer repeats across sessions, the INSERT can fail even though
                # the mapping is valid. Resolve by updating the existing row by customer_id,
                # moving it to the new session id (PK update is allowed if it doesn't clash).
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
                            f"stripe_links: UNIQUE(stripe_customer_id) conflict but UPDATE by customer_id affected {updated} rows"
                        ) from e
                else:
                    raise
            finally:
                cur.close()

            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e

    def resolve_stripe_link(self, stripe_session_id: str) -> Optional[AccountId]:
        if not stripe_session_id or not str(stripe_session_id).strip():
            raise ValidationError("stripe_session_id must be non-empty")
        cur = self._conn.cursor()
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
        cur = self._conn.cursor()
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
