from __future__ import annotations

from pathlib import Path
import unittest

from app.text_to_strategy_api import _MAX_WORDS, _compile, _words


ROOT = Path(__file__).resolve().parents[1]


class TextToStrategyAction2Tests(unittest.TestCase):
    def test_risk_managers_example_maps_to_existing_strategy_schema(self) -> None:
        text = (
            "Create a strategy called Risk Managers. Trade Over 3 on Volatility 100 1s "
            "when the last 3 digits are less than or equal to 3 and the Over 3 percentage "
            "over the last 1000 ticks is above 78%. Stake 0.50 and use a take profit of 2 "
            "with a stop loss of 3."
        )
        result = _compile(text)

        self.assertEqual(result["name"], "Risk Managers")
        self.assertEqual(result["custom_strategy"]["trade_type"], "over")
        self.assertEqual(result["custom_strategy"]["prediction"], 3)
        self.assertEqual(result["custom_strategy"]["market_mode"], "single")
        self.assertEqual(result["custom_strategy"]["markets"], ["1HZ100V"])

        conditions = result["custom_strategy"]["conditions"]
        digit = next(item for item in conditions if item["kind"] == "digit_compare")
        percentage = next(item for item in conditions if item["kind"] == "percentage")
        self.assertEqual(digit, {"kind": "digit_compare", "window": 3, "operator": "<=", "value": 3})
        self.assertEqual(percentage["target"], "over")
        self.assertEqual(percentage["value"], 3)
        self.assertEqual(percentage["window"], 1000)
        self.assertEqual(percentage["threshold"], 78.0)
        self.assertEqual(result["settings"]["stake_amount"], 0.5)
        self.assertEqual(result["settings"]["take_profit"], 2.0)
        self.assertEqual(result["settings"]["stop_loss"], 3.0)
        self.assertFalse(result["direct_execution_allowed"])
        self.assertTrue(result["review_required"])

    def test_incomplete_description_returns_nearest_supported_workable_draft(self) -> None:
        result = _compile("Make me a simple bot that trades when the market looks good.")
        self.assertEqual(result["status"], "ready_for_review")
        self.assertEqual(result["compiler"], "nearest-supported-v1")
        self.assertEqual(result["custom_strategy"]["trade_type"], "over")
        self.assertEqual(result["custom_strategy"]["prediction"], 3)
        self.assertEqual(result["custom_strategy"]["markets"], ["1HZ100V"])
        self.assertTrue(result["custom_strategy"]["conditions"])
        self.assertGreaterEqual(len(result["adjustments"]), 2)
        self.assertFalse(result["direct_execution_allowed"])

    def test_word_limit_is_exactly_250(self) -> None:
        self.assertEqual(_MAX_WORDS, 250)
        self.assertEqual(len(_words("word " * 250)), 250)
        self.assertEqual(len(_words("word " * 251)), 251)

    def test_mobile_text_to_strategy_ui_matches_action_two_contract(self) -> None:
        js = (ROOT / "dashboard" / "text-to-strategy-v1.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "text-to-strategy-v1.css").read_text(encoding="utf-8")
        self.assertIn("const MAX_WORDS = 250", js)
        self.assertIn("Text to Strategy", js)
        self.assertIn("Describe your strategy in plain English", js)
        self.assertIn("Generate Strategy", js)
        self.assertIn("Templates you can modify", js)
        self.assertIn("Best possible interpretation", js)
        self.assertIn('data-automation-scaffold="ai"', js)
        self.assertIn("/me/text-to-strategy/compile", js)
        self.assertIn("foa-ai-template-grid", css)
        self.assertIn("@media (max-width: 430px)", css)

    def test_public_landing_uses_new_mobile_automation_identity(self) -> None:
        js = (ROOT / "dashboard" / "prelogin-landing-v2.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "prelogin-landing-v2.css").read_text(encoding="utf-8")
        self.assertIn("Build it.", js)
        self.assertIn("Describe it.", js)
        self.assertIn("Schedule it.", js)
        self.assertIn("Strategy Builder", js)
        self.assertIn("Text to Strategy", js)
        self.assertIn("Schedule Trading", js)
        self.assertIn("Login with Deriv", js)
        self.assertIn("Register", js)
        self.assertIn("Built for mobile", js)
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn(".foa-public-mobile-brand", css)

    def test_full_vps_build_installs_action_two_without_worker_changes(self) -> None:
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        api = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        self.assertIn("/text-to-strategy-v1.css?v=20260817-1", build)
        self.assertIn("/text-to-strategy-v1.js?v=20260817-1", build)
        self.assertIn("nearest-supported-v1-250-words", build)
        self.assertIn("mobile-automation-action2-v1", build)
        self.assertIn("install_text_to_strategy_api(app)", api)
        self.assertNotIn("custom_strategy_worker", api)


if __name__ == "__main__":
    unittest.main()
