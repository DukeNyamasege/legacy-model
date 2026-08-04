from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from app.strategy_preferences import (
    default_strategy,
    normalize_strategy,
    strategy_catalog_payload,
)


ROOT = Path(__file__).resolve().parents[1]


class StrategyPreferenceTests(unittest.TestCase):
    def test_default_preserves_existing_aidr_over_accounts(self) -> None:
        selection = default_strategy()
        self.assertEqual(selection.family, "digits")
        self.assertEqual(selection.side, "over")
        self.assertEqual(selection.contract_type, "DIGITOVER")

    def test_all_requested_contract_families_are_supported(self) -> None:
        expected = {
            ("digits", "over"): "DIGITOVER",
            ("digits", "under"): "DIGITUNDER",
            ("parity", "even"): "DIGITEVEN",
            ("parity", "odd"): "DIGITODD",
            ("direction", "rise"): "CALL",
            ("direction", "fall"): "PUT",
        }
        for key, contract_type in expected.items():
            with self.subTest(selection=key):
                self.assertEqual(normalize_strategy(*key).contract_type, contract_type)

    def test_invalid_family_side_combinations_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_strategy("parity", "rise")
        with self.assertRaises(ValueError):
            normalize_strategy("unknown", "over")

    def test_catalog_explains_safe_switching(self) -> None:
        payload = strategy_catalog_payload()
        self.assertIn("Stop AutoTrade", payload["switching_rule"])
        self.assertIn("history is retained", payload["switching_rule"].lower())
        self.assertEqual(set(payload["families"]), {"digits", "parity", "direction"})


class VirtualOutcomeTests(unittest.TestCase):
    def test_parity_virtual_results_match_final_digit(self) -> None:
        import app.repositories.rf_dir5_repository as repository_module
        from app.multi_strategy_runtime import _install_parity_virtual_outcome

        _install_parity_virtual_outcome()
        even = repository_module._virtual_trade_outcome(
            direction="EVEN",
            contract_type="DIGITEVEN",
            barrier="",
            prediction_digit=None,
            entry_quote=Decimal("100.01"),
            exit_quote=Decimal("100.04"),
            exit_digit=4,
        )
        odd = repository_module._virtual_trade_outcome(
            direction="ODD",
            contract_type="DIGITODD",
            barrier="",
            prediction_digit=None,
            entry_quote=Decimal("100.01"),
            exit_quote=Decimal("100.05"),
            exit_digit=5,
        )
        wrong = repository_module._virtual_trade_outcome(
            direction="EVEN",
            contract_type="DIGITEVEN",
            barrier="",
            prediction_digit=None,
            entry_quote=Decimal("100.01"),
            exit_quote=Decimal("100.05"),
            exit_digit=5,
        )
        self.assertEqual(even, ("WIN", 4))
        self.assertEqual(odd, ("WIN", 5))
        self.assertEqual(wrong, ("LOSS", 5))


class SourceContractTests(unittest.TestCase):
    def test_worker_installs_startup_safety_before_runtime(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        safety = source.index("install_unresolved_contract_safety()")
        runtime = source.index("install_multi_strategy_runtime()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(safety, runtime)
        self.assertLess(runtime, bot)

    def test_malformed_contract_ids_are_not_converted_by_startup(self) -> None:
        source = (ROOT / "app" / "unresolved_contract_safety.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("text.isdigit()", source)
        self.assertIn('row.outcome = "INVALID_CONTRACT_ID"', source)
        self.assertIn("row.requires_manual_review = True", source)

    def test_aidr_is_scoped_to_digits_over_only(self) -> None:
        source = (ROOT / "app" / "multi_strategy_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("aidr._enabled_accounts = _filter_aidr_over_accounts", source)
        self.assertIn('for route in _routes_for(bot, "digits", "over")', source)
        self.assertIn("if route.selection.family == family and route.selection.side == side", source)
        self.assertIn('"DIGITEVEN"', source)
        self.assertIn('"DIGITODD"', source)
        self.assertIn('contract_type == "PUT"', source)

    def test_strategy_switch_requires_full_stop_and_preserves_history(self) -> None:
        source = (ROOT / "app" / "multi_strategy_api.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Stop AutoTrade completely", source)
        self.assertIn('"history_preserved": True', source)
        self.assertNotIn("delete(Trade)", source)

    def test_final_dashboard_route_contains_selector(self) -> None:
        api_source = (ROOT / "app" / "api_v3.py").read_text(encoding="utf-8")
        ui_source = (ROOT / "app" / "multi_strategy_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("install_multi_strategy_api(app)", api_source)
        self.assertIn("install_multi_strategy_ui(app)", api_source)
        self.assertIn("FOA_MULTI_STRATEGY_UI_VERSION", ui_source)
        self.assertIn("Choose what this account trades", ui_source)
        self.assertIn("Start ${label} AutoTrade", ui_source)


if __name__ == "__main__":
    unittest.main()
