from __future__ import annotations

import asyncio
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app import custom_strategy_instant_start as instant_runtime
from app.account_execution_session import AccountExecutionSession
from app.premium_access_service import (
    PREMIUM_REQUIRED_REASON,
    pause_managed_account_for_premium,
    premium_access_for_managed_account,
)
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_ORIGINAL_FAST_RUNTIME_ACCOUNTS: Any = None
_ORIGINAL_EXECUTE: Any = None
_ORIGINAL_PROPOSAL: Any = None
_ORIGINAL_BUY: Any = None


def _premium_allows_new_execution(bot: RFDir5TradingBot, managed_id: int) -> bool:
    try:
        state = premium_access_for_managed_account(
            bot.repository.database,
            int(managed_id),
        )
    except Exception:
        # Entitlement storage is part of the financial access boundary. A failed
        # read must fail closed rather than letting a new purchase through.
        bot.logger.exception(
            "PREMIUM_ACCESS_LOOKUP_FAILED managed_id=%s purchase=false",
            managed_id,
        )
        return False
    if state.active:
        return True

    try:
        pause_managed_account_for_premium(
            bot.repository.database,
            int(managed_id),
            reason=PREMIUM_REQUIRED_REASON,
        )
    except Exception:
        bot.logger.exception(
            "PREMIUM_ACCESS_PAUSE_FAILED managed_id=%s purchase=false",
            managed_id,
        )
    bot.logger.info(
        "PREMIUM_EXECUTION_BLOCKED managed_id=%s status=%s expires_at=%s purchase=false",
        managed_id,
        state.status,
        state.current_period_end.isoformat() if state.current_period_end else "none",
    )
    return False


def install_premium_worker_guard() -> None:
    """Make premium access the last barrier before proposal/BUY.

    Unpaid/expired accounts are removed from fresh private-session admission. An
    already-open contract can still settle, but every new execution task, proposal,
    and BUY rechecks the exact server timestamp against the paid period end.
    """

    global _INSTALLED
    global _ORIGINAL_FAST_RUNTIME_ACCOUNTS, _ORIGINAL_EXECUTE
    global _ORIGINAL_PROPOSAL, _ORIGINAL_BUY
    if _INSTALLED:
        return

    _ORIGINAL_FAST_RUNTIME_ACCOUNTS = instant_runtime._fast_runtime_accounts
    _ORIGINAL_EXECUTE = direct_runtime._execute_for_account
    _ORIGINAL_PROPOSAL = AccountExecutionSession.proposal
    _ORIGINAL_BUY = AccountExecutionSession.buy_proposal

    def premium_runtime_accounts(
        bot: RFDir5TradingBot,
    ) -> tuple[list[str], dict[str, dict[str, Any]]]:
        tokens, profiles = _ORIGINAL_FAST_RUNTIME_ACCOUNTS(bot)
        allowed_tokens: list[str] = []
        allowed_profiles: dict[str, dict[str, Any]] = {}
        for token in tokens:
            profile = dict(profiles.get(token) or {})
            managed_id = int(profile.get("managed_account_id") or 0)
            if bool(profile.get("settlement_only")):
                # Settlement-only sessions cannot enter proposal/BUY, but they
                # must remain alive long enough to reconcile already-open money.
                allowed_tokens.append(token)
                allowed_profiles[token] = profile
                continue
            if managed_id > 0 and _premium_allows_new_execution(bot, managed_id):
                allowed_tokens.append(token)
                allowed_profiles[token] = profile
        return allowed_tokens, allowed_profiles

    async def premium_execute(bot: RFDir5TradingBot, item: Any, *, signal: Any) -> None:
        managed_id = int(item.managed_id)
        if not _premium_allows_new_execution(bot, managed_id):
            getattr(bot, "_custom_direct_inflight", set()).discard(managed_id)
            return
        await _ORIGINAL_EXECUTE(bot, item, signal=signal)

    async def premium_proposal(
        self: AccountExecutionSession,
        signal: Any,
        *,
        stake: float,
        predicted_probability: float,
    ) -> Any:
        if not _premium_allows_new_execution(
            self.bot,
            int(self.managed_account_id),
        ):
            raise asyncio.CancelledError("premium subscription blocks proposal")
        return await _ORIGINAL_PROPOSAL(
            self,
            signal,
            stake=stake,
            predicted_probability=predicted_probability,
        )

    async def premium_buy(
        self: AccountExecutionSession,
        economics: Any,
    ) -> dict[str, Any]:
        if not _premium_allows_new_execution(
            self.bot,
            int(self.managed_account_id),
        ):
            raise asyncio.CancelledError("premium subscription blocks BUY")
        return await _ORIGINAL_BUY(self, economics)

    instant_runtime._fast_runtime_accounts = premium_runtime_accounts
    direct_runtime._execute_for_account = premium_execute
    AccountExecutionSession.proposal = premium_proposal  # type: ignore[method-assign]
    AccountExecutionSession.buy_proposal = premium_buy  # type: ignore[method-assign]

    RFDir5TradingBot._premium_worker_guard_installed = True
    RFDir5TradingBot._premium_settlement_preserved = True
    RFDir5TradingBot._premium_exact_expiry_purchase_gate = True
    _INSTALLED = True
