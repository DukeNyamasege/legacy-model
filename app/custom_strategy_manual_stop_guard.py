from __future__ import annotations

import asyncio
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app.account_execution_session import AccountExecutionSession
from app.account_mode_execution_lock import account_allows_new_execution
from app.models import ManagedAccount
from app.rf_dir5_bot import RFDir5TradingBot

_INSTALLED = False
_ORIGINAL_EXECUTE: Any = None
_ORIGINAL_SCHEDULE: Any = None
_ORIGINAL_PROPOSAL: Any = None
_ORIGINAL_BUY: Any = None
_ORIGINAL_HISTORY_COUNT: Any = None


def _allows_new_execution(bot: RFDir5TradingBot, managed_id: int) -> bool:
    try:
        with bot.repository.database.session() as session:
            row = session.get(ManagedAccount, int(managed_id))
            return bool(row is not None and account_allows_new_execution(row))
    except Exception:
        # A failed lifecycle read must fail closed for a new purchase.
        return False


def _cancelled_by_manual_stop(bot: RFDir5TradingBot, managed_id: int, signal: Any) -> None:
    try:
        bot.repository.mark_signal(
            str(getattr(signal, "signal_id", "")),
            status="CANCELLED_BY_MANUAL_STOP",
        )
    except Exception:
        pass
    bot.logger.info(
        "CUSTOM_EXECUTION_CANCELLED_BY_MANUAL_STOP managed_id=%s signal_id=%s purchase=false",
        managed_id,
        str(getattr(signal, "signal_id", "-")),
    )


def _prediction_window(config: dict[str, Any]) -> int:
    try:
        reanalyze = config.get("reanalyze") if isinstance(config.get("reanalyze"), dict) else {}
        value = int(reanalyze.get("prediction_window") or config.get("prediction_window") or 0)
    except (TypeError, ValueError, AttributeError):
        value = 0
    return max(0, min(1000, value))


def install_custom_strategy_manual_stop_guard() -> None:
    """Make manual Stop a final barrier for every not-yet-sent purchase.

    The worker can have a qualifying task already queued when the HTTP Stop request
    commits. This guard re-reads the persisted ManagedAccount lifecycle before the
    task, before proposal and again immediately before BUY. A disabled/stopped row
    cancels the task without sending a new financial purchase. Contracts whose BUY
    request was already sent before Stop are still registered and settled normally.
    """

    global _INSTALLED
    global _ORIGINAL_EXECUTE, _ORIGINAL_SCHEDULE, _ORIGINAL_PROPOSAL, _ORIGINAL_BUY
    global _ORIGINAL_HISTORY_COUNT
    if _INSTALLED:
        return

    _ORIGINAL_EXECUTE = direct_runtime._execute_for_account
    _ORIGINAL_SCHEDULE = direct_runtime._schedule_account_matches
    _ORIGINAL_PROPOSAL = AccountExecutionSession.proposal
    _ORIGINAL_BUY = AccountExecutionSession.buy_proposal
    _ORIGINAL_HISTORY_COUNT = RFDir5TradingBot._public_history_count

    async def guarded_execute(bot: RFDir5TradingBot, item: Any, *, signal: Any) -> None:
        managed_id = int(item.managed_id)
        if not _allows_new_execution(bot, managed_id):
            _cancelled_by_manual_stop(bot, managed_id, signal)
            getattr(bot, "_custom_direct_inflight", set()).discard(managed_id)
            return
        try:
            await _ORIGINAL_EXECUTE(bot, item, signal=signal)
        except asyncio.CancelledError:
            if not _allows_new_execution(bot, managed_id):
                _cancelled_by_manual_stop(bot, managed_id, signal)
                getattr(bot, "_custom_direct_inflight", set()).discard(managed_id)
                return
            raise

    def guarded_schedule(bot: RFDir5TradingBot, *, symbol: str, tick: dict[str, Any]) -> None:
        runtime = dict(getattr(bot, "_custom_direct_accounts", {}) or {})
        if not runtime:
            return _ORIGINAL_SCHEDULE(bot, symbol=symbol, tick=tick)
        allowed = {
            int(managed_id): item
            for managed_id, item in runtime.items()
            if _allows_new_execution(bot, int(managed_id))
        }
        bot._custom_direct_accounts = allowed
        try:
            _ORIGINAL_SCHEDULE(bot, symbol=symbol, tick=tick)
        finally:
            bot._custom_direct_accounts = runtime

    async def guarded_proposal(
        self: AccountExecutionSession,
        signal: Any,
        *,
        stake: float,
        predicted_probability: float,
    ) -> Any:
        if not _allows_new_execution(self.bot, int(self.managed_account_id)):
            raise asyncio.CancelledError("manual Stop blocks proposal")
        return await _ORIGINAL_PROPOSAL(
            self,
            signal,
            stake=stake,
            predicted_probability=predicted_probability,
        )

    async def guarded_buy(self: AccountExecutionSession, economics: Any) -> dict[str, Any]:
        if not _allows_new_execution(self.bot, int(self.managed_account_id)):
            raise asyncio.CancelledError("manual Stop blocks BUY")
        return await _ORIGINAL_BUY(self, economics)

    def history_count(self: RFDir5TradingBot) -> int:
        required = int(_ORIGINAL_HISTORY_COUNT(self)) if _ORIGINAL_HISTORY_COUNT else 0
        for item in (getattr(self, "_custom_direct_accounts", {}) or {}).values():
            required = max(required, _prediction_window(dict(getattr(item, "config", {}) or {})))
        for routing in (getattr(self, "_custom_result_routing", {}) or {}).values():
            route = (routing or {}).get("after_loss") or {}
            try:
                required = max(required, int(route.get("prediction_window") or 0))
            except (TypeError, ValueError):
                pass
        return min(1000, max(1, required)) if required else 0

    direct_runtime._execute_for_account = guarded_execute
    direct_runtime._schedule_account_matches = guarded_schedule
    AccountExecutionSession.proposal = guarded_proposal  # type: ignore[method-assign]
    AccountExecutionSession.buy_proposal = guarded_buy  # type: ignore[method-assign]
    RFDir5TradingBot._public_history_count = history_count
    RFDir5TradingBot._custom_strategy_manual_stop_guard_installed = True
    _INSTALLED = True
