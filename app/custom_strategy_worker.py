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

from app.custom_execution_consistency_authority import (
    install_custom_execution_consistency_authority,
)
from app.custom_split_cap_defaults_authority import (
    install_custom_split_cap_defaults_authority,
)
from app.custom_split_equal_spread_authority import (
    install_custom_split_equal_spread_authority,
)
from app.custom_split_recovery_authority import install_custom_split_recovery_authority
from app.custom_strategy_connection_stampede_guard import (
    install_custom_strategy_connection_stampede_guard,
)
from app.custom_strategy_current_runtime_fix import install_custom_strategy_current_runtime_fix
from app.custom_strategy_direct_runtime import install_custom_strategy_direct_runtime
from app.custom_strategy_instant_start import install_custom_strategy_instant_start
from app.custom_strategy_last_digit_runtime import install_custom_strategy_last_digit_runtime
from app.custom_strategy_manual_stop_guard import install_custom_strategy_manual_stop_guard
from app.custom_strategy_result_router import install_custom_strategy_result_router
from app.custom_strategy_runtime_lifecycle import install_custom_strategy_runtime_lifecycle
from app.custom_strategy_settlement import install_custom_strategy_settlement
from app.custom_strategy_startup_authority import install_custom_strategy_startup_authority
from app.custom_virtual_contract_parity import install_custom_virtual_contract_parity
from app.custom_virtual_integrity_authority import install_custom_virtual_integrity_authority
from app.custom_virtual_post_loss_barrier_authority import (
    install_custom_virtual_post_loss_barrier_authority,
)
from app.deriv_rate_limit_circuit import install_deriv_rate_limit_circuit
from app.deriv_request_broker import install_deriv_request_broker
from app.direct_browser_runtime_authority import install_direct_browser_runtime_authority
from app.direct_execution_worker_fence import install_direct_execution_worker_fence
from app.exact_strategy_execution_authority import install_exact_strategy_execution_authority
from app.execution_stop_reason_authority import install_execution_stop_reason_authority
from app.final_execution_continuity import install_final_execution_continuity
from app.manual_martingale_execution_authority import (
    install_manual_martingale_execution_authority,
)
from app.manual_martingale_v2 import install_manual_martingale_v2_worker
from app.netlify_worker_bridge import install_netlify_worker_bridge
from app.per_account_virtual_runtime import install_account_isolation_invariants
from app.premium_worker_guard import install_premium_worker_guard
from app.private_websocket_rate_limit import install_private_websocket_rate_limit
from app.profit_accuracy_guard import install_profit_accuracy_guard
from app.public_testing_access import public_testing_free_access_enabled
from app.public_websocket_resilience import install_public_websocket_resilience
from app.real_demo_trading_support import install_dual_demo_real_trading_support
from app.rf_dir5_bot import RFDir5TradingBot
from app.seamless_execution_recovery import install_seamless_execution_recovery
from app.session_risk_stop_authority import install_session_risk_stop_worker
from app.telegram_silence import install_telegram_silence
from app.trade_registration_idempotency import install_trade_registration_idempotency
from app.unresolved_contract_safety import install_unresolved_contract_safety
from app.vps_low_latency_runtime import install_vps_low_latency_runtime
from app.vps_provider_connection_resilience_v2 import (
    install_vps_provider_connection_resilience_v2,
)


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
    # it wraps that same proposal+BUY path. Latched-entry compatibility may relax
    # purchase age, but the signal remains the exact saved strategy contract.
    install_exact_strategy_execution_authority()
    install_custom_strategy_last_digit_runtime()
    install_custom_strategy_runtime_lifecycle()

    # Explicit Start is a per-account state transition and must never depend only
    # on a global MAX(updated_at) revision. Install after the runtime lifecycle so
    # it can force-pick newly started rows into validation/session creation.
    install_custom_strategy_startup_authority()

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

    # Custom Virtual Hook is a normal qualified Custom Strategy trade with zero
    # financial stake. Install after result routing so primary and after-loss
    # routes share the same parity. PostgreSQL OPEN rows block real execution,
    # missed infrastructure samples are VOID+RETRY (never fake CANCELLED), and the
    # user's configured virtual-win exit count is authoritative over legacy AIDR.
    install_custom_virtual_integrity_authority()

    # Manual Stop is the final purchase authority. It is installed after result
    # routing and virtual integrity so both primary and after-loss routes are
    # checked against the latest persisted account lifecycle before BUY.
    install_custom_strategy_manual_stop_guard()

    # Normal runtime/transport faults may never deliberately close a healthy
    # private WebSocket or convert themselves into an account-level stop. The
    # account's own ClientSession reconnect loop remains authoritative and is woken
    # immediately when execution needs it.
    install_final_execution_continuity()

    # Install after every lifecycle/failure wrapper. This is the final invariant:
    # terminal account states keep the exact initiating reason, generic worker
    # housekeeping cannot erase it, and a started account with a missing private
    # session is automatically repaired instead of silently becoming idle.
    install_execution_stop_reason_authority()

    # Startup no longer waits for REST account discovery or serial history loads.
    install_custom_strategy_instant_start()

    # A fresh Start admits/wakes only that account. Existing private reconnect
    # loops own their own backoff and sibling accounts are never globally rebuilt.
    install_custom_strategy_connection_stampede_guard()

    # Full-VPS latency correction stays on top of the proven b315 ownership model:
    # faster bounded connection capacity, dual-stack racing, deduplicated local
    # admission/market work, and bounded open-contract reconciliation. It does not
    # alter strategy qualification, stake, proposal, BUY, settlement or rate-limit
    # protection.
    install_vps_low_latency_runtime()
    # The shared pooled broker is already bounded. Keep the low-latency wrapper
    # from cancelling that broker after only 8 seconds, reduce ordinary OTP
    # bootstrap concurrency, and preserve urgent priority for browser->VPS takeover.
    install_vps_provider_connection_resilience_v2()

    # Transport/Virtual Hook/Multiplier consistency installs first. Split Recovery
    # is then wrapped so the configured spread width (1/2/3), not the number of
    # remaining successful legs, is always the risk divisor. The compatibility cap
    # authority restores canonical 10%/$0.50 safety defaults when the generic
    # execution session omits those optional kwargs. Finally, a real loss that
    # enters Virtual Hook consumes its settlement tick; only a later provider tick
    # may qualify the first zero-stake observation.
    install_custom_execution_consistency_authority()
    install_custom_split_equal_spread_authority()
    install_custom_split_cap_defaults_authority()
    install_custom_virtual_post_loss_barrier_authority()
    install_telegram_silence()

    # Browser-direct ownership is installed LAST, after every lifecycle, liveness,
    # low-latency and financial wrapper. A fresh browser lease is intentionally not
    # a VPS runtime, so worker housekeeping may neither normalize it to STOPPED nor
    # run its liveness repair loop against it. The final scope fence still performs
    # an uncached database ownership check immediately before any server BUY and
    # wakes server takeover only after the browser lease really expires.
    install_direct_browser_runtime_authority()
    install_direct_execution_worker_fence()

    # Keep the complete premium execution guard available for the paid launch, but
    # do not install it while public testing is free. This is critical because the
    # worker guard sits immediately before proposal and BUY and would otherwise
    # pause every unpaid tester even when the HTTP gate has been bypassed.
    public_testing = public_testing_free_access_enabled()
    if not public_testing:
        install_premium_worker_guard()

    bot = RFDir5TradingBot()
    bot.logger.warning(
        "CUSTOM_STRATEGY_WORKER_READY architecture=hybrid_browser_direct_v2 "
        "frontend=full_vps_same_origin realtime=browser_deriv_direct "
        "scheduled_and_offline=server_worker browser_owner_fenced=true "
        "legacy_rf=false legacy_aidr=false multi_strategy=false cohorts=false "
        "bulk=false tick_db_persistence=false start_required=true "
        "explicit_start_pickup=true instant_start=true provider_account_sweep_blocking=false "
        "history_startup=parallel_bounded exact_entry_guard=true manual_stop_buy_guard=true "
        "premium_access_gate=%s "
        "premium_settlement_preserved=true "
        "result_routing=account_outcome_debt "
        "martingale_multiplier=previous_actual_stake_times_multiplier "
        "martingale_split=equal_loss_pool_by_configured_split_count "
        "split_payout_sized=true split_rebase_after_actual_loss=true hidden_buffer=false "
        "split_cap_defaults=canonical_10pct_and_0_50_reserve "
        "virtual_hook=exact_zero_stake_mirror persistent_open_lock=true immediate_ui=true "
        "virtual_entry=real_position_settled_then_future_qualified_tick "
        "virtual_void_policy=retry_without_real_unlock same_tick_reentry=false "
        "runtime_fault_policy=reconnect_reconcile_never_stop "
        "ambiguous_buy_policy=reconcile_before_next_real duplicate_buy_retry=false "
        "stop_reason_authority=durable execution_liveness_watchdog=browser_aware "
        "connection_repair=targeted_singleflight sibling_wake=false global_revalidation=false "
        "vps_low_latency=true provider_connection_resilience_v2=true "
        "provider_rate_limit_backoff=preserved",
        "public_testing_bypass" if public_testing else "exact_timestamp_before_proposal_and_buy",
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
