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
        self.assertIn("only the browser heartbeat may extend", authority)
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

    def test_frontend_build_uses_generated_direct_v2_and_one_runtime_ux(self) -> None:
        dockerfile = self.text("Dockerfile.frontend")
        self.assertIn("node scripts/build-direct-runtime-v2.mjs", dockerfile)
        self.assertIn("node scripts/finalize-direct-runtime-v2.mjs", dockerfile)
        self.assertIn("node scripts/finalize-direct-ux-v4.mjs", dockerfile)
        self.assertIn("/deriv-direct-execution-v2.js?v=20260818-browser-direct-v2", dockerfile)
        self.assertIn("/direct-runtime-ux-v4.js?v=20260818-runtime-ux-v4", dockerfile)
        self.assertIn("/direct-run-panel-authority-v5.js?v=20260818-single-run-v5", dockerfile)
        self.assertNotIn('/single-run-controller-v1.js', dockerfile)
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

    def test_final_run_panel_authority_stops_both_owners_but_never_starts(self) -> None:
        authority = self.text("dashboard/direct-run-panel-authority-v5.js")
        self.assertIn('/api/me/direct-execution/status', authority)
        self.assertIn('/api/me/direct-execution/stop', authority)
        self.assertIn('window.addEventListener("click"', authority)
        self.assertIn("function effectiveRunning()", authority)
        self.assertIn("stopEverything", authority)
        self.assertIn("direct-live-transactions-v5", authority)
        self.assertIn("height:calc(100dvh - 72px)", authority)
        self.assertNotIn("/me/resume-trading", authority)

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

    def test_single_run_button_sticky_journal_and_full_balance_contract(self) -> None:
        ux = self.text("dashboard/direct-runtime-ux-v3.js")
        self.assertIn('[data-run-execution-toggle]', ux)
        self.assertIn("node.remove()", ux)
        self.assertIn("Bot currently executing trades", ux)
        self.assertIn("Bot currently stopped", ux)
        self.assertIn("NOT MET · ANALYZING", ux)
        self.assertIn("MET · ENTRY FOUND", ux)
        self.assertIn("TAB_STORE", ux)
        self.assertIn("text-overflow:clip", ux)
        selected = self.text("scripts/finalize-direct-ux-v4.mjs")
        self.assertIn("account-switch-summary small", selected)

    def test_reset_waits_for_real_api_confirmation(self) -> None:
        reset = self.text("dashboard/direct-reset-authority-v1.js")
        self.assertIn('window.confirm("Do you want to reset all trades?")', reset)
        self.assertIn('body: JSON.stringify({ scope: "all" })', reset)
        self.assertIn("if (!response.ok)", reset)
        self.assertIn("DERIVADMIN_DIRECT_EXECUTION_V1?.clear", reset)

    def test_start_confirmation_summarizes_current_builder(self) -> None:
        guard = self.text("dashboard/direct-interaction-guard-v3.js")
        self.assertIn("function builderSummary()", guard)
        self.assertIn("Start this trading strategy?", guard)
        self.assertIn("All 10 markets", guard)
        self.assertIn("Proceed", guard)
        self.assertIn("A strategy is already running", guard)


if __name__ == "__main__":
    unittest.main()
