"""
FeeSink SQLite: accounts.

Split from feesink/storage/sqlite.py without behavior changes.

Version:
- FEESINK-SQLITE-ACCOUNTS v2026.01.16-01
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from feesink.domain.models import Account, AccountId
from feesink.storage.interfaces import NotFound, StorageError, ValidationError

from feesink.storage._sqlite_utils import UTC, dt_to_str_utc, parse_account_status


class SQLiteAccountsMixin:
    _conn: sqlite3.Connection

    def get_account(self, account_id: AccountId) -> Account:
        if not account_id or not str(account_id).strip():
            raise ValidationError("account_id must be non-empty")

        cur = self._conn.cursor()
        try:
            cur.execute(
                "SELECT account_id, balance_units, status FROM accounts WHERE account_id = ?",
                (str(account_id),),
            )
            row = cur.fetchone()
            if row is None:
                raise NotFound("account not found")

            acc_id = str(row["account_id"])
            status = parse_account_status(row["status"], acc_id)

            return Account(
                account_id=acc_id,
                balance_units=int(row["balance_units"]),
                status=status,
            )
        finally:
            cur.close()

    def ensure_account(self, account_id: AccountId) -> Account:
        if not account_id or not str(account_id).strip():
            raise ValidationError("account_id must be non-empty")

        now = datetime.now(tz=UTC)
        now_s = dt_to_str_utc(now)

        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO accounts(account_id, balance_units, status, created_at_utc, updated_at_utc)
                VALUES(?,?,?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET
                    updated_at_utc = excluded.updated_at_utc
                """,
                (str(account_id), 0, "active", now_s, now_s),
            )
            cur.close()
            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e

        return self.get_account(account_id)

    def set_account_status(self, account_id: AccountId, status: str) -> None:
        if not account_id or not str(account_id).strip():
            raise ValidationError("account_id must be non-empty")
        if not status or not str(status).strip():
            raise ValidationError("status must be non-empty")

        _ = parse_account_status(str(status), str(account_id))

        now_s = dt_to_str_utc(datetime.now(tz=UTC))
        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE accounts SET status = ?, updated_at_utc = ? WHERE account_id = ?",
                (str(status), now_s, str(account_id)),
            )
            if cur.rowcount <= 0:
                cur.close()
                self._conn.rollback()
                raise NotFound("account not found")
            cur.close()
            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e
