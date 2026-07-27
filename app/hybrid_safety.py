from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy import select

import app.hybrid_digit_put as hybrid
import app.hybrid_recent_digit_bias as recent
import app.hybrid_runtime_config as runtime
from app.model_accounting import CANONICAL_BASE_STAKE, canonical_fixed_profit
from app.models import AccountRiskState, SystemModelTrade, Trade, utc_now
from app.repositories.rf_dir5_repository import RFDir5Repository, StakePlan
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot

HYBRID_V3_VERSION = "HYBRID-O2-U7-RECENT20-PUTFIX-V3"
HYBRID_V3_TRIGGER = "O2U7-R20-PUTFIX-V3"
HYBRID_V3_STATE_KEY = "hybrid_o2u7_put_v2:state"
HYBRID_V3_ACCOUNT_EPOCH_PREFIX = "hybrid_o2u7_put_v2:account_epoch:"
HYBRID_V3_RUN_ID = "hybrid_o2u7_put_v2"
MAX_RECOVERY_BALANCE_FRACTION = 0.10

_ACCOUNTING_INSTALLED = False
_WORKER_INSTALLED = False


def _repair_one_canonical_row(repository: Test2Repository, contract_id: str, fallback_outcome: str) -> None:
    """Keep SystemModelTrade outcome/P&L coherent and independent of copier timing."""
    with repository.database.session() as session:
        actual = session.scalar(
            select(Trade).where(Trade.contract_id == str(contract_id))
        )
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
        outcome = str(row.outcome or fallback_outcome or "").upper()
        if outcome not in {"WIN", "LOSS"}:
            return
        if row.outcome is None:
            row.outcome = outcome
        row.reference_base_stake = CANONICAL_BASE_STAKE
        row.fixed_stake_profit = canonical_fixed_profit(
            outcome,
            float(row.expected_profit_ratio or 0.0),
        )
        if row.settlement_timestamp is None:
            row.settlement_timestamp = (
                actual.provider_settlement_time
                or actual.settlement_time
                or utc_now()
            )


def install_hybrid_accounting_integrity() -> None:
    """Prevent copier settlements from corrupting account-independent model P/L."""
    global _ACCOUNTING_INSTALLED
    if _ACCOUNTING_INSTALLED:
        return

    original_settle_trade = Test2Repository.settle_trade
    original_system_model_trades = Test2Repository.system_model_trades

    def settle_trade_coherent(self: Test2Repository, **kwargs: Any) -> bool:
        settled = original_settle_trade(self, **kwargs)
        if settled:
            _repair_one_canonical_row(
                self,
                str(kwargs.get("contract_id") or ""),
                str(kwargs.get("outcome") or ""),
            )
        return settled

    def system_model_trades_coherent(self: Test2Repository, **kwargs: Any) -> list[dict[str, Any]]:
        rows = original_system_model_trades(self, **kwargs)
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
        return rows

    Test2Repository.settle_trade = settle_trade_coherent
    Test2Repository.system_model_trades = system_model_trades_coherent
    _ACCOUNTING_INSTALLED = True


def _fixed_canonical_settlement(bot: RFDir5TradingBot, payload: dict[str, Any]) -> None:
    contract_type = str(payload.get("contract_type") or "").upper()
    outcome = str(payload.get("outcome") or "").upper()
    signal_id = str(payload.get("signal_id") or "")
    ratio = max(0.0, float(payload.get("expected_profit_ratio") or 0.0))

    if contract_type in {"DIGITOVER", "DIGITUNDER"}:
        if hybrid._mode(bot) == hybrid.PRIMARY_DIGITS and outcome == "LOSS":
            hybrid._enter_recovery(bot, signal_id)
        return

    if contract_type != "PUT" or hybrid._mode(bot) != hybrid.PUT_RECOVERY:
        return
    if outcome not in {"WIN", "LOSS"}:
        return

    debt = max(0.0, float(bot.hybrid_state.get("canonical_debt") or 0.0))
    canonical_stake = CANONICAL_BASE_STAKE
    if outcome == "WIN":
        debt = max(0.0, round(debt - canonical_stake * ratio, 2))
    else:
        debt = round(debt + canonical_stake, 2)
    bot.hybrid_state["canonical_debt"] = debt
    hybrid._save_state(bot)
    bot.logger.warning(
        "HYBRID_FIXED_RECOVERY_SETTLED signal_id=%s outcome=%s stake=%.2f "
        "canonical_debt=%.2f stake_policy=fixed_base",
        signal_id,
        outcome,
        canonical_stake,
        debt,
    )
    hybrid._maybe_complete_recovery(bot)


def _assert_runtime_invariants(bot: RFDir5TradingBot) -> None:
    cfg = bot.test2_config.hybrid_strategy
    risk = bot.risk_config
    virtual = bot.virtual_config

    failures: list[str] = []
    if str(bot.test2_config.model.run_id) != HYBRID_V3_RUN_ID:
        failures.append(f"run_id={bot.test2_config.model.run_id}")
    if str(cfg.version) != HYBRID_V3_VERSION:
        failures.append(f"hybrid_version={cfg.version}")
    if int(getattr(cfg, "recent_window", 0)) != 20:
        failures.append("recent_window_must_be_20")
    if len(HYBRID_V3_TRIGGER) > 30:
        failures.append("ledger_trigger_exceeds_varchar30")
    if float(risk.maximum_recovery_balance_fraction) > MAX_RECOVERY_BALANCE_FRACTION + 1e-9:
        failures.append(
            f"maximum_recovery_balance_fraction={risk.maximum_recovery_balance_fraction}"
        )
    if int(virtual.trigger_actual_losses) != 2:
        failures.append(f"virtual_trigger_actual_losses={virtual.trigger_actual_losses}")
    if int(virtual.exit_after_wins) != 2:
        failures.append(f"virtual_exit_after_wins={virtual.exit_after_wins}")
    if hybrid.HYBRID_STATE_KEY != HYBRID_V3_STATE_KEY:
        failures.append(f"state_key={hybrid.HYBRID_STATE_KEY}")
    if hybrid.ACCOUNT_EPOCH_PREFIX != HYBRID_V3_ACCOUNT_EPOCH_PREFIX:
        failures.append(f"account_epoch_prefix={hybrid.ACCOUNT_EPOCH_PREFIX}")
    if failures:
        raise RuntimeError("HYBRID_SAFETY_INVARIANT_FAILED: " + "; ".join(failures))


def install_hybrid_worker_safety() -> None:
    """Install V3 fixed-base recovery and fail closed on unsafe configuration.

    Persistent recovery debt is still measured. It no longer controls stake size.
    The strict PUT 15->5->1 entry brain and two-loss virtual protection remain in
    force; an account in virtual mode still makes no monetary purchase.
    """
    global _WORKER_INSTALLED
    if _WORKER_INSTALLED:
        return

    install_hybrid_accounting_integrity()

    # New epoch: V1/V2 debt/runtime state can never be silently inherited.
    hybrid.HYBRID_STATE_KEY = HYBRID_V3_STATE_KEY
    hybrid.ACCOUNT_EPOCH_PREFIX = HYBRID_V3_ACCOUNT_EPOCH_PREFIX
    hybrid._apply_canonical_settlement = _fixed_canonical_settlement

    recent.STRATEGY_VERSION = HYBRID_V3_VERSION
    recent.LEDGER_TRIGGER_NAME = HYBRID_V3_TRIGGER

    runtime.HYBRID_RUNTIME_CONFIG = replace(
        runtime.HYBRID_RUNTIME_CONFIG,
        version=HYBRID_V3_VERSION,
    )

    # Account recovery debt remains durable, but debt can never increase the next
    # monetary PUT stake in this hybrid strategy. Calling the existing planner with
    # recovery sizing disabled preserves base-stake affordability and the virtual
    # protection block while eliminating debt-derived martingale escalation.
    original_plan_stake = RFDir5Repository.plan_stake

    def fixed_base_plan(self: RFDir5Repository, **kwargs: Any) -> StakePlan:
        hybrid_fixed = bool(getattr(self, "_hybrid_fixed_base_recovery", False))
        requested_recovery = bool(kwargs.get("recovery_enabled", False))
        if not (hybrid_fixed and requested_recovery):
            return original_plan_stake(self, **kwargs)

        debt = 0.0
        managed_id = kwargs.get("managed_account_id")
        if managed_id is not None:
            with self.database.session() as session:
                state = session.get(AccountRiskState, int(managed_id))
                if state is not None:
                    debt = max(0.0, float(state.recovery_loss_debt or 0.0))

        safe_kwargs = dict(kwargs)
        safe_kwargs["recovery_enabled"] = False
        plan = original_plan_stake(self, **safe_kwargs)
        if plan.stake is None:
            return StakePlan(
                None,
                plan.reason,
                is_recovery=debt > 0.009,
                recovery_debt=debt,
                required_recovery_stake=0.0,
            )
        return StakePlan(
            stake=float(plan.stake),
            reason=(
                "Hybrid fixed-base recovery: debt retained for accounting; "
                "stake escalation disabled"
                if debt > 0.009
                else plan.reason
            ),
            is_recovery=debt > 0.009,
            recovery_debt=debt,
            required_recovery_stake=float(plan.stake),
        )

    RFDir5Repository.plan_stake = fixed_base_plan

    original_init = RFDir5TradingBot.__init__

    def safe_init(self: RFDir5TradingBot, config_path: str | None = None) -> None:
        original_init(self, config_path)
        self.rf_repository._hybrid_fixed_base_recovery = True
        _assert_runtime_invariants(self)
        self.logger.warning(
            "HYBRID_SAFETY_ACTIVE version=%s recovery_stake_policy=fixed_account_base "
            "debt_escalation=false virtual_guard=2_losses_then_2_consecutive_virtual_wins "
            "max_recovery_balance_fraction=%.2f state_epoch=v2",
            HYBRID_V3_VERSION,
            float(self.risk_config.maximum_recovery_balance_fraction),
        )

    RFDir5TradingBot.__init__ = safe_init
    _WORKER_INSTALLED = True
