from __future__ import annotations

import time
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_ORIGINAL_HANDLE_CONTRACT: Any = None
_ORIGINAL_SCHEDULE: Any = None


def _barriers(bot: RFDir5TradingBot) -> dict[int, int]:
    values = getattr(bot, "_custom_virtual_post_real_loss_barriers", None)
    if not isinstance(values, dict):
        values = {}
        bot._custom_virtual_post_real_loss_barriers = values
    return values


def _provider_epoch(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _tick_epoch(tick: dict[str, Any]) -> int:
    direct = _provider_epoch(tick.get("epoch"))
    if direct:
        return direct
    nested = tick.get("tick")
    if isinstance(nested, dict):
        return _provider_epoch(nested.get("epoch"))
    return 0


def install_custom_virtual_post_loss_barrier_authority() -> None:
    """Do not let the real-loss settlement tick double as a virtual entry tick.

    The real contract remains authoritative until its settlement handler finishes.
    If that settlement enters Virtual Hook, every Custom Strategy account scheduler
    is blocked until a strictly later provider tick arrives. The future tick still
    has to qualify the normal saved strategy; there is no virtual-only fast path.
    """

    global _INSTALLED, _ORIGINAL_HANDLE_CONTRACT, _ORIGINAL_SCHEDULE
    if _INSTALLED:
        return

    _ORIGINAL_HANDLE_CONTRACT = RFDir5TradingBot.handle_contract_update
    _ORIGINAL_SCHEDULE = direct_runtime._schedule_account_matches

    async def handle_contract_then_arm_virtual_barrier(
        self: RFDir5TradingBot,
        token: str,
        contract_id: int,
        contract: dict[str, Any],
    ) -> Any:
        managed_id = self._managed_account_id_for_token(token)
        before_mode = ""
        if managed_id is not None:
            try:
                before = self.rf_repository.virtual_protection_for_account(
                    managed_account_id=int(managed_id),
                    account_id_masked="",
                )
                before_mode = str(before.get("mode") or "")
            except Exception:
                before_mode = ""

        original = _ORIGINAL_HANDLE_CONTRACT
        if original is None:
            return None
        result = await original(self, token, contract_id, contract)

        if managed_id is None:
            return result
        try:
            after = self.rf_repository.virtual_protection_for_account(
                managed_account_id=int(managed_id),
                account_id_masked="",
            )
            after_mode = str(after.get("mode") or "")
        except Exception:
            return result

        if before_mode == "VIRTUAL_MODE" or after_mode != "VIRTUAL_MODE":
            return result

        # This transition can only be observed after the real contract settlement
        # path has completed. Prefer the provider's settlement epoch; wall-clock is
        # only a conservative fallback when an old provider payload omits it.
        settlement_epoch = max(
            _provider_epoch(contract.get("sell_time")),
            _provider_epoch(contract.get("date_expiry")),
            _provider_epoch(contract.get("current_spot_time")),
            _provider_epoch(contract.get("date_start")),
        )
        if settlement_epoch <= 0:
            settlement_epoch = int(time.time())
        _barriers(self)[int(managed_id)] = settlement_epoch

        self._set_account_execution_status(
            int(managed_id),
            "virtual_protection",
            "Actual losing position is fully settled; Virtual Hook is waiting for the next future qualifying market tick.",
        )
        self.logger.warning(
            "CUSTOM_VIRTUAL_AFTER_REAL_LOSS_BARRIER managed_id=%s contract_id=%s "
            "settlement_epoch=%s open_actual_closed=true same_settlement_tick_entry=false "
            "virtual_fast_path=false",
            int(managed_id),
            int(contract_id),
            settlement_epoch,
        )
        return result

    def schedule_only_after_future_post_loss_tick(
        bot: RFDir5TradingBot,
        *,
        symbol: str,
        tick: dict[str, Any],
    ) -> None:
        original = _ORIGINAL_SCHEDULE
        if original is None:
            return

        runtime: dict[int, Any] = getattr(bot, "_custom_direct_accounts", {})
        barriers = _barriers(bot)
        if not runtime or not barriers:
            return original(bot, symbol=symbol, tick=tick)

        epoch = _tick_epoch(tick)
        blocked: set[int] = set()
        for managed_id, barrier_epoch in list(barriers.items()):
            # If recovery was cancelled/stopped or already left Virtual Hook, this
            # barrier no longer owns the account.
            try:
                protection = bot.rf_repository.virtual_protection_for_account(
                    managed_account_id=int(managed_id),
                    account_id_masked="",
                )
                virtual_mode = str(protection.get("mode") or "") == "VIRTUAL_MODE"
            except Exception:
                virtual_mode = True
            if not virtual_mode:
                barriers.pop(int(managed_id), None)
                continue

            if epoch <= 0 or epoch <= int(barrier_epoch):
                blocked.add(int(managed_id))
                continue

            # A strictly later provider tick has arrived. Remove the barrier and
            # let the existing normal strategy scheduler decide whether it qualifies.
            barriers.pop(int(managed_id), None)
            bot.logger.info(
                "CUSTOM_VIRTUAL_POST_LOSS_FUTURE_TICK_READY managed_id=%s symbol=%s "
                "settlement_epoch=%s tick_epoch=%s normal_strategy_qualification_required=true",
                int(managed_id),
                str(symbol),
                int(barrier_epoch),
                epoch,
            )

        if not blocked:
            return original(bot, symbol=symbol, tick=tick)

        bot._custom_direct_accounts = {
            int(managed_id): item
            for managed_id, item in runtime.items()
            if int(managed_id) not in blocked
        }
        try:
            original(bot, symbol=symbol, tick=tick)
        finally:
            bot._custom_direct_accounts = runtime

    RFDir5TradingBot.handle_contract_update = handle_contract_then_arm_virtual_barrier  # type: ignore[method-assign]
    direct_runtime._schedule_account_matches = schedule_only_after_future_post_loss_tick
    RFDir5TradingBot._custom_virtual_post_loss_barrier_authority_installed = True
    RFDir5TradingBot._custom_virtual_entry_policy = "real_position_settled_then_future_qualified_tick"
    _INSTALLED = True
