"""
FeeSink SQLite schema/connection layer.

Split from feesink/storage/sqlite.py without behavior changes.

Version:
- FEESINK-SQLITE-SCHEMA v2026.01.25-01 (safe accounts.last_provider_event_at_utc migration + layout flag)
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
    """
    IMPORTANT:
    SQLiteStorage facade expects:
      - self.conn: sqlite3.Connection
      - self.layout: dict
      - close(): None
    """

    def __init__(self, config: SQLiteStorageConfig):
        if not isinstance(config.db_path, str) or not config.db_path.strip():
            raise ValueError("config.db_path must be a non-empty string")
        self._config = config

        # Facade contract (used by feesink/storage/sqlite.py)
        self.conn: sqlite3.Connection = self._connect()
        self.layout: dict = self.detect_layout(self.conn)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            # Best-effort: storage close must not raise in shutdown paths.
            pass

    # ---------------------------------------------------------------------
    # Internal
    # ---------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._config.ensure_parent_dir:
            parent = os.path.dirname(os.path.abspath(self._config.db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)

        conn = sqlite3.connect(self._config.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row

        # Canon: foreign keys ON per connection.
        conn.execute("PRAGMA foreign_keys = ON;")

        if self._config.enable_wal:
            conn.execute("PRAGMA journal_mode = WAL;")

        self._apply_schema(conn)

        # Safe migrations (must be safe on existing LIVE DB and on fresh DB)
        self._migrate_provider_events_audit(conn)
        self._migrate_accounts_last_provider_event(conn)

        return conn

    def _apply_schema(self, conn: sqlite3.Connection) -> None:
        schema_sql_path = self._config.schema_sql_path
        if not schema_sql_path:
            return

        try:
            with open(schema_sql_path, "r", encoding="utf-8") as f:
                sql = f.read()
            if not sql.strip():
                return
            conn.executescript(sql)
        except Exception as e:
            raise StorageError(f"failed to apply schema from {schema_sql_path!r}: {e}") from e

    def _migrate_provider_events_audit(self, conn: sqlite3.Connection) -> None:
        """
        P1 hardening migration (safe on existing DB, safe on fresh DB):

        - add provider_events.raw_body_sha256 TEXT (SHA256 hex of raw bytes)
        - add provider_events.signature_verified_at_utc TEXT (UTC ISO8601)
        - add index on provider_events(received_at_utc)

        Must NOT fail if provider_events table is not present yet (fresh DB before schema.sql).
        """
        try:
            tables = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            }
        except Exception as e:
            raise StorageError(f"failed to inspect sqlite_master: {e}") from e

        if "provider_events" not in tables:
            return

        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(provider_events);").fetchall()}

            if "raw_body_sha256" not in cols:
                conn.execute("ALTER TABLE provider_events ADD COLUMN raw_body_sha256 TEXT NULL;")
            if "signature_verified_at_utc" not in cols:
                conn.execute("ALTER TABLE provider_events ADD COLUMN signature_verified_at_utc TEXT NULL;")

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_provider_events_received_at_utc "
                "ON provider_events(received_at_utc);"
            )
        except Exception as e:
            raise StorageError(f"failed to migrate provider_events audit fields: {e}") from e

    def _migrate_accounts_last_provider_event(self, conn: sqlite3.Connection) -> None:
        """
        P2 ops-only migration (safe on existing LIVE DB, safe on fresh DB):

        - add accounts.last_provider_event_at_utc TEXT NULL

        Must NOT fail if accounts table is not present yet.
        """
        try:
            tables = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            }
        except Exception as e:
            raise StorageError(f"failed to inspect sqlite_master: {e}") from e

        if "accounts" not in tables:
            return

        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(accounts);").fetchall()}
            if "last_provider_event_at_utc" not in cols:
                conn.execute("ALTER TABLE accounts ADD COLUMN last_provider_event_at_utc TEXT NULL;")
        except Exception as e:
            raise StorageError(f"failed to migrate accounts.last_provider_event_at_utc: {e}") from e

    def detect_layout(self, conn: sqlite3.Connection) -> dict:
        """
        Small helper for diagnostics; does not affect runtime behavior.
        """
        layout: dict = {}
        try:
            tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        except Exception:
            return layout

        layout["has_provider_events"] = "provider_events" in tables
        layout["has_accounts"] = "accounts" in tables

        if layout["has_provider_events"]:
            try:
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(provider_events);").fetchall()}
                layout["provider_events_has_audit"] = (
                    "raw_body_sha256" in cols and "signature_verified_at_utc" in cols
                )
            except Exception:
                layout["provider_events_has_audit"] = False

        if layout["has_accounts"]:
            try:
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(accounts);").fetchall()}
                layout["accounts_has_last_provider_event"] = "last_provider_event_at_utc" in cols
            except Exception:
                layout["accounts_has_last_provider_event"] = False

        return layout
