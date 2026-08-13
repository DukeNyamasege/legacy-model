from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from app.custom_strategy_last_digit_prediction import (
    _resolve_prediction,
    install_custom_strategy_last_digit_prediction,
)

install_custom_strategy_last_digit_prediction()

from app.custom_strategy_result_router import _route_from_debt  # noqa: E402
from app.custom_strategy_result_routing import (  # noqa: E402
    AFTER_LOSS,
    AFTER_WIN,
    merge_result_route,
    normalize_result_routing,
)
from app.custom_strategy_v1 import normalize_custom_strategy  # noqa: E402
from app.manual_martingale_v2 import normalize_manual_martingale_settings  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


class ResultRoutingAndRecoverySpreadTests(unittest.TestCase):
    def primary_config(self) -> dict:
        return normalize_custom_strategy(
            {
                "market_mode": "selected",
                "markets": ["1HZ100V"],
                "trade_type": "over",
                "prediction": 1,
                "duration_ticks": 1,
                "conditions": [
                    {
                        "kind": "digit_compare",
                        "window": 2,
                        "operator": ">=",
                        "value": 2,
                    }
                ],
                "match": "all",
                "virtual_hook_enabled": True,
            }
        )

    def test_disabled_routing_is_backward_compatible(self) -> None:
        primary = self.primary_config()
        routing = normalize_result_routing({"enabled": False})
        self.assertEqual(merge_result_route(primary, routing, AFTER_LOSS), primary)
        self.assertEqual(merge_result_route(primary, routing, AFTER_WIN), primary)

    def test_after_loss_can_change_contract_and_analysis_independently(self) -> None:
        primary = self.primary_config()
        routing = normalize_result_routing(
            {
                "enabled": True,
                "after_loss": {
                    "trade_type": "over",
                    "prediction": 4,
                    "duration_ticks": 2,
                    "conditions": [
                        {
                            "kind": "percentage",
                            "window": 100,
                            "target": "even",
                            "operator": ">=",
                            "threshold": 55,
                        },
                        {
                            "kind": "direction",
                            "window": 2,
                            "direction": "rising",
                        },
                    ],
                },
            }
        )
        recovery = merge_result_route(primary, routing, AFTER_LOSS)
        normal = merge_result_route(primary, routing, AFTER_WIN)

        self.assertEqual(normal["trade_type"], "over")
        self.assertEqual(normal["prediction"], 1)
        self.assertEqual(normal["conditions"], primary["conditions"])
        self.assertEqual(recovery["trade_type"], "over")
        self.assertEqual(recovery["prediction"], 4)
        self.assertEqual(recovery["duration_ticks"], 2)
        self.assertEqual(recovery["conditions"][0]["kind"], "percentage")
        self.assertEqual(recovery["conditions"][1]["kind"], "direction")
        self.assertEqual(recovery["markets"], primary["markets"])
        self.assertEqual(recovery["virtual_hook"], primary["virtual_hook"])

    def test_after_loss_supports_other_contract_families(self) -> None:
        primary = self.primary_config()
        for trade_type, prediction in (
            ("odd", None),
            ("even", None),
            ("rise", None),
            ("fall", None),
            ("under", 5),
            ("matches", 3),
            ("differs", 8),
        ):
            with self.subTest(trade_type=trade_type):
                route = normalize_result_routing(
                    {
                        "enabled": True,
                        "after_loss": {
                            "trade_type": trade_type,
                            "prediction": prediction,
                            "duration_ticks": 1,
                            "conditions": [
                                {
                                    "kind": "digit_compare",
                                    "window": 1,
                                    "operator": "all_even",
                                }
                            ],
                        },
                    }
                )
                active = merge_result_route(primary, route, AFTER_LOSS)
                self.assertEqual(active["trade_type"], trade_type)
                self.assertEqual(active["prediction"], prediction)
                self.assertEqual(active["conditions"][0]["operator"], "all_even")

    def test_matches_differs_dynamic_prediction_modes_are_exact(self) -> None:
        primary = self.primary_config()
        for mode in ("last_digit", "most_appearing", "second_most_appearing"):
            with self.subTest(mode=mode):
                route = normalize_result_routing(
                    {
                        "enabled": True,
                        "after_loss": {
                            "trade_type": "matches",
                            "prediction": mode,
                            "duration_ticks": 1,
                            "conditions": [
                                {
                                    "kind": "digit_compare",
                                    "window": 6,
                                    "operator": ">=",
                                    "value": 0,
                                }
                            ],
                        },
                    }
                )
                active = merge_result_route(primary, route, AFTER_LOSS)
                self.assertEqual(active["trade_type"], "matches")
                self.assertIsNone(active["prediction"])
                self.assertEqual(active["reanalyze"]["prediction_mode"], mode)

        config = {"conditions": [{"window": 6}]}
        digits = [1, 2, 2, 3, 3, 3]
        self.assertEqual(_resolve_prediction("last_digit", digits, config), 3)
        self.assertEqual(_resolve_prediction("most_appearing", digits, config), 3)
        self.assertEqual(_resolve_prediction("second_most_appearing", digits, config), 2)

    def test_actual_debt_is_the_route_selector(self) -> None:
        self.assertEqual(_route_from_debt(0), AFTER_WIN)
        self.assertEqual(_route_from_debt(0.009), AFTER_WIN)
        self.assertEqual(_route_from_debt(0.01), AFTER_LOSS)
        self.assertEqual(_route_from_debt(70.0), AFTER_LOSS)

    def test_spread_supports_one_two_or_three_successful_parts(self) -> None:
        for parts in (1, 2, 3):
            settings = normalize_manual_martingale_settings(
                {"mode": "split", "split_count": parts}
            )
            self.assertEqual(settings["mode"], "split")
            self.assertEqual(settings["split_count"], parts)
            self.assertEqual(settings["policy"], "split_exact_debt_recovery")

    def test_final_ui_layers_are_wired_into_production_build(self) -> None:
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        ui = (ROOT / "dashboard" / "result-based-strategy.js").read_text(encoding="utf-8")
        ui_fix = (ROOT / "dashboard" / "result-ui-fixes.js").read_text(encoding="utf-8")
        css_fix = (ROOT / "dashboard" / "result-ui-fixes.css").read_text(encoding="utf-8")
        compact = (ROOT / "dashboard" / "result-based-mobile-compact.css").read_text(encoding="utf-8")
        prediction_ui = (ROOT / "dashboard" / "prediction-ui-fix.js").read_text(encoding="utf-8")
        build = (ROOT / "scripts" / "build-netlify.mjs").read_text(encoding="utf-8")

        self.assertIn("install_custom_strategy_result_router()", worker)
        self.assertIn("install_custom_split_recovery_authority()", worker)
        self.assertIn("result-based-strategy.js", index)
        self.assertIn("result-based-strategy.css", index)
        self.assertIn("Use a different strategy after a loss", ui)
        self.assertIn("Martingale Spread — exact debt", ui)
        self.assertIn("Recover loss in how many splits", ui_fix)
        self.assertIn("recovered equally", ui_fix)
        self.assertIn("result-routing-enabled:not(:checked)", compact)
        self.assertIn("result-routing-toggle", css_fix)
        self.assertIn("data-theme=light", css_fix)
        self.assertIn("most_appearing", prediction_ui)
        self.assertIn("second_most_appearing", prediction_ui)
        self.assertIn("prediction-ui-fix.js", build)
        self.assertIn("result-ui-fixes.js", build)
        self.assertIn("result-ui-fixes.css", build)
        self.assertIn("result-based-mobile-compact.css", build)

        for relative in (
            "dashboard/prediction-ui-fix.js",
            "dashboard/result-ui-fixes.js",
        ):
            subprocess.run(
                ["node", "--check", str(ROOT / relative)],
                check=True,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
