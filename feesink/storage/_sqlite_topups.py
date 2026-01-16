"""
FeeSink SQLite: topups / credit.

Split from feesink/storage/sqlite.py without behavior changes.

Version:
- FEESINK-SQLITE-TOPUPS v2026.01.16-01
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from feesink.domain.models import TopUp, TxHash
from feesink.storage.interfaces import CreditResult, NotFound, StorageError, ValidationError
from feesink.storage._sqlite_utils import UTC, dt_to_str_utc, ensure_utc


class SQLiteTopupsMixin:
    _conn: sqlite3.Connection

    def has_tx_hash(self, tx_hash: TxHash) -> bool:
        if not tx_hash or not str(tx_hash).strip():
            raise ValidationError("tx_hash must be non-empty")
        cur = self._conn.cursor()
        try:
            cur.execute("SELECT 1 FROM topups WHERE tx_hash = ? LIMIT 1", (str(tx_hash),))
            return cur.fetchone() is not None
        finally:
            cur.close()

    def credit_topup(self, topup: TopUp) -> CreditResult:
        if not topup:
            raise ValidationError("topup must be provided")

        topup_id = getattr(topup, "topup_id", None)
        if not topup_id:
            topup_id = getattr(topup, "tx_hash", None)

        created_at = getattr(topup, "created_at_utc", None)
        if created_at is None:
            created_at = getattr(topup, "ts", None)

        if not topup_id or not str(topup_id).strip():
            raise ValidationError("topup_id must be non-empty (topup.topup_id or topup.tx_hash)")
        if not topup.account_id or not str(topup.account_id).strip():
            raise ValidationError("topup.account_id must be non-empty")
        if not topup.tx_hash or not str(topup.tx_hash).strip():
            raise ValidationError("topup.tx_hash must be non-empty")
        if created_at is None:
            raise ValidationError("topup timestamp must be provided (topup.created_at_utc or topup.ts)")

        now_s = dt_to_str_utc(datetime.now(tz=UTC))
        created_s = dt_to_str_utc(ensure_utc(created_at))

        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()

            cur.execute(
                "SELECT account_id, balance_units, status FROM accounts WHERE account_id = ?",
                (str(topup.account_id),),
            )
            row = cur.fetchone()
            if row is None:
                cur.close()
                self._conn.rollback()
                raise NotFound("account not found")

            balance_units = int(row["balance_units"])
            status_str = str(row["status"])

            cur.execute(
                """
                INSERT INTO topups(
                    topup_id, account_id, tx_hash, amount_usdt, credited_units, created_at_utc
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    str(topup_id),
                    str(topup.account_id),
                    str(topup.tx_hash),
                    int(topup.amount_usdt),
                    int(topup.credited_units),
                    created_s,
                ),
            )

            new_balance = balance_units + int(topup.credited_units)
            new_status = "active" if new_balance > 0 else status_str
            cur.execute(
                "UPDATE accounts SET balance_units = ?, status = ?, updated_at_utc = ? WHERE account_id = ?",
                (int(new_balance), str(new_status), now_s, str(topup.account_id)),
            )

            cur.close()
            self._conn.commit()
            return CreditResult(inserted=True, topup=topup)

        except sqlite3.IntegrityError:
            self._conn.rollback()
            return CreditResult(inserted=False, topup=topup)
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e
