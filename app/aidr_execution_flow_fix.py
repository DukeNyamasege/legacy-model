from __future__ import annotations

import time
from typing import Any

from app.ai_digit_recovery_v1 import _read_split_remaining
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


def _required_aidr_action(
    *,
    mode: str,
    split_remaining: int,
    recovery_debt: float,
) -> tuple[str, bool, str]:
    """Return the only barrier and execution kind allowed by account state."""

    normalized = str(mode or NORMAL_MODE)
    if normalized == VIRTUAL_MODE:
        return "4", True, "virtual_over4"
    if normalized == RECOVERY_PENDING or float(recovery_debt or 0.0) > 0.009:
        if int(split_remaining or 0) > 0:
            return "4", False, "real_over4_full_recovery"
        return "3", False, "real_over3_first_recovery"
    return "1", False, "real_over1_normal"


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
        signal_barrier = str(getattr(signal, "barrier", "") or "").strip()
        role_mismatches: set[str] = set()

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
            masked = mask_account_id(account_id)
            expected_barrier, virtual_only, expected_action = _required_aidr_action(
                mode=mode,
                split_remaining=_read_split_remaining(repository.base, managed_id),
                recovery_debt=float(protection.get("actual_recovery_debt") or 0.0),
            )
            if signal_barrier != expected_barrier:
                role_mismatches.add(masked)
                self.logger.warning(
                    "AIDR_ROLE_MISMATCH_BLOCKED account=%s signal_barrier=%s "
                    "required_barrier=%s required_action=%s mode=%s; retrying_with_correct_role=true",
                    masked,
                    signal_barrier or "missing",
                    expected_barrier,
                    expected_action,
                    mode,
                )
                continue
            if not virtual_only:
                real_managed_ids.add(managed_id)
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
            if role_mismatches and not virtual_opened and not virtual_waiting:
                self.repository.mark_signal(
                    signal.signal_id,
                    status="SKIP_AIDR_ROLE_STATE_MISMATCH",
                    expected_account_masks=sorted(role_mismatches),
                    registered_account_masks=[],
                )
                self.rf_repository.set_signal_decision(
                    signal.signal_id,
                    "SKIP_AIDR_ROLE_STATE_MISMATCH",
                    "ACCOUNT_STATE_CHANGED_BEFORE_PURCHASE",
                    selected=False,
                    validated_edge=getattr(signal, "validated_edge", None),
                )
                return
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
