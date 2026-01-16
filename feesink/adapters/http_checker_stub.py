"""
HTTP checker stub for FeeSink (MVP: HTTP Endpoint Watchdog)

Purpose:
- Provide a deterministic HttpChecker implementation WITHOUT real network I/O.
- Useful to validate runtime orchestration, storage idempotency, and scheduling.

Source of truth:
- SPEC.md (CANON v1)
- feesink.config.canon (HTTP policy)
- feesink.runtime.worker (HttpChecker protocol + CheckOutcome)

No network calls. No randomness by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from feesink.config.canon import HttpCheckPolicy
from feesink.domain.models import CheckResult, ErrorClass
from feesink.runtime.worker import CheckOutcome, HttpChecker


@dataclass(frozen=True, slots=True)
class StubRule:
    """
    One rule for matching a URL prefix and returning a fixed outcome.
    """
    url_prefix: str
    outcome: CheckOutcome


class StubHttpChecker(HttpChecker):
    """
    Deterministic stub checker.

    Matching strategy:
    - First matching url_prefix wins.
    - If no rule matches, default_outcome is used.

    Notes:
    - This is NOT a real HTTP client.
    - It ignores redirects/timeouts in the sense of execution; those are encoded into outcomes.
    - It still accepts HttpCheckPolicy to mirror runtime API, but does not use it for network.
    """

    def __init__(
        self,
        rules: Optional[list[StubRule]] = None,
        default_outcome: Optional[CheckOutcome] = None,
    ) -> None:
        self._rules: list[StubRule] = rules or []
        self._default: CheckOutcome = default_outcome or CheckOutcome(
            result=CheckResult.OK,
            latency_ms=50,
            http_status=200,
            error_class=None,
        )

    def check(self, url: str, policy: HttpCheckPolicy) -> CheckOutcome:
        # Policy is accepted for signature compatibility and future validation,
        # but this stub does not perform I/O.
        _ = policy  # intentionally unused

        for r in self._rules:
            if url.startswith(r.url_prefix):
                return r.outcome
        return self._default


def default_stub_rules() -> Dict[str, CheckOutcome]:
    """
    Convenience preset outcomes by pseudo-scheme / prefix.
    Use these prefixes when adding endpoints to quickly simulate different results.

    Examples:
    - ok://service -> OK 200
    - fail://service -> FAIL 500
    - timeout://service -> TIMEOUT (no http_status)
    - tls://service -> FAIL TLS
    - redirectloop://service -> FAIL redirect_loop
    """
    return {
        "ok://": CheckOutcome(result=CheckResult.OK, latency_ms=40, http_status=200, error_class=None),
        "fail://": CheckOutcome(result=CheckResult.FAIL, latency_ms=60, http_status=500, error_class=ErrorClass.HTTP_NON_2XX),
        "timeout://": CheckOutcome(result=CheckResult.TIMEOUT, latency_ms=5000, http_status=None, error_class=ErrorClass.TIMEOUT),
        "tls://": CheckOutcome(result=CheckResult.FAIL, latency_ms=30, http_status=None, error_class=ErrorClass.TLS),
        "redirectloop://": CheckOutcome(result=CheckResult.FAIL, latency_ms=25, http_status=None, error_class=ErrorClass.REDIRECT_LOOP),
    }


def preset_checker() -> StubHttpChecker:
    """
    Ready-to-use stub checker with common presets.

    How it matches:
    - URL starting with "ok://", "fail://", etc. uses corresponding outcome.
    - Anything else falls back to OK 200.
    """
    presets = default_stub_rules()
    rules = [StubRule(url_prefix=k, outcome=v) for k, v in presets.items()]
    return StubHttpChecker(rules=rules)
