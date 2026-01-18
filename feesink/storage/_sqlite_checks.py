"""
FeeSink SQLite: checks (record_check_and_charge).

CANON invariants:
- prepaid balance only (no negative balances)
- 1 check = 1 unit
- charge strictly after the check event exists (same atomic tx is OK)
- idempotent charging via dedup_key (UNIQUE(dedup_key))
- storage must NOT enforce dedup_key format (any non-empty string is valid)

Version:
- FEESINK-SQLITE-CHECKS v2026.01.16-03
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
        # ----------- validation -----------
        if not account_id or not str(account_id).strip():
            raise ValidationError("account_id must be non-empty")
        if event is None:
            raise ValidationError("event must be provided")
        if int(charge_units) != 1:
            raise ValidationError("CANON: charge_units must be 1 (1 check = 1 unit)")
        if not dedup_key or not str(dedup_key).strip():
            raise ValidationError("dedup_key must be non-empty")

        try:
            # Domain model uses ts (UTC enforced)
            ts_utc = ensure_utc(event.ts)
            ts_s = dt_to_str_utc(ts_utc)

            # scheduled_at_utc: for MVP we accept it equals event.ts
            # (format/true scheduled time is worker responsibility)
            scheduled_at_s = ts_s

            result_s = str(event.result.value)
            http_status_i = int(event.http_status) if event.http_status is not None else None
            latency_ms_i = int(event.latency_ms) if event.latency_ms is not None else None
            error_class_s = str(event.error_class.value) if event.error_class is not None else None
        except Exception as e:
            raise ValidationError(f"invalid CheckEvent: {e}") from e

        now_s = dt_to_str_utc(datetime.now(tz=UTC))

        # Canon decision:
        # check_id derived from dedup_key to keep idempotency key auditable.
        check_id = str(dedup_key)

        # ----------- atomic section -----------
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

            # prepaid only: do not insert check_event, do not charge, never go negative
            if balance_units < 1:
                if status_str != "depleted":
                    cur.execute(
                        "UPDATE accounts SET status = ?, updated_at_utc = ? WHERE account_id = ?",
                        ("depleted", now_s, str(account_id)),
                    )
                cur.close()
                self._conn.rollback()
                raise Conflict("insufficient balance_units")

            # 1) Insert check_event (idempotent by UNIQUE(dedup_key))
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
                    http_status_i,
                    latency_ms_i,
                    error_class_s,
                    str(dedup_key),
                    1,
                    now_s,
                ),
            )

            # 2) Charge account (still inside same tx)
            new_balance = balance_units - 1
            new_status = "depleted" if new_balance <= 0 else "active"

            cur.execute(
                "UPDATE accounts SET balance_units = ?, status = ?, updated_at_utc = ? WHERE account_id = ?",
                (int(new_balance), str(new_status), now_s, str(account_id)),
            )

            cur.close()
            self._conn.commit()
            return ChargeResult(inserted=True, event=event, new_balance_units=int(new_balance))

        except sqlite3.IntegrityError:
            # Dedup hit -> must NOT charge again
            self._conn.rollback()
            try:
                acc = self.get_account(account_id)  # type: ignore[attr-defined]
                return ChargeResult(inserted=False, event=event, new_balance_units=int(acc.balance_units))
            except Exception as e:
                raise StorageError(f"dedup rollback ok, but failed to read account: {e}") from e

        except (NotFound, Conflict, ValidationError):
            self._conn.rollback()
            raise
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e
        except Exception as e:
            self._conn.rollback()
            raise StorageError(f"unexpected error: {e}") from e
