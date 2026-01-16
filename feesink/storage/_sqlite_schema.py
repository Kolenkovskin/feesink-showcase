"""
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
