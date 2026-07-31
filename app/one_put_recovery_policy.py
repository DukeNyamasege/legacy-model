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
    RECOVERY_PENDING,
    RFDir5Repository,
    StakePlan,
    VIRTUAL_MODE,
    VIRTUAL_WAITING_FOR_WIN,
)
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy.over2_strategy import TEST2_SYMBOLS

_INSTALLED = False


POLICY_NAME = "over2_put_virtual_guard"
VIRTUAL_PUT_WINS_REQUIRED = 2


def _reset_virtual_counters(state: AccountRiskState) -> None:
    state.protection_mode = NORMAL_MODE
    state.entered_virtual_mode_at = None
    state.virtual_observation_count = 0
    state.virtual_win_count = 0
    state.virtual_loss_count = 0
    state.current_virtual_loss_streak = 0


def _enter_virtual_put_mode(state: AccountRiskState) -> None:
    state.protection_mode = VIRTUAL_WAITING_FOR_WIN
    state.recovery_pending = True
    state.recovery_attempt_active = False
    state.entered_virtual_mode_at = utc_now()
    state.virtual_observation_count = 0
    state.virtual_win_count = 0
    state.virtual_loss_count = 0
    state.current_virtual_loss_streak = 0
    if state.recovery_pending_since is None:
        state.recovery_pending_since = utc_now()


def _mode_for_state(state: AccountRiskState | None) -> str:
    if state is None:
        return NORMAL_MODE
    if state.protection_mode == VIRTUAL_WAITING_FOR_WIN:
        return VIRTUAL_MODE
    if state.protection_mode == REAL_RECOVERY_PENDING:
        return RECOVERY_PENDING
    return NORMAL_MODE


def _normalize_virtual_state(state: AccountRiskState) -> None:
    """Keep virtual mode only when there is debt; never auto-exit it."""

    if state.protection_mode != VIRTUAL_WAITING_FOR_WIN:
        return
    if float(state.recovery_loss_debt or 0.0) <= 0.009:
        _reset_virtual_counters(state)
        state.recovery_pending = False
        state.recovery_attempt_active = False
        state.recovery_pending_since = None


def _virtual_guard_runtime_invariants(bot: RFDir5TradingBot) -> None:
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
    if bool(getattr(virtual, "enabled", False)) is not True:
        failures.append("virtual_protection_must_be_enabled_after_failed_put")
    if int(getattr(virtual, "exit_after_wins", 0)) != VIRTUAL_PUT_WINS_REQUIRED:
        failures.append(f"virtual_exit_after_wins={virtual.exit_after_wins}")
    if failures:
        raise RuntimeError("PUT_VIRTUAL_GUARD_INVARIANT_FAILED: " + "; ".join(failures))


def _record_account_outcome_virtual_guard(
    self: RFDir5Repository,
    *,
    managed_account_id: int,
    account_id_masked: str = "",
    profit: float,
    current_balance: float,
    recovery_enabled: bool = True,
    recovery_trigger_losses: int = 1,
    virtual_protection_enabled: bool = True,
    virtual_trigger_actual_losses: int = 2,
) -> dict[str, Any]:
    del recovery_trigger_losses, virtual_protection_enabled, virtual_trigger_actual_losses
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

        _normalize_virtual_state(state)
        previous_mode = state.protection_mode
        was_recovery = bool(
            state.recovery_attempt_active
            or state.protection_mode == REAL_RECOVERY_PENDING
        )
        state.session_profit += float(profit)
        state.recovery_attempt_active = False

        if float(profit) <= 0:
            loss_amount = round(abs(float(profit)), 2)
            state.consecutive_losses = int(state.consecutive_losses or 0) + 1
            state.recovery_loss_debt = round(
                float(state.recovery_loss_debt or 0.0) + loss_amount,
                2,
            )
            state.recovery_pending = bool(recovery_enabled and state.recovery_loss_debt > 0.009)
            if not state.recovery_pending:
                _reset_virtual_counters(state)
            elif was_recovery:
                # This is the second loss in the cycle: OVER-2 loss first,
                # then the real PUT recovery loss.  From here no real contract
                # is purchased until two consecutive virtual PUT wins appear.
                _enter_virtual_put_mode(state)
            else:
                # First loss came from the primary OVER-2 side.  Arm exactly one
                # real PUT recovery attempt before virtual protection can start.
                state.protection_mode = REAL_RECOVERY_PENDING
                if state.recovery_pending_since is None:
                    state.recovery_pending_since = utc_now()
        else:
            if was_recovery:
                # One winning real PUT completes the cycle, even if tiny residual
                # cents remain in the arithmetic.  Never recover the same loss twice.
                pass
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
            "recovery_policy": POLICY_NAME,
        }


def _wrap_plan_stake(original_plan_stake):
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
                _normalize_virtual_state(state)
                state.updated_at = utc_now()
        return original_plan_stake(
            self,
            managed_account_id=managed_account_id,
            account_id_masked=account_id_masked,
            current_balance=current_balance,
            requested_stake=requested_stake,
            proposal_profit_ratio=proposal_profit_ratio,
            recovery_enabled=recovery_enabled,
            recovery_trigger_losses=1,
            minimum_stake=minimum_stake,
            virtual_protection_enabled=True,
            maximum_recovery_balance_fraction=maximum_recovery_balance_fraction,
            minimum_balance_reserve=minimum_balance_reserve,
        )
    return wrapped


def _wrap_start_virtual_trade(original_start_virtual_trade):
    def wrapped(self: RFDir5Repository, **kwargs: Any) -> dict[str, Any] | None:
        opened = original_start_virtual_trade(self, **kwargs)
        if opened is not None:
            managed_id = int(kwargs.get("managed_account_id"))
            self.base.set_managed_account_execution_status(
                managed_id,
                "virtual_protection",
                (
                    "PUT recovery loss triggered virtual protection. Real contracts are "
                    "skipped until 2 consecutive virtual PUT wins confirm the next real PUT."
                ),
            )
        return opened
    return wrapped


def _wrap_settle_due_virtual_trades(original_settle_due_virtual_trades):
    def wrapped(self: RFDir5Repository, **kwargs: Any) -> list[dict[str, Any]]:
        settled = original_settle_due_virtual_trades(
            self,
            **{**kwargs, "exit_after_wins": VIRTUAL_PUT_WINS_REQUIRED},
        )
        for item in settled:
            account_masked = str(item.get("account") or "")
            if not account_masked:
                continue
            with self.database.session() as session:
                state = session.scalar(
                    select(AccountRiskState).where(
                        AccountRiskState.account_id_masked == account_masked
                    )
                )
                managed_id = int(state.managed_account_id) if state is not None else None
                mode = state.protection_mode if state is not None else NORMAL_MODE
                wins = int(state.virtual_win_count or 0) if state is not None else 0
            if managed_id is None:
                continue
            result = str(item.get("result") or "").replace("_", " ").lower()
            if mode == REAL_RECOVERY_PENDING:
                self.base.set_managed_account_execution_status(
                    managed_id,
                    "recovery_pending",
                    "2 consecutive virtual PUT wins confirmed. The next qualifying PUT will be real.",
                )
            elif mode == VIRTUAL_WAITING_FOR_WIN:
                self.base.set_managed_account_execution_status(
                    managed_id,
                    "virtual_protection",
                    (
                        f"Virtual PUT protection active: latest observation {result}; "
                        f"consecutive virtual PUT wins {wins}/{VIRTUAL_PUT_WINS_REQUIRED}."
                    ),
                )
        return settled
    return wrapped


def _wrap_system_performance_summary(original_summary):
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
        fixed_peak = martingale_peak = 0.0
        max_fixed_drawdown = max_martingale_drawdown = 0.0
        current_fixed_drawdown = current_martingale_drawdown = 0.0
        maximum_martingale_stake = base
        total_martingale_staked = 0.0
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
        result.update({
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
            "recovery_simulation_policy": "over2_loss_real_put_loss_virtual_until_two_put_wins",
        })
        return result
    return wrapped


def install_one_put_recovery_policy() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    hybrid_safety._assert_runtime_invariants = _virtual_guard_runtime_invariants
    original_start_virtual_trade = RFDir5Repository.start_virtual_trade
    original_settle_due_virtual_trades = RFDir5Repository.settle_due_virtual_trades
    RFDir5Repository.record_account_outcome = _record_account_outcome_virtual_guard
    RFDir5Repository.plan_stake = _wrap_plan_stake(RFDir5Repository.plan_stake)
    RFDir5Repository.start_virtual_trade = _wrap_start_virtual_trade(original_start_virtual_trade)
    RFDir5Repository.settle_due_virtual_trades = _wrap_settle_due_virtual_trades(original_settle_due_virtual_trades)
    Test2Repository.system_performance_summary = _wrap_system_performance_summary(Test2Repository.system_performance_summary)
    RFDir5TradingBot._one_put_recovery_policy_installed = True
    _INSTALLED = True
