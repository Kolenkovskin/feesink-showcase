"""
FeeSink SQLite: endpoints.

Split from feesink/storage/sqlite.py without behavior changes.

Version:
- FEESINK-SQLITE-ENDPOINTS v2026.01.16-01
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import List, Sequence

from feesink.domain.models import Endpoint, EndpointId, AccountId, ensure_utc
from feesink.storage.interfaces import Conflict, NotFound, StorageError, ValidationError
from feesink.storage._sqlite_utils import UTC, dt_to_str_utc, str_to_dt_utc, to_int_bool, parse_paused_reason


class SQLiteEndpointsMixin:
    _conn: sqlite3.Connection

    def add_endpoint(self, endpoint: Endpoint) -> Endpoint:
        if not endpoint:
            raise ValidationError("endpoint must be provided")
        if not endpoint.endpoint_id or not str(endpoint.endpoint_id).strip():
            raise ValidationError("endpoint.endpoint_id must be non-empty")
        if not endpoint.account_id or not str(endpoint.account_id).strip():
            raise ValidationError("endpoint.account_id must be non-empty")
        if not endpoint.url or not str(endpoint.url).strip():
            raise ValidationError("endpoint.url must be non-empty")

        now = datetime.now(tz=UTC)
        now_s = dt_to_str_utc(now)

        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO endpoints(
                    endpoint_id, account_id,
                    url, interval_minutes,
                    enabled, paused_reason,
                    next_check_at_utc,
                    last_check_at_utc, last_result,
                    created_at_utc, updated_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(endpoint.endpoint_id),
                    str(endpoint.account_id),
                    str(endpoint.url),
                    int(endpoint.interval_minutes),
                    to_int_bool(bool(endpoint.enabled)),
                    str(endpoint.paused_reason.value) if endpoint.paused_reason else None,
                    dt_to_str_utc(endpoint.next_check_at),
                    dt_to_str_utc(endpoint.last_check_at) if endpoint.last_check_at else None,
                    str(endpoint.last_result.value) if endpoint.last_result else None,
                    now_s,
                    now_s,
                ),
            )
            cur.close()
            self._conn.commit()
            return endpoint
        except sqlite3.IntegrityError as e:
            self._conn.rollback()
            raise Conflict(str(e)) from e
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e

    def update_endpoint(self, endpoint: Endpoint) -> Endpoint:
        if not endpoint:
            raise ValidationError("endpoint must be provided")
        if not endpoint.endpoint_id or not str(endpoint.endpoint_id).strip():
            raise ValidationError("endpoint.endpoint_id must be non-empty")
        if not endpoint.account_id or not str(endpoint.account_id).strip():
            raise ValidationError("endpoint.account_id must be non-empty")
        if not endpoint.url or not str(endpoint.url).strip():
            raise ValidationError("endpoint.url must be non-empty")

        now_s = dt_to_str_utc(datetime.now(tz=UTC))

        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE endpoints SET
                    url = ?,
                    interval_minutes = ?,
                    enabled = ?,
                    paused_reason = ?,
                    next_check_at_utc = ?,
                    last_check_at_utc = ?,
                    last_result = ?,
                    updated_at_utc = ?
                WHERE endpoint_id = ? AND account_id = ?
                """,
                (
                    str(endpoint.url),
                    int(endpoint.interval_minutes),
                    to_int_bool(bool(endpoint.enabled)),
                    str(endpoint.paused_reason.value) if endpoint.paused_reason else None,
                    dt_to_str_utc(endpoint.next_check_at),
                    dt_to_str_utc(endpoint.last_check_at) if endpoint.last_check_at else None,
                    str(endpoint.last_result.value) if endpoint.last_result else None,
                    now_s,
                    str(endpoint.endpoint_id),
                    str(endpoint.account_id),
                ),
            )
            if cur.rowcount <= 0:
                cur.close()
                self._conn.rollback()
                raise NotFound("endpoint not found")
            cur.close()
            self._conn.commit()
            return endpoint
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e

    def get_endpoint(self, endpoint_id: EndpointId) -> Endpoint:
        if not endpoint_id or not str(endpoint_id).strip():
            raise ValidationError("endpoint_id must be non-empty")

        cur = self._conn.cursor()
        try:
            cur.execute(
                """
                SELECT
                    endpoint_id, account_id,
                    url, interval_minutes,
                    enabled, paused_reason,
                    next_check_at_utc,
                    last_check_at_utc, last_result
                FROM endpoints
                WHERE endpoint_id = ?
                """,
                (str(endpoint_id),),
            )
            row = cur.fetchone()
            if row is None:
                raise NotFound("endpoint not found")

            enabled = bool(int(row["enabled"]))
            paused_reason = parse_paused_reason(row["paused_reason"], str(endpoint_id))
            next_check_at = str_to_dt_utc(str(row["next_check_at_utc"]))
            last_check_at = str_to_dt_utc(str(row["last_check_at_utc"])) if row["last_check_at_utc"] else None

            last_result = None
            if row["last_result"]:
                from feesink.domain.models import CheckResult  # local import to avoid cycles
                last_result = CheckResult(str(row["last_result"]))

            return Endpoint(
                endpoint_id=str(row["endpoint_id"]),
                account_id=str(row["account_id"]),
                url=str(row["url"]),
                interval_minutes=int(row["interval_minutes"]),
                enabled=enabled,
                paused_reason=paused_reason,
                next_check_at=next_check_at,
                last_check_at=last_check_at,
                last_result=last_result,
            )
        finally:
            cur.close()

    def list_endpoints(self, account_id: AccountId) -> Sequence[Endpoint]:
        if not account_id or not str(account_id).strip():
            raise ValidationError("account_id must be non-empty")
        cur = self._conn.cursor()
        try:
            cur.execute(
                """
                SELECT
                    endpoint_id, account_id,
                    url, interval_minutes,
                    enabled, paused_reason,
                    next_check_at_utc,
                    last_check_at_utc, last_result
                FROM endpoints
                WHERE account_id = ?
                ORDER BY created_at_utc ASC
                """,
                (str(account_id),),
            )
            rows = cur.fetchall()
            out: List[Endpoint] = []
            for row in rows:
                enabled = bool(int(row["enabled"]))
                paused_reason = parse_paused_reason(row["paused_reason"], str(row["endpoint_id"]))
                next_check_at = str_to_dt_utc(str(row["next_check_at_utc"]))
                last_check_at = str_to_dt_utc(str(row["last_check_at_utc"])) if row["last_check_at_utc"] else None

                last_result = None
                if row["last_result"]:
                    from feesink.domain.models import CheckResult  # local import to avoid cycles
                    last_result = CheckResult(str(row["last_result"]))

                out.append(
                    Endpoint(
                        endpoint_id=str(row["endpoint_id"]),
                        account_id=str(row["account_id"]),
                        url=str(row["url"]),
                        interval_minutes=int(row["interval_minutes"]),
                        enabled=enabled,
                        paused_reason=paused_reason,
                        next_check_at=next_check_at,
                        last_check_at=last_check_at,
                        last_result=last_result,
                    )
                )
            return out
        finally:
            cur.close()

    def due_endpoints(self, now_utc: datetime, limit: int) -> Sequence[Endpoint]:
        now_utc = ensure_utc(now_utc)
        if limit <= 0:
            return []

        now_s = dt_to_str_utc(now_utc)

        cur = self._conn.cursor()
        try:
            cur.execute(
                """
                SELECT
                    endpoint_id, account_id,
                    url, interval_minutes,
                    enabled, paused_reason,
                    next_check_at_utc,
                    last_check_at_utc, last_result
                FROM endpoints
                WHERE enabled = 1
                  AND next_check_at_utc <= ?
                ORDER BY next_check_at_utc ASC
                LIMIT ?
                """,
                (now_s, int(limit)),
            )
            rows = cur.fetchall()
            out: List[Endpoint] = []
            for row in rows:
                enabled = bool(int(row["enabled"]))
                paused_reason = parse_paused_reason(row["paused_reason"], str(row["endpoint_id"]))
                next_check_at = str_to_dt_utc(str(row["next_check_at_utc"]))
                last_check_at = str_to_dt_utc(str(row["last_check_at_utc"])) if row["last_check_at_utc"] else None

                last_result = None
                if row["last_result"]:
                    from feesink.domain.models import CheckResult  # local import to avoid cycles
                    last_result = CheckResult(str(row["last_result"]))

                out.append(
                    Endpoint(
                        endpoint_id=str(row["endpoint_id"]),
                        account_id=str(row["account_id"]),
                        url=str(row["url"]),
                        interval_minutes=int(row["interval_minutes"]),
                        enabled=enabled,
                        paused_reason=paused_reason,
                        next_check_at=next_check_at,
                        last_check_at=last_check_at,
                        last_result=last_result,
                    )
                )
            return out
        finally:
            cur.close()

    def delete_endpoint(self, account_id: AccountId, endpoint_id: EndpointId) -> None:
        if not account_id or not str(account_id).strip():
            raise ValidationError("account_id must be non-empty")
        if not endpoint_id or not str(endpoint_id).strip():
            raise ValidationError("endpoint_id must be non-empty")

        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
            cur.execute(
                "DELETE FROM endpoints WHERE endpoint_id = ? AND account_id = ?",
                (str(endpoint_id), str(account_id)),
            )
            if cur.rowcount <= 0:
                cur.close()
                self._conn.rollback()
                raise NotFound("endpoint not found")
            cur.close()
            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e
