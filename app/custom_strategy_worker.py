from __future__ import annotations

import asyncio
import signal

from app.account_lifecycle import install_worker_account_lifecycle
from app.account_mode_execution_lock import install_account_mode_execution_lock
from app.account_reenrollment import install_account_reenrollment
from app.account_scoped_websocket_runtime import install_account_scoped_websocket_runtime
from app.custom_strategy_comparator_extension import (
    install_custom_strategy_comparator_extension,
)
from app.custom_strategy_last_digit_prediction import (
    install_custom_strategy_last_digit_prediction,
)

# Extend the canonical Custom Strategy schema before direct-runtime imports bind
# the normalization/evaluation functions used by the worker.
install_custom_strategy_comparator_extension()
install_custom_strategy_last_digit_prediction()

from app.custom_split_recovery_authority import install_custom_split_recovery_authority
from app.custom_strategy_current_runtime_fix import install_custom_strategy_current_runtime_fix
from app.custom_strategy_direct_runtime import install_custom_strategy_direct_runtime
from app.custom_strategy_last_digit_runtime import install_custom_strategy_last_digit_runtime
from app.custom_strategy_manual_stop_guard import install_custom_strategy_manual_stop_guard
from app.custom_strategy_result_router import install_custom_strategy_result_router
from app.custom_strategy_runtime_lifecycle import install_custom_strategy_runtime_lifecycle
from app.custom_strategy_settlement import install_custom_strategy_settlement
from app.custom_virtual_contract_parity import install_custom_virtual_contract_parity
from app.deriv_rate_limit_circuit import install_deriv_rate_limit_circuit
from app.deriv_request_broker import install_deriv_request_broker
from app.exact_strategy_execution_authority import install_exact_strategy_execution_authority
from app.final_execution_continuity import install_final_execution_continuity
from app.manual_martingale_execution_authority import (
    install_manual_martingale_execution_authority,
)
from app.manual_martingale_v2 import install_manual_martingale_v2_worker
from app.netlify_worker_bridge import install_netlify_worker_bridge
from app.per_account_virtual_runtime import install_account_isolation_invariants
from app.private_websocket_rate_limit import install_private_websocket_rate_limit
from app.profit_accuracy_guard import install_profit_accuracy_guard
from app.public_websocket_resilience import install_public_websocket_resilience
from app.real_demo_trading_support import install_dual_demo_real_trading_support
from app.rf_dir5_bot import RFDir5TradingBot
from app.seamless_execution_recovery import install_seamless_execution_recovery
from app.session_risk_stop_authority import install_session_risk_stop_worker
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

    # Virtual observations must settle the exact saved contract family: Over,
    # Under, Match, Differs, Even, Odd, CALL or PUT with the same market/duration.
    install_custom_virtual_contract_parity()

    # Install the independent execution authority, then the final current-runtime
    # correction that keeps proposal+buy on one account session and suppresses
    # inherited RF/unrelated-history work from the Custom Strategy path.
    install_custom_strategy_direct_runtime()
    install_custom_strategy_current_runtime_fix()

    # Exact-entry authority is installed after the current private-session fix so
    # it wraps that same proposal+BUY path. It re-validates every condition and
    # skips stale trigger ticks instead of purchasing one or more digits late.
    install_exact_strategy_execution_authority()
    install_custom_strategy_last_digit_runtime()
    install_custom_strategy_runtime_lifecycle()

    # TP/SL are final account stops. The canonical counter is the persisted
    # AccountRiskState.session_profit for the current fresh Start session, with TP
    # positive and SL negative from the frozen settings snapshot.
    install_session_risk_stop_worker()

    # UI delivery is never allowed to sit on the financial execution path. This
    # bridge handles bounded provider reconnects; the final recovery authority
    # below broadens that rule to ownership/state synchronization faults too.
    install_netlify_worker_bridge()
    install_seamless_execution_recovery()

    # Business-rule stake policies install after transport recovery so a valid
    # financial skip can never be mistaken for a private-WebSocket fault.
    install_manual_martingale_execution_authority()
    install_custom_split_recovery_authority()

    # Result routing is account scoped and outcome driven. The first/debt-free
    # route remains the exact existing Custom Strategy. Only users who explicitly
    # enable the new option get an independent after-loss contract/analysis route.
    # It is installed after Martingale settlement wrappers so the final debt ledger
    # determines when recovery routing begins and ends.
    install_custom_strategy_result_router()

    # Manual Stop is the final purchase authority. It is installed after result
    # routing so both primary and after-loss routes are checked against the latest
    # persisted account lifecycle before scheduling, proposal and BUY.
    install_custom_strategy_manual_stop_guard()

    # Install last: normal runtime/transport faults may never deliberately close a
    # healthy private WebSocket or convert themselves into an account-level stop.
    # The account's own ClientSession reconnect loop remains authoritative and is
    # woken immediately when execution needs it.
    install_final_execution_continuity()
    install_telegram_silence()

    bot = RFDir5TradingBot()
    bot.logger.warning(
        "CUSTOM_STRATEGY_WORKER_READY architecture=account_scoped_direct "
        "frontend=netlify_static realtime=nonblocking_vps_websocket "
        "legacy_rf=false legacy_aidr=false multi_strategy=false cohorts=false "
        "bulk=false tick_db_persistence=false start_required=true "
        "exact_entry_guard=true manual_stop_buy_guard=true "
        "result_routing=account_outcome_debt "
        "martingale_spread=1_to_3_successful_parts "
        "runtime_fault_policy=soft_reconnect_no_forced_disconnect"
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
