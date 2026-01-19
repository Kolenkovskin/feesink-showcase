# file: feesink/api/app.py
# FEESINK-API-APP v2026.01.19-02

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple
from wsgiref.util import setup_testing_defaults

from feesink.api._http import error, get_bearer_token, get_query_param
from feesink.api.handlers_core import (
    handle_delete_endpoint,
    handle_get_accounts_balance,
    handle_get_me,
    handle_get_ui_success,
    handle_patch_endpoint,
    handle_post_alerts_test,
    handle_post_endpoints,
    handle_post_topups_dev,
)
from feesink.api.handlers_stripe import (
    handle_post_stripe_checkout_sessions,
    handle_post_webhooks_stripe,
)
from feesink.config.canon import load_canon
from feesink.storage.sqlite import SQLiteStorage


@dataclass(frozen=True)
class AppConfig:
    sqlite_db_path: str
    topup_mode: str


class App:
    def __init__(self) -> None:
        load_canon()

        sqlite_db_path = os.getenv("FEESINK_SQLITE_DB", "/var/data/feesink.db").strip()
        topup_mode = os.getenv("FEESINK_TOPUP_MODE", "dev").strip()

        self.config = AppConfig(sqlite_db_path=sqlite_db_path, topup_mode=topup_mode)
        self.storage = SQLiteStorage(sqlite_db_path=sqlite_db_path)

        # Token store (legacy + optional)
        # NOTE: In self-issued canon token == account_id, resolve() may return None.
        self.tokens = self.storage  # storage implements token methods
        self.topup_mode = topup_mode

        self._init_dev_token()

    def _init_dev_token(self) -> None:
        # Optional local-only convenience. In prod self-issued flow is primary.
        if (os.getenv("FEESINK_ENV") or "").lower().strip() == "prod":
            return

        dev_token = os.getenv("FEESINK_DEV_TOKEN", "").strip() or None
        dev_account = os.getenv("FEESINK_DEV_ACCOUNT", "demo-user").strip()

        self.storage.ensure_account(dev_account)
        if dev_token:
            self.tokens.link_token(dev_token, dev_account)
            print(f"[DEV] Linked FEESINK_DEV_TOKEN to account_id={dev_account}")
            print(f"[DEV] Success page: http://127.0.0.1:8789/ui/success?token={dev_token}")
        else:
            token = self.tokens.issue_token(dev_account)
            print(f"[DEV] Issued token for account_id={dev_account}: {token}")
            print(f"[DEV] Success page: http://127.0.0.1:8789/ui/success?token={token}")

    # ---- token helpers ----
    def get_token(self, environ) -> Optional[str]:
        return get_bearer_token(environ) or get_query_param(environ, "token")

    def _self_issued_account_id(self, token: str) -> str:
        # Canon: token == account_id (no preregistration)
        return token

    def auth_account_id(self, environ) -> Tuple[Optional[str], Optional[Tuple[int, list, bytes]]]:
        token = self.get_token(environ)
        if not token:
            return None, error(401, "unauthorized", "Missing Bearer token")
        token = token.strip()
        if not token:
            return None, error(401, "unauthorized", "Missing Bearer token")

        # 1) legacy: resolve via tokens table if present
        account_id = self.tokens.resolve(token)
        if account_id:
            return account_id, None

        # 2) self-issued: accept token as account_id
        return self._self_issued_account_id(token), None

    def auth_token_and_account(self, environ) -> Tuple[Optional[str], Optional[str], Optional[Tuple[int, list, bytes]]]:
        token = self.get_token(environ)
        if not token:
            return None, None, error(401, "unauthorized", "Missing Bearer token")
        token = token.strip()
        if not token:
            return None, None, error(401, "unauthorized", "Missing Bearer token")

        account_id = self.tokens.resolve(token)
        if account_id:
            return token, account_id, None

        # self-issued fallback
        return token, self._self_issued_account_id(token), None

    # ---- WSGI entry ----
    def __call__(self, environ, start_response):
        setup_testing_defaults(environ)
        t0 = time.monotonic()

        method = (environ.get("REQUEST_METHOD") or "GET").upper()
        path = environ.get("PATH_INFO") or "/"

        try:
            if path == "/ui/success" and method == "GET":
                status, headers, body = handle_get_ui_success(self, environ)

            elif path == "/v1/me" and method == "GET":
                status, headers, body = handle_get_me(self, environ)

            elif path == "/v1/accounts/balance" and method == "GET":
                status, headers, body = handle_get_accounts_balance(self, environ)

            elif path == "/v1/topups" and method == "POST":
                status, headers, body = handle_post_topups_dev(self, environ)

            elif path == "/v1/endpoints" and method == "POST":
                status, headers, body = handle_post_endpoints(self, environ)

            elif path.startswith("/v1/endpoints/") and method == "PATCH":
                endpoint_id = path.split("/")[-1]
                status, headers, body = handle_patch_endpoint(self, environ, endpoint_id)

            elif path.startswith("/v1/endpoints/") and method == "DELETE":
                endpoint_id = path.split("/")[-1]
                status, headers, body = handle_delete_endpoint(self, environ, endpoint_id)

            elif path == "/v1/alerts/test" and method == "POST":
                status, headers, body = handle_post_alerts_test(self, environ)

            elif path == "/v1/stripe/checkout_sessions" and method == "POST":
                status, headers, body = handle_post_stripe_checkout_sessions(self, environ)

            elif path == "/v1/webhooks/stripe" and method == "POST":
                status, headers, body = handle_post_webhooks_stripe(self, environ)

            else:
                status, headers, body = error(404, "not_found", "No such endpoint")

        except Exception as ex:
            status, headers, body = error(500, "internal_error", "Unhandled exception", {"exception": type(ex).__name__})

        # minimal access log
        dt_ms = int((time.monotonic() - t0) * 1000)
        try:
            ip = environ.get("REMOTE_ADDR") or "-"
            print(f"{ip} - - [{dt_ms}ms] \"{method} {path}\" {status} {len(body)}")
        except Exception:
            pass

        start_response(f"{status} OK", headers)
        return [body]


def build_app() -> App:
    return App()
