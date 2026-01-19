# file: feesink/api/app.py
# FeeSink API app (routing + auth)
# FEESINK-API-APP v2026.01.19-02

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Optional, Tuple
from wsgiref.util import setup_testing_defaults

from feesink.api._http import UTC, error, get_bearer_token, get_query_param, json_response, utc_iso
from feesink.api.deps import TokenStore, make_storage
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


class FeeSinkApiApp:
    """
    Auth canon (self-issued):
      - Bearer token == account_id
      - No preregistration, no custody, prepaid-only

    Legacy support:
      - If TokenStore has a mapping, use it.
      - Otherwise fall back to self-issued.
    """

    def __init__(self, api_version: str):
        self.api_version = api_version
        self.storage = make_storage()
        self.tokens = TokenStore()
        self.topup_mode = (os.getenv("FEESINK_TOPUP_MODE") or "dev").strip().lower()

        # DEV convenience only (TokenStore is in-memory; resets on restart)
        dev_token = os.getenv("FEESINK_DEV_TOKEN", "").strip()
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
        tok = get_bearer_token(environ) or get_query_param(environ, "token")
        if tok is None:
            return None
        tok = str(tok).strip()
        return tok or None

    def _self_issued_account_id(self, token: str) -> str:
        # Canon: token == account_id
        return token

    def auth_account_id(self, environ) -> Tuple[Optional[str], Optional[Tuple[int, list, bytes]]]:
        token = self.get_token(environ)
        if not token:
            return None, error(401, "unauthorized", "Missing Bearer token")

        # 1) legacy mapping (dev-only, in-memory)
        account_id = self.tokens.resolve(token)
        if account_id:
            return account_id, None

        # 2) self-issued fallback
        return self._self_issued_account_id(token), None

    def auth_token_and_account(self, environ) -> Tuple[Optional[str], Optional[str], Optional[Tuple[int, list, bytes]]]:
        token = self.get_token(environ)
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

            elif path == "/v1/stripe/checkout_sessions" and method == "POST":
                status, headers, body = handle_post_stripe_checkout_sessions(self, environ)

            else:
                m = re.match(r"^/v1/endpoints/([^/]+)$", path)
                if m and method == "PATCH":
                    status, headers, body = handle_patch_endpoint(self, environ, m.group(1))
                elif m and method == "DELETE":
                    status, headers, body = handle_delete_endpoint(self, environ, m.group(1))
                elif path == "/v1/alerts/test" and method == "POST":
                    status, headers, body = handle_post_alerts_test(self, environ)
                elif path == "/v1/webhooks/stripe" and method == "POST":
                    status, headers, body = handle_post_webhooks_stripe(self, environ)
                elif path == "/healthz" and method == "GET":
                    payload = {"ok": True, "ts": utc_iso(datetime.now(tz=UTC)), "version": self.api_version}
                    status, headers, body = json_response(200, payload)
                else:
                    status, headers, body = error(404, "not_found", "Route not found")

        except Exception as ex:
            status, headers, body = error(500, "internal_error", "Unhandled error", {"exception": type(ex).__name__})

        duration_ms = int((time.monotonic() - t0) * 1000)
        print(
            json.dumps(
                {
                    "type": "api_request",
                    "ts": utc_iso(datetime.now(tz=UTC)),
                    "api": self.api_version,
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": duration_ms,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

        start_response(f"{status} OK", headers)
        return [body]
