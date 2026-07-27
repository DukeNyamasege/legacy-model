from __future__ import annotations

import sys
from pathlib import Path

# When Python runs this file as ``python scripts/preflight_hybrid_v3.py``,
# sys.path[0] is /app/scripts, not /app. Add the project root explicitly so
# imports like ``from app...`` work inside the Docker worker image.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.account_execution_diagnostics import install_account_execution_diagnostics
from app.account_execution_feedback import install_account_execution_feedback
from app.account_lifecycle import install_worker_account_lifecycle
from app.hybrid_data_integrity import install_hybrid_data_integrity
from app.hybrid_digit_put import PRIMARY_DIGITS, install_hybrid_digit_put_strategy
from app.hybrid_recent_digit_bias import install_recent_digit_bias_strategy
from app.hybrid_runtime_config import install_hybrid_runtime_config
from app.hybrid_safety import (
    HYBRID_V3_ACCOUNT_EPOCH_PREFIX,
    HYBRID_V3_RUN_ID,
    HYBRID_V3_STATE_KEY,
    HYBRID_V3_TRIGGER,
    HYBRID_V3_VERSION,
    install_hybrid_worker_safety,
)
from app.model_accounting import canonical_fixed_profit
from app.models import AccountRiskState, ManagedAccount, RuntimePreference, Trade, utc_now
from app.rf_dir5_bot import RFDir5TradingBot
from app.strict_streak_guard import install_strict_streak_guard
from app.telegram_admin_integration import install_telegram_admin_integration
from app.websocket_only_execution import install_websocket_only_execution


def install_production_stack() -> None:
    install_worker_account_lifecycle()
    install_websocket_only_execution()
    install_account_execution_feedback()
    install_account_execution_diagnostics()
    install_telegram_admin_integration()
    install_strict_streak_guard()
    install_hybrid_runtime_config()
    install_hybrid_data_integrity()
    install_hybrid_digit_put_strategy()
    install_recent_digit_bias_strategy()
    install_hybrid_worker_safety()


def main() -> None:
    install_production_stack()
    bot = RFDir5TradingBot()

    assert bot.test2_config.model.run_id == HYBRID_V3_RUN_ID
    assert bot.test2_config.hybrid_strategy.version == HYBRID_V3_VERSION
    assert bot.test2_config.deriv.environment == "demo"
    assert bot.test2_config.deriv.allow_real_trading is False
    assert bot.test2_config.execution.real_enabled is False
    assert bot.hybrid_state["mode"] == PRIMARY_DIGITS
    assert bot.hybrid_state.get("canonical_debt", 0.0) == 0.0
    assert len(HYBRID_V3_TRIGGER) <= 30
    assert bot.risk_config.maximum_recovery_balance_fraction <= 0.10
    assert bot.virtual_config.trigger_actual_losses == 2
    assert bot.virtual_config.exit_after_wins == 2

    # Canonical P/L must never have an impossible sign/outcome combination.
    win_pnl = canonical_fixed_profit("WIN", 0.38)
    loss_pnl = canonical_fixed_profit("LOSS", 0.38)
    assert win_pnl == 0.19
    assert loss_pnl == -0.50

    # A clean V3 start must not inherit either old state namespace.
    with bot.repository.database.session() as session:
        for key in (
            "hybrid_o2u7_put_v1:state",
            HYBRID_V3_STATE_KEY,
        ):
            row = session.get(RuntimePreference, key)
            assert row is None, f"stale hybrid runtime state exists: {key}"
        assert session.query(Trade).filter(Trade.settlement_time.is_(None)).count() == 0
        managed = session.query(ManagedAccount).order_by(ManagedAccount.id.asc()).first()
        assert managed is not None, "no managed account available for fixed-base safety preflight"
        managed_id = int(managed.id)
        masked = "PRE***FLT"
        existing = session.get(AccountRiskState, managed_id)
        assert existing is None, "clean reset did not clear AccountRiskState"
        session.add(
            AccountRiskState(
                managed_account_id=managed_id,
                account_id_masked=masked,
                trading_day=utc_now().date().isoformat(),
                daily_start_balance=10000.0,
                session_profit=-1000.0,
                consecutive_losses=1,
                recovery_loss_debt=1000.0,
                recovery_pending=True,
                recovery_attempt_active=False,
                protection_mode="NORMAL_MODE",
                equity_high_water=10000.0,
            )
        )

    plan = None
    virtual_plan = None
    try:
        # Debt must never change V3 stake: $1,000 debt still plans exactly $0.50.
        plan = bot.rf_repository.plan_stake(
            managed_account_id=managed_id,
            account_id_masked=masked,
            current_balance=10000.0,
            requested_stake=0.50,
            proposal_profit_ratio=0.38,
            recovery_enabled=True,
            recovery_trigger_losses=1,
            minimum_stake=0.35,
            virtual_protection_enabled=True,
            maximum_recovery_balance_fraction=0.10,
            minimum_balance_reserve=0.50,
        )
        assert plan.stake == 0.50, plan
        assert plan.is_recovery is True
        assert plan.recovery_debt == 1000.0

        # Two-loss virtual protection must still block a real monetary recovery trade.
        with bot.repository.database.session() as session:
            state = session.get(AccountRiskState, managed_id, with_for_update=True)
            assert state is not None
            state.consecutive_losses = 2
            state.protection_mode = "VIRTUAL_WAITING_FOR_WIN"

        virtual_plan = bot.rf_repository.plan_stake(
            managed_account_id=managed_id,
            account_id_masked=masked,
            current_balance=10000.0,
            requested_stake=0.50,
            proposal_profit_ratio=0.38,
            recovery_enabled=True,
            recovery_trigger_losses=1,
            minimum_stake=0.35,
            virtual_protection_enabled=True,
            maximum_recovery_balance_fraction=0.10,
            minimum_balance_reserve=0.50,
        )
        assert virtual_plan.stake is None, virtual_plan
    finally:
        # Always remove the artificial state, even when a preflight assertion fails.
        with bot.repository.database.session() as session:
            state = session.get(AccountRiskState, managed_id)
            if state is not None and state.account_id_masked == masked:
                session.delete(state)

    assert plan is not None
    assert virtual_plan is not None

    print("============================================================")
    print("HYBRID V3 SAFETY PREFLIGHT PASSED")
    print("============================================================")
    print("Run ID              :", HYBRID_V3_RUN_ID)
    print("Strategy             :", HYBRID_V3_VERSION)
    print("Environment          : DEMO ONLY")
    print("Initial mode         :", PRIMARY_DIGITS)
    print("State key            :", HYBRID_V3_STATE_KEY)
    print("Account epoch prefix :", HYBRID_V3_ACCOUNT_EPOCH_PREFIX)
    print("Ledger trigger       :", HYBRID_V3_TRIGGER)
    print("Canonical WIN @ 38%  :", f"${win_pnl:+.2f}")
    print("Canonical LOSS       :", f"${loss_pnl:+.2f}")
    print("$1,000 debt stake    :", f"${plan.stake:.2f}")
    print("Virtual monetary buy : BLOCKED")
    print("Debt escalation      : DISABLED")
    print("============================================================")


if __name__ == "__main__":
    main()
