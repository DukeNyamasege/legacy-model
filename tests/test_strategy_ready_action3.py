from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StrategyReadyAction3Tests(unittest.TestCase):
    def test_strategy_ready_has_complete_review_surface(self) -> None:
        source = (ROOT / "dashboard" / "strategy-ready-v1.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "strategy-ready-v1.css").read_text(encoding="utf-8")

        for label in (
            "Strategy Ready",
            "Market & Contract",
            "Rules understood",
            "Stake & Session Risk",
            "Re-analysis & Virtual Guard",
            "BEST POSSIBLE INTERPRETATION",
            "Save Strategy",
            "Trade Now",
            "Schedule",
            "Open in Strategy Builder",
        ):
            self.assertIn(label, source)

        self.assertIn("data-ready-condition", source)
        self.assertIn("data-ready-setting", source)
        self.assertIn("data-ready-reanalyze", source)
        self.assertIn("data-ready-virtual", source)
        self.assertIn("@media (max-width: 720px)", css)
        self.assertIn("@media (max-width: 500px)", css)
        self.assertIn("@media (max-width: 360px)", css)

    def test_trade_now_reuses_existing_custom_strategy_execution_authority(self) -> None:
        source = (ROOT / "dashboard" / "strategy-ready-v1.js").read_text(encoding="utf-8")
        save_index = source.index('fetch("/me/custom-strategy"')
        start_index = source.index('fetch("/me/resume-trading"')
        trades_index = source.index('navigate("trades")')

        self.assertLess(save_index, start_index)
        self.assertLess(start_index, trades_index)
        self.assertIn('body: JSON.stringify({ mode: "start_again" })', source)
        self.assertNotIn("custom_strategy_worker", source)
        self.assertNotIn("/buy", source)
        self.assertNotIn("proposal_open_contract", source)

    def test_schedule_is_a_handoff_and_does_not_start_execution(self) -> None:
        source = (ROOT / "dashboard" / "strategy-ready-v1.js").read_text(encoding="utf-8")
        block = source.split("function scheduleAction()", 1)[1].split("function openBuilder()", 1)[0]
        self.assertIn("foa-schedule-selected-strategy-v1", source)
        self.assertIn('navigate("schedule")', block)
        self.assertNotIn("/me/resume-trading", block)
        self.assertNotIn("/me/auto-trade", block)

    def test_ai_strategy_is_saved_into_existing_unified_template_store(self) -> None:
        ready = (ROOT / "dashboard" / "strategy-ready-v1.js").read_text(encoding="utf-8")
        home = (ROOT / "dashboard" / "automation-home-v1.js").read_text(encoding="utf-8")
        library = (ROOT / "dashboard" / "strategy-template-library.js").read_text(encoding="utf-8")

        self.assertIn('const USER_TEMPLATE_KEY = "foa-user-strategy-templates-v1"', ready)
        self.assertIn('source: "ai"', ready)
        self.assertIn('item?.source === "ai"', home)
        self.assertIn('filter((item) => item?.source === "ai")', home)
        self.assertIn('const STORAGE_KEY = "foa-user-strategy-templates-v1"', library)
        self.assertIn("readLocalTemplates()", library)

    def test_action_three_route_is_first_class_and_ai_nav_stays_active(self) -> None:
        home = (ROOT / "dashboard" / "automation-home-v1.js").read_text(encoding="utf-8")
        self.assertIn('"ai", "ready", "schedule"', home)
        self.assertIn('name === "ai" && route === "ready"', home)
        self.assertIn('route === "ready"', home)
        self.assertIn("Action 3 Strategy Ready owns the main content", home)
        self.assertIn("window.FOA_AUTOMATION_NAVIGATE = navigate", home)

    def test_vps_build_installs_action_three_assets(self) -> None:
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertIn("/strategy-ready-v1.css?v=20260817-1", build)
        self.assertIn("/strategy-ready-v1.js?v=20260817-1", build)
        self.assertIn("automation-home-action3-v1", build)
        self.assertIn("review-save-trade-schedule-v1", build)
        self.assertIn("built-in-my-ai-unified-v1", build)
        self.assertIn("/automation-home-v1.js?v=20260817-3", build)

    def test_action_three_javascript_is_syntax_valid(self) -> None:
        for path in (
            ROOT / "dashboard" / "strategy-ready-v1.js",
            ROOT / "dashboard" / "automation-home-v1.js",
        ):
            result = subprocess.run(
                ["node", "--check", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f"{path.name}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
