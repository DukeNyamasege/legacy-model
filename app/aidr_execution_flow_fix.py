from __future__ import annotations

import time
from typing import Any

from app.repositories.rf_dir5_repository import NORMAL_MODE, RECOVERY_PENDING, VIRTUAL_MODE
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import mask_account_id

_INSTALLED = False


def _is_aidr_digit_signal(signal: Any) -> bool:
    contract_type = str(getattr(signal, "contract_type", "") or "").upper()
    barrier = str(getattr(signal, "barrier", "") or "").strip()
    trigger = str(getattr(signal, "trigger_name", "") or "").upper()
    direction = str(getattr(signal, "direction", "") or "").upper()
    return (
        contract_type == "DIGITOVER"
        and barrier in {"1", "3", "4"}
        and (trigger.startswith("AIDR-") or direction in {"OVER_1", "OVER_3", "OVER_4"})
    )


def _is_virtual_recovery_signal(signal: Any) -> bool:
    barrier = str(getattr(signal, "barrier", "") or "").strip()
    trigger = str(getattr(signal, "trigger_name", "") or "").upper()
    direction = str(getattr(signal, "direction", "") or "").upper()
    return barrier == "4" or trigger == "AIDR-O4-V2" or direction == "OVER_4"


def _configured_stake(bot: RFDir5TradingBot, token: str, account_id: str, managed_id: int) -> float:
    try:
        profile = bot._managed_account_profile(int(managed_id))
    except Exception:
        profile = {}
    try:
        state = bot._client_state_for_token(token, account_id=account_id)
    except Exception:
        state = {}
    return max(
        0.35,
        float(
            profile.get("stake_amount")
            or state.get("base_stake")
            or getattr(bot, "base_stake", 0.50)
            or 0.50
        ),
    )


def _mark_virtual_signal(
    bot: RFDir5TradingBot,
    signal: Any,
    *,
    opened: list[dict[str, Any]],
    waiting: set[str],
) -> None:
    try:
        bot.repository.consume_signal(signal.signal_id)
    except Exception:
        pass
    signal.consumed = True
    if opened:
        bot.repository.mark_signal(
            signal.signal_id,
            status="VIRTUAL_TRADE",
            purchase_requested=False,
            expected_account_masks=[str(item.get("account") or "") for item in opened],
            registered_account_masks=[],
        )
        bot.rf_repository.set_signal_decision(
            signal.signal_id,
            "VIRTUAL_TRADE",
            "AIDR_VIRTUAL_OVER4_NO_PURCHASE",
            selected=True,
            validated_edge=getattr(signal, "validated_edge", None),
        )
    elif waiting:
        bot.repository.mark_signal(
            signal.signal_id,
            status="VIRTUAL_WAITING_SETTLEMENT",
            expected_account_masks=sorted(waiting),
            registered_account_masks=[],
        )
        bot.rf_repository.set_signal_decision(
            signal.signal_id,
            "VIRTUAL_WAITING_SETTLEMENT",
            "ACTIVE_AIDR_VIRTUAL_OBSERVATION",
            selected=True,
            validated_edge=getattr(signal, "validated_edge", None),
        )


def install_aidr_execution_flow_fix() -> None:
    """Install direct AIDR real-recovery and virtual execution routing.

    The legacy RF purchase envelope was written for DIGITOVER primary entries and
    PUT recovery. AIDR uses DIGITOVER 3 for the first real recovery and
    DIGITOVER 4 for $0 virtual confirmation plus one post-virtual full-debt
    recovery. Older code relied on the number of times a dict's ``mode`` key
    happened to be read. This implementation partitions the scoped accounts once:

    * VIRTUAL_MODE accounts open a $0 DIGITOVER 4 observation directly.
    * RECOVERY_PENDING accounts enter the normal purchase envelope with the real
      database recovery state preserved for full-debt stake planning.
    * Normal accounts continue through the shared authenticated purchase path.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_buy_selected = RFDir5TradingBot._buy_selected_accounts

    async def buy_selected_with_direct_aidr_routing(
        self: RFDir5TradingBot,
        signal: Any,
        economics: Any,
    ) -> None:
        if not _is_aidr_digit_signal(signal):
            return await original_buy_selected(self, signal, economics)

        repository = getattr(self, "rf_repository", None)
        if repository is None:
            return await original_buy_selected(self, signal, economics)

        eligible = list(self._eligible_purchase_accounts())
        if not eligible:
            return await original_buy_selected(self, signal, economics)

        real_managed_ids: set[int] = set()
        virtual_opened: list[dict[str, Any]] = []
        virtual_waiting: set[str] = set()
        virtual_recovery_signal = _is_virtual_recovery_signal(signal)

        for token, account_id in eligible:
            managed_id = self._managed_account_id_for_token(token)
            if managed_id is None:
                continue
            managed_id = int(managed_id)
            protection = repository.virtual_protection_for_account(
                managed_account_id=managed_id,
                account_id_masked=mask_account_id(account_id),
            )
            mode = str(protection.get("mode") or NORMAL_MODE)
            if mode != VIRTUAL_MODE:
                real_managed_ids.add(managed_id)
                continue

            masked = mask_account_id(account_id)
            if not virtual_recovery_signal:
                virtual_waiting.add(masked)
                self.logger.info(
                    "AIDR_VIRTUAL_WAITING account=%s reason=requires_over4_signal",
                    masked,
                )
                continue

            configured_stake = _configured_stake(self, token, account_id, managed_id)
            simulated_stake = round(configured_stake, 2)
            expected_payout = None
            if float(getattr(economics, "stake", 0.0) or 0.0) > 0:
                expected_payout = round(
                    (float(economics.payout) / float(economics.stake)) * simulated_stake,
                    2,
                )
            virtual = repository.start_virtual_trade(
                managed_account_id=managed_id,
                account_id_masked=masked,
                signal=signal,
                configured_stake=configured_stake,
                simulated_stake=simulated_stake,
                expected_payout=expected_payout,
            )
            if virtual is None:
                virtual_waiting.add(masked)
                self.logger.info(
                    "VIRTUAL_TRADE_WAITING account=%s signal_id=%s reason=active_virtual_observation",
                    masked,
                    signal.signal_id,
                )
                continue

            virtual_opened.append(virtual)
            self._set_account_execution_status(
                managed_id,
                "virtual_protection",
                "AIDR virtual OVER-4 confirmation is active; no real contract was purchased.",
            )
            self.logger.warning(
                "VIRTUAL_TRADE_OPENED account=%s market=%s contract_type=DIGITOVER barrier=4 "
                "simulated_stake=%.2f expected_payout=%s actual_buy=false actual_financial_impact=0 "
                "recovery_debt=%.2f",
                masked,
                signal.symbol,
                simulated_stake,
                f"{expected_payout:.2f}" if expected_payout is not None else "unavailable",
                float(virtual.get("recovery_debt") or 0.0),
            )

        if virtual_opened:
            self.rf_last_purchase_monotonic = time.monotonic()

        if not real_managed_ids:
            _mark_virtual_signal(
                self,
                signal,
                opened=virtual_opened,
                waiting=virtual_waiting,
            )
            return

        previous_scope = getattr(self, "_aidr_purchase_scope_ids", None)
        original_protection = repository.virtual_protection_for_account

        def recovery_compatible_protection(*args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = dict(original_protection(*args, **kwargs) or {})
            if str(payload.get("mode") or NORMAL_MODE) == RECOVERY_PENDING:
                payload["aidr_mode"] = RECOVERY_PENDING
                payload["mode"] = NORMAL_MODE
                payload["next_action"] = "Next AIDR entry is the account's configured full-debt recovery"
            return payload

        repository.virtual_protection_for_account = recovery_compatible_protection
        self._aidr_purchase_scope_ids = set(real_managed_ids)
        try:
            await original_buy_selected(self, signal, economics)
        finally:
            repository.virtual_protection_for_account = original_protection
            self._aidr_purchase_scope_ids = previous_scope

    RFDir5TradingBot._buy_selected_accounts = buy_selected_with_direct_aidr_routing
    RFDir5TradingBot._aidr_execution_flow_fix_installed = True
    _INSTALLED = True
