from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StrategyTemplateRuntimeUpgradeTests(unittest.TestCase):
    def test_template_library_covers_three_analysis_modes_and_all_trade_types(self) -> None:
        source = (ROOT / "dashboard" / "strategy-template-library.js").read_text(
            encoding="utf-8"
        )
        # One helper declaration plus exactly 24 built-in preset invocations:
        # 3 analysis modes x 8 supported contract sides.
        self.assertEqual(source.count("\n    preset("), 24)
        for analysis in ("percentage", "last_digit", "combined"):
            self.assertIn(f'"{analysis}"', source)
        for side in ("over", "under", "matches", "differs", "odd", "even", "rise", "fall"):
            self.assertIn(f'"{side}"', source)

    def test_golden_default_and_over3_split_template_are_present(self) -> None:
        source = (ROOT / "dashboard" / "strategy-template-library.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('const DEFAULT_ID = "golden-over1-recovery-over4"', source)
        self.assertIn('"Over 1 Recovery Over 4 Golden Bot"', source)
        self.assertIn('percentageRule: { target: "over", value: 1, window: 1000, operator: ">", threshold: 80 }', source)
        self.assertIn('money: { stake: 5.5, takeProfit: 100, stopLoss: 1000, martingale: 2.1, ticks: 1 }', source)
        self.assertIn('virtualHook: { enabled: true, enterAfterLosses: 2, exitAfterConsecutiveWins: 1 }', source)
        self.assertIn('tradeType: "over"', source)
        self.assertIn('prediction: 4', source)
        self.assertIn('lastRule: { window: 5, operator: "<=", value: 5 }', source)
        self.assertIn('"Over 3 Spread Recovery x2"', source)
        self.assertIn('trade: { group: "over_under", side: "over", prediction: 3 }', source)
        self.assertIn('recoveryMode: "split", splitCount: 2', source)

    def test_personal_templates_are_local_and_survive_builder_reset(self) -> None:
        source = (ROOT / "dashboard" / "strategy-template-library.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('const STORAGE_KEY = "foa-user-strategy-templates-v1"', source)
        self.assertIn("Save Current as Template", source)
        self.assertIn('localStorage.setItem(STORAGE_KEY, JSON.stringify(rows))', source)
        self.assertIn("[data-reset-strategy]", source)
        self.assertIn("Your locally saved My Templates will stay available", source)
        self.assertNotIn("localStorage.clear", source)
        self.assertNotIn("localStorage.removeItem(STORAGE_KEY)", source)

    def test_template_picker_stays_mounted_while_user_selects(self) -> None:
        source = (ROOT / "dashboard" / "strategy-template-library.js").read_text(
            encoding="utf-8"
        )
        build = (ROOT / "scripts" / "build-netlify.mjs").read_text(encoding="utf-8")
        loader = (ROOT / "dashboard" / "strategy-edit-authority.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("function syncLibraryState(section)", source)
        self.assertIn("function refreshTemplateOptions(section)", source)
        self.assertIn("if (current) {", source)
        self.assertIn("syncLibraryState(current);", source)
        self.assertNotIn("current?.remove();", source)
        self.assertNotIn("scheduleEnhance();\n    });\n    q(\"#strategy-template-load\"", source)
        self.assertIn("/strategy-template-library.js?v=20260814-2", build)
        self.assertIn("/strategy-template-library.js?v=20260814-2", loader)

    def test_split_recovery_draft_is_not_overwritten_by_silent_get(self) -> None:
        source = (ROOT / "dashboard" / "result-based-strategy.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("force || !state.recoveryTouched", source)
        self.assertIn("state.recoveryTouched = true", source)
        self.assertIn('mode: "split"', source)
        self.assertIn("split_count: whole(state.splitCount, 2, 1, 3)", source)
        self.assertIn("FOA_RESULT_BASED_API", source)
        self.assertIn("applyState", source)

    def test_account_switch_and_pnl_runtime_are_atomic_and_signed(self) -> None:
        frontend = (ROOT / "dashboard" / "runtime-ux-authority.js").read_text(
            encoding="utf-8"
        )
        backend = (ROOT / "app" / "seamless_account_switch.py").read_text(
            encoding="utf-8"
        )
        entrypoint = (ROOT / "app" / "netlify_backend_api.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('strong.textContent = "Syncing…"', frontend)
        self.assertIn("foa-pnl-positive", frontend)
        self.assertIn("foa-pnl-negative", frontend)
        self.assertIn('url.includes("/me/switch-account")', frontend)
        self.assertIn('"me": me', backend)
        self.assertIn('"balance": me.get("balance")', backend)
        self.assertIn("install_seamless_account_switch(app)", entrypoint)

    def test_explicit_start_bypasses_old_idle_sleep(self) -> None:
        source = (ROOT / "app" / "custom_strategy_startup_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_FAST_START_POLL_SECONDS = 0.5", source)
        self.assertIn("CUSTOM_RUNTIME_FAST_START_WAKEUP", source)
        self.assertIn("CUSTOM_RUNTIME_EXPLICIT_START_PICKUP", source)
        self.assertIn("await self._refresh_runtime_accounts_if_needed()", source)
        self.assertIn("_ensure_sessions_for_valid_clients", source)


if __name__ == "__main__":
    unittest.main()
