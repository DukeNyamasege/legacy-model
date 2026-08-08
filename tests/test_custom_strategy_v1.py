from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from app.custom_strategy_v1 import (
    DEFAULT_DURATION_TICKS,
    MAX_DURATION_TICKS,
    MIN_DURATION_TICKS,
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
        "duration_ticks": DEFAULT_DURATION_TICKS,
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

    def test_contract_duration_is_user_configurable_and_separate_from_lookback(self) -> None:
        custom = config(
            duration_ticks=7,
            conditions=[{"kind": "digit_parity", "window": 3, "parity": "odd"}],
        )
        self.assertEqual(custom["duration_ticks"], 7)
        self.assertEqual(custom["conditions"][0]["window"], 3)

    def test_legacy_custom_config_defaults_to_one_tick(self) -> None:
        legacy = normalize_custom_strategy({
            "market_mode": "all",
            "markets": [],
            "trade_type": "even",
            "prediction": None,
            "conditions": [{"kind": "digit_parity", "window": 3, "parity": "odd"}],
            "match": "all",
        })
        self.assertEqual(legacy["duration_ticks"], DEFAULT_DURATION_TICKS)

    def test_contract_duration_range_is_validated(self) -> None:
        self.assertEqual(config(duration_ticks=MIN_DURATION_TICKS)["duration_ticks"], MIN_DURATION_TICKS)
        self.assertEqual(config(duration_ticks=MAX_DURATION_TICKS)["duration_ticks"], MAX_DURATION_TICKS)
        with self.assertRaises(ValueError):
            config(duration_ticks=MIN_DURATION_TICKS - 1)
        with self.assertRaises(ValueError):
            config(duration_ticks=MAX_DURATION_TICKS + 1)

    def test_preview_matches_compound_user_rule_and_duration(self) -> None:
        custom = config(
            market_mode="selected",
            markets=["1HZ100V", "R_25"],
            trade_type="over",
            prediction=2,
            duration_ticks=5,
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
        self.assertIn("for 5 ticks", preview)


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

    def test_custom_duration_reaches_purchase_and_trade_registration(self) -> None:
        runtime = (ROOT / "app" / "custom_strategy_runtime.py").read_text(encoding="utf-8")
        strategy = (ROOT / "app" / "custom_strategy_v1.py").read_text(encoding="utf-8")
        virtual = (ROOT / "app" / "repositories" / "rf_dir5_repository.py").read_text(encoding="utf-8")
        self.assertIn('duration_ticks=int(normalized["duration_ticks"])', strategy)
        self.assertIn('parameters["duration"] = _signal_duration(signal)', runtime)
        self.assertIn('duration=_signal_duration(signal)', runtime)
        self.assertIn('kwargs["contract_duration"] = int(duration)', runtime)
        self.assertIn("duration=int(signal.duration_ticks)", virtual)
        self.assertIn("exit_tick_sequence=int(signal.tick_sequence) + int(signal.duration_ticks)", virtual)

    def test_custom_api_is_module_annotated_and_saves_martingale_with_strategy(self) -> None:
        api = (ROOT / "app" / "custom_strategy_api.py").read_text(encoding="utf-8")
        hardening = (ROOT / "app" / "database_runtime_hardening.py").read_text(encoding="utf-8")
        self.assertIn("class CustomStrategyRequest(BaseModel):", api)
        self.assertIn("class CustomMartingaleRequest(BaseModel):", api)
        self.assertIn("martingale: CustomMartingaleRequest | None = None", api)
        self.assertIn("duration_ticks: int = Field(", api)
        self.assertIn('"duration_ticks": body.duration_ticks', api)
        self.assertIn("_write_custom_martingale(", api)
        self.assertIn("MANUAL_MARTINGALE_PREFIX", api)
        self.assertIn("SPLIT_REMAINING_PREFIX", api)
        self.assertIn('"martingale": martingale', api)
        self.assertIn('family="custom"', api)
        self.assertLess(
            hardening.index("install_custom_strategy_api(app)"),
            hardening.index("install_custom_strategy_final_ui(app)"),
        )
        self.assertLess(
            hardening.index("install_trading_controls_final_ui(app)"),
            hardening.index("install_custom_strategy_final_ui(app)"),
        )

    def test_builder_is_one_complete_strategy_card(self) -> None:
        source = (ROOT / "app" / "custom_strategy_final_ui.py").read_text(encoding="utf-8")
        for label in (
            "Choose markets",
            "All Markets",
            "Choose Markets",
            "Choose contract",
            "Define entry pattern",
            "Contract duration (ticks)",
            "Choose recovery / Martingale",
            "System Martingale",
            "Custom Multiplier",
            "Split Recovery",
            "Save Strategy",
            "Bot execution rule",
        ):
            self.assertIn(label, source)
        for trade_type in ("even", "odd", "over", "under", "rise", "fall"):
            self.assertIn(f'data-trade-type=\\"${{esc(item.value)}}', source)
            self.assertIn(trade_type, source)
        self.assertIn("data-market-mode", source)
        self.assertIn("data-market=", source)
        self.assertIn("data-prediction", source)
        self.assertIn("data-duration", source)
        self.assertIn("data-multiplier", source)
        self.assertIn("data-split-count", source)
        self.assertIn("AND", source)
        self.assertIn("FOA_CUSTOM_STRATEGY_BUILDER_V3", source)
        self.assertIn('"X-FOA-Custom-Strategy-Card": "complete-builder-v1"', source)

    def test_custom_card_hides_duplicate_manual_panel_when_custom_is_active(self) -> None:
        source = (ROOT / "app" / "custom_strategy_final_ui.py").read_text(encoding="utf-8")
        self.assertIn('document.getElementById("foa-manual-martingale-v2")', source)
        self.assertIn('Boolean(payload?.active) || dirty', source)

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
