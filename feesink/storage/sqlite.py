"""
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
