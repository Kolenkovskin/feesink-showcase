"""
FeeSink CANON config (MVP: HTTP Endpoint Watchdog)

Source of truth:
- Project root SPEC.md (CANON v1)

This module freezes:
- Pricing units rules
- HTTP check parameters

No runtime side-effects. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Final, Literal


# ----------------------------
# Versions / Labels (CANON)
# ----------------------------

FEESINK_PRODUCT: Final[str] = "FeeSink"
FEESINK_MVP: Final[str] = "HTTP Endpoint Watchdog"
CANON_VERSION: Final[str] = "CANON v1"
SPEC_SOURCE: Final[str] = "SPEC.md (root)"


def canon_label() -> str:
    """Human-readable label for logs/diagnostics."""
    return f"{FEESINK_PRODUCT} {FEESINK_MVP} | {CANON_VERSION}"


# ----------------------------
# Pricing (CANON v1)
# ----------------------------

# 1 check = 1 unit
UNITS_PER_CHECK: Final[int] = 1

# 1 USDT = 100 units
USDT_TO_UNITS_RATE: Final[int] = 100

# Minimum top-up: 50 USDT
MIN_TOPUP_USDT: Final[Decimal] = Decimal("50")


def credited_units(amount_usdt: Decimal) -> int:
    """
    Convert a USDT top-up amount to credited units.

    Rules:
    - amount_usdt must be >= MIN_TOPUP_USDT
    - credited units are floored to integer units (no fractional units)
    """
    if not isinstance(amount_usdt, Decimal):
        raise TypeError("amount_usdt must be Decimal")
    if amount_usdt < MIN_TOPUP_USDT:
        raise ValueError(f"Top-up amount must be >= {MIN_TOPUP_USDT} USDT")
    units = (amount_usdt * Decimal(USDT_TO_UNITS_RATE)).to_integral_value(rounding=ROUND_FLOOR)
    return int(units)


# ----------------------------
# HTTP check parameters (CANON v1)
# ----------------------------

HttpMethod = Literal["GET"]

HTTP_METHOD: Final[HttpMethod] = "GET"

# One unified timeout: connection + response
HTTP_TIMEOUT_SECONDS: Final[float] = 5.0

# Redirects allowed up to 3
HTTP_MAX_REDIRECTS: Final[int] = 3

# Fixed user-agent (not user-configurable)
HTTP_USER_AGENT: Final[str] = "FeeSink-Endpoint-Watchdog/1.0"

# TLS validation enabled
HTTP_TLS_VERIFY: Final[bool] = True

# No body, no custom headers (CANON); only the fixed UA is permitted.
HTTP_ALLOW_CUSTOM_HEADERS: Final[bool] = False
HTTP_ALLOW_REQUEST_BODY: Final[bool] = False


@dataclass(frozen=True, slots=True)
class HttpCheckPolicy:
    """
    Structured view of the CANON HTTP policy for dependency injection / adapters.
    """
    method: HttpMethod = HTTP_METHOD
    timeout_seconds: float = HTTP_TIMEOUT_SECONDS
    max_redirects: int = HTTP_MAX_REDIRECTS
    user_agent: str = HTTP_USER_AGENT
    tls_verify: bool = HTTP_TLS_VERIFY

    allow_custom_headers: bool = HTTP_ALLOW_CUSTOM_HEADERS
    allow_request_body: bool = HTTP_ALLOW_REQUEST_BODY

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.max_redirects < 0:
            raise ValueError("max_redirects must be >= 0")
        if not self.user_agent or not self.user_agent.strip():
            raise ValueError("user_agent must be non-empty")


@dataclass(frozen=True, slots=True)
class PricingPolicy:
    """
    Structured view of the CANON pricing policy.
    """
    units_per_check: int = UNITS_PER_CHECK
    usdt_to_units_rate: int = USDT_TO_UNITS_RATE
    min_topup_usdt: Decimal = MIN_TOPUP_USDT

    def validate(self) -> None:
        if self.units_per_check <= 0:
            raise ValueError("units_per_check must be > 0")
        if self.usdt_to_units_rate <= 0:
            raise ValueError("usdt_to_units_rate must be > 0")
        if self.min_topup_usdt <= 0:
            raise ValueError("min_topup_usdt must be > 0")


def canon_policies() -> tuple[HttpCheckPolicy, PricingPolicy]:
    """
    Returns validated CANON policies.
    Useful for wiring in runtime without duplicating constants.
    """
    http = HttpCheckPolicy()
    pricing = PricingPolicy()
    http.validate()
    pricing.validate()
    return http, pricing
