# FeeSink API deps (storage wiring + token store)
# FEESINK-API-DEPS v2026.01.16-01

from __future__ import annotations

import os
import secrets
from typing import Dict, Optional


class TokenStore:
    def __init__(self) -> None:
        self._token_to_account: Dict[str, str] = {}

    def issue_token(self, account_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._token_to_account[token] = account_id
        return token

    def link_token(self, token: str, account_id: str) -> None:
        self._token_to_account[token] = account_id

    def resolve(self, token: str) -> Optional[str]:
        return self._token_to_account.get(token)


def make_storage():
    storage_kind = (os.getenv("FEESINK_STORAGE") or "memory").strip().lower()

    if storage_kind == "sqlite":
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        db_path = os.path.join(repo_root, os.getenv("FEESINK_SQLITE_DB", "feesink.db"))
        schema_path = os.path.join(repo_root, os.getenv("FEESINK_SCHEMA_SQL", "schema.sql"))
        from feesink.storage.sqlite import SQLiteStorage, SQLiteStorageConfig  # type: ignore

        return SQLiteStorage(SQLiteStorageConfig(db_path=db_path, schema_sql_path=schema_path))

    from feesink.storage.memory import InMemoryStorage  # type: ignore
    return InMemoryStorage()
