"""
FeeSink real HTTP checker (Phase 2)

Implements feesink.runtime.worker.HttpChecker with real network I/O.

CANON constraints (from feesink.config.canon.HttpCheckPolicy):
- method: GET
- timeout_seconds: unified timeout (connect + response)
- max_redirects: <= 3
- user_agent: fixed, not user-configurable
- tls_verify: enabled by default
- no custom headers
- no request body

Returns feesink.runtime.worker.CheckOutcome:
- result: OK / FAIL / TIMEOUT
- latency_ms
- http_status (if available)
- error_class (if available)

Version:
- FEESINK-HTTP-REAL v2026.01.01-01
"""

from __future__ import annotations

import ssl
import time
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from feesink.config.canon import HttpCheckPolicy
from feesink.domain.models import CheckResult, ErrorClass
from feesink.runtime.worker import CheckOutcome, HttpChecker

ADAPTER_VERSION = "FEESINK-HTTP-REAL v2026.01.01-01"


@dataclass(frozen=True, slots=True)
class RealHttpCheckerConfig:
    """
    Adapter-level knobs (should generally remain stable; policy is the main control).

    read_body_bytes:
        How many bytes to read from response body (0 means "don't read").
        Keeping it 0 reduces bandwidth and avoids storing content.
    """
    read_body_bytes: int = 0


class RealHttpChecker(HttpChecker):
    """
    Real network implementation of HttpChecker.

    Redirect handling:
    - Follow up to policy.max_redirects.
    - If redirects exceed limit => FAIL + REDIRECT_LOOP.
    """

    def __init__(self, config: RealHttpCheckerConfig = RealHttpCheckerConfig()) -> None:
        self._cfg = config

    def check(self, url: str, policy: HttpCheckPolicy) -> CheckOutcome:
        policy.validate()
        start = time.perf_counter()

        # Enforce CANON: only GET
        if policy.method != "GET":
            latency_ms = int((time.perf_counter() - start) * 1000)
            return CheckOutcome(
                result=CheckResult.FAIL,
                latency_ms=latency_ms,
                http_status=None,
                error_class=ErrorClass.UNKNOWN,
            )

        try:
            outcome = self._check_with_redirects(url=url, policy=policy)
        except Exception:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return CheckOutcome(
                result=CheckResult.FAIL,
                latency_ms=latency_ms,
                http_status=None,
                error_class=ErrorClass.UNKNOWN,
            )

        return outcome

    def _check_with_redirects(self, url: str, policy: HttpCheckPolicy) -> CheckOutcome:
        current_url = url
        redirects = 0

        while True:
            if redirects > policy.max_redirects:
                latency_ms = self._latency_ms()
                return CheckOutcome(
                    result=CheckResult.FAIL,
                    latency_ms=latency_ms,
                    http_status=None,
                    error_class=ErrorClass.REDIRECT_LOOP,
                )

            res = self._single_request(current_url, policy)
            if res._redirect_location is None:
                return res.outcome

            # redirect
            redirects += 1
            current_url = self._resolve_redirect(current_url, res._redirect_location)

    @staticmethod
    def _resolve_redirect(base_url: str, location: str) -> str:
        # location can be absolute or relative
        return urljoin(base_url, location)

    def _single_request(self, url: str, policy: HttpCheckPolicy) -> "_SingleResult":
        start = time.perf_counter()

        headers = {
            "User-Agent": policy.user_agent,
            "Accept": "*/*",
        }

        req = Request(url=url, method="GET", headers=headers)

        ctx = None
        if urlparse(url).scheme.lower() == "https":
            if policy.tls_verify:
                ctx = ssl.create_default_context()
            else:
                ctx = ssl._create_unverified_context()  # noqa: SLF001 (intentional for explicit opt-out)

        try:
            with urlopen(req, timeout=policy.timeout_seconds, context=ctx) as resp:
                status = getattr(resp, "status", None)
                # Read minimal bytes (default 0) to avoid large I/O; still completes headers phase.
                if self._cfg.read_body_bytes > 0:
                    try:
                        resp.read(self._cfg.read_body_bytes)
                    except Exception:
                        # Ignore body read issues; outcome determined by status/exception path
                        pass

                latency_ms = int((time.perf_counter() - start) * 1000)

                # Handle redirects manually: urllib may auto-handle some, but we treat 3xx as redirect signal.
                if status is not None and 300 <= int(status) <= 399:
                    location = resp.headers.get("Location")
                    if not location:
                        return _SingleResult(
                            outcome=CheckOutcome(
                                result=CheckResult.FAIL,
                                latency_ms=latency_ms,
                                http_status=int(status),
                                error_class=ErrorClass.UNKNOWN,
                            ),
                            _redirect_location=None,
                        )
                    return _SingleResult(
                        outcome=CheckOutcome(
                            result=CheckResult.FAIL,
                            latency_ms=latency_ms,
                            http_status=int(status),
                            error_class=ErrorClass.REDIRECT_LOOP,
                        ),
                        _redirect_location=location,
                    )

                # OK / FAIL by status code
                if status is None:
                    return _SingleResult(
                        outcome=CheckOutcome(
                            result=CheckResult.FAIL,
                            latency_ms=latency_ms,
                            http_status=None,
                            error_class=ErrorClass.UNKNOWN,
                        ),
                        _redirect_location=None,
                    )

                code = int(status)
                if 200 <= code <= 299:
                    return _SingleResult(
                        outcome=CheckOutcome(
                            result=CheckResult.OK,
                            latency_ms=latency_ms,
                            http_status=code,
                            error_class=None,
                        ),
                        _redirect_location=None,
                    )

                return _SingleResult(
                    outcome=CheckOutcome(
                        result=CheckResult.FAIL,
                        latency_ms=latency_ms,
                        http_status=code,
                        error_class=ErrorClass.HTTP_NON_2XX,
                    ),
                    _redirect_location=None,
                )

        except HTTPError as e:
            # HTTPError is also a response with status
            latency_ms = int((time.perf_counter() - start) * 1000)
            status = int(getattr(e, "code", 0)) or None

            # Redirect responses can surface here too
            if status is not None and 300 <= status <= 399:
                location = None
                try:
                    location = e.headers.get("Location") if e.headers else None
                except Exception:
                    location = None
                if location:
                    return _SingleResult(
                        outcome=CheckOutcome(
                            result=CheckResult.FAIL,
                            latency_ms=latency_ms,
                            http_status=status,
                            error_class=ErrorClass.REDIRECT_LOOP,
                        ),
                        _redirect_location=location,
                    )

            return _SingleResult(
                outcome=CheckOutcome(
                    result=CheckResult.FAIL,
                    latency_ms=latency_ms,
                    http_status=status,
                    error_class=ErrorClass.HTTP_NON_2XX if status is not None else ErrorClass.UNKNOWN,
                ),
                _redirect_location=None,
            )

        except URLError as e:
            latency_ms = int((time.perf_counter() - start) * 1000)

            # Timeout cases
            if isinstance(getattr(e, "reason", None), socket.timeout):
                return _SingleResult(
                    outcome=CheckOutcome(
                        result=CheckResult.TIMEOUT,
                        latency_ms=latency_ms,
                        http_status=None,
                        error_class=ErrorClass.TIMEOUT,
                    ),
                    _redirect_location=None,
                )

            # DNS resolution
            reason = getattr(e, "reason", None)
            if isinstance(reason, socket.gaierror):
                return _SingleResult(
                    outcome=CheckOutcome(
                        result=CheckResult.FAIL,
                        latency_ms=latency_ms,
                        http_status=None,
                        error_class=ErrorClass.DNS,
                    ),
                    _redirect_location=None,
                )

            # TLS
            if isinstance(reason, ssl.SSLError):
                return _SingleResult(
                    outcome=CheckOutcome(
                        result=CheckResult.FAIL,
                        latency_ms=latency_ms,
                        http_status=None,
                        error_class=ErrorClass.TLS,
                    ),
                    _redirect_location=None,
                )

            # Connection errors
            if isinstance(reason, (ConnectionRefusedError, ConnectionResetError, TimeoutError, OSError)):
                return _SingleResult(
                    outcome=CheckOutcome(
                        result=CheckResult.FAIL,
                        latency_ms=latency_ms,
                        http_status=None,
                        error_class=ErrorClass.CONNECT,
                    ),
                    _redirect_location=None,
                )

            return _SingleResult(
                outcome=CheckOutcome(
                    result=CheckResult.FAIL,
                    latency_ms=latency_ms,
                    http_status=None,
                    error_class=ErrorClass.UNKNOWN,
                ),
                _redirect_location=None,
            )

        except socket.timeout:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return _SingleResult(
                outcome=CheckOutcome(
                    result=CheckResult.TIMEOUT,
                    latency_ms=latency_ms,
                    http_status=None,
                    error_class=ErrorClass.TIMEOUT,
                ),
                _redirect_location=None,
            )

        except ssl.SSLError:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return _SingleResult(
                outcome=CheckOutcome(
                    result=CheckResult.FAIL,
                    latency_ms=latency_ms,
                    http_status=None,
                    error_class=ErrorClass.TLS,
                ),
                _redirect_location=None,
            )

        except Exception:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return _SingleResult(
                outcome=CheckOutcome(
                    result=CheckResult.FAIL,
                    latency_ms=latency_ms,
                    http_status=None,
                    error_class=ErrorClass.UNKNOWN,
                ),
                _redirect_location=None,
            )

    @staticmethod
    def _latency_ms() -> int:
        # Fallback helper when we can't measure per-request path; keep minimal.
        return 0


@dataclass(frozen=True, slots=True)
class _SingleResult:
    outcome: CheckOutcome
    _redirect_location: Optional[str] = None


def real_checker() -> RealHttpChecker:
    """
    Convenience factory mirroring adapters/http_checker_stub.preset_checker().
    """
    return RealHttpChecker()
