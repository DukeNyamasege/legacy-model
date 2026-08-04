from __future__ import annotations

import asyncio
import os
import random
import time
from contextlib import suppress
from dataclasses import dataclass

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
                0.25,
                minimum=0.05,
            ),
            handshake_concurrency=_positive_int(
                "PRIVATE_WS_HANDSHAKE_CONCURRENCY",
                4,
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
                1.0,
                minimum=0.0,
            ),
            otp_failure_backoff_seconds=_positive_float(
                "PRIVATE_WS_OTP_FAILURE_BACKOFF_SECONDS",
                5.0,
                minimum=1.0,
            ),
        )


class _PrivateConnectionGate:
    """Coordinate one OTP+handshake start slot per account connection attempt."""

    def __init__(self, config: _PrivateConnectionConfig) -> None:
        self.config = config
        self._schedule_lock = asyncio.Lock()
        self._handshake_slots = asyncio.Semaphore(config.handshake_concurrency)
        self._next_start_at = 0.0
        self._penalty_until = 0.0

    async def wait_for_start_slot(self) -> None:
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
        # The caller already reserved the one start slot for this OTP+handshake.
        # Do not wait a second time; the old double wait delayed large account sets.
        async with self._handshake_slots:
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
        "maximum_backoff_seconds=%.1f start_slots_per_attempt=1",
        gate.config.interval_seconds,
        gate.config.handshake_concurrency,
        gate.config.rate_limit_backoff_seconds,
        gate.config.maximum_backoff_seconds,
    )
    return gate


def _wake_event(session: ClientSession) -> asyncio.Event:
    event = getattr(session, "_private_ws_wake_event", None)
    if not isinstance(event, asyncio.Event):
        event = asyncio.Event()
        session._private_ws_wake_event = event
    return event


def _ready_event(session: ClientSession) -> asyncio.Event:
    event = getattr(session, "_private_ws_ready_event", None)
    if not isinstance(event, asyncio.Event):
        event = asyncio.Event()
        session._private_ws_ready_event = event
    if session.is_connected and session.ws is not None:
        event.set()
    return event


def wake_private_connection(session: ClientSession) -> None:
    """Interrupt ordinary reconnect sleep when a qualified purchase needs this account."""

    _wake_event(session).set()


async def wait_until_connected(
    session: ClientSession,
    *,
    timeout: float,
) -> bool:
    """Wait a bounded time for this account's existing private session."""

    if session.is_connected and session.ws is not None:
        return True
    wake_private_connection(session)
    ready = _ready_event(session)
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        if session.is_connected and session.ws is not None:
            return True
        remaining = deadline - time.monotonic()
        try:
            await asyncio.wait_for(ready.wait(), timeout=min(1.0, remaining))
        except asyncio.TimeoutError:
            continue
        finally:
            if not (session.is_connected and session.ws is not None):
                ready.clear()
    return bool(session.is_connected and session.ws is not None)


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


def _still_configured(session: ClientSession) -> bool:
    return any(
        token == session.token
        for token, _account_id in session.bot.valid_clients
    )


async def _sleep_or_wake(session: ClientSession, delay: float) -> None:
    if delay <= 0:
        return
    wake = _wake_event(session)
    if wake.is_set():
        wake.clear()
        return
    try:
        await asyncio.wait_for(wake.wait(), timeout=delay)
    except asyncio.TimeoutError:
        pass
    finally:
        wake.clear()


async def _rate_limited_connect_and_run(self: ClientSession) -> None:
    attempt = 0
    gate = _gate_for(self)
    config = gate.config
    ready_event = _ready_event(self)

    while self.bot.is_running and (self.pending_contracts or _still_configured(self)):
        retry_delay = 0.0
        websocket = None
        try:
            # Reserve exactly one globally spaced slot for OTP plus handshake.
            await gate.wait_for_start_slot()
            url = await self.get_otp_url()
            if not url:
                if not self.pending_contracts and not _still_configured(self):
                    return
                attempt += 1
                retry_delay = min(
                    config.maximum_backoff_seconds,
                    config.otp_failure_backoff_seconds
                    * (1.5 ** min(attempt - 1, 5)),
                ) + _jitter(config)
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
            else:
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
                ready_event.set()
                if _still_configured(self):
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
                self.reconcile_task = asyncio.create_task(
                    self._reconcile_contracts_loop()
                )
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
                    ready_event.clear()
                    if _still_configured(self):
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
            ready_event.clear()
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
            if _still_configured(self):
                self.bot._set_account_execution_status(
                    self.managed_account_id,
                    "reconnecting",
                    (
                        f"Deriv connection rate-limited; retrying in "
                        f"{retry_delay:.0f} seconds"
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
            ready_event.clear()

        if retry_delay > 0 and self.bot.is_running:
            await _sleep_or_wake(self, retry_delay)


def install_private_websocket_rate_limit() -> None:
    """Install one-slot connection scheduling and purchase-triggered wake-up."""

    global _INSTALLED
    if _INSTALLED:
        return
    ClientSession.connect_and_run = _rate_limited_connect_and_run
    ClientSession._private_websocket_rate_limit_installed = True
    _INSTALLED = True
