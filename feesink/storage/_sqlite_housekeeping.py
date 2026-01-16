"""
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
