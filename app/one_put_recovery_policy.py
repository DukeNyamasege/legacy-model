from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

import app.hybrid_safety as hybrid_safety
from app.models import AccountRiskState, VirtualTrade, utc_now
from app.recovery import calculate_recovery_stake, ceil_cents
from app.repositories.rf_dir5_repository import (
    NORMAL_MODE,
    REAL_RECOVERY_PENDING,
    RFDir5Repository,
    StakePlan,
    VIRTUAL_LOSS,
    VIRTUAL_STALE,
    VIRTUAL_TRADE,
    VIRTUAL_WAITING_FOR_WIN,
    VIRTUAL_WIN,
)
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy.over2_strategy import TEST2_SYMBOLS

_INSTALLED = False


def _reset_virtual_counters(state: AccountRiskState) -> None:
    state.protection_mode = NORMAL_MODE
    state.entered_virtual_mode_at = None
    state.virtual_observation_count = 0
    state.virtual_win_count = 0
    state.virtual_loss_count = 0
    state.current_virtual_loss_streak = 0


def _mode_for_state(state: AccountRiskState | None) -> str:
    if state is None:
        return NORMAL_MODE
    if state.protection_mode == REAL_RECOVERY_PENDING:
        return "RECOVERY_PENDING"
    return NORMAL_MODE


def _normalize_stale_virtual_state(state: AccountRiskState) -> None:
    if state.protection_mode == VIRTUAL_WAITING_FOR_WIN:
        if float(state.recovery_loss_debt or 0.0) > 0.009:
            state.protection_mode = REAL_RECOVERY_PENDING
            state.recovery_pending = True
            if state.recovery_pending_since is None:
                state.recovery_pending_since = utc_now()
        else:
            _reset_virtual_counters(state)
            state.recovery_pending = False
            state.recovery_attempt_active = False


def _one_put_runtime_invariants(bot: RFDir5TradingBot) -> None:
    cfg = bot.test2_config.hybrid_strategy
    risk = bot.risk_config
    virtual = bot.virtual_config
    failures: list[str] = []
    if str(bot.test2_config.model.run_id) != hybrid_safety.HYBRID_V4_RUN_ID:
        failures.append(f"run_id={bot.test2_config.model.run_id}")
    if str(cfg.version) != hybrid_safety.HYBRID_V4_VERSION:
        failures.append(f"hybrid_version={cfg.version}")
    if int(getattr(cfg, "recent_window", 0)) != 20:
        failures.append("recent_window_must_be_20")
    if tuple(cfg.primary_markets) != tuple(TEST2_SYMBOLS):
        failures.append(f"primary_markets={cfg.primary_markets}")
    if tuple(cfg.recovery_markets) != tuple(TEST2_SYMBOLS):
        failures.append(f"recovery_markets={cfg.recovery_markets}")
    if str(cfg.primary_contract_type).upper() != "DIGITOVER" or int(cfg.primary_barrier) != 2:
        failures.append("primary_contract_must_be_DIGITOVER_2")
    if int(risk.recovery_trigger_losses) != 1:
        failures.append(f"recovery_trigger_losses={risk.recovery_trigger_losses}")
    if bool(getattr(virtual, "enabled", False)):
        failures.append("virtual_protection_must_be_disabled_for_one_put_recovery")
    if failures:
        raise RuntimeError("ONE_PUT_RECOVERY_INVARIANT_FAILED: " + "; ".join(failures))


def _record_account_outcome_one_put(
    self: RFDir5Repository,
    *,
    managed_account_id: int,
    account_id_masked: str = "",
    profit: float,
    current_balance: float,
    recovery_enabled: bool = True,
    recovery_trigger_losses: int = 1,
    virtual_protection_enabled: bool = False,
    virtual_trigger_actual_losses: int = 1,
) -> dict[str, Any]:
    """One primary loss arms PUT; one real PUT win exits recovery.

    Every monetary loss is added to recovery debt once. A winning recovery PUT
    clears the whole cycle and returns the account to OVER-2, preventing residual
    cents or previously recovered losses from being chased again.
    """

    del virtual_protection_enabled, virtual_trigger_actual_losses
    today = datetime.now(timezone.utc).date().isoformat()
    with self.database.session() as session:
        state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
        if state is None:
            state = AccountRiskState(
                managed_account_id=int(managed_account_id),
                account_id_masked=str(account_id_masked or ""),
                trading_day=today,
                daily_start_balance=max(0.0, float(current_balance) - float(profit)),
                session_profit=0.0,
                consecutive_losses=0,
                recovery_loss_debt=0.0,
                recovery_pending=False,
                recovery_attempt_active=False,
                equity_high_water=max(0.0, float(current_balance)),
                protection_mode=NORMAL_MODE,
            )
            session.add(state)
        elif account_id_masked and state.account_id_masked != account_id_masked:
            state.account_id_masked = str(account_id_masked)

        if state.trading_day != today:
            state.trading_day = today
            state.daily_start_balance = max(0.0, float(current_balance) - float(profit))
            state.session_profit = 0.0
            state.consecutive_losses = 0
            state.recovery_loss_debt = 0.0
            state.recovery_pending = False
            state.recovery_attempt_active = False
            state.recovery_pending_since = None
            _reset_virtual_counters(state)

        _normalize_stale_virtual_state(state)
        previous_mode = state.protection_mode
        was_recovery = bool(state.recovery_attempt_active or state.protection_mode == REAL_RECOVERY_PENDING)
        state.session_profit += float(profit)
        state.recovery_attempt_active = False

        if float(profit) <= 0:
            loss_amount = round(abs(float(profit)), 2)
            if was_recovery:
                # A failed PUT is a new real monetary loss. Add it once and keep
                # trying PUT until a single real PUT win closes the cycle.
                state.consecutive_losses = max(1, int(state.consecutive_losses or 0) + 1)
                state.recovery_loss_debt = round(float(state.recovery_loss_debt or 0.0) + loss_amount, 2)
            else:
                # First OVER-2 loss enters recovery immediately.
                state.consecutive_losses = int(state.consecutive_losses or 0) + 1
                state.recovery_loss_debt = round(float(state.recovery_loss_debt or 0.0) + loss_amount, 2)
            state.recovery_pending = bool(recovery_enabled and state.recovery_loss_debt > 0.009)
            if state.recovery_pending:
                state.protection_mode = REAL_RECOVERY_PENDING
                if state.recovery_pending_since is None:
                    state.recovery_pending_since = utc_now()
            else:
                _reset_virtual_counters(state)
        else:
            if was_recovery:
                # One winning PUT is the end of this recovery cycle. Do not chase
                # residual debt from quote rounding or already recovered losses.
                state.recovery_loss_debt = 0.0
                state.recovery_pending = False
                state.recovery_pending_since = None
                state.recovery_attempt_active = False
                state.consecutive_losses = 0
                _reset_virtual_counters(state)
            else:
                # Normal OVER-2 win clears any provisional primary loss marker.
                state.recovery_loss_debt = 0.0
                state.recovery_pending = False
                state.recovery_pending_since = None
                state.recovery_attempt_active = False
                state.consecutive_losses = 0
                _reset_virtual_counters(state)

        state.equity_high_water = max(float(state.equity_high_water or 0.0), float(current_balance))
        state.updated_at = utc_now()
        return {
            "session_profit": state.session_profit,
            "consecutive_losses": state.consecutive_losses,
            "recovery_loss_debt": state.recovery_loss_debt,
            "recovery_pending": state.recovery_pending,
            "recovery_attempt_active": state.recovery_attempt_active,
            "settled_recovery_attempt": was_recovery,
            "daily_start_balance": state.daily_start_balance,
            "equity_high_water": state.equity_high_water,
            "protection_mode": _mode_for_state(state),
            "raw_protection_state": state.protection_mode,
            "protection_state_changed": previous_mode != state.protection_mode,
        }


def _plan_stake_one_put(
    original_plan_stake,
):
    def wrapped(
        self: RFDir5Repository,
        *,
        managed_account_id: int,
        account_id_masked: str = "",
        current_balance: float,
        requested_stake: float,
        proposal_profit_ratio: float,
        recovery_enabled: bool,
        recovery_trigger_losses: int,
        minimum_stake: float,
        virtual_protection_enabled: bool = True,
        maximum_recovery_balance_fraction: float = 0.10,
        minimum_balance_reserve: float = 0.50,
    ) -> StakePlan:
        with self.database.session() as session:
            state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
            if state is not None:
                _normalize_stale_virtual_state(state)
                state.updated_at = utc_now()

        plan = original_plan_stake(
            self,
            managed_account_id=managed_account_id,
            account_id_masked=account_id_masked,
            current_balance=current_balance,
            requested_stake=requested_stake,
            proposal_profit_ratio=proposal_profit_ratio,
            recovery_enabled=recovery_enabled,
            recovery_trigger_losses=1,
            minimum_stake=minimum_stake,
            virtual_protection_enabled=False,
            maximum_recovery_balance_fraction=maximum_recovery_balance_fraction,
            minimum_balance_reserve=minimum_balance_reserve,
        )
        if plan.reason and "virtual protection" in plan.reason.lower():
            # Fail-safe: virtual mode is no longer part of the production policy.
            base_stake = ceil_cents(max(float(minimum_stake), float(requested_stake)))
            with self.database.session() as session:
                state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
                debt = float(state.recovery_loss_debt or 0.0) if state is not None else 0.0
            return StakePlan(base_stake, "one-PUT recovery policy bypassed stale virtual guard", is_recovery=debt > 0.009, recovery_debt=debt)
        return plan

    return wrapped


def _start_virtual_trade_disabled(
    self: RFDir5Repository,
    **kwargs: Any,
) -> dict[str, Any] | None:
    return None


def _settle_due_virtual_trades_disabled(
    self: RFDir5Repository,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    # Close old open virtual rows as stale so they stop appearing as active
    # recovery blockers. Historical virtual results remain visible for audit.
    with self.database.session() as session:
        rows = session.scalars(
            select(VirtualTrade).where(
                VirtualTrade.run_id == self.run_id,
                VirtualTrade.result == "OPEN",
            )
        ).all()
        now = utc_now()
        for row in rows:
            row.result = VIRTUAL_STALE
            row.reason = "Virtual protection disabled by one-PUT recovery policy"
            row.amount_charged = 0.0
            row.actual_profit_loss = 0.0
            row.actual_payout = 0.0
            row.recovery_debt_change = 0.0
            row.settled_at = now
    return []


def _one_put_system_performance_summary(original_summary):
    def wrapped(self: Test2Repository, **kwargs: Any) -> dict[str, Any]:
        result = original_summary(self, **kwargs)
        trades = kwargs.get("trades")
        if trades is None:
            trades = self.system_model_trades(
                start=kwargs["start"],
                end=kwargs["end"],
                include_virtual=False,
                viewer_managed_account_id=kwargs.get("viewer_managed_account_id"),
            )
        base = min(1000.0, max(0.50, float(kwargs.get("simulated_base_stake", 0.50))))
        ordered = sorted(
            [row for row in trades if str(row.get("outcome") or "").upper() in {"WIN", "LOSS"}],
            key=lambda row: str(row.get("settlement_timestamp") or row.get("signal_timestamp") or ""),
        )
        fixed_profit = 0.0
        martingale_profit = 0.0
        fixed_peak = 0.0
        martingale_peak = 0.0
        max_fixed_drawdown = 0.0
        max_martingale_drawdown = 0.0
        current_fixed_drawdown = 0.0
        current_martingale_drawdown = 0.0
        total_martingale_staked = 0.0
        maximum_martingale_stake = base
        recovery_debt = 0.0
        in_recovery = False

        for trade in ordered:
            outcome = str(trade.get("outcome") or "").upper()
            contract_type = str(trade.get("contract_type") or "").upper()
            ratio = max(0.0, float(trade.get("expected_profit_ratio") or 0.0))
            actual_stake = float(trade.get("actual_stake") or trade.get("martingale_stake") or 0.0)
            actual_profit = trade.get("actual_profit")
            fixed_pnl = ratio * base if outcome == "WIN" else -base
            fixed_profit += fixed_pnl

            if actual_profit is not None and actual_stake > 0:
                martingale_stake = actual_stake
                martingale_pnl = float(actual_profit or 0.0)
            else:
                if in_recovery or contract_type == "PUT":
                    calculation = calculate_recovery_stake(
                        base_stake=base,
                        recovery_debt=recovery_debt,
                        pre_trade_profit_ratio=ratio,
                        minimum_stake=base,
                    )
                    martingale_stake = float(calculation.requested_stake)
                else:
                    martingale_stake = base
                martingale_pnl = ratio * martingale_stake if outcome == "WIN" else -martingale_stake
            martingale_profit += martingale_pnl
            total_martingale_staked += martingale_stake
            maximum_martingale_stake = max(maximum_martingale_stake, martingale_stake)

            if contract_type == "DIGITOVER":
                if outcome == "LOSS":
                    recovery_debt = round(recovery_debt + abs(martingale_pnl), 2)
                    in_recovery = True
                else:
                    recovery_debt = 0.0
                    in_recovery = False
            elif contract_type == "PUT":
                if outcome == "WIN":
                    recovery_debt = 0.0
                    in_recovery = False
                else:
                    recovery_debt = round(recovery_debt + abs(martingale_pnl), 2)
                    in_recovery = True

            fixed_peak = max(fixed_peak, fixed_profit)
            martingale_peak = max(martingale_peak, martingale_profit)
            current_fixed_drawdown = fixed_peak - fixed_profit
            current_martingale_drawdown = martingale_peak - martingale_profit
            max_fixed_drawdown = max(max_fixed_drawdown, current_fixed_drawdown)
            max_martingale_drawdown = max(max_martingale_drawdown, current_martingale_drawdown)

        total = len(ordered)
        wins = sum(str(row.get("outcome") or "").upper() == "WIN" for row in ordered)
        losses = total - wins
        result.update(
            {
                "total_trades": total,
                "wins": wins,
                "losses": losses,
                "win_rate": wins / total if total else 0.0,
                "fixed_pnl": round(fixed_profit, 2),
                "martingale_pnl": round(martingale_profit, 2),
                "observed_martingale_pnl": round(martingale_profit, 2),
                "simulated_martingale_pnl": round(martingale_profit, 2),
                "maximum_martingale_stake": round(maximum_martingale_stake, 2),
                "observed_maximum_stake": round(maximum_martingale_stake, 2),
                "simulated_maximum_martingale_stake": round(maximum_martingale_stake, 2),
                "max_drawdown_fixed": round(max_fixed_drawdown, 2),
                "max_drawdown_martingale": round(max_martingale_drawdown, 2),
                "simulated_max_drawdown_martingale": round(max_martingale_drawdown, 2),
                "current_drawdown_fixed": round(current_fixed_drawdown, 2),
                "current_drawdown_martingale": round(current_martingale_drawdown, 2),
                "simulated_current_drawdown_martingale": round(current_martingale_drawdown, 2),
                "simulated_max_drawdown_martingale_pct": round(max_martingale_drawdown / total_martingale_staked * 100.0, 2) if total_martingale_staked else 0.0,
                "simulated_current_drawdown_martingale_pct": round(current_martingale_drawdown / total_martingale_staked * 100.0, 2) if total_martingale_staked else 0.0,
                "recovery_simulation_policy": "one_over2_loss_repeat_put_until_one_win",
            }
        )
        return result

    return wrapped


def install_one_put_recovery_policy() -> None:
    """Install the user's production recovery rule.

    OVER-2 is the only primary mode. The first real OVER-2 loss arms PUT. PUT
    repeats on real losses, and the first real PUT win clears the full recovery
    cycle and returns to OVER-2.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    hybrid_safety._assert_runtime_invariants = _one_put_runtime_invariants
    RFDir5Repository.record_account_outcome = _record_account_outcome_one_put
    RFDir5Repository.plan_stake = _plan_stake_one_put(RFDir5Repository.plan_stake)
    RFDir5Repository.start_virtual_trade = _start_virtual_trade_disabled
    RFDir5Repository.settle_due_virtual_trades = _settle_due_virtual_trades_disabled
    Test2Repository.system_performance_summary = _one_put_system_performance_summary(Test2Repository.system_performance_summary)
    RFDir5TradingBot._one_put_recovery_policy_installed = True
    _INSTALLED = True
