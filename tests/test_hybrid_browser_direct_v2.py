from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HybridBrowserDirectV2Contract(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_worker_installs_browser_authority_and_server_buy_fence_last(self) -> None:
        worker = self.text("app/custom_strategy_worker.py")
        browser = worker.index("install_direct_browser_runtime_authority()")
        fence = worker.index("install_direct_execution_worker_fence()")
        low_latency = worker.index("install_vps_low_latency_runtime()")
        self.assertGreater(browser, low_latency)
        self.assertGreater(fence, browser)
        self.assertIn("execution_liveness_watchdog=browser_aware", worker)

    def test_browser_authority_does_not_extend_its_own_lease(self) -> None:
        authority = self.text("app/direct_browser_runtime_authority.py")
        self.assertIn("direct_browser_lease_fresh(row)", authority)
        self.assertIn("return None", authority)
        self.assertIn("only a browser heartbeat may extend", authority)
        self.assertNotIn("row.execution_status_updated_at =", authority)

    def test_demo_reset_uses_official_deriv_options_endpoint(self) -> None:
        reset = self.text("app/vps_demo_balance_reset.py")
        self.assertIn('/trading/v1/options/accounts/{account_id}/reset-demo-balance', reset)
        self.assertIn("Only Deriv demo accounts", reset)
        self.assertIn("managed_account_id", reset)
        self.assertIn("_identity(payload) != _identity(current_payload)", reset)

    def test_linked_account_api_returns_full_account_identifier(self) -> None:
        linked = self.text("app/vps_linked_accounts_latency_hotfix.py")
        self.assertIn('"account_id": account_id', linked)
        self.assertIn('"account_id_masked": base_api.mask_account_id(account_id)', linked)
        self.assertIn("vps-linked-accounts-stale-while-revalidate-v2-full-id", linked)

    def test_frontend_build_ships_production_single_authority_and_scheduler_v2(self) -> None:
        dockerfile = self.text("Dockerfile.frontend")
        self.assertIn("node scripts/build-direct-runtime-v2.mjs", dockerfile)
        self.assertIn("node scripts/finalize-direct-runtime-v2.mjs", dockerfile)
        self.assertIn("node scripts/finalize-direct-ux-v4.mjs", dockerfile)
        self.assertIn("node scripts/finalize-production-controls-v6.mjs", dockerfile)
        self.assertIn("node scripts/finalize-production-controls-v6b.mjs", dockerfile)
        self.assertIn("node scripts/finalize-scheduler-v2.mjs", dockerfile)
        self.assertIn("/direct-hard-stop-fence-v1.js?v=20260818-browser-hard-stop-v1", dockerfile)
        self.assertIn("/deriv-direct-execution-v2.js?v=20260818-browser-direct-v6b", dockerfile)
        self.assertIn("/direct-continuity-checkpoint-v1.js?v=20260818-direct-continuity-v2-split", dockerfile)
        self.assertIn("/direct-transaction-ledger-v6.js?v=20260818-unified-ledger-v9", dockerfile)
        self.assertIn("/direct-runtime-ux-v4.js?v=20260818-runtime-ux-v6", dockerfile)
        self.assertIn("/direct-run-panel-authority-v6.js?v=20260818-scheduler-start-stop-v2", dockerfile)
        self.assertIn("/mobile-layout-authority-v1.js?v=20260818-mobile-layout-v1", dockerfile)
        self.assertIn("/run-panel-usability-v1.js?v=20260818-run-panel-usability-v2", dockerfile)
        self.assertNotIn('/single-run-controller-v1.js', dockerfile)
        self.assertNotIn('/direct-run-panel-authority-v5.js?v=', dockerfile)
        self.assertNotIn('/deriv-direct-execution-v1.js?v=', dockerfile)
        self.assertNotIn('/direct-runtime-ux-v3.js?v=', dockerfile)

    def test_public_testing_runtime_has_zero_execution_authority(self) -> None:
        testing = self.text("dashboard/public-testing-runtime-v1.js")
        self.assertIn('execution_authority: "none"', testing)
        self.assertNotIn("directMainRun", testing)
        self.assertNotIn("/me/resume-trading", testing)
        self.assertNotIn("/me/stop-trading", testing)
        self.assertNotIn("execution-runtime", testing)
        self.assertNotIn("setOptimisticRunUi", testing)
        self.assertNotIn("ACTIVE_RUNTIME_STATES", testing)

    def test_free_testing_premium_finalizer_can_never_render_fail_closed_gate(self) -> None:
        finalizer = self.text("scripts/finalize-production-controls-v6.mjs")
        self.assertIn("testing-free gate cannot render premium lock", finalizer)
        self.assertIn("testing-free boot catch fail-open", finalizer)
        self.assertIn("if (TESTING_FREE_ACCESS)", finalizer)
        self.assertIn('dataset.premiumState = "testing-free"', finalizer)
        self.assertIn("20260818-production-v6", finalizer)

    def test_browser_hard_stop_blocks_buy_synchronously(self) -> None:
        fence = self.text("dashboard/direct-hard-stop-fence-v1.js")
        self.assertIn("function hardStop()", fence)
        self.assertIn("state.stopped = true", fence)
        self.assertIn('hasOwnProperty.call(payload, "buy") && state.stopped', fence)
        self.assertIn("Trading is stopped; BUY blocked locally", fence)
        self.assertIn('path === "/me/direct-execution/arm"', fence)
        self.assertIn("armAfterServerAck", fence)

    def test_server_hard_stop_is_checked_at_uncached_final_buy_boundary(self) -> None:
        state = self.text("app/direct_execution_hard_stop_state.py")
        api = self.text("app/vps_direct_hard_stop_v2.py")
        worker = self.text("app/direct_execution_worker_fence.py")
        backend = self.text("app/vps_backend_api.py")
        self.assertIn("direct_execution:hard_stop:v2:", state)
        self.assertIn("set_direct_hard_stop", api)
        self.assertIn("background_tasks.add_task(_normalize_stopped_account", api)
        self.assertIn('"purchase_allowed": False', api)
        self.assertIn("not direct_hard_stop_active(session, int(row.id))", worker)
        self.assertIn("force=True", worker)
        self.assertIn("hard_stop_browser_owner_stopped_or_open_handoff", worker)
        self.assertIn("install_vps_direct_hard_stop_v2(app)", backend)

    def test_saved_virtual_hook_is_final_server_loss_threshold(self) -> None:
        barrier = self.text("app/custom_virtual_post_loss_barrier_authority.py")
        self.assertIn("virtual_hook_settings_from_session", barrier)
        self.assertIn('patched["virtual_trigger_actual_losses"]', barrier)
        self.assertIn('patched["virtual_protection_enabled"]', barrier)
        self.assertIn("losses >= threshold and debt >= 0.01", barrier)
        self.assertIn("VIRTUAL_WAITING_FOR_WIN", barrier)
        self.assertIn("saved_hook_authoritative=true", barrier)

    def test_equal_split_never_falls_back_to_base_while_debt_exists(self) -> None:
        authority = self.text("app/custom_split_debt_continuity_authority.py")
        cap = self.text("app/custom_split_cap_defaults_authority.py")
        self.assertIn("debt <= 0.009", authority)
        self.assertIn("remaining = split_count", authority)
        self.assertIn("basis = debt", authority)
        self.assertIn("manual._arm_next_split", authority)
        self.assertIn("equal_split_recovery_stake", authority)
        self.assertIn("base_fallback_forbidden=true", authority)
        self.assertIn("install_custom_split_debt_continuity_authority()", cap)

    def test_browser_split_has_persistent_remaining_success_ledger(self) -> None:
        finalizer = self.text("scripts/finalize-production-controls-v6.mjs")
        self.assertIn("splitBasisDebt", finalizer)
        self.assertIn("splitRemainingWins", finalizer)
        self.assertIn("targetProfitPerSuccessfulLeg", finalizer)
        self.assertIn("A losing recovery does not consume a successful part", finalizer)
        self.assertIn("Never fall", finalizer)

    def test_split_state_survives_browser_to_vps_takeover(self) -> None:
        exporter = self.text("scripts/finalize-production-controls-v6b.mjs")
        browser = self.text("dashboard/direct-continuity-checkpoint-v1.js")
        server = self.text("app/vps_direct_execution_checkpoint.py")
        self.assertIn("split_basis_debt: state.splitBasisDebt", exporter)
        self.assertIn("split_remaining_wins: state.splitRemainingWins", exporter)
        self.assertIn("split_basis_debt", browser)
        self.assertIn("split_remaining_wins", browser)
        self.assertIn("_persist_split_handoff", server)
        self.assertIn("equal_split._write_basis_debt", server)
        self.assertIn("manual._write_split_remaining", server)

    def test_run_panel_v6_is_start_stop_only_and_has_no_status_strips(self) -> None:
        authority = self.text("dashboard/direct-run-panel-authority-v6.js")
        self.assertIn('/api/me/direct-execution/status', authority)
        self.assertIn('/api/me/direct-execution/stop', authority)
        self.assertIn("hardStopEverything", authority)
        self.assertIn("DERIVADMIN_DIRECT_HARD_STOP_FENCE_V1", authority)
        self.assertIn('content:"Start"', authority)
        self.assertIn('content:"Stop"', authority)
        self.assertIn("#0c9365", authority)
        self.assertIn("#ef4444", authority)
        self.assertIn("run-panel-execution", authority)
        self.assertIn("direct-bot-state-pill", authority)
        self.assertNotIn("Stopping bot —", authority)
        self.assertNotIn("Bot currently stopped", authority)
        self.assertNotIn("new MutationObserver", authority)
        self.assertNotIn("startEverything", authority)

    def test_mobile_run_panel_totals_and_reopen_handle_cannot_be_hidden(self) -> None:
        authority = self.text("dashboard/mobile-layout-authority-v1.js")
        self.assertIn("run-panel-reopen-v1", authority)
        self.assertIn('handle.dataset.runPanelToggle = ""', authority)
        self.assertIn(".global-run-panel.collapsed .run-panel-reopen-v1", authority)
        self.assertIn("grid-template-rows:repeat(2,minmax(40px,auto))", authority)
        self.assertIn(".global-run-panel.open .run-panel-bar", authority)
        self.assertIn("position:relative!important", authority)
        self.assertIn("env(safe-area-inset-bottom", authority)
        self.assertNotIn("setInterval", authority)

    def test_mobile_builder_is_strictly_contained_inside_phone_viewport(self) -> None:
        authority = self.text("dashboard/mobile-layout-authority-v1.js")
        self.assertIn("@media (max-width:700px)", authority)
        self.assertIn("max-width:100vw!important", authority)
        self.assertIn("overflow-x:hidden!important", authority)
        self.assertIn(".restored-builder .form-grid.two", authority)
        self.assertIn("grid-template-columns:minmax(0,1fr)!important", authority)
        self.assertIn(".restored-builder .builder-market-grid", authority)
        self.assertIn("width:100%!important", authority)
        self.assertIn("overflow-wrap:anywhere", authority)
        self.assertNotIn("/me/resume-trading", authority)
        self.assertNotIn("/me/stop-trading", authority)

    def test_reset_is_local_first_and_never_toggles_execution(self) -> None:
        authority = self.text("dashboard/direct-run-panel-authority-v6.js")
        reset = authority.split("function resetTrades()", 1)[1].split("// Window capture", 1)[0]
        self.assertIn('window.confirm("Do you want to reset all trades?")', reset)
        self.assertIn("engine()?.clear?.()", reset)
        self.assertIn("derivadmin:direct-reset-all", reset)
        self.assertIn("xhrClearAll()", reset)
        self.assertNotIn("hardStopEverything", reset)
        self.assertNotIn("state.serverActive =", reset)

    def test_transactions_have_time_market_type_spots_buy_and_profit_columns(self) -> None:
        finalizer = self.text("scripts/finalize-production-controls-v6.mjs")
        ledger = self.text("dashboard/direct-transaction-ledger-v6.js")
        exporter = self.text("scripts/finalize-production-controls-v6b.mjs")
        self.assertIn("Time / Market", finalizer)
        self.assertIn("Entry / Exit", finalizer)
        self.assertIn("Buy price", finalizer)
        self.assertIn("Profit / Loss", finalizer)
        self.assertIn("transactionMarketLabel", finalizer)
        self.assertIn("transactionTimeLabel", finalizer)
        self.assertIn('"1HZ100V": "V100 (1s)"', finalizer)
        self.assertIn('second: "2-digit"', finalizer)
        self.assertIn("direct-local-transaction-row-v6", ledger)
        self.assertIn('"1HZ100V": "V100 1S"', ledger)
        self.assertIn('second: "2-digit"', ledger)
        self.assertIn("entry_spot: contract?.entry_spot", exporter)

    def test_transactions_no_longer_get_strategy_checker_and_no_400ms_loop(self) -> None:
        finalizer = self.text("scripts/finalize-production-controls-v6.mjs")
        self.assertIn("remove strategy checker from Transactions", finalizer)
        self.assertIn("renderLoadedBadge", finalizer)
        self.assertIn("setInterval(() => { unobserve(); try { renderRunState();", finalizer)

    def test_build_compiler_preserves_result_route_and_special_comparators(self) -> None:
        compiler = self.text("scripts/build-direct-runtime-v2.mjs")
        self.assertIn('condition.operator === "all_even"', compiler)
        self.assertIn('condition.operator === "all_odd"', compiler)
        self.assertIn('routing?.enabled && routing?.after_loss', compiler)
        self.assertIn('normalizeExecutionRoute(routing.after_loss, "after_loss")', compiler)
        self.assertIn("activeExecutionRoute()", compiler)
        self.assertIn("active-route MET/NOT-MET parity", compiler)

    def test_hydrated_history_can_never_own_a_buy_entry(self) -> None:
        finalizer = self.text("scripts/finalize-direct-runtime-v2.mjs")
        fence = self.text("dashboard/direct-financial-fence-v1.js")
        self.assertIn("if (tick?.__history_hydration) return;", finalizer)
        self.assertIn("ticks_history", fence)
        self.assertIn("__history_hydration: true", fence)
        self.assertIn("hydrationPending", fence)

    def test_start_confirmation_summarizes_current_builder(self) -> None:
        guard = self.text("dashboard/direct-interaction-guard-v3.js")
        self.assertIn("function builderSummary()", guard)
        self.assertIn("Start this trading strategy?", guard)
        self.assertIn("All 10 markets", guard)
        self.assertIn("Proceed", guard)
        self.assertIn("A strategy is already running", guard)


if __name__ == "__main__":
    unittest.main()
