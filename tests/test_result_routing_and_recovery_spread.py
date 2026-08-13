from __future__ import annotations

import unittest
from pathlib import Path

from app.custom_strategy_result_router import _route_from_debt
from app.custom_strategy_result_routing import (
    AFTER_LOSS,
    AFTER_WIN,
    merge_result_route,
    normalize_result_routing,
)
from app.custom_strategy_v1 import normalize_custom_strategy
from app.manual_martingale_v2 import normalize_manual_martingale_settings


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
        self.assertEqual(
            merge_result_route(primary, routing, AFTER_LOSS),
            primary,
        )
        self.assertEqual(
            merge_result_route(primary, routing, AFTER_WIN),
            primary,
        )

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
        # Markets and non-entry account controls stay inherited from the primary.
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

    def test_worker_and_ui_install_only_additive_feature_layers(self) -> None:
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        ui = (ROOT / "dashboard" / "result-based-strategy.js").read_text(encoding="utf-8")

        self.assertIn("install_custom_strategy_result_router()", worker)
        self.assertIn("install_custom_split_recovery_authority()", worker)
        self.assertIn("result-based-strategy.js", index)
        self.assertIn("result-based-strategy.css", index)
        self.assertIn("Use a different strategy after a loss", ui)
        self.assertIn("Current multiplier — unchanged", ui)
        self.assertIn("Martingale Spread — exact debt", ui)
        self.assertIn("Successful recovery parts", ui)
        self.assertIn("split_count", ui)


if __name__ == "__main__":
    unittest.main()
