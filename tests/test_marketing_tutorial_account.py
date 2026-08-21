from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class MarketingTutorialAccountTests(unittest.TestCase):
    def test_marketing_split_is_not_installed_in_backend(self) -> None:
        source = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        self.assertNotIn("from app.marketing_tutorial_account import", source)
        self.assertNotIn("install_marketing_tutorial_account(app)", source)
        self.assertIn("Marketing/tutorial DOT/ROT splitting is presentation-only in the browser", source)
        self.assertIn("install_final_linked_accounts_6f2(app)", source)
        self.assertIn("install_vps_demo_balance_reset(app)", source)
        self.assertIn("install_vps_cross_device_runtime_sync(app)", source)

    def test_frontend_uses_exact_ui_pair_and_75_25_split(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn('const DOT_ID = "DOT93427967"', source)
        self.assertIn('const ROT_ID = "ROT92069206"', source)
        self.assertIn("const DOT_SHARE = 0.75", source)
        self.assertIn("const ROT_SHARE = 0.25", source)
        self.assertIn("provider - rot", source)

    def test_frontend_switch_is_visual_only_and_keeps_one_managed_account(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn("Both visible rows keep the same managed account ID", source)
        self.assertIn('rotRow.dataset.accountId = String(managedId())', source)
        self.assertIn('row.dataset.marketingView = selectedView', source)
        self.assertIn('window.addEventListener("click"', source)
        self.assertNotIn('fetch("/api/me/switch-account"', source)
        self.assertNotIn('fetch("/me/switch-account"', source)

    def test_provider_balance_events_change_only_opening_visual_partition(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn("const delta = provider - ledger.provider", source)
        self.assertIn('if (targetView === "rot") ledger.rot = roundMoney(ledger.rot + delta)', source)
        self.assertIn('else ledger.dot = roundMoney(ledger.dot + delta)', source)
        self.assertIn("ledger.provider = roundMoney(provider)", source)
        self.assertIn("detail.balance = visibleBalance(ledger)", source)
        self.assertIn("rememberContractOwner(contractId, view())", source)
        self.assertIn("movementView(detail)", source)
        self.assertNotIn("absolute * ROT_SHARE", source)
        self.assertNotIn("absolute * DOT_SHARE", source)

    def test_standard_deriv_execution_is_not_wrapped_or_guarded_by_marketing_ui(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertNotIn("WebSocket.prototype.send =", source)
        self.assertNotIn("guardedDemoPartitionSend", source)
        self.assertNotIn("demo partition insufficient", source)
        self.assertIn("The backend and Deriv still receive/use the real provider balance", source)

    def test_demo_reset_resplits_ui_only(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn('window.addEventListener("derivadmin:demo-balance-reset"', source)
        self.assertIn("const ledger = splitReset(provider)", source)
        self.assertIn("detail.balance = visibleBalance(ledger)", source)
        self.assertIn("writeOwners({})", source)

    def test_final_production_layer_removes_banner_and_restores_lower_stats(self) -> None:
        source = (ROOT / "scripts" / "finalize-marketing-ui-layout-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("Run totals restored below tab content", source)
        self.assertIn("tutorial implementation badge removed", source)
        self.assertIn("run panel totals are not below tab content", source)
        self.assertIn('document.querySelectorAll(".marketing-tutorial-runtime-badge")', source)
        self.assertIn("display_balance: () => displayBalance()", source)
        self.assertIn("marketingUi.display_balance?.()", source)
        self.assertIn("if (row.dataset.marketingView) return;", source)
        self.assertIn("backend and Deriv execution path unchanged", source)

    def test_final_production_selector_is_exactly_dot_plus_synthetic_rot(self) -> None:
        source = (ROOT / "scripts" / "finalize-marketing-ui-layout-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("selector shows only DOT plus synthetic ROT", source)
        self.assertIn('.top-account-switch [data-account-id]', source)
        self.assertIn("rowId !== providerId) row.remove()", source)
        self.assertIn('row.dataset.accountKindRow = isRot ? "real" : "demo"', source)
        self.assertIn('flag.className = "deriv-real-flag"', source)
        self.assertIn('strong.className = "marketing-rot-balance"', source)
        self.assertIn("extra linked real rows stay hidden", source)

    def test_marketing_tabs_cannot_switch_to_hidden_real_backend_account(self) -> None:
        source = (ROOT / "scripts" / "finalize-marketing-ui-layout-v1.mjs").read_text(encoding="utf-8")
        self.assertIn('tab.removeAttribute("data-account-id")', source)
        self.assertIn("tab.dataset.marketingView = targetView", source)
        self.assertIn("Demo/Real tabs are UI-only DOT/ROT selectors", source)
        self.assertIn("with no backend real-account ID", source)

    def test_fresh_deploy_rebases_ui_split_from_current_provider_balance(self) -> None:
        source = (ROOT / "scripts" / "finalize-marketing-ui-layout-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("derivadmin-marketing-demo-ui-ledger-v6", source)
        self.assertIn("Number(value.version) === 6", source)
        self.assertIn("fresh current provider balance is split 75% DOT / 25% ROT", source)
        self.assertIn("marketing-dot-rot-v7-safe-two-view-ui", source)

    def test_buy_ownership_is_not_blocked_by_global_history_hydration(self) -> None:
        source = (ROOT / "scripts" / "finalize-browser-buy-readiness-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("current event-owned browser epoch no longer depends on global history hydration", source)
        self.assertIn(
            '`  function leaseAllowsBuy() {\\n    return Boolean(state.armed && state.epoch);\\n  }`',
            source,
        )
        self.assertIn(
            'if (fence.includes("state.armed && state.epoch && state.hydrationPending <= 0"))',
            source,
        )
        self.assertIn(
            'throw new Error("browser-buy-readiness current global hydration BUY lock survived")',
            source,
        )
        self.assertIn("ownership_ready:", source)
        self.assertIn("history_pending:", source)

    def test_buy_diagnostics_separate_ownership_history_and_conditions(self) -> None:
        source = (ROOT / "scripts" / "finalize-browser-buy-readiness-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("browser financial ownership is not armed yet", source)
        self.assertIn("loading the required previous Deriv ticks", source)
        self.assertIn("financial.history_pending || financial.hydrationPending", source)
        self.assertIn("observable public reconnect diagnosis", source)

    def test_public_market_websocket_uses_one_current_socket_and_bounded_recovery(self) -> None:
        source = (ROOT / "scripts" / "finalize-browser-buy-readiness-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("Public Deriv WebSocket opening handshake exceeded 15 seconds", source)
        self.assertIn("state.publicWs !== ws || generation !== state.publicGeneration", source)
        self.assertIn("publicReconnectTimer", source)
        self.assertIn("publicRetryMs", source)
        self.assertIn("Math.min(15000", source)
        self.assertIn("connectPublic().catch(() => {})", source)
        self.assertIn("if (!state.running || state.publicReconnectTimer) return", source)
        self.assertIn("Auto Trading remains ON while public market transport recovers", source)
        self.assertIn('if (engine.includes("setTimeout(connectPublic, 700)"))', source)
        self.assertIn("legacy fixed public reconnect loop survived", source)

    def test_public_socket_stale_close_cannot_clear_replacement(self) -> None:
        source = (ROOT / "scripts" / "finalize-browser-buy-readiness-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("const current = state.publicWs === ws && generation === state.publicGeneration", source)
        self.assertIn("if (!current)", source)
        self.assertIn("state.publicWs = null", source)
        self.assertIn("Stale public Deriv WebSocket closed after replacement", source)

    def test_current_deriv_errors_array_is_handled_during_history_hydration(self) -> None:
        source = (ROOT / "scripts" / "finalize-browser-buy-readiness-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("Array.isArray(message?.errors)", source)
        self.assertIn("const firstError = message?.error", source)
        self.assertIn("firstError.message || firstError.code", source)
        self.assertIn("Deriv errors-array history failures retry", source)

    def test_public_websocket_finalizer_preserves_oauth_otp_helper_scope(self) -> None:
        source = (ROOT / "scripts" / "finalize-browser-buy-readiness-v1.mjs").read_text(encoding="utf-8")
        self.assertIn('engine.includes("  function clearDirectBrowserCredential() {")', source)
        self.assertIn('"async function directBrowserBootstrap("', source)
        self.assertIn('"async function requestDirectDerivOtp("', source)
        self.assertIn('"function directDerivError("', source)
        self.assertIn('"const wsUrl = await requestDirectDerivOtp(false);"', source)
        self.assertIn("single-owner public market WebSocket transport without deleting OAuth helpers", source)
        self.assertIn("arm before authenticated private Deriv connection", source)
        self.assertIn("eager private Deriv connection still runs before arm", source)
        self.assertIn("OAuth bootstrap and requestDirectDerivOtp helpers survive", source)

    def test_finalizers_run_in_safe_order(self) -> None:
        source = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
        copy_history = source.index("COPY scripts/finalize-history-preload-runpanel-v1.mjs")
        copy_buy = source.index("COPY scripts/finalize-browser-buy-readiness-v1.mjs")
        copy_layout = source.index("COPY scripts/finalize-marketing-ui-layout-v1.mjs")
        run_history = source.rindex("node scripts/finalize-history-preload-runpanel-v1.mjs")
        run_buy = source.rindex("node scripts/finalize-browser-buy-readiness-v1.mjs")
        run_layout = source.rindex("node scripts/finalize-marketing-ui-layout-v1.mjs")
        self.assertLess(copy_history, copy_buy)
        self.assertLess(copy_buy, copy_layout)
        self.assertLess(run_history, run_buy)
        self.assertLess(run_buy, run_layout)
        self.assertIn("node --check dist/direct-runtime-ux-v4.js", source)
        self.assertIn("node --check dist/direct-demo-reset-router-v1.js", source)
        self.assertIn("node --check dist/direct-financial-fence-v1.js", source)

    def test_frontend_asset_is_cache_busted_in_final_production_layers(self) -> None:
        marketing = (ROOT / "scripts" / "finalize-marketing-ui-layout-v1.mjs").read_text(encoding="utf-8")
        buy = (ROOT / "scripts" / "finalize-browser-buy-readiness-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("20260821-marketing-ui-v7", marketing)
        self.assertIn("20260821-marketing-balance-v9", marketing)
        self.assertIn("20260821-runpanel-bottom-v4", marketing)
        self.assertIn("20260821-public-history-errors-v1", buy)
        self.assertIn("20260821-otp-helper-preserved-v2", buy)


if __name__ == "__main__":
    unittest.main()
