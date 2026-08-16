from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any

import websockets

from app import custom_execution_consistency_authority as consistency
from app import custom_strategy_connection_stampede_guard as connection_guard
from app import private_websocket_rate_limit as private_ws
from app import vps_execution_start_recovery as vps_recovery
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import ClientSession, mask_account_id, sanitize_account_ids


_INSTALLED = False

_WATCHDOG_INTERVAL_SECONDS = 2.0
_DEAD_SESSION_REPAIR_GRACE_SECONDS = 8.0
_RECONNECT_LOG_INTERVAL_SECONDS = 60.0


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _provider_backoff_active(row: Any) -> bool:
    reason = str(_row_value(row, "execution_status_reason", "") or "").lower()
    return "rate-limit" in reason or "rate limited" in reason


def _stability_state(bot: RFDir5TradingBot) -> dict[int, dict[str, float]]:
    state = getattr(bot, "_custom_connection_stability_state", None)
    if not isinstance(state, dict):
        state = {}
        bot._custom_connection_stability_state = state
    return state


async def _stable_execution_watchdog(bot: RFDir5TradingBot) -> None:
    """Repair missing/dead sessions without disturbing a live reconnect loop.

    A live ClientSession owns its queued connection slot, OTP request, provider
    backoff and WebSocket handshake. The watchdog must never wake, cancel or recycle
    that task merely because it has not connected yet.
    """

    while bot.is_running:
        try:
            now = time.monotonic()
            state = _stability_state(bot)
            enabled_ids: set[int] = set()

            for row in bot.repository.list_managed_accounts():
                managed_id = int(_row_value(row, "id"))
                if not bool(_row_value(row, "enabled", False)):
                    state.pop(managed_id, None)
                    continue
                enabled_ids.add(managed_id)

                session = connection_guard._private_session_for_account(bot, managed_id)
                connected = bool(
                    session is not None and getattr(session, "is_connected", False)
                )
                if connected:
                    state.pop(managed_id, None)
                    if connection_guard._direct_runtime_for_account(bot, managed_id) is None:
                        connection_guard._schedule_targeted_runtime_repair(bot, managed_id)
                    continue

                entry = state.setdefault(
                    managed_id,
                    {
                        "disconnected_since": now,
                        "last_repair": 0.0,
                        "last_log": 0.0,
                    },
                )
                disconnected_for = now - float(entry.get("disconnected_since") or now)

                # Provider penalties are authoritative. Do not shorten a 429/1015
                # backoff by manufacturing another connection attempt.
                if _provider_backoff_active(row):
                    continue

                task_alive = connection_guard._session_task_alive(session)

                # Critical invariant: an existing live session task already owns
                # OTP scheduling, handshake timeout, backoff and reconnection.
                # Leaving it alone prevents watchdog/recycle storms from keeping
                # dozens of accounts forever in STARTING.
                if session is not None and task_alive:
                    last_log = float(entry.get("last_log") or 0.0)
                    if (
                        disconnected_for >= _DEAD_SESSION_REPAIR_GRACE_SECONDS
                        and now - last_log >= _RECONNECT_LOG_INTERVAL_SECONDS
                    ):
                        entry["last_log"] = now
                        bot.logger.info(
                            "VPS_EXECUTION_RECONNECT_OWNED managed_id=%s "
                            "disconnected_seconds=%.1f session_task_alive=true "
                            "watchdog_wake=false session_recycle=false",
                            managed_id,
                            disconnected_for,
                        )
                    continue

                if disconnected_for < _DEAD_SESSION_REPAIR_GRACE_SECONDS:
                    continue

                last_repair = float(entry.get("last_repair") or 0.0)
                if now - last_repair < _DEAD_SESSION_REPAIR_GRACE_SECONDS:
                    continue
                entry["last_repair"] = now
                connection_guard._schedule_targeted_runtime_repair(bot, managed_id)
                bot.logger.warning(
                    "VPS_EXECUTION_DEAD_SESSION_REPAIR managed_id=%s "
                    "session_object=%s session_task_alive=false "
                    "sibling_sessions_rebuilt=false",
                    managed_id,
                    session is not None,
                )

            for managed_id in list(state):
                if managed_id not in enabled_ids:
                    state.pop(managed_id, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            bot.logger.exception("VPS_EXECUTION_STABILITY_WATCHDOG_FAILED")
        await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)


def _soft_private_reconnect(
    bot: RFDir5TradingBot,
    managed_id: int,
    reason: str,
) -> None:
    """Recover one private execution stream without force-closing a live session."""

    account = bot.repository.managed_account(int(managed_id)) or {}
    if not bool(account.get("enabled")):
        return

    bot._set_account_execution_status(
        int(managed_id),
        "reconnecting",
        "Private trading connection is recovering automatically; Auto Trading remains active.",
    )

    session = connection_guard._private_session_for_account(bot, int(managed_id))
    connected = bool(session is not None and getattr(session, "is_connected", False))
    task_alive = connection_guard._session_task_alive(session)

    # A qualified execution may wake an existing disconnected session. This is a
    # soft event handled by ClientSession; it never cancels the task or closes its
    # socket. Missing/dead sessions use the single-flight targeted repair path.
    if session is not None and task_alive and not connected:
        try:
            private_ws.wake_private_connection(session)
        except Exception:
            pass

    if session is None or not task_alive:
        connection_guard._schedule_targeted_runtime_repair(bot, int(managed_id))
    elif connection_guard._direct_runtime_for_account(bot, int(managed_id)) is None:
        connection_guard._schedule_targeted_runtime_repair(bot, int(managed_id))

    bot.logger.warning(
        "CUSTOM_EXECUTION_SOFT_RECONNECT managed_id=%s lifecycle_stop=false "
        "forced_disconnect=false public_reconnect=false session_task_alive=%s "
        "session_connected=%s reason=%s",
        int(managed_id),
        task_alive,
        connected,
        str(reason or "execution transport fault")[:140],
    )
    try:
        consistency._dashboard_wakeup(bot)
    except Exception:
        pass


def _skip_execution_driven_public_reconnect(
    bot: RFDir5TradingBot,
    reason: str,
) -> None:
    """Keep account-private faults from restarting the shared market stream.

    Public WebSocket resilience owns genuine market-stream failures independently.
    Direct account proposal/BUY preparation errors are private-session concerns.
    """

    bot.logger.info(
        "CUSTOM_PUBLIC_RECONNECT_SKIPPED source=account_private_execution "
        "public_stream_owner=public_websocket_resilience reason=%s",
        str(reason or "account private execution fault")[:140],
    )


async def _fresh_otp_connect_and_run(self: ClientSession) -> None:
    """Reserve handshake capacity before requesting each one-time WebSocket URL.

    The previous limiter requested an OTP after only a global start-slot reservation
    and then queued the one-time URL behind the handshake semaphore. With dozens of
    accounts and only two handshake slots, URLs could age for minutes before use and
    be rejected with HTTP 401. This loop acquires handshake capacity first, then
    obtains and consumes that account's OTP immediately.
    """

    attempt = 0
    gate = private_ws._gate_for(self)
    config = gate.config
    ready_event = private_ws._ready_event(self)

    while self.bot.is_running and (
        self.pending_contracts or private_ws._still_configured(self)
    ):
        retry_delay = 0.0
        websocket = None
        try:
            url = ""
            # Handshake capacity is reserved before OTP generation. The slot is
            # held only through OTP bootstrap + opening handshake, never for the
            # lifetime of the connected WebSocket.
            async with gate._handshake_slots:
                await gate.wait_for_start_slot()
                url = await self.get_otp_url()
                if url:
                    self.bot.logger.info(
                        "Connecting to private WebSocket for account %s...",
                        mask_account_id(self.account_id),
                        extra={
                            "token_tag": self.token_tag,
                            "masked_account_id": mask_account_id(self.account_id),
                        },
                    )
                    websocket = await websockets.connect(
                        url,
                        open_timeout=20,
                        close_timeout=5,
                        ping_interval=20,
                        ping_timeout=20,
                    )

            if not url:
                if not self.pending_contracts and not private_ws._still_configured(self):
                    return
                attempt += 1
                retry_delay = min(
                    config.maximum_backoff_seconds,
                    config.otp_failure_backoff_seconds
                    * (1.5 ** min(attempt - 1, 5)),
                ) + private_ws._jitter(config)
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
                self.ws = websocket
                self.is_connected = True
                ready_event.set()
                if private_ws._still_configured(self):
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
                    if private_ws._still_configured(self):
                        self.bot._set_account_execution_status(
                            self.managed_account_id,
                            "reconnecting",
                            "Private trading connection closed",
                        )
                    retry_delay = private_ws._normal_backoff(self, config, attempt)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.is_connected = False
            self.ws = None
            ready_event.clear()
            attempt += 1
            rate_limited = private_ws._is_rate_limit(exc)
            if rate_limited:
                retry_delay = private_ws._rate_backoff(config, attempt)
                await gate.penalize(retry_delay)
                self.bot.logger.warning(
                    "PRIVATE_WS_RATE_LIMITED account=%s status=%s attempt=%s "
                    "global_backoff_seconds=%.1f",
                    mask_account_id(self.account_id),
                    private_ws._http_status(exc) or "unknown",
                    attempt,
                    retry_delay,
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
            else:
                retry_delay = private_ws._normal_backoff(self, config, attempt)
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
            if private_ws._still_configured(self):
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
            await private_ws._sleep_or_wake(self, retry_delay)


def install_custom_strategy_connection_stability_fix() -> None:
    """Final connection invariant for the full-VPS Custom Strategy worker."""

    global _INSTALLED
    if _INSTALLED:
        return

    # vps_execution_start_recovery wraps RFDir5TradingBot.run, but resolves this
    # module-global watchdog when run() actually starts. Replace only the watchdog;
    # keep its OAuth refresh wrapper intact.
    vps_recovery._stalled_execution_watchdog = _stable_execution_watchdog

    # custom_execution_consistency_authority installs after the earlier continuity
    # layer and had reintroduced force-close + public reconnect behavior for
    # private account faults. Its nested handlers resolve these globals at runtime.
    consistency._request_private_reconnect = _soft_private_reconnect
    consistency._request_public_reconnect = _skip_execution_driven_public_reconnect

    # The original limiter queued already-issued OTP URLs behind the handshake
    # semaphore. Rebind before the bot creates sessions so every connection task
    # reserves handshake capacity before requesting its one-time URL.
    ClientSession.connect_and_run = _fresh_otp_connect_and_run

    RFDir5TradingBot._custom_strategy_connection_stability_fix_installed = True
    RFDir5TradingBot._vps_stalled_execution_recycle_seconds = None
    RFDir5TradingBot._private_ws_fresh_otp_before_handshake = True
    _INSTALLED = True
