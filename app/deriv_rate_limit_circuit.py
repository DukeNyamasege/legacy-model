from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import app.deriv_request_broker as broker


_INSTALLED = False
LOGGER = logging.getLogger(__name__)


def _positive_float(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value)


RATE_LIMIT_BASE_SECONDS = _positive_float(
    "DERIV_RATE_LIMIT_CIRCUIT_SECONDS",
    120.0,
    30.0,
)
RATE_LIMIT_MAX_SECONDS = _positive_float(
    "DERIV_RATE_LIMIT_CIRCUIT_MAX_SECONDS",
    600.0,
    RATE_LIMIT_BASE_SECONDS,
)


def _error_code(response: dict[str, Any]) -> str:
    error = response.get("error") if isinstance(response, dict) else None
    if not isinstance(error, dict):
        return ""
    return str(error.get("code") or "").strip().upper()


def _cooldown_payload(remaining: float) -> dict[str, Any]:
    return {
        "error": {
            "code": "RATE_LIMITED",
            "message": (
                "Deriv API rate-limit circuit is open; no network request was sent"
            ),
            "retry_after_seconds": max(1, int(remaining)),
        }
    }


def _state(request_broker: Any) -> tuple[float, int]:
    return (
        float(getattr(request_broker, "_rate_limit_until", 0.0) or 0.0),
        int(getattr(request_broker, "_rate_limit_streak", 0) or 0),
    )


def _open_circuit(request_broker: Any) -> float:
    _until, streak = _state(request_broker)
    streak += 1
    delay = min(
        RATE_LIMIT_MAX_SECONDS,
        RATE_LIMIT_BASE_SECONDS * (2 ** min(streak - 1, 4)),
    )
    request_broker._rate_limit_streak = streak
    request_broker._rate_limit_until = max(
        float(getattr(request_broker, "_rate_limit_until", 0.0) or 0.0),
        time.monotonic() + delay,
    )
    LOGGER.error(
        "DERIV_RATE_LIMIT_CIRCUIT_OPEN streak=%s cooldown_seconds=%.1f "
        "new_network_requests_blocked=true financial_execution_transport=PRIVATE_WEBSOCKET_ONLY",
        streak,
        delay,
    )
    return delay


def install_deriv_rate_limit_circuit() -> None:
    """Stop REST account/OTP request storms after Cloudflare 1015 or HTTP 429."""

    global _INSTALLED
    if _INSTALLED:
        return

    request_broker_class = broker._DerivRequestBroker
    original_request = request_broker_class.request
    original_retryable = request_broker_class._retryable_response

    def retryable_without_rate_limit(self: Any, response: dict[str, Any]) -> bool:
        if _error_code(response) in {"RATE_LIMITED", "HTTP_429"}:
            return False
        return bool(original_retryable(self, response))

    async def request_with_rate_limit_circuit(
        self: Any,
        method: str,
        path: str,
        app_id: str,
        base_url: str,
        credential: str = "",
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        until, _streak = _state(self)
        now = time.monotonic()
        if until > now:
            remaining = until - now
            LOGGER.warning(
                "DERIV_RATE_LIMIT_CIRCUIT_BLOCKED method=%s path=%s "
                "retry_after_seconds=%.1f network_request_sent=false",
                str(method).upper(),
                path,
                remaining,
            )
            return _cooldown_payload(remaining)

        response = await original_request(
            self,
            method,
            path,
            app_id,
            base_url,
            credential,
            json_data,
        )
        if _error_code(response) in {"RATE_LIMITED", "HTTP_429"}:
            _open_circuit(self)
            return response

        # A successful setup request proves the provider accepted this VPS again.
        if "error" not in response and getattr(self, "_rate_limit_streak", 0):
            self._rate_limit_streak = 0
            self._rate_limit_until = 0.0
            LOGGER.warning(
                "DERIV_RATE_LIMIT_CIRCUIT_CLOSED provider_requests_resumed=true"
            )
        return response

    request_broker_class._retryable_response = retryable_without_rate_limit
    request_broker_class.request = request_with_rate_limit_circuit

    # Lower the first production wave. This affects only REST account discovery
    # and OTP setup; each financial buy remains on its own private WebSocket.
    broker._BROKER.config.request_concurrency = min(
        int(broker._BROKER.config.request_concurrency),
        int(os.getenv("DERIV_HTTP_CONCURRENCY", "4")),
    )
    broker._BROKER.config.limit_per_host = min(
        int(broker._BROKER.config.limit_per_host),
        int(os.getenv("DERIV_HTTP_LIMIT_PER_HOST", "4")),
    )

    _INSTALLED = True
    LOGGER.warning(
        "DERIV_RATE_LIMIT_CIRCUIT_INSTALLED base_seconds=%.1f max_seconds=%.1f "
        "rest_concurrency=%s per_host=%s bulk_purchase=false copy_trading=false",
        RATE_LIMIT_BASE_SECONDS,
        RATE_LIMIT_MAX_SECONDS,
        broker._BROKER.config.request_concurrency,
        broker._BROKER.config.limit_per_host,
    )
