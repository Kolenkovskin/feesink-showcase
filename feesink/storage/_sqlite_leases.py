"""
FeeSink SQLite: endpoint leases.

Split from feesink/storage/sqlite.py without behavior changes.

Version:
- FEESINK-SQLITE-LEASES v2026.01.16-01
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Optional

from feesink.domain.models import EndpointId, ensure_utc
from feesink.storage.interfaces import Lease, StorageError, ValidationError
from feesink.storage._sqlite_utils import UTC, dt_to_str_utc, str_to_dt_utc


class SQLiteLeasesMixin:
    _conn: sqlite3.Connection

    def acquire_endpoint_lease(
        self,
        endpoint_id: EndpointId,
        lease_for: timedelta,
        now_utc: datetime,
    ) -> Optional[Lease]:
        if not endpoint_id or not str(endpoint_id).strip():
            raise ValidationError("endpoint_id must be non-empty")
        lease_for = lease_for if isinstance(lease_for, timedelta) else timedelta(seconds=0)
        if lease_for.total_seconds() <= 0:
            raise ValidationError("lease_for must be > 0")
        now_utc = ensure_utc(now_utc)

        lease_token = uuid.uuid4().hex
        lease_until = now_utc + lease_for

        now_s = dt_to_str_utc(now_utc)
        lease_until_s = dt_to_str_utc(lease_until)

        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()

            cur.execute(
                "SELECT lease_until_utc FROM endpoint_leases WHERE endpoint_id = ?",
                (str(endpoint_id),),
            )
            row = cur.fetchone()
            if row is not None:
                existing_until = str_to_dt_utc(str(row["lease_until_utc"]))
                if existing_until > now_utc:
                    cur.close()
                    self._conn.rollback()
                    return None

            cur.execute(
                """
                INSERT INTO endpoint_leases(
                    endpoint_id, lease_token, lease_until_utc,
                    created_at_utc, updated_at_utc
                )
                VALUES(?,?,?,?,?)
                ON CONFLICT(endpoint_id) DO UPDATE SET
                    lease_token = excluded.lease_token,
                    lease_until_utc = excluded.lease_until_utc,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (str(endpoint_id), str(lease_token), str(lease_until_s), now_s, now_s),
            )

            cur.close()
            self._conn.commit()
            return Lease(endpoint_id=str(endpoint_id), lease_token=str(lease_token), lease_until=lease_until)
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e

    def release_endpoint_lease(self, lease: Lease) -> None:
        if lease is None:
            return
        if not lease.endpoint_id or not str(lease.endpoint_id).strip():
            return
        if not lease.lease_token or not str(lease.lease_token).strip():
            return

        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
            cur.execute(
                "DELETE FROM endpoint_leases WHERE endpoint_id = ? AND lease_token = ?",
                (str(lease.endpoint_id), str(lease.lease_token)),
            )
            cur.close()
            self._conn.commit()
        except sqlite3.Error:
            self._conn.rollback()
            # best-effort
