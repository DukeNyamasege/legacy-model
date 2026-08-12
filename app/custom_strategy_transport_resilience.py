from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress
from typing import Any

import aiohttp

from app import custom_strategy_direct_runtime as direct_runtime
from app.account_execution_session import AccountExecutionError, AccountExecutionSession
from app.private_websocket_rate_limit import wake_private_connection, wait_until_connected
from enhanced_bot import TradingBot, mask_account_id


_INSTALLED = False
_TRANSIENT_MARKERS = (
    "request timed out",
    "timed out",
    "not connected",
    "connection closed",
    "connection reset",
    "connection interrupted",
    "temporarily unavailable",
    "service restart",
    "websocket is not connected",
    "transport interruption",
    "reconnecting automatically",
    "purchase acknowledgement uncertain",
)


def _is_transient(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


async def _reconnect_private_session(
    execution: AccountExecutionSession,
    *,
    reason: str,
    wait_seconds: float = 5.0,
) -> bool:
    bot = execution.bot
    session = getattr(bot, "sessions", {}).get(execution.token)
    if session is None:
        return False

    bot._set_account_execution_status(
        execution.managed_account_id,
        "reconnecting",
        "Temporary Deriv transport interruption; reconnecting this account automatically.",
    )
    bot.logger.warning(
        "CUSTOM_PRIVATE_FAST_RECONNECT managed_id=%s account=%s reason=%s",
        execution.managed_account_id,
        mask_account_id(execution.account_id),
        str(reason or "transport interruption")[:80],
    )

    websocket = getattr(session, "ws", None)
    # A timed-out request can leave the socket apparently connected while its
    # request/response router is no longer healthy. With no known open contract at
    # proposal time, recycle that transport and let the existing OTP connection
    # loop obtain a fresh account-scoped URL.
    if websocket is not None and not getattr(session, "pending_contracts", set()):
        with suppress(Exception):
            await asyncio.wait_for(
                websocket.close(code=1012, reason="account_transport_refresh"),
                timeout=1.0,
            )

    wake_private_connection(session)
    return await wait_until_connected(session, timeout=max(0.5, float(wait_seconds)))


def _transport_hold_active(item: Any) -> bool:
    bot = item.execution.bot
    holds: dict[int, float] = getattr(bot, "_custom_direct_transport_hold_until", {})
    managed_id = int(item.managed_id)
    until = float(holds.get(managed_id, 0.0) or 0.0)
    if until <= 0:
        return False
    if time.monotonic() < until:
        return True
    holds.pop(managed_id, None)
    return False


async def _nonblocking_dashboard_notify(self: TradingBot) -> None:
    """Never make contract cleanup wait for the optional API refresh hook."""

    url = os.getenv("INTERNAL_DASHBOARD_REFRESH_URL", "").strip()
    api_key = os.getenv("CONTROL_API_KEY", "").strip()
    if not url or not api_key:
        return

    async def push() -> None:
        try:
            timeout = aiohttp.ClientTimeout(total=0.75, connect=0.35)
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.post(url, headers={"X-API-Key": api_key}) as response:
                    if response.status >= 500:
                        self.logger.debug(
                            "DASHBOARD_SETTLEMENT_PUSH_DEFERRED status=%s",
                            response.status,
                        )
        except Exception:
            # The browser SSE/live-snapshot path is authoritative. A saturated API
            # must never delay contract settlement cleanup or turn into WARNING spam.
            self.logger.debug("DASHBOARD_SETTLEMENT_PUSH_DEFERRED transport=unavailable")

    task = asyncio.create_task(push(), name="dashboard_settlement_refresh")
    tasks: set[asyncio.Task[Any]] = getattr(self, "_dashboard_notify_tasks", set())
    self._dashboard_notify_tasks = tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def install_custom_strategy_transport_resilience() -> None:
    """Keep transient network faults from disabling a healthy trader account.

    Proposal is read-only and may be retried once after a fresh private WebSocket.
    Buy acknowledgement is deliberately never retried blindly: if its response is
    uncertain, new purchases are held briefly while the connection recovers so a
    duplicate monetary contract cannot be created.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    exact_proposal = AccountExecutionSession.proposal
    exact_buy = AccountExecutionSession.buy_proposal
    fail_closed = direct_runtime._fail_closed
    has_open_actual = direct_runtime._account_has_open_actual

    async def resilient_proposal(
        self: AccountExecutionSession,
        signal: Any,
        *,
        stake: float,
        predicted_probability: float,
    ):
        try:
            return await exact_proposal(
                self,
                signal,
                stake=stake,
                predicted_probability=predicted_probability,
            )
        except AccountExecutionError as exc:
            if not _is_transient(exc):
                raise
            connected = await _reconnect_private_session(
                self,
                reason=str(exc),
                wait_seconds=float(os.getenv("PRIVATE_WS_PURCHASE_WAKE_SECONDS", "5")),
            )
            if not connected:
                raise AccountExecutionError(
                    "Temporary Deriv transport interruption; reconnecting automatically"
                ) from exc
            self.bot.logger.info(
                "CUSTOM_PROPOSAL_RETRY_AFTER_RECONNECT managed_id=%s account=%s retry=1",
                self.managed_account_id,
                mask_account_id(self.account_id),
            )
            return await exact_proposal(
                self,
                signal,
                stake=stake,
                predicted_probability=predicted_probability,
            )

    async def resilient_buy(self: AccountExecutionSession, economics: Any) -> dict[str, Any]:
        try:
            return await exact_buy(self, economics)
        except AccountExecutionError as exc:
            if not _is_transient(exc):
                raise

            holds: dict[int, float] = getattr(
                self.bot,
                "_custom_direct_transport_hold_until",
                {},
            )
            self.bot._custom_direct_transport_hold_until = holds
            hold_seconds = max(
                10.0,
                float(os.getenv("CUSTOM_BUY_ACK_HOLD_SECONDS", "30")),
            )
            holds[int(self.managed_account_id)] = time.monotonic() + hold_seconds
            self.bot.logger.warning(
                "CUSTOM_BUY_ACK_UNKNOWN managed_id=%s account=%s hold_seconds=%.1f "
                "blind_retry=false trading_enabled=true",
                self.managed_account_id,
                mask_account_id(self.account_id),
                hold_seconds,
            )
            await _reconnect_private_session(
                self,
                reason=str(exc),
                wait_seconds=float(os.getenv("PRIVATE_WS_PURCHASE_WAKE_SECONDS", "5")),
            )
            raise AccountExecutionError(
                "Purchase acknowledgement uncertain after transport timeout; "
                "reconnecting automatically and holding new purchases"
            ) from exc

    def transient_aware_fail_closed(
        bot: Any,
        managed_id: int,
        reason: str,
        *,
        log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
    ) -> None:
        if _is_transient(reason):
            bot._set_account_execution_status(
                int(managed_id),
                "reconnecting",
                "Temporary Deriv connection interruption. Auto trading remains enabled and will resume automatically.",
            )
            bot.logger.warning(
                "CUSTOM_STRATEGY_TRANSIENT_RECOVERY managed_id=%s source=%s "
                "account_disabled=false automatic_reconnect=true",
                int(managed_id),
                log_event,
            )
            return
        fail_closed(bot, managed_id, reason, log_event=log_event)

    def open_or_transport_hold(item: Any) -> bool:
        return _transport_hold_active(item) or bool(has_open_actual(item))

    AccountExecutionSession.proposal = resilient_proposal  # type: ignore[method-assign]
    AccountExecutionSession.buy_proposal = resilient_buy  # type: ignore[method-assign]
    direct_runtime._fail_closed = transient_aware_fail_closed
    direct_runtime._account_has_open_actual = open_or_transport_hold
    TradingBot._notify_dashboard_settlement = _nonblocking_dashboard_notify
    TradingBot._custom_strategy_transport_resilience_installed = True
    _INSTALLED = True
