from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from app.custom_strategy_v1 import (
    SUPPORTED_MARKETS,
    contract_for_config,
    describe_custom_strategy,
    evaluate_custom_strategy,
    market_selected,
    normalize_custom_strategy,
)
from app.strategy_v2_preferences import STRATEGY_CATALOG, normalize_strategy


ROOT = Path(__file__).resolve().parents[1]


def config(**overrides):
    value = {
        "market_mode": "all",
        "markets": [],
        "trade_type": "even",
        "prediction": None,
        "conditions": [{"kind": "digit_parity", "window": 3, "parity": "odd"}],
        "match": "all",
    }
    value.update(overrides)
    return normalize_custom_strategy(value)


class CustomStrategyPatternTests(unittest.TestCase):
    def test_catalog_exposes_custom_family(self) -> None:
        self.assertIn("custom", STRATEGY_CATALOG)
        selected = normalize_strategy("custom", "custom")
        self.assertEqual(selected.contract_type, "CUSTOM")

    def test_all_markets_and_selected_markets(self) -> None:
        all_markets = config()
        self.assertTrue(all(market_selected(all_markets, symbol) for symbol in SUPPORTED_MARKETS))
        selected = config(market_mode="selected", markets=["1HZ100V", "R_25"])
        self.assertTrue(market_selected(selected, "1HZ100V"))
        self.assertTrue(market_selected(selected, "R_25"))
        self.assertFalse(market_selected(selected, "R_50"))

    def test_last_digits_parity_requires_every_digit(self) -> None:
        custom = config(conditions=[{"kind": "digit_parity", "window": 3, "parity": "odd"}])
        self.assertTrue(evaluate_custom_strategy(custom, digits=[2, 1, 3, 9], quotes=[1, 2]))
        self.assertFalse(evaluate_custom_strategy(custom, digits=[1, 2, 9], quotes=[1, 2]))

    def test_digit_comparators_support_requested_relations(self) -> None:
        for operator, digits, expected in (
            ("<", [1, 2, 3], True),
            ("<=", [4, 4, 3], True),
            ("==", [4, 4, 4], True),
            ("!=", [1, 2, 3], True),
            (">=", [4, 7, 9], True),
            (">", [5, 7, 9], True),
            (">=", [4, 3, 9], False),
        ):
            with self.subTest(operator=operator):
                custom = config(conditions=[{
                    "kind": "digit_compare",
                    "window": 3,
                    "operator": operator,
                    "value": 4,
                }])
                self.assertEqual(
                    evaluate_custom_strategy(custom, digits=digits, quotes=[1, 2]),
                    expected,
                )

    def test_compound_conditions_are_and(self) -> None:
        custom = config(
            trade_type="even",
            conditions=[
                {"kind": "digit_parity", "window": 6, "parity": "odd"},
                {"kind": "digit_compare", "window": 3, "operator": ">=", "value": 4},
            ],
        )
        self.assertTrue(
            evaluate_custom_strategy(
                custom,
                digits=[1, 3, 5, 5, 7, 9],
                quotes=[Decimal("1"), Decimal("2")],
            )
        )
        self.assertFalse(
            evaluate_custom_strategy(
                custom,
                digits=[1, 3, 5, 3, 7, 9],
                quotes=[Decimal("1"), Decimal("2")],
            )
        )

    def test_direction_uses_last_n_movements(self) -> None:
        rise = config(conditions=[{"kind": "direction", "window": 3, "direction": "rise"}])
        fall = config(conditions=[{"kind": "direction", "window": 2, "direction": "fall"}])
        self.assertTrue(evaluate_custom_strategy(rise, digits=[1], quotes=[10, 11, 12, 13]))
        self.assertFalse(evaluate_custom_strategy(rise, digits=[1], quotes=[10, 11, 10, 13]))
        self.assertTrue(evaluate_custom_strategy(fall, digits=[1], quotes=[10, 9, 8]))

    def test_individual_trade_types_map_to_exact_contracts(self) -> None:
        expectations = {
            "even": ("DIGITEVEN", "EVEN", ""),
            "odd": ("DIGITODD", "ODD", ""),
            "rise": ("CALL", "RISE", ""),
            "fall": ("PUT", "FALL", ""),
            "over": ("DIGITOVER", "OVER_2", "2"),
            "under": ("DIGITUNDER", "UNDER_7", "7"),
        }
        for trade_type, expected in expectations.items():
            with self.subTest(trade_type=trade_type):
                prediction = 2 if trade_type == "over" else 7 if trade_type == "under" else None
                custom = config(trade_type=trade_type, prediction=prediction)
                self.assertEqual(contract_for_config(custom), expected)

    def test_preview_matches_compound_user_rule(self) -> None:
        custom = config(
            market_mode="selected",
            markets=["1HZ100V", "R_25"],
            trade_type="over",
            prediction=2,
            conditions=[
                {"kind": "digit_parity", "window": 6, "parity": "odd"},
                {"kind": "digit_compare", "window": 3, "operator": ">=", "value": 4},
            ],
        )
        preview = describe_custom_strategy(custom)
        self.assertIn("last 6 digit(s) are Odd", preview)
        self.assertIn("AND", preview)
        self.assertIn("BUY Over 2", preview)
        self.assertIn("1HZ100V", preview)


class CustomStrategyIntegrationTests(unittest.TestCase):
    def test_runtime_is_final_and_excludes_custom_from_system_aidr(self) -> None:
        production = (ROOT / "app" / "production_worker_integration.py").read_text(encoding="utf-8")
        runtime = (ROOT / "app" / "custom_strategy_runtime.py").read_text(encoding="utf-8")
        self.assertLess(
            production.index("install_manual_martingale_v2_hardening()"),
            production.index("install_custom_strategy_runtime()"),
        )
        self.assertIn("_exclude_custom_from_shared_aidr", runtime)
        self.assertIn('family", "") or "") == "custom"', runtime)
        self.assertIn("silent_scanning=true", runtime)
        self.assertIn("entry_gate=user_custom_pattern", runtime)
        self.assertIn("edge_gate=false", runtime)

    def test_custom_api_is_module_annotated_and_final_ui_is_last(self) -> None:
        api = (ROOT / "app" / "custom_strategy_api.py").read_text(encoding="utf-8")
        hardening = (ROOT / "app" / "database_runtime_hardening.py").read_text(encoding="utf-8")
        self.assertIn("class CustomStrategyRequest(BaseModel):", api)
        self.assertIn('family="custom"', api)
        self.assertLess(
            hardening.index("install_custom_strategy_api(app)"),
            hardening.index("install_custom_strategy_final_ui(app)"),
        )
        self.assertLess(
            hardening.index("install_trading_controls_final_ui(app)"),
            hardening.index("install_custom_strategy_final_ui(app)"),
        )

    def test_builder_contains_requested_controls(self) -> None:
        source = (ROOT / "app" / "custom_strategy_final_ui.py").read_text(encoding="utf-8")
        for label in (
            "All Markets",
            "Select Markets",
            "Trade type",
            "Last N digits are Even/Odd",
            "Last N digits compare to a digit",
            "Last N tick directions are Rise/Fall",
            "Save & Select Custom Strategy",
        ):
            self.assertIn(label, source)
        self.assertIn("AND", source)
        self.assertIn("FOA_CUSTOM_STRATEGY_BUILDER_V1", source)

    def test_custom_builder_javascript_has_valid_syntax(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is unavailable")
        source = (ROOT / "app" / "custom_strategy_final_ui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        javascript = None
        for statement in tree.body:
            if not isinstance(statement, ast.Assign):
                continue
            if any(isinstance(target, ast.Name) and target.id == "_CUSTOM_JS" for target in statement.targets):
                javascript = ast.literal_eval(statement.value)
                break
        self.assertIsInstance(javascript, str)
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(javascript)
            path = handle.name
        try:
            result = subprocess.run(
                [node, "--check", path],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
