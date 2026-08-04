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
from app.hybrid_data_integrity import install_hybrid_data_integrity
from app.hybrid_digit_put import install_hybrid_digit_put_strategy
from app.hybrid_runtime_config import install_hybrid_runtime_config
from app.multi_strategy_concurrency import install_multi_strategy_concurrency_guard
from app.multi_strategy_runtime import install_multi_strategy_runtime
from app.private_buy_parameter_hardening import install_private_buy_parameter_hardening
from app.private_websocket_rate_limit import install_private_websocket_rate_limit
from app.production_worker_integration import install_production_worker_integration
from app.profit_accuracy_guard import install_profit_accuracy_guard
from app.real_demo_trading_support import install_dual_demo_real_trading_support
from app.rf_dir5_bot import RFDir5TradingBot
from app.stake_only_balance_policy import install_stake_only_balance_policy
from app.strict_streak_guard import install_strict_streak_guard
from app.telegram_admin_integration import install_telegram_admin_integration
from app.telegram_silence import install_telegram_silence
from app.tick_debug_logging import install_every_tick_debug_logging
from app.unresolved_contract_safety import install_unresolved_contract_safety
from app.websocket_only_execution import install_websocket_only_execution


async def run_worker() -> None:
    # Only the current enrollment generation is visible to the worker. Historical
    # registrations remain preserved but cannot auto-start after a reset.
    install_account_reenrollment()

    # A malformed legacy placeholder contract ID must never restart the whole
    # worker. It is retained for audit, quarantined with zero financial impact,
    # and excluded from private-WebSocket reconciliation.
    install_unresolved_contract_safety()

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
    # account WebSockets, settlement, virtual observations and bulk purchases.
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

    # Existing Digits -> Over strategy remains unchanged for accounts that select
    # it: OVER 1 normal, OVER 3 first recovery, virtual OVER 4, then one real
    # OVER 4 full-debt recovery.
    install_ai_digit_recovery_v1_strategy()
    install_aidr_execution_flow_fix()
    install_aidr_loss_continuation_fix()

    # Virtual OVER-4 and post-virtual recovery use the ordinary 50% baseline with
    # only five-percent relative tightening (52.5%).
    install_aidr_virtual_soft_gate()

    # Stop/Reset wins settlement races and cannot be reversed by a late callback.
    install_aidr_strict_recovery_guard()

    # Final account strategy router. AIDR receives only Digits/Over accounts;
    # Digits/Under, Even/Odd and Rise/Fall use the same private purchase,
    # recovery, virtual protection and account-isolation infrastructure.
    install_multi_strategy_runtime()

    # Every family has its own candidate stream, but the authenticated purchase
    # boundary is atomic. Recheck staleness/open cycles after acquiring the gate
    # so two strategy groups can never race into overlapping provider contracts.
    install_multi_strategy_concurrency_guard()

    install_production_worker_integration()
    install_every_tick_debug_logging()

    bot = RFDir5TradingBot()
    promoted = reconcile_existing_virtual_confirmations(bot.rf_repository)
    if promoted:
        bot.logger.warning(
            "AIDR_EXISTING_VIRTUAL_WINS_PROMOTED accounts=%s required_wins=1",
            promoted,
        )
    loop = asyncio.get_running_loop()

    def stop() -> None:
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
