from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from enhanced_bot import PublicMarketDataClient


_INSTALLED = False
LOGGER = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "true" if default else "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _positive_float(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value)


PUBLIC_WS_OPEN_TIMEOUT_SECONDS = _positive_float(
    "DERIV_PUBLIC_WS_OPEN_TIMEOUT_SECONDS",
    25.0,
    5.0,
)
PUBLIC_WS_BACKOFF_BASE_SECONDS = _positive_float(
    "DERIV_PUBLIC_WS_BACKOFF_BASE_SECONDS",
    15.0,
    1.0,
)
PUBLIC_WS_BACKOFF_MAX_SECONDS = _positive_float(
    "DERIV_PUBLIC_WS_BACKOFF_MAX_SECONDS",
    300.0,
    30.0,
)
PUBLIC_WS_RATE_LIMIT_BACKOFF_SECONDS = _positive_float(
    "DERIV_PUBLIC_WS_RATE_LIMIT_BACKOFF_SECONDS",
    300.0,
    60.0,
)
PUBLIC_WS_JITTER_SECONDS = _positive_float(
    "DERIV_PUBLIC_WS_JITTER_SECONDS",
    5.0,
    0.0,
)
PUBLIC_WS_PLANNED_RESTART_SECONDS = _positive_float(
    "DERIV_PUBLIC_WS_PLANNED_RESTART_SECONDS",
    0.35,
    0.05,
)


def _preflight_stream_disabled() -> bool:
    deployment_id = os.getenv("DEPLOYMENT_ID", "").strip().lower()
    return _env_flag("DERIV_PUBLIC_WS_PREFLIGHT_DISABLED", False) or deployment_id.startswith(
        "preflight-worker"
    )


def _rate_limited(error: BaseException) -> bool:
    text = f"{type(error).__name__}: {error}".lower()
    return any(
        marker in text
        for marker in (
            "1015",
            "rate limit",
            "too many requests",
            "temporarily banned",
            "http 429",
            "status 429",
        )
    )


def _planned_restart(error: BaseException) -> bool:
    """Recognize an intentional market-subscription rebuild.

    A Custom Strategy market-set change closes the public socket with service
    restart code 1012. That is not a provider outage and must not inherit the
    normal 15+ second outage backoff seen in production logs.
    """

    text = f"{type(error).__name__}: {error}".lower()
    return "custom_market_set_changed" in text or (
        "1012" in text and "service restart" in text
    )


def _expected_connection_error(error: BaseException) -> bool:
    return isinstance(
        error,
        (
            TimeoutError,
            asyncio.TimeoutError,
            ConnectionClosed,
            OSError,
            ConnectionError,
        ),
    )


def _retry_delay(
    attempt: int,
    *,
    rate_limited: bool,
    planned_restart: bool = False,
) -> float:
    if planned_restart:
        return PUBLIC_WS_PLANNED_RESTART_SECONDS + random.uniform(0.0, 0.15)
    exponential = min(
        PUBLIC_WS_BACKOFF_MAX_SECONDS,
        PUBLIC_WS_BACKOFF_BASE_SECONDS * (2 ** min(max(0, attempt - 1), 8)),
    )
    if rate_limited:
        exponential = max(exponential, PUBLIC_WS_RATE_LIMIT_BACKOFF_SECONDS)
    jitter = random.uniform(0.0, PUBLIC_WS_JITTER_SECONDS)
    return min(PUBLIC_WS_BACKOFF_MAX_SECONDS, exponential + jitter)


async def _sleep_while_running(client: PublicMarketDataClient, seconds: float) -> None:
    remaining = max(0.0, float(seconds))
    while client.bot.is_running and remaining > 0:
        interval = min(0.25 if remaining < 1.0 else 1.0, remaining)
        await asyncio.sleep(interval)
        remaining -= interval


async def _resilient_connect_and_run(self: PublicMarketDataClient) -> None:
    """Maintain one public market stream without reconnect storms.

    Candidate workers deliberately avoid an external market-data connection. The
    candidate API and worker are validated locally while one standalone smoke
    connection verifies the official public endpoint. Production uses one stream
    with bounded exponential backoff and a long cooldown after provider throttling.
    Intentional subscription rebuilds use a separate near-immediate reconnect path.
    """

    if _preflight_stream_disabled():
        self.bot.logger.warning(
            "PUBLIC_STREAM_PREFLIGHT_DISABLED deployment_id=%s "
            "duplicate_external_connection=false provider_checked_by_smoke=true",
            os.getenv("DEPLOYMENT_ID", "preflight-worker"),
        )
        while self.bot.is_running:
            await asyncio.sleep(1.0)
        return

    attempt = 0
    url = self.bot.public_ws_url
    while self.bot.is_running:
        retry_delay = PUBLIC_WS_BACKOFF_BASE_SECONDS
        try:
            self.bot.logger.info(
                "Connecting to public market WebSocket: %s attempt=%s",
                url,
                attempt + 1,
            )
            async with websockets.connect(
                url,
                open_timeout=PUBLIC_WS_OPEN_TIMEOUT_SECONDS,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_queue=64,
            ) as websocket:
                self.ws = websocket
                self.is_connected = True
                attempt = 0
                self.bot._on_public_connection_established()
                self.bot.logger.info(
                    "Public WebSocket connection established reconnect_policy=bounded"
                )

                await self._fetch_precision()
                await self._fetch_tick_history()
                await self._subscribe_ticks()
                self.bot._on_market_subscriptions_ready()
                self.bot._mark_tick_received()

                async for message in websocket:
                    await self._on_message(message)

            if self.bot.is_running:
                raise ConnectionError("Public WebSocket stream ended unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            planned = _planned_restart(error)
            # Planned restarts are operational events, not outage attempts. Reset
            # the exponential counter so a prior network event cannot slow them.
            if planned:
                attempt = 0
            else:
                attempt += 1
            self._handle_disconnect(error)
            limited = _rate_limited(error)
            retry_delay = _retry_delay(
                max(1, attempt),
                rate_limited=limited,
                planned_restart=planned,
            )
            log = (
                self.bot.logger.info
                if planned
                else self.bot.logger.warning
                if _expected_connection_error(error) or limited
                else self.bot.logger.error
            )
            log(
                "PUBLIC_STREAM_BACKOFF error_type=%s error=%r attempt=%s "
                "planned_restart=%s rate_limited=%s retry_seconds=%.2f "
                "max_retry_seconds=%.1f global_execution_continues=true",
                type(error).__name__,
                error,
                attempt,
                str(planned).lower(),
                str(limited).lower(),
                retry_delay,
                PUBLIC_WS_BACKOFF_MAX_SECONDS,
            )

        if self.bot.is_running:
            await _sleep_while_running(self, retry_delay)


def install_public_websocket_resilience() -> None:
    """Install the final public-market connection authority."""

    global _INSTALLED
    if _INSTALLED:
        return
    PublicMarketDataClient.connect_and_run = _resilient_connect_and_run
    PublicMarketDataClient._public_websocket_resilience_installed = True
    _INSTALLED = True
    LOGGER.warning(
        "PUBLIC_WEBSOCKET_RESILIENCE_INSTALLED open_timeout_seconds=%.1f "
        "backoff_base_seconds=%.1f backoff_max_seconds=%.1f "
        "rate_limit_backoff_seconds=%.1f planned_restart_seconds=%.2f "
        "preflight_external_stream=false",
        PUBLIC_WS_OPEN_TIMEOUT_SECONDS,
        PUBLIC_WS_BACKOFF_BASE_SECONDS,
        PUBLIC_WS_BACKOFF_MAX_SECONDS,
        PUBLIC_WS_RATE_LIMIT_BACKOFF_SECONDS,
        PUBLIC_WS_PLANNED_RESTART_SECONDS,
    )
