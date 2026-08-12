from __future__ import annotations

import asyncio
import os
from typing import Any

import aiohttp

from app import custom_strategy_direct_runtime as direct_runtime
from app.account_execution_session import AccountExecutionError, AccountExecutionSession
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import TradingBot


_INSTALLED = False


def _temporary_transport_problem(reason: str) -> bool:
    text = str(reason or "").lower()
    if "purchase acknowledgement timed out" in text:
        return False
    return any(
        marker in text
        for marker in (
            "request timed out",
            "not connected",
            "connection interrupted",
            "connection closed",
            "connection lost",
            "authenticated deriv trading session is not connected",
        )
    )


def _drop_hot_runtime_only(bot: RFDir5TradingBot, managed_id: int) -> None:
    runtime = getattr(bot, "_custom_direct_accounts", {})
    runtime.pop(int(managed_id), None)
    getattr(bot, "_custom_direct_virtual_due", {}).pop(int(managed_id), None)
    getattr(bot, "_custom_direct_inflight", set()).discard(int(managed_id))

    try:
        current = asyncio.current_task()
    except RuntimeError:
        current = None
    for task in list(getattr(bot, "_custom_direct_tasks", set()) or set()):
        if task is current or task.done():
            continue
        if task.get_name().startswith(f"custom_direct_{int(managed_id)}_"):
            task.cancel()


def _schedule_private_reconnect(bot: RFDir5TradingBot, managed_id: int) -> None:
    runtime = getattr(bot, "_custom_direct_accounts", {})
    item = runtime.get(int(managed_id))
    token = str(getattr(item, "token", "") or "") if item is not None else ""
    if not token:
        for candidate, _account_id in list(getattr(bot, "valid_clients", []) or []):
            try:
                if bot._managed_account_id_for_token(candidate) == int(managed_id):
                    token = str(candidate)
                    break
            except Exception:
                continue
    session = getattr(bot, "sessions", {}).get(token) if token else None
    websocket = getattr(session, "ws", None) if session is not None else None
    if websocket is None:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            websocket.close(code=1012, reason="account_transport_reconnect")
        )
    except (RuntimeError, AttributeError):
        return


async def _dashboard_wakeup(bot: TradingBot) -> None:
    """Best-effort worker -> API wake-up that never gates contract settlement."""

    await asyncio.sleep(0.03)  # coalesce purchase/settlement bursts
    url = os.getenv("INTERNAL_DASHBOARD_REFRESH_URL", "").strip()
    api_key = os.getenv("CONTROL_API_KEY", "").strip()
    if not url or not api_key:
        return
    try:
        timeout = aiohttp.ClientTimeout(total=0.8, connect=0.35)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(url, headers={"X-API-Key": api_key}) as response:
                if response.status >= 400:
                    bot.logger.debug(
                        "REALTIME_WAKEUP_DEFERRED status=%s fallback_revision_poll=true",
                        response.status,
                    )
    except Exception as exc:
        # Realtime WebSockets also check the durable DB revision periodically. A
        # failed UI wake-up is therefore not an execution/settlement failure.
        bot.logger.debug(
            "REALTIME_WAKEUP_DEFERRED error_type=%s fallback_revision_poll=true",
            type(exc).__name__,
        )


def _schedule_dashboard_wakeup(bot: TradingBot) -> None:
    pending = getattr(bot, "_netlify_dashboard_wakeup_task", None)
    if pending is not None and not pending.done():
        return
    try:
        task = asyncio.create_task(
            _dashboard_wakeup(bot),
            name="netlify_dashboard_wakeup",
        )
    except RuntimeError:
        return
    bot._netlify_dashboard_wakeup_task = task

    def _done(completed: asyncio.Task[Any]) -> None:
        if getattr(bot, "_netlify_dashboard_wakeup_task", None) is completed:
            bot._netlify_dashboard_wakeup_task = None
        try:
            completed.result()
        except asyncio.CancelledError:
            return
        except Exception:
            bot.logger.debug("REALTIME_WAKEUP_TASK_FAILED", exc_info=True)

    task.add_done_callback(_done)


def install_netlify_worker_bridge() -> None:
    """Keep trading independent from UI delivery and recover transient transports."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_proposal = AccountExecutionSession.proposal
    original_buy = AccountExecutionSession.buy_proposal
    original_register = AccountExecutionSession.register_purchase
    original_fail_closed = direct_runtime._fail_closed

    async def proposal_with_one_safe_retry(
        self: AccountExecutionSession,
        signal: Any,
        *,
        stake: float,
        predicted_probability: float,
    ):
        try:
            return await original_proposal(
                self,
                signal,
                stake=stake,
                predicted_probability=predicted_probability,
            )
        except AccountExecutionError as exc:
            if not _temporary_transport_problem(str(exc)):
                raise
            # A proposal is non-financial, so one retry cannot duplicate a trade.
            # Purchases deliberately do NOT use this retry rule.
            await asyncio.sleep(0.12)
            return await original_proposal(
                self,
                signal,
                stake=stake,
                predicted_probability=predicted_probability,
            )

    async def buy_without_ambiguous_retry(
        self: AccountExecutionSession,
        economics: Any,
    ) -> dict[str, Any]:
        try:
            return await original_buy(self, economics)
        except AccountExecutionError as exc:
            text = str(exc or "")
            if "request timed out" in text.lower():
                # Never retry an uncertain financial buy. The provider may have
                # accepted it even though the acknowledgement was lost.
                raise AccountExecutionError(
                    "Purchase acknowledgement timed out; reconciliation is required before another financial purchase"
                ) from exc
            raise

    async def register_and_wake_dashboard(
        self: AccountExecutionSession,
        **kwargs: Any,
    ) -> int:
        contract_id = await original_register(self, **kwargs)
        _schedule_dashboard_wakeup(self.bot)
        return contract_id

    async def nonblocking_dashboard_notify(self: TradingBot) -> None:
        _schedule_dashboard_wakeup(self)

    def reconnect_instead_of_stop(
        bot: RFDir5TradingBot,
        managed_id: int,
        reason: str,
        *,
        log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
    ) -> None:
        if not _temporary_transport_problem(reason):
            original_fail_closed(
                bot,
                managed_id,
                reason,
                log_event=log_event,
            )
            return

        _schedule_private_reconnect(bot, int(managed_id))
        _drop_hot_runtime_only(bot, int(managed_id))
        bot._set_account_execution_status(
            int(managed_id),
            "reconnecting",
            "Temporary Deriv transport interruption; Auto Trading remains enabled and will resume after reconnection.",
        )
        bot.logger.warning(
            "CUSTOM_RUNTIME_TRANSIENT_RECONNECT managed_id=%s enabled_preserved=true "
            "purchase_retry=false reason=%s",
            int(managed_id),
            str(reason or "transport interruption")[:120],
        )
        _schedule_dashboard_wakeup(bot)

    AccountExecutionSession.proposal = proposal_with_one_safe_retry  # type: ignore[method-assign]
    AccountExecutionSession.buy_proposal = buy_without_ambiguous_retry  # type: ignore[method-assign]
    AccountExecutionSession.register_purchase = register_and_wake_dashboard  # type: ignore[method-assign]
    TradingBot._notify_dashboard_settlement = nonblocking_dashboard_notify
    direct_runtime._fail_closed = reconnect_instead_of_stop

    RFDir5TradingBot._netlify_worker_bridge_installed = True
    _INSTALLED = True
