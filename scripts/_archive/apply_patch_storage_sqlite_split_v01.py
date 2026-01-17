"""
FeeSink patch: split feesink/storage/sqlite.py into smaller modules (<=700 LOC facade)

- Keeps public imports stable: feesink.storage.sqlite.SQLiteStorage, SQLiteStorageConfig, STORAGE_VERSION
- Moves implementation into feesink/storage/_sqlite_*.py modules
- Removes transitional allowlist entry for sqlite.py in scripts/lint_module_size.py

Version:
- FEESINK-APPLY-PATCH-STORAGE-SQLITE-SPLIT v2026.01.16-01
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

UTC = timezone.utc
PATCH_VERSION = "FEESINK-APPLY-PATCH-STORAGE-SQLITE-SPLIT v2026.01.16-01"


def _ts_utc_compact() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha1_file(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def _append_log(root: Path, text: str) -> None:
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "apply_patch_storage_sqlite_split_v01.txt"
    with log_path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(8):
        if (cur / "feesink").is_dir() and (cur / "scripts").is_dir():
            return cur
        cur = cur.parent
    raise RuntimeError("Could not locate repo root (expected folders: feesink/ and scripts/)")


def _backup_file(src: Path, ts: str) -> Path:
    bak = src.with_name(src.name + f".bak.{ts}")
    bak.write_bytes(src.read_bytes())
    return bak


def _update_lint_allowlist(lint_py: Path) -> bool:
    """
    Make ALLOWLIST_OVER_700 empty (or remove sqlite entry) because after split, sqlite.py becomes small.
    We do minimal deterministic text surgery.
    """
    s = lint_py.read_text(encoding="utf-8")
    old = 'ALLOWLIST_OVER_700: dict[str, int] = {\n    "feesink/storage/sqlite.py": 1040,\n}\n'
    if old in s:
        s2 = s.replace(old, "ALLOWLIST_OVER_700: dict[str, int] = {}\n")
        lint_py.write_text(s2, encoding="utf-8", newline="\n")
        return True

    # fallback: remove only the sqlite.py line if present
    if '"feesink/storage/sqlite.py"' in s:
        lines = s.splitlines()
        out: List[str] = []
        for line in lines:
            if '"feesink/storage/sqlite.py"' in line:
                continue
            out.append(line)
        lint_py.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
        return True

    return False


def _py_compile_or_die(root: Path) -> None:
    import py_compile

    # compile only touched modules deterministically
    targets = [
        root / "feesink" / "storage" / "sqlite.py",
        root / "feesink" / "storage" / "_sqlite_utils.py",
        root / "feesink" / "storage" / "_sqlite_schema.py",
        root / "feesink" / "storage" / "_sqlite_accounts.py",
        root / "feesink" / "storage" / "_sqlite_endpoints.py",
        root / "feesink" / "storage" / "_sqlite_leases.py",
        root / "feesink" / "storage" / "_sqlite_topups.py",
        root / "feesink" / "storage" / "_sqlite_checks.py",
        root / "feesink" / "storage" / "_sqlite_stripe.py",
        root / "feesink" / "storage" / "_sqlite_housekeeping.py",
        root / "scripts" / "lint_module_size.py",
    ]
    for p in targets:
        if not p.exists():
            raise RuntimeError(f"Expected file missing: {p}")
        py_compile.compile(str(p), doraise=True)


def main() -> int:
    repo_root = _find_repo_root(Path(__file__).parent)
    ts = _ts_utc_compact()

    sqlite_py = repo_root / "feesink" / "storage" / "sqlite.py"
    lint_py = repo_root / "scripts" / "lint_module_size.py"

    banner = []
    banner.append("=" * 80)
    banner.append(PATCH_VERSION)
    banner.append(f"TS_UTC= {datetime.now(tz=UTC).isoformat()}")
    banner.append(f"ROOT= {str(repo_root)}")
    banner.append("=" * 80)
    print("\n".join(banner))

    log_block = "\n".join(banner) + "\n"

    if not sqlite_py.exists():
        raise RuntimeError(f"Target not found: {sqlite_py}")

    bak_sqlite = _backup_file(sqlite_py, ts)
    log_block += f"BACKUP= {bak_sqlite}\n"
    print(f"BACKUP= {bak_sqlite}")

    # --- New module contents (pure refactor, no behavior change) ---

    sqlite_utils = r'''"""
FeeSink SQLite helpers (split from feesink/storage/sqlite.py)

Version:
- FEESINK-SQLITE-UTILS v2026.01.16-01
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from feesink.domain.models import AccountStatus, PausedReason, ensure_utc
from feesink.storage.interfaces import StorageError

UTC = timezone.utc

STORAGE_VERSION = "FEESINK-SQLITE-STORAGE v2026.01.05-ACCOUNT-MAPPING-FIX-01"


def dt_to_str_utc(dt: datetime) -> str:
    dt = ensure_utc(dt)
    return dt.isoformat()


def str_to_dt_utc(s: str) -> datetime:
    if not s or not str(s).strip():
        raise ValueError("datetime string must be non-empty")
    dt = datetime.fromisoformat(str(s))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_int_bool(b: bool) -> int:
    return 1 if bool(b) else 0


def parse_paused_reason(value: object, endpoint_id: str) -> Optional[PausedReason]:
    if value is None:
        return None
    s = str(value)
    if not s:
        return None
    try:
        return PausedReason(s)
    except Exception as e:
        raise StorageError(f"invalid paused_reason in DB for endpoint {endpoint_id}: {s!r}") from e


def parse_account_status(value: object, account_id: str) -> AccountStatus:
    s = "active" if value is None else str(value)
    try:
        return AccountStatus(s)
    except Exception as e:
        raise StorageError(f"invalid account.status in DB for account {account_id}: {s!r}") from e
'''

    sqlite_schema = r'''"""
FeeSink SQLite schema/connection layer.

Split from feesink/storage/sqlite.py without behavior changes.

Version:
- FEESINK-SQLITE-SCHEMA v2026.01.16-01
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

from feesink.storage.interfaces import StorageError


@dataclass(frozen=True, slots=True)
class SQLiteStorageConfig:
    db_path: str
    schema_sql_path: Optional[str] = None
    enable_wal: bool = True
    ensure_parent_dir: bool = True


class SQLiteSchema:
    def __init__(self, config: SQLiteStorageConfig):
        if not isinstance(config.db_path, str) or not config.db_path.strip():
            raise ValueError("db_path must be non-empty")

        if config.ensure_parent_dir:
            parent = os.path.dirname(os.path.abspath(config.db_path))
            if parent and not os.path.isdir(parent):
                raise ValueError(f"DB parent directory does not exist: {parent}")

        self._config = config
        self._conn = sqlite3.connect(
            config.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

        self._apply_pragmas()
        if config.schema_sql_path:
            self._ensure_schema(config.schema_sql_path)

        self._layout = self._detect_layout()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    @property
    def layout(self) -> dict[str, bool]:
        return self._layout

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ----------------------------
    # Connection / schema helpers
    # ----------------------------

    def _apply_pragmas(self) -> None:
        cur = self._conn.cursor()
        try:
            cur.execute("PRAGMA foreign_keys = ON;")
            if self._config.enable_wal:
                cur.execute("PRAGMA journal_mode = WAL;")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute("PRAGMA busy_timeout = 5000;")
        finally:
            cur.close()

    def _ensure_schema(self, schema_path: str) -> None:
        if not os.path.isfile(schema_path):
            raise ValueError(f"schema_sql_path not found: {schema_path}")
        with open(schema_path, "r", encoding="utf-8") as f:
            sql = f.read()
        self._conn.executescript(sql)
        self._conn.commit()

    def _list_tables(self) -> set[str]:
        cur = self._conn.cursor()
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            return {str(r[0]) for r in cur.fetchall()}
        finally:
            cur.close()

    def _table_columns(self, table: str) -> set[str]:
        cur = self._conn.cursor()
        try:
            cur.execute(f"PRAGMA table_info({table})")
            rows = cur.fetchall()
            return {str(r[1]) for r in rows}
        finally:
            cur.close()

    def _detect_layout(self) -> dict[str, bool]:
        tables = self._list_tables()
        layout: dict[str, bool] = {}

        if "endpoint_leases" in tables:
            cols = self._table_columns("endpoint_leases")
            layout["leases_has_created_updated"] = ("created_at_utc" in cols and "updated_at_utc" in cols)

        if "topups" in tables:
            cols = self._table_columns("topups")
            layout["topups_has_ts_utc"] = "ts_utc" in cols

        return layout
'''

    sqlite_accounts = r'''"""
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
'''

    sqlite_endpoints = r'''"""
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
'''

    sqlite_leases = r'''"""
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
'''

    sqlite_topups = r'''"""
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
'''

    sqlite_checks = r'''"""
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
'''

    sqlite_stripe = r'''"""
FeeSink SQLite: Stripe helper methods used by API.

Split from feesink/storage/sqlite.py without behavior changes.

Version:
- FEESINK-SQLITE-STRIPE v2026.01.16-01
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

        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO stripe_links(
                    stripe_session_id, stripe_customer_id, account_id, created_at_utc
                ) VALUES(?,?,?,?)
                ON CONFLICT(stripe_session_id) DO UPDATE SET
                    stripe_customer_id = excluded.stripe_customer_id,
                    account_id = excluded.account_id
                """,
                (
                    str(stripe_session_id),
                    str(stripe_customer_id) if stripe_customer_id else None,
                    str(account_id),
                    created_s,
                ),
            )
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
'''

    sqlite_housekeeping = r'''"""
FeeSink SQLite: housekeeping.

Split from feesink/storage/sqlite.py without behavior changes.

Version:
- FEESINK-SQLITE-HOUSEKEEPING v2026.01.16-01
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from feesink.domain.models import ensure_utc
from feesink.storage.interfaces import StorageError
from feesink.storage._sqlite_utils import dt_to_str_utc


class SQLiteHousekeepingMixin:
    _conn: sqlite3.Connection

    def trim_check_events(self, older_than_utc: datetime) -> int:
        older_than_utc = ensure_utc(older_than_utc)
        older_s = dt_to_str_utc(older_than_utc)
        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM check_events WHERE created_at_utc < ?", (older_s,))
            removed = int(cur.rowcount or 0)
            cur.close()
            self._conn.commit()
            return removed
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(str(e)) from e
'''

    # New facade sqlite.py (keeps import path stable)
    sqlite_facade = r'''"""
FeeSink SQLite storage (facade, split into modules)

Implements Storage contract from feesink.storage.interfaces.

CANON invariants:
- prepaid balance only (no negative balances)
- 1 check = 1 unit
- idempotent charging via dedup_key
- idempotent topup via tx_hash (unique)
- leasing: 1 endpoint = 1 worker
- retries must not double-charge

IMPORTANT CONTRACT NOTE:
- Domain Account does NOT carry created_at_utc/updated_at_utc.
  Storage keeps those columns in DB, but must NOT pass them into Account(...).

Version:
- FEESINK-SQLITE-STORAGE v2026.01.05-ACCOUNT-MAPPING-FIX-01
"""

from __future__ import annotations

import sqlite3

from feesink.storage.interfaces import Storage

from feesink.storage._sqlite_utils import STORAGE_VERSION
from feesink.storage._sqlite_schema import SQLiteSchema, SQLiteStorageConfig
from feesink.storage._sqlite_accounts import SQLiteAccountsMixin
from feesink.storage._sqlite_endpoints import SQLiteEndpointsMixin
from feesink.storage._sqlite_leases import SQLiteLeasesMixin
from feesink.storage._sqlite_topups import SQLiteTopupsMixin
from feesink.storage._sqlite_checks import SQLiteChecksMixin
from feesink.storage._sqlite_stripe import SQLiteStripeMixin
from feesink.storage._sqlite_housekeeping import SQLiteHousekeepingMixin


class SQLiteStorage(
    SQLiteAccountsMixin,
    SQLiteEndpointsMixin,
    SQLiteLeasesMixin,
    SQLiteTopupsMixin,
    SQLiteChecksMixin,
    SQLiteStripeMixin,
    SQLiteHousekeepingMixin,
    Storage,
):
    """
    SQLite-backed implementation of Storage (facade).

    Important:
    - Uses BEGIN IMMEDIATE for atomic sections.
    - Applies schema.sql if provided.
    - Must remain compatible with Storage interface (no signature regressions).
    """

    def __init__(self, config: SQLiteStorageConfig):
        self._schema = SQLiteSchema(config)
        self._conn: sqlite3.Connection = self._schema.conn
        self._layout = self._schema.layout

    def close(self) -> None:
        self._schema.close()
'''

    # Write new modules
    touched: List[Path] = []
    def write_rel(rel: str, content: str) -> None:
        p = repo_root / rel
        _write_text(p, content)
        touched.append(p)

    write_rel("feesink/storage/_sqlite_utils.py", sqlite_utils)
    write_rel("feesink/storage/_sqlite_schema.py", sqlite_schema)
    write_rel("feesink/storage/_sqlite_accounts.py", sqlite_accounts)
    write_rel("feesink/storage/_sqlite_endpoints.py", sqlite_endpoints)
    write_rel("feesink/storage/_sqlite_leases.py", sqlite_leases)
    write_rel("feesink/storage/_sqlite_topups.py", sqlite_topups)
    write_rel("feesink/storage/_sqlite_checks.py", sqlite_checks)
    write_rel("feesink/storage/_sqlite_stripe.py", sqlite_stripe)
    write_rel("feesink/storage/_sqlite_housekeeping.py", sqlite_housekeeping)

    # Overwrite facade
    _write_text(sqlite_py, sqlite_facade)
    touched.append(sqlite_py)

    # Update lint allowlist
    bak_lint = _backup_file(lint_py, ts)
    print(f"BACKUP= {bak_lint}")
    log_block += f"BACKUP= {bak_lint}\n"
    changed_allowlist = _update_lint_allowlist(lint_py)
    if not changed_allowlist:
        print("WARN: lint allowlist not changed (pattern not found).")
        log_block += "WARN: lint allowlist not changed (pattern not found).\n"
    touched.append(lint_py)

    # Compile check
    try:
        _py_compile_or_die(repo_root)
        print("PY_COMPILE= OK")
        log_block += "PY_COMPILE= OK\n"
    except Exception as e:
        print("PY_COMPILE= FAIL")
        log_block += f"PY_COMPILE= FAIL: {e}\n"
        _append_log(repo_root, log_block)
        raise

    # Print sha1 touched
    print("TOUCHED_FILES:")
    log_block += "TOUCHED_FILES:\n"
    for p in touched:
        rel = p.relative_to(repo_root).as_posix()
        sha1 = _sha1_file(p)
        print(f"  - {rel} sha1={sha1}")
        log_block += f"  - {rel} sha1={sha1}\n"

    print("DONE")
    log_block += "DONE\n"
    _append_log(repo_root, log_block)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
