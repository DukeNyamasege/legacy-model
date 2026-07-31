from __future__ import annotations

import asyncio
import os
import random
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import websockets

from enhanced_bot import ClientSession, mask_account_id, sanitize_account_ids


_INSTALLED = False


def _positive_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value)


def _positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), value)


def _http_status(exc: BaseException) -> int | None:
    for owner in (exc, getattr(exc, "response", None)):
        if owner is None:
            continue
        for attribute in ("status_code", "status"):
            raw = getattr(owner, attribute, None)
            try:
                if raw is not None:
                    return int(raw)
            except (TypeError, ValueError):
                continue
    return None


def _is_rate_limit(exc: BaseException) -> bool:
    if _http_status(exc) == 429:
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in (
            "http 429",
            "status 429",
            "too many requests",
            "rate limit",
            "error 1015",
            "temporarily banned",
        )
    )


@dataclass(slots=True)
class _PrivateConnectionConfig:
    interval_seconds: float
    handshake_concurrency: int
    rate_limit_backoff_seconds: float
    maximum_backoff_seconds: float
    reconnect_jitter_seconds: float
    otp_failure_backoff_seconds: float

    @classmethod
    def load(cls) -> "_PrivateConnectionConfig":
        return cls(
            interval_seconds=_positive_float(
                "PRIVATE_WS_CONNECT_INTERVAL_SECONDS",
                0.75,
                minimum=0.10,
            ),
            handshake_concurrency=_positive_int(
                "PRIVATE_WS_HANDSHAKE_CONCURRENCY",
                2,
                minimum=1,
            ),
            rate_limit_backoff_seconds=_positive_float(
                "PRIVATE_WS_RATE_LIMIT_BACKOFF_SECONDS",
                60.0,
                minimum=10.0,
            ),
            maximum_backoff_seconds=_positive_float(
                "PRIVATE_WS_MAX_BACKOFF_SECONDS",
                300.0,
                minimum=30.0,
            ),
            reconnect_jitter_seconds=_positive_float(
                "PRIVATE_WS_RECONNECT_JITTER_SECONDS",
                3.0,
                minimum=0.0,
            ),
            otp_failure_backoff_seconds=_positive_float(
                "PRIVATE_WS_OTP_FAILURE_BACKOFF_SECONDS",
                15.0,
                minimum=2.0,
            ),
        )


class _PrivateConnectionGate:
    """Coordinate OTP/handshake starts across every account in one worker."""

    def __init__(self, config: _PrivateConnectionConfig) -> None:
        self.config = config
        self._schedule_lock = asyncio.Lock()
        self._handshake_slots = asyncio.Semaphore(config.handshake_concurrency)
        self._next_start_at = 0.0
        self._penalty_until = 0.0

    async def wait_for_start_slot(self) -> None:
        # Re-check after every sleep so a 429 penalty imposed by another account
        # also delays sessions that were already waiting for their turn.
        while True:
            async with self._schedule_lock:
                now = time.monotonic()
                target = max(self._next_start_at, self._penalty_until)
                if target <= now:
                    self._next_start_at = now + self.config.interval_seconds
                    return
                delay = target - now
            await asyncio.sleep(delay)

    async def penalize(self, seconds: float) -> None:
        async with self._schedule_lock:
            penalty_until = time.monotonic() + max(0.0, float(seconds))
            self._penalty_until = max(self._penalty_until, penalty_until)
            self._next_start_at = max(self._next_start_at, self._penalty_until)

    async def open_websocket(self, url: str):
        # Limit only concurrent handshakes. The semaphore is released as soon as
        # the connection opens; established account sessions remain concurrent.
        async with self._handshake_slots:
            await self.wait_for_start_slot()
            return await websockets.connect(
                url,
                open_timeout=20,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
            )


def _gate_for(session: ClientSession) -> _PrivateConnectionGate:
    bot = session.bot
    gate = getattr(bot, "_private_ws_connection_gate", None)
    if isinstance(gate, _PrivateConnectionGate):
        return gate
    gate = _PrivateConnectionGate(_PrivateConnectionConfig.load())
    bot._private_ws_connection_gate = gate
    bot.logger.info(
        "PRIVATE_WS_RATE_LIMITER_ACTIVE interval_seconds=%.2f "
        "handshake_concurrency=%s rate_limit_backoff_seconds=%.1f "
        "maximum_backoff_seconds=%.1f",
        gate.config.interval_seconds,
        gate.config.handshake_concurrency,
        gate.config.rate_limit_backoff_seconds,
        gate.config.maximum_backoff_seconds,
    )
    return gate


def _jitter(config: _PrivateConnectionConfig) -> float:
    return random.uniform(0.0, config.reconnect_jitter_seconds)


def _normal_backoff(
    session: ClientSession,
    config: _PrivateConnectionConfig,
    attempt: int,
) -> float:
    base = max(1.0, float(session.bot.reconnect_delay_seconds))
    return min(
        config.maximum_backoff_seconds,
        base * (1.5 ** min(max(0, attempt), 10)),
    ) + _jitter(config)


def _rate_backoff(config: _PrivateConnectionConfig, attempt: int) -> float:
    return min(
        config.maximum_backoff_seconds,
        config.rate_limit_backoff_seconds * (2 ** min(max(0, attempt - 1), 3)),
    ) + _jitter(config)


async def _rate_limited_connect_and_run(self: ClientSession) -> None:
    attempt = 0
    gate = _gate_for(self)
    config = gate.config

    while self.bot.is_running and (
        self.pending_contracts
        or any(token == self.token for token, _account_id in self.bot.valid_clients)
    ):
        retry_delay = 0.0
        websocket = None
        try:
            await gate.wait_for_start_slot()
            url = await self.get_otp_url()
            if not url:
                attempt += 1
                retry_delay = min(
                    config.maximum_backoff_seconds,
                    config.otp_failure_backoff_seconds * (1.5 ** min(attempt - 1, 5)),
                ) + _jitter(config)
                await gate.penalize(min(retry_delay, config.rate_limit_backoff_seconds))
                self.bot.logger.warning(
                    "PRIVATE_WS_OTP_RETRY account=%s attempt=%s backoff_seconds=%.1f",
                    mask_account_id(self.account_id),
                    attempt,
                    retry_delay,
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
                continue

            self.bot.logger.info(
                "Connecting to private WebSocket for account %s...",
                mask_account_id(self.account_id),
                extra={
                    "token_tag": self.token_tag,
                    "masked_account_id": mask_account_id(self.account_id),
                },
            )
            websocket = await gate.open_websocket(url)
            self.ws = websocket
            self.is_connected = True
            if any(token == self.token for token, _ in self.bot.valid_clients):
                self.bot._set_account_execution_status(
                    self.managed_account_id,
                    "active",
                    "Private trading connection is active",
                )
            self.pending_requests.clear()
            attempt = 0
            self.bot.logger.info(
                "Private WebSocket connected for account %s",
                mask_account_id(self.account_id),
                extra={
                    "token_tag": self.token_tag,
                    "masked_account_id": mask_account_id(self.account_id),
                },
            )
            await websocket.send(
                '{"balance":1,"subscribe":1,"req_id":900001}'
            )

            for contract_id in list(self.pending_contracts):
                await self.subscribe_contract(contract_id)

            self.bot._on_private_session_ready(self)
            ping_task = asyncio.create_task(self._ping_loop())
            self.reconcile_task = asyncio.create_task(self._reconcile_contracts_loop())
            try:
                async for message in websocket:
                    await self._on_message(message)
            finally:
                if self.reconcile_task:
                    self.reconcile_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self.reconcile_task
                    self.reconcile_task = None
                ping_task.cancel()
                with suppress(asyncio.CancelledError):
                    await ping_task

                self.is_connected = False
                self.ws = None
                if any(token == self.token for token, _ in self.bot.valid_clients):
                    self.bot._set_account_execution_status(
                        self.managed_account_id,
                        "reconnecting",
                        "Private trading connection closed",
                    )
                retry_delay = _normal_backoff(self, config, attempt)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.is_connected = False
            self.ws = None
            attempt += 1
            rate_limited = _is_rate_limit(exc)
            if rate_limited:
                retry_delay = _rate_backoff(config, attempt)
                await gate.penalize(retry_delay)
                self.bot.logger.warning(
                    "PRIVATE_WS_RATE_LIMITED account=%s status=%s attempt=%s "
                    "global_backoff_seconds=%.1f",
                    mask_account_id(self.account_id),
                    _http_status(exc) or "unknown",
                    attempt,
                    retry_delay,
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
            else:
                retry_delay = _normal_backoff(self, config, attempt)
                self.bot.logger.warning(
                    "Private connection lost for account %s: %s. "
                    "Reconnecting in %.1fs...",
                    mask_account_id(self.account_id),
                    sanitize_account_ids(str(exc)),
                    retry_delay,
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
            if any(token == self.token for token, _ in self.bot.valid_clients):
                self.bot._set_account_execution_status(
                    self.managed_account_id,
                    "rate_limited" if rate_limited else "reconnecting",
                    (
                        f"Deriv connection rate-limited; retrying in {retry_delay:.0f} seconds"
                        if rate_limited
                        else "Private trading connection interrupted"
                    ),
                )
        finally:
            if websocket is not None:
                with suppress(Exception):
                    await websocket.close()
            self.is_connected = False
            self.ws = None

        if retry_delay > 0 and self.bot.is_running:
            await asyncio.sleep(retry_delay)


def install_private_websocket_rate_limit() -> None:
    """Install a global private-connection scheduler before the bot is created."""
    global _INSTALLED
    if _INSTALLED:
        return
    ClientSession.connect_and_run = _rate_limited_connect_and_run
    ClientSession._private_websocket_rate_limit_installed = True
    _INSTALLED = True
