from __future__ import annotations

import time
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app.custom_strategy_virtual_hook import virtual_hook_settings_from_session
from app.models import AccountRiskState, utc_now
from app.repositories.rf_dir5_repository import RFDir5Repository, VIRTUAL_WAITING_FOR_WIN
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_ORIGINAL_HANDLE_CONTRACT: Any = None
_ORIGINAL_SCHEDULE: Any = None
_ORIGINAL_RECORD_OUTCOME: Any = None


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


def _saved_hook(repository: RFDir5Repository, managed_id: int):
    try:
        with repository.database.session() as session:
            return virtual_hook_settings_from_session(session, int(managed_id))
    except Exception:
        return None


def install_custom_virtual_post_loss_barrier_authority() -> None:
    """Final saved Virtual Hook entry + future-tick barrier authority.

    The Builder's account-scoped Virtual Hook controls both sides of the state
    machine on the server:

      actual losses >= configured threshold -> VIRTUAL_WAITING_FOR_WIN
      configured consecutive virtual wins   -> REAL_RECOVERY_PENDING

    The real contract remains authoritative until settlement completes.  If that
    settlement enters Virtual Hook, every Custom Strategy account scheduler is
    blocked until a strictly later provider tick arrives.  The future tick still
    has to qualify the saved strategy; there is no virtual-only fast path.
    """

    global _INSTALLED, _ORIGINAL_HANDLE_CONTRACT, _ORIGINAL_SCHEDULE, _ORIGINAL_RECORD_OUTCOME
    if _INSTALLED:
        return

    _ORIGINAL_HANDLE_CONTRACT = RFDir5TradingBot.handle_contract_update
    _ORIGINAL_SCHEDULE = direct_runtime._schedule_account_matches
    _ORIGINAL_RECORD_OUTCOME = RFDir5Repository.record_account_outcome

    def record_outcome_with_saved_virtual_hook(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        original = _ORIGINAL_RECORD_OUTCOME
        if original is None:
            raise RuntimeError("Outcome recorder is unavailable")

        try:
            managed_id = int(kwargs.get("managed_account_id"))
        except (TypeError, ValueError):
            return original(self, *args, **kwargs)

        hook = _saved_hook(self, managed_id)
        patched = dict(kwargs)
        if hook is not None:
            patched["virtual_protection_enabled"] = bool(hook.enabled)
            patched["virtual_trigger_actual_losses"] = max(1, int(hook.enter_after_losses))

        result = original(self, *args, **patched)
        if hook is None or not hook.enabled:
            return result

        # The historic repository guard never entered virtual before two losses.
        # Respect an explicitly saved Custom Strategy threshold of one as well, and
        # repair any wrapper that accidentally advanced the mode too early/late.
        try:
            profit = float(kwargs.get("profit") or 0.0)
        except (TypeError, ValueError):
            profit = 0.0
        if profit > 0:
            return result

        changed = False
        with self.database.session() as session:
            state = session.get(AccountRiskState, managed_id, with_for_update=True)
            if state is None:
                return result
            threshold = max(1, int(hook.enter_after_losses))
            losses = int(state.consecutive_losses or 0)
            debt = float(state.recovery_loss_debt or 0.0)
            if losses >= threshold and debt >= 0.01:
                if state.protection_mode != VIRTUAL_WAITING_FOR_WIN:
                    state.protection_mode = VIRTUAL_WAITING_FOR_WIN
                    state.entered_virtual_mode_at = utc_now()
                    state.virtual_win_count = 0
                    state.current_virtual_loss_streak = 0
                    changed = True
                state.recovery_pending = True
                state.recovery_pending_since = state.recovery_pending_since or utc_now()
                state.updated_at = utc_now()

                result.update(
                    {
                        "protection_mode": "VIRTUAL_MODE",
                        "raw_protection_state": VIRTUAL_WAITING_FOR_WIN,
                        "recovery_pending": True,
                        "virtual_hook_enabled": True,
                        "virtual_hook_enter_after_losses": threshold,
                        "virtual_hook_exit_after_consecutive_wins": max(
                            1, int(hook.exit_after_consecutive_wins)
                        ),
                    }
                )

        if changed:
            self.base.audit(
                "CUSTOM_VIRTUAL_HOOK_ENTERED",
                "worker",
                "settlement",
                {
                    "managed_account_id": managed_id,
                    "enter_after_losses": max(1, int(hook.enter_after_losses)),
                    "exit_after_consecutive_wins": max(
                        1, int(hook.exit_after_consecutive_wins)
                    ),
                    "financial_purchase": False,
                },
            )
        return result

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
            "virtual_fast_path=false saved_hook_authoritative=true",
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

    RFDir5Repository.record_account_outcome = record_outcome_with_saved_virtual_hook  # type: ignore[method-assign]
    RFDir5TradingBot.handle_contract_update = handle_contract_then_arm_virtual_barrier  # type: ignore[method-assign]
    direct_runtime._schedule_account_matches = schedule_only_after_future_post_loss_tick
    RFDir5TradingBot._custom_virtual_post_loss_barrier_authority_installed = True
    RFDir5TradingBot._custom_virtual_entry_policy = "saved_hook_real_loss_threshold_then_future_qualified_tick"
    _INSTALLED = True
