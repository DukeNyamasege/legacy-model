from __future__ import annotations

import asyncio
import signal

from app.account_execution_diagnostics import install_account_execution_diagnostics
from app.account_execution_feedback import install_account_execution_feedback
from app.account_lifecycle import install_worker_account_lifecycle
from app.account_mode_execution_lock import install_account_mode_execution_lock
from app.account_reenrollment import install_account_reenrollment
from app.account_scoped_websocket_runtime import install_account_scoped_websocket_runtime
from app.ai_digit_recovery_v1 import install_ai_digit_recovery_v1_strategy
from app.aidr_execution_flow_fix import install_aidr_execution_flow_fix
from app.aidr_loss_continuation_fix import install_aidr_loss_continuation_fix
from app.aidr_strict_recovery_guard import (
    install_aidr_strict_recovery_guard,
    reconcile_existing_virtual_confirmations,
)
from app.aidr_virtual_settlement_fix import install_aidr_virtual_settlement_fix
from app.aidr_virtual_soft_gate import install_aidr_virtual_soft_gate
from app.custom_martingale import install_custom_martingale_worker
from app.dashboard_actual_trade_fallback import install_dashboard_actual_trade_fallback
from app.deployment_announcement import install_dynamic_deployment_announcement
from app.deriv_request_broker import install_deriv_request_broker
from app.guaranteed_signal_delivery import install_guaranteed_signal_delivery
from app.hybrid_data_integrity import install_hybrid_data_integrity
from app.hybrid_digit_put import install_hybrid_digit_put_strategy
from app.hybrid_runtime_config import install_hybrid_runtime_config
from app.multi_strategy_concurrency import install_multi_strategy_concurrency_guard
from app.multi_strategy_runtime import install_multi_strategy_runtime
from app.per_account_virtual_runtime import (
    install_account_isolation_invariants,
    install_uniform_virtual_runtime,
)
from app.private_buy_parameter_hardening import install_private_buy_parameter_hardening
from app.private_websocket_rate_limit import install_private_websocket_rate_limit
from app.production_worker_integration import install_production_worker_integration
from app.profit_accuracy_guard import install_profit_accuracy_guard
from app.real_demo_trading_support import install_dual_demo_real_trading_support
from app.recovery_state_persistence_hardening import (
    install_recovery_state_persistence_hardening,
)
from app.rf_dir5_bot import RFDir5TradingBot
from app.scalable_group_execution import install_scalable_group_execution
from app.scalable_group_execution_hardening import (
    install_scalable_group_execution_hardening,
)
from app.settlement_observability_hardening import (
    install_settlement_observability_hardening,
)
from app.stake_only_balance_policy import install_stake_only_balance_policy
from app.standardized_execution_runtime import install_standardized_execution_runtime
from app.standardized_signal_metadata import install_standardized_signal_metadata
from app.strategy_settlement_integrity import install_strategy_settlement_integrity
from app.strategy_v2_runtime import install_strategy_v2_runtime
from app.strict_streak_guard import install_strict_streak_guard
from app.telegram_admin_integration import install_telegram_admin_integration
from app.telegram_silence import install_telegram_silence
from app.tick_debug_logging import install_every_tick_debug_logging
from app.tick_persistence_buffer import install_tick_persistence_buffer
from app.trade_registration_idempotency import install_trade_registration_idempotency
from app.unresolved_contract_safety import install_unresolved_contract_safety
from app.websocket_only_execution import install_websocket_only_execution


async def run_worker() -> None:
    # Only the current enrollment generation is visible to the worker. Historical
    # registrations remain preserved but cannot auto-start after a reset.
    install_account_reenrollment()

    # Account lifecycle outcomes are never allowed to become platform control
    # states. TP, SL, manual Stop, credential or balance failures isolate only the
    # affected account while the worker and every other account remain active.
    install_account_isolation_invariants()

    # A malformed legacy placeholder contract ID must never restart the whole
    # worker. It is retained for audit, quarantined with zero financial impact,
    # and excluded from private-WebSocket reconciliation.
    install_unresolved_contract_safety()

    # Provider purchase confirmations can arrive through more than one private
    # callback. Conflict-safe registration keeps the first committed Trade row and
    # turns later identical callbacks into harmless no-ops.
    install_trade_registration_idempotency()

    # Persist public ticks in bounded batches. Strategy state remains in memory,
    # while PostgreSQL WAL and BotState row churn fall from one transaction per
    # tick to roughly one transaction per second.
    install_tick_persistence_buffer()

    # REST is used only for account discovery and OTP creation needed to establish
    # private WebSockets. One keep-alive pool coalesces safe account reads, bounds
    # per-host pressure, and rejects any multi-account REST trading path locally.
    install_deriv_request_broker()

    install_dashboard_actual_trade_fallback()
    install_worker_account_lifecycle()
    install_account_mode_execution_lock()
    install_dual_demo_real_trading_support()
    install_account_scoped_websocket_runtime()
    install_websocket_only_execution()
    install_private_buy_parameter_hardening()
    install_private_websocket_rate_limit()
    install_account_execution_feedback()
    install_account_execution_diagnostics()

    # Operator kill switch is installed before channel announcements, private
    # admin polling and lifecycle alerts. While suspended, no Telegram request is
    # sent by any worker notification path.
    install_telegram_silence()
    install_telegram_admin_integration()
    install_dynamic_deployment_announcement()

    # Build the shared RF/hybrid envelope for public ticks, proposals, private
    # account WebSockets, settlement and virtual observations.
    install_strict_streak_guard()
    install_hybrid_runtime_config()
    install_hybrid_data_integrity()
    install_hybrid_digit_put_strategy()

    install_profit_accuracy_guard()
    install_stake_only_balance_policy()
    install_custom_martingale_worker()

    # Patch the virtual-settlement factory before strategy wrappers are installed.
    # Results resolve by immutable managed_account_id, never duplicate masked IDs.
    install_aidr_virtual_settlement_fix()

    # System Strategy remains the default AIDR sequence: OVER 1 normal, OVER 3
    # first recovery, virtual OVER 4 and then real OVER 4 full-debt recovery.
    install_ai_digit_recovery_v1_strategy()
    install_aidr_execution_flow_fix()
    install_aidr_loss_continuation_fix()

    # OVER-4 uses the ordinary 50% contract baseline. The final uniform virtual
    # layer below removes proposal and real-purchase cadence from $0 observations.
    install_aidr_virtual_soft_gate()

    # Stop/Reset wins settlement races and cannot be reversed by a late callback.
    install_aidr_strict_recovery_guard()

    # Recovery start is idempotent. A recovery state that was already committed by
    # the strict planner is verified as persisted instead of being reported as a
    # false state_persisted=False failure at the WebSocket purchase boundary.
    install_recovery_state_persistence_hardening()

    # Build the legacy router first, then make v2 authoritative. V2 separates the
    # System Strategy from manual Over/Under, persists the user's prediction, and
    # creates the database parent required before any manual virtual trade opens.
    install_multi_strategy_runtime()
    install_strategy_v2_runtime()

    # Settle Over/Under, Even/Odd and Rise/Fall by their own contract rules. The
    # virtual-trade boundary also creates its directional parent transactionally,
    # preventing FK errors from escaping into the tick loop.
    install_strategy_settlement_integrity()

    # Every strategy now shares the same account-level lifecycle: two actual
    # losses enter virtual mode, one qualifying virtual win arms real recovery,
    # and virtual observations bypass provider proposal/cadence because they risk
    # no money. Failures remain isolated to their exact managed account.
    install_uniform_virtual_runtime()

    # Serialize provider setup and local registration mutations. Standardized
    # account groups are allowed to coexist with contracts owned by other groups;
    # an account skips only when its own previous contract is still settling.
    install_multi_strategy_concurrency_guard()

    # Remove cross-account competition after every legacy strategy wrapper has
    # been installed. On one qualified opportunity, System NORMAL accounts receive
    # OVER-1, first-recovery accounts OVER-3, post-virtual accounts OVER-4, and each
    # manual strategy group receives its exact selected contract. Every scoped
    # account receives either a purchase/virtual receipt or a recorded skip reason.
    install_standardized_signal_metadata()
    install_standardized_execution_runtime()

    # Replace legacy signal expiry with a short immediate purchase boundary and
    # refresh account membership before execution.
    install_guaranteed_signal_delivery()

    # Final financial transport authority. Contract metadata is shared publicly,
    # but every account keeps its own authenticated private WebSocket. Accounts
    # trading the same contract/stake are placed into logical scheduling groups;
    # each still receives an independent buy request and provider confirmation.
    # Group concurrency, per-account locks, safe connection retries, and exact
    # confirmation diagnostics reduce saturation without changing the commission
    # route or introducing multi-account REST/copy execution.
    install_scalable_group_execution()

    # NORMAL, first recovery and post-virtual execute as fresh role subcycles. A
    # role creates its proposal only when its own WebSocket groups are ready, so a
    # previous role cannot leave it holding an aging signal. Any role/account error
    # remains isolated and cannot stop the global worker.
    install_scalable_group_execution_hardening()

    install_production_worker_integration()
    install_every_tick_debug_logging()

    # Install last so settlement logs observe the final wrapped execution path.
    # It corrects the legacy global-duration log and keeps absent provider markup
    # metadata as an informational verification item rather than a trade error.
    install_settlement_observability_hardening()

    bot = RFDir5TradingBot()
    promoted = reconcile_existing_virtual_confirmations(bot.rf_repository)
    if promoted:
        bot.logger.warning(
            "AIDR_EXISTING_VIRTUAL_WINS_PROMOTED accounts=%s required_wins=1",
            promoted,
        )
    loop = asyncio.get_running_loop()

    def stop() -> None:
        # Only an operating-system shutdown signal can stop the worker process.
        # Account Pause/Stop/TP/SL paths never call this function.
        bot.is_running = False

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop)
        except NotImplementedError:
            pass

    await bot.run()


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
