"""
FeeSink SQLite: checks (record_check_and_charge).

CANON invariants:
- prepaid balance only (no negative balances)
- 1 check = 1 unit
- charge strictly after the check event exists (same atomic tx is OK)
- idempotent charging via dedup_key (UNIQUE(dedup_key))
- storage must NOT enforce dedup_key format (any non-empty string is valid)

Critical semantic fix (v2026.01.30-01):
- Dedup MUST be checked before balance gate.
  Rationale:
  - If a check was already recorded+charged earlier, retries with the same dedup_key
    must return inserted=False regardless of current balance.
  - Otherwise, retries can incorrectly fail with "insufficient balance_units" after depletion.

Version:
- FEESINK-SQLITE-CHECKS v2026.01.30-01
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
            ts_utc = ensure_utc(event.ts)
            ts_s = dt_to_str_utc(ts_utc)

            # scheduled_at_utc: for MVP we accept it equals event.ts
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

            # 0) DEDUP BEFORE BALANCE:
            # If this dedup_key already exists, this is a retry and must NOT fail due to depleted balance.
            cur.execute(
                "SELECT check_id FROM check_events WHERE dedup_key = ? LIMIT 1",
                (str(dedup_key),),
            )
            dedup_row = cur.fetchone()
            if dedup_row is not None:
                # Best effort: return current balance (account must exist for a consistent response).
                cur.execute(
                    "SELECT account_id, balance_units FROM accounts WHERE account_id = ?",
                    (str(account_id),),
                )
                arow = cur.fetchone()
                if arow is None:
                    cur.close()
                    self._conn.rollback()
                    raise NotFound("account not found")

                balance_units = int(arow["balance_units"])
                cur.close()
                self._conn.rollback()
                return ChargeResult(inserted=False, event=event, new_balance_units=int(balance_units))

            # 1) Read account (must exist)
            cur.execute(
                "SELECT account_id, balance_units, status FROM accounts WHERE account_id = ?",
                (str(account_id),),
            )
            row = cur.fetchone()
            if row is None:
                cur.close()
                self._conn.rollback()
                raise NotFound("account not found")

            balance_units = int(row["balance_units"])
            status_str = str(row["status"])

            # 2) prepaid only: never go negative
            if balance_units < 1:
                if status_str != "depleted":
                    cur.execute(
                        "UPDATE accounts SET status = ?, updated_at_utc = ? WHERE account_id = ?",
                        ("depleted", now_s, str(account_id)),
                    )
                cur.close()
                self._conn.rollback()
                raise Conflict("insufficient balance_units")

            # 3) Insert check_event (idempotent by UNIQUE(dedup_key))
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

            # 4) Charge account (same tx)
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
            # Dedup hit -> must NOT charge again.
            # This can happen if two transactions race on the same dedup_key.
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
