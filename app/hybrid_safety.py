from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy import select

import app.hybrid_digit_put as hybrid
import app.hybrid_recent_digit_bias as recent
import app.hybrid_runtime_config as runtime
from enhanced_bot import optional_float
from app.model_accounting import CANONICAL_BASE_STAKE, canonical_fixed_profit
from app.models import AccountRiskState, CandidateSignalRecord, SystemModelTrade, Trade, utc_now
from app.recovery import calculate_recovery_stake
from app.repositories.rf_dir5_repository import RFDir5Repository, StakePlan
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot

HYBRID_V4_VERSION = "HYBRID-OVER2-PUT-RECOVERY-V4"
HYBRID_V4_TRIGGER = "OVER2-PUT-V4"
HYBRID_V4_STATE_KEY = "hybrid_over2_put_v4:state"
HYBRID_V4_ACCOUNT_EPOCH_PREFIX = "hybrid_over2_put_v4:account_epoch:"
HYBRID_V4_RUN_ID = "hybrid_over2_put_v4"
# Compatibility names retained for deployment/preflight scripts written for V3.
HYBRID_V3_VERSION = HYBRID_V4_VERSION
HYBRID_V3_TRIGGER = HYBRID_V4_TRIGGER
HYBRID_V3_STATE_KEY = HYBRID_V4_STATE_KEY
HYBRID_V3_ACCOUNT_EPOCH_PREFIX = HYBRID_V4_ACCOUNT_EPOCH_PREFIX
HYBRID_V3_RUN_ID = HYBRID_V4_RUN_ID
MAX_RECOVERY_BALANCE_FRACTION = 0.10

_ACCOUNTING_INSTALLED = False
_WORKER_INSTALLED = False


def _repair_one_canonical_row(
    repository: Test2Repository,
    contract_id: str,
) -> None:
    """Repair canonical P/L only after the canonical model outcome already exists.

    A copied account is never allowed to define the canonical outcome. If the copier
    settles first, this function intentionally leaves the open SystemModelTrade alone;
    the tick/model settlement path will later set both outcome and canonical P/L.
    """
    with repository.database.session() as session:
        actual = session.scalar(select(Trade).where(Trade.contract_id == str(contract_id)))
        if actual is None or not actual.signal_id:
            return
        row = session.scalar(
            select(SystemModelTrade)
            .where(
                SystemModelTrade.signal_id == actual.signal_id,
                SystemModelTrade.run_id == repository.run_id,
            )
            .with_for_update()
        )
        if row is None:
            return
        outcome = str(row.outcome or "").upper()
        if outcome not in {"WIN", "LOSS"}:
            # Canonical tick/model settlement has not happened yet. Never infer the
            # model result from one copier's provider settlement timing/outcome.
            return
        row.reference_base_stake = CANONICAL_BASE_STAKE
        row.fixed_stake_profit = canonical_fixed_profit(
            outcome,
            float(row.expected_profit_ratio or 0.0),
        )
        if row.settlement_timestamp is None:
            row.settlement_timestamp = utc_now()


def _primary_digit_signal(repository: Test2Repository, signal_id: str) -> bool:
    if not signal_id:
        return False
    with repository.database.session() as session:
        candidate = session.get(CandidateSignalRecord, str(signal_id))
        if candidate is None:
            return False
        contract_type = str(candidate.contract_type or "").upper()
        if contract_type not in {"DIGITOVER", "DIGITUNDER"}:
            return False
        if str(candidate.run_id or "") != HYBRID_V4_RUN_ID:
            return False
        trigger_name = str(candidate.trigger_name or "")
        strategy_version = str(getattr(candidate, "strategy_version", "") or "")
        return (
            trigger_name == HYBRID_V4_TRIGGER
            or strategy_version == HYBRID_V4_VERSION
            or trigger_name.startswith("OVER2")
        )


def _contract_terminal_outcome(contract: dict[str, Any]) -> str:
    status = str(contract.get("status") or "").strip().lower()
    if status == "won":
        return "win"
    if status == "lost":
        return "loss"
    profit = optional_float(contract.get("profit"))
    if profit is None:
        return ""
    return "win" if profit > 0 else "loss"


def _signal_id_for_contract(repository: Test2Repository, contract_id: int | str) -> str:
    with repository.database.session() as session:
        trade = session.scalar(select(Trade).where(Trade.contract_id == str(contract_id)))
        return str(trade.signal_id or "") if trade is not None else ""


def install_hybrid_accounting_integrity() -> None:
    """Prevent copier settlements/simulations from corrupting canonical model P/L."""
    global _ACCOUNTING_INSTALLED
    if _ACCOUNTING_INSTALLED:
        return

    original_settle_trade = Test2Repository.settle_trade
    original_system_model_trades = Test2Repository.system_model_trades
    original_system_performance_summary = Test2Repository.system_performance_summary

    def settle_trade_coherent(self: Test2Repository, *args: Any, **kwargs: Any) -> bool:
        settled = original_settle_trade(self, *args, **kwargs)
        if settled:
            contract_id = str(kwargs.get("contract_id") or (args[0] if args else ""))
            _repair_one_canonical_row(self, contract_id)
        return settled

    def system_model_trades_coherent(
        self: Test2Repository,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        rows = original_system_model_trades(self, **kwargs)
        settled_rows: list[dict[str, Any]] = []
        for row in rows:
            outcome = str(row.get("outcome") or "").upper()
            if outcome not in {"WIN", "LOSS"}:
                continue
            row["reference_base_stake"] = CANONICAL_BASE_STAKE
            row["fixed_stake_profit"] = canonical_fixed_profit(
                outcome,
                float(row.get("expected_profit_ratio") or 0.0),
            )
            if row.get("execution_source") != "viewer_actual":
                row["execution_source"] = "canonical_model"
            settled_rows.append(row)
        return settled_rows

    def coherent_performance_summary(
        self: Test2Repository,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = original_system_performance_summary(self, **kwargs)
        trades = kwargs.get("trades")
        if trades is None:
            trades = self.system_model_trades(
                start=kwargs["start"],
                end=kwargs["end"],
                include_virtual=False,
                viewer_managed_account_id=kwargs.get("viewer_managed_account_id"),
            )
        base = min(1000.0, max(CANONICAL_BASE_STAKE, float(
            kwargs.get("simulated_base_stake", CANONICAL_BASE_STAKE)
        )))
        ordered = sorted(
            [row for row in trades if str(row.get("outcome") or "").upper() in {"WIN", "LOSS"}],
            key=lambda row: str(row.get("settlement_timestamp") or row.get("signal_timestamp") or ""),
        )
        fixed_profit = 0.0
        martingale_profit = 0.0
        current_fixed_drawdown = 0.0
        current_martingale_drawdown = 0.0
        fixed_peak = 0.0
        martingale_peak = 0.0
        max_fixed_drawdown = 0.0
        max_martingale_drawdown = 0.0
        recovery_debt = 0.0
        primary_loss_streak = 0
        maximum_martingale_stake = base
        total_martingale_staked = 0.0
        for trade in ordered:
            outcome = str(trade.get("outcome") or "").upper()
            ratio = max(0.0, float(trade.get("expected_profit_ratio") or 0.0))
            fixed_stake = base
            fixed_pnl = ratio * fixed_stake if outcome == "WIN" else -fixed_stake
            contract_type = str(trade.get("contract_type") or "").upper()
            if contract_type == "PUT" and recovery_debt > 0.009:
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
            fixed_profit += fixed_pnl
            martingale_profit += martingale_pnl
            total_martingale_staked += martingale_stake
            maximum_martingale_stake = max(maximum_martingale_stake, martingale_stake)
            fixed_peak = max(fixed_peak, fixed_profit)
            martingale_peak = max(martingale_peak, martingale_profit)
            current_fixed_drawdown = fixed_peak - fixed_profit
            current_martingale_drawdown = martingale_peak - martingale_profit
            max_fixed_drawdown = max(max_fixed_drawdown, current_fixed_drawdown)
            max_martingale_drawdown = max(max_martingale_drawdown, current_martingale_drawdown)
            if contract_type == "DIGITOVER":
                if outcome == "LOSS":
                    primary_loss_streak += 1
                    if primary_loss_streak >= 2:
                        recovery_debt = round(recovery_debt + fixed_stake, 2)
                else:
                    primary_loss_streak = 0
                    recovery_debt = 0.0
            elif contract_type == "PUT":
                if outcome == "WIN":
                    recovery_debt = max(0.0, round(recovery_debt - martingale_pnl, 2))
                    if recovery_debt <= 0.009:
                        primary_loss_streak = 0
                else:
                    recovery_debt = round(recovery_debt + martingale_stake, 2)

        total = len(ordered)
        wins = sum(str(row.get("outcome") or "").upper() == "WIN" for row in ordered)
        losses = total - wins
        # Keep the canonical outcome/streak fields from the settled model rows,
        # but ensure all monetary fields describe this exact replay.
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
                "simulated_max_drawdown_martingale_pct": round(
                    max_martingale_drawdown / total_martingale_staked * 100.0, 2
                ) if total_martingale_staked else 0.0,
                "simulated_current_drawdown_martingale_pct": round(
                    current_martingale_drawdown / total_martingale_staked * 100.0, 2
                ) if total_martingale_staked else 0.0,
                "recovery_simulation_policy": "two_primary_losses_one_put_recovery",
            }
        )
        return result

    Test2Repository.settle_trade = settle_trade_coherent
    Test2Repository.system_model_trades = system_model_trades_coherent
    Test2Repository.system_performance_summary = coherent_performance_summary
    _ACCOUNTING_INSTALLED = True


def _fixed_canonical_settlement(bot: RFDir5TradingBot, payload: dict[str, Any]) -> None:
    bot.logger.info(
        "HYBRID_CANONICAL_SETTLED signal_id=%s contract_type=%s outcome=%s "
        "account_state=per_account",
        payload.get("signal_id", ""),
        payload.get("contract_type", ""),
        payload.get("outcome", ""),
    )


def _assert_runtime_invariants(bot: RFDir5TradingBot) -> None:
    cfg = bot.test2_config.hybrid_strategy
    risk = bot.risk_config
    virtual = bot.virtual_config

    failures: list[str] = []
    if str(bot.test2_config.model.run_id) != HYBRID_V4_RUN_ID:
        failures.append(f"run_id={bot.test2_config.model.run_id}")
    if str(cfg.version) != HYBRID_V4_VERSION:
        failures.append(f"hybrid_version={cfg.version}")
    if int(getattr(cfg, "recent_window", 0)) != 20:
        failures.append("recent_window_must_be_20")
    if len(HYBRID_V3_TRIGGER) > 30:
        failures.append("ledger_trigger_exceeds_varchar30")
    if (
        float(risk.maximum_recovery_balance_fraction)
        > MAX_RECOVERY_BALANCE_FRACTION + 1e-9
    ):
        failures.append(
            f"maximum_recovery_balance_fraction={risk.maximum_recovery_balance_fraction}"
        )
    if int(virtual.trigger_actual_losses) != 2:
        failures.append(f"virtual_trigger_actual_losses={virtual.trigger_actual_losses}")
    if int(virtual.exit_after_wins) != 2:
        failures.append(f"virtual_exit_after_wins={virtual.exit_after_wins}")
    if tuple(cfg.primary_markets) != ("1HZ100V",):
        failures.append(f"primary_markets={cfg.primary_markets}")
    if tuple(cfg.recovery_markets) != ("1HZ100V",):
        failures.append(f"recovery_markets={cfg.recovery_markets}")
    if str(cfg.primary_contract_type).upper() != "DIGITOVER" or int(cfg.primary_barrier) != 2:
        failures.append("primary_contract_must_be_DIGITOVER_2")
    if int(risk.recovery_trigger_losses) != 2:
        failures.append(f"recovery_trigger_losses={risk.recovery_trigger_losses}")
    if hybrid.HYBRID_STATE_KEY != HYBRID_V4_STATE_KEY:
        failures.append(f"state_key={hybrid.HYBRID_STATE_KEY}")
    if hybrid.ACCOUNT_EPOCH_PREFIX != HYBRID_V3_ACCOUNT_EPOCH_PREFIX:
        failures.append(f"account_epoch_prefix={hybrid.ACCOUNT_EPOCH_PREFIX}")
    if failures:
        raise RuntimeError("HYBRID_SAFETY_INVARIANT_FAILED: " + "; ".join(failures))


def install_hybrid_worker_safety() -> None:
    """Install V4 invariants and keep recovery transitions account-scoped."""
    global _WORKER_INSTALLED
    if _WORKER_INSTALLED:
        return

    install_hybrid_accounting_integrity()

    # New epoch: old global O2/U7 runtime state can never be silently inherited.
    hybrid.HYBRID_STATE_KEY = HYBRID_V4_STATE_KEY
    hybrid.ACCOUNT_EPOCH_PREFIX = HYBRID_V4_ACCOUNT_EPOCH_PREFIX
    hybrid._apply_canonical_settlement = _fixed_canonical_settlement

    recent.STRATEGY_VERSION = HYBRID_V4_VERSION
    recent.LEDGER_TRIGGER_NAME = HYBRID_V4_TRIGGER

    runtime.HYBRID_RUNTIME_CONFIG = replace(
        runtime.HYBRID_RUNTIME_CONFIG,
        version=HYBRID_V4_VERSION,
        primary_markets=("1HZ100V",),
        recovery_markets=("1HZ100V",),
        primary_contract_type="DIGITOVER",
        primary_barrier=2,
    )

    original_handle_contract_update = RFDir5TradingBot.handle_contract_update

    async def recovery_safe_handle_contract_update(
        self: RFDir5TradingBot,
        token: str,
        contract_id: int,
        contract: dict[str, Any],
    ) -> None:
        terminal = self._contract_is_terminal(contract)
        await original_handle_contract_update(self, token, contract_id, contract)
        if terminal:
            self.logger.info(
                "ACCOUNT_CONTRACT_SETTLED account_state=per_account contract_id=%s",
                contract_id,
            )

    RFDir5TradingBot.handle_contract_update = recovery_safe_handle_contract_update

    original_init = RFDir5TradingBot.__init__

    def safe_init(self: RFDir5TradingBot, config_path: str | None = None) -> None:
        original_init(self, config_path)
        _assert_runtime_invariants(self)
        self.logger.warning(
            "HYBRID_SAFETY_ACTIVE version=%s recovery_stake_policy=%s "
            "two_losses_then_two_virtual_wins one_put_per_cycle global_recovery_stop=false "
            "max_recovery_balance_fraction=%.2f",
            HYBRID_V4_VERSION,
            "martingale_or_flat_base",
            float(self.risk_config.maximum_recovery_balance_fraction),
        )

    RFDir5TradingBot.__init__ = safe_init
    _WORKER_INSTALLED = True
