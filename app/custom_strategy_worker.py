from __future__ import annotations

import asyncio
import signal

from app.account_lifecycle import install_worker_account_lifecycle
from app.account_mode_execution_lock import install_account_mode_execution_lock
from app.account_reenrollment import install_account_reenrollment
from app.account_scoped_websocket_runtime import install_account_scoped_websocket_runtime
from app.custom_strategy_current_runtime_fix import install_custom_strategy_current_runtime_fix
from app.custom_strategy_direct_runtime import install_custom_strategy_direct_runtime
from app.custom_strategy_runtime_lifecycle import install_custom_strategy_runtime_lifecycle
from app.custom_strategy_settlement import install_custom_strategy_settlement
from app.deriv_rate_limit_circuit import install_deriv_rate_limit_circuit
from app.deriv_request_broker import install_deriv_request_broker
from app.manual_martingale_v2 import install_manual_martingale_v2_worker
from app.netlify_worker_bridge import install_netlify_worker_bridge
from app.per_account_virtual_runtime import install_account_isolation_invariants
from app.private_websocket_rate_limit import install_private_websocket_rate_limit
from app.profit_accuracy_guard import install_profit_accuracy_guard
from app.public_websocket_resilience import install_public_websocket_resilience
from app.real_demo_trading_support import install_dual_demo_real_trading_support
from app.rf_dir5_bot import RFDir5TradingBot
from app.telegram_silence import install_telegram_silence
from app.trade_registration_idempotency import install_trade_registration_idempotency
from app.unresolved_contract_safety import install_unresolved_contract_safety


async def run_worker() -> None:
    """Run only account-scoped Custom Strategy execution.

    The old RF/AIDR, shared signal, cohort, bulk, multi-strategy and standardized
    execution routers are intentionally not imported or installed here.
    """

    install_account_reenrollment()
    install_account_isolation_invariants()
    install_unresolved_contract_safety()
    install_trade_registration_idempotency()

    # Provider connection safety remains shared infrastructure; none of these
    # layers chooses a trading signal or routes a purchase across accounts.
    install_deriv_request_broker()
    install_deriv_rate_limit_circuit()
    install_public_websocket_resilience()
    install_private_websocket_rate_limit()

    install_worker_account_lifecycle()
    install_account_mode_execution_lock()
    install_dual_demo_real_trading_support()
    install_account_scoped_websocket_runtime()
    install_profit_accuracy_guard()
    install_manual_martingale_v2_worker()
    install_custom_strategy_settlement()

    # Install the independent execution authority, then the final current-runtime
    # correction that keeps proposal+buy on one account session and suppresses
    # inherited RF/unrelated-history work from the Custom Strategy path.
    install_custom_strategy_direct_runtime()
    install_custom_strategy_current_runtime_fix()
    install_custom_strategy_runtime_lifecycle()

    # UI delivery is never allowed to sit on the financial execution path. This
    # final bridge also keeps transient proposal/session interruptions in an
    # account-local reconnect state while treating an uncertain BUY timeout as a
    # reconciliation event that must never be blindly retried.
    install_netlify_worker_bridge()
    install_telegram_silence()

    bot = RFDir5TradingBot()
    bot.logger.warning(
        "CUSTOM_STRATEGY_WORKER_READY architecture=account_scoped_direct "
        "frontend=netlify_static realtime=nonblocking_vps_websocket "
        "legacy_rf=false legacy_aidr=false multi_strategy=false cohorts=false "
        "bulk=false tick_db_persistence=false start_required=true"
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
