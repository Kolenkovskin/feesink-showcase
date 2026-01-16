"""
FeeSink SQLite: checks (record_check_and_charge).

Split from feesink/storage/sqlite.py without behavior changes.

Version:
- FEESINK-SQLITE-CHECKS v2026.01.16-01
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from feesink.domain.models import AccountId, CheckEvent, ensure_utc
from feesink.storage.interfaces import ChargeResult, Conflict, NotFound, StorageError, ValidationError
from feesink.storage._sqlite_utils import UTC, dt_to_str_utc


class SQLiteChecksMixin:
    _conn: sqlite3.Connection

    def record_check_and_charge(
        self,
        account_id: AccountId,
        event: CheckEvent,
        charge_units: int,
        dedup_key: str,
    ) -> ChargeResult:
        if not account_id or not str(account_id).strip():
            raise ValidationError("account_id must be non-empty")
        if not event:
            raise ValidationError("event must be provided")
        if charge_units <= 0:
            raise ValidationError("charge_units must be > 0")
        if not dedup_key or not str(dedup_key).strip():
            raise ValidationError("dedup_key must be non-empty")

        if int(charge_units) != 1:
            raise ValidationError("CANON: charge_units must be 1 (1 check = 1 unit)")

        try:
            check_id = str(event.check_id)
            result_s = str(event.result.value)
            ts_s = dt_to_str_utc(ensure_utc(event.ts_utc))
            scheduled_at_s = dt_to_str_utc(ensure_utc(event.scheduled_at_utc))
        except Exception as e:
            raise ValidationError(f"invalid CheckEvent: {e}") from e

        now_s = dt_to_str_utc(datetime.now(tz=UTC))

        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()

            cur.execute(
                "SELECT account_id, balance_units, status FROM accounts WHERE account_id = ?",
                (str(account_id),),
            )
            row = cur.fetchone()
            if row is None:
                raise NotFound("account not found")
            balance_units = int(row["balance_units"])
            status_str = str(row["status"])

            if balance_units < charge_units:
                if status_str != "depleted":
                    cur.execute(
                        "UPDATE accounts SET status = ?, updated_at_utc = ? WHERE account_id = ?",
                        ("depleted", now_s, str(account_id)),
                    )
                cur.close()
                self._conn.rollback()
                raise Conflict("insufficient balance_units")

            cur.execute(
                """
                INSERT INTO check_events(
                    check_id, account_id, endpoint_id,
                    scheduled_at_utc, ts_utc,
                    result, http_status, latency_ms, error_class,
                    dedup_key, units_charged, created_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(check_id),
                    str(account_id),
                    str(event.endpoint_id),
                    str(scheduled_at_s),
                    str(ts_s),
                    str(result_s),
                    int(event.http_status) if event.http_status is not None else None,
                    int(event.latency_ms) if event.latency_ms is not None else None,
                    str(event.error_class.value) if event.error_class is not None else None,
                    str(dedup_key),
                    int(charge_units),
                    now_s,
                ),
            )

            new_balance = balance_units - charge_units
            new_status = "depleted" if new_balance <= 0 else "active"
            cur.execute(
                "UPDATE accounts SET balance_units = ?, status = ?, updated_at_utc = ? WHERE account_id = ?",
                (int(new_balance), str(new_status), now_s, str(account_id)),
            )

            cur.close()
            self._conn.commit()
            return ChargeResult(inserted=True, event=event, new_balance_units=int(new_balance))

        except sqlite3.IntegrityError:
            self._conn.rollback()
            # import cycle safe: call self.get_account (provided by Accounts mixin)
            acc = self.get_account(account_id)  # type: ignore[attr-defined]
            return ChargeResult(inserted=False, event=event, new_balance_units=int(acc.balance_units))
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e
