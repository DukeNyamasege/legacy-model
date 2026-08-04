from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from app.private_buy_parameter_hardening import (
    install_private_buy_parameter_hardening,
)
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy_settlement_integrity import _strategy_outcome


ROOT = Path(__file__).resolve().parents[1]


class WorkerStartupCompatibilityTests(unittest.TestCase):
    def test_private_buy_installer_targets_live_rf_proposal_builder(self) -> None:
        self.assertTrue(callable(getattr(RFDir5TradingBot, "_proposal_request_for", None)))
        install_private_buy_parameter_hardening()
        self.assertTrue(
            getattr(RFDir5TradingBot, "_private_buy_parameter_hardening_installed", False)
        )

    def test_source_does_not_patch_missing_base_method(self) -> None:
        source = (ROOT / "app" / "private_buy_parameter_hardening.py").read_text(
            encoding="utf-8"
        )
        installer = source.split("def install_private_buy_parameter_hardening", 1)[1]
        self.assertIn("RFDir5TradingBot._proposal_request_for", installer)
        self.assertNotIn("TradingBot._proposal_request_for", installer)


class StrategySettlementRuleTests(unittest.TestCase):
    def test_even_and_odd_use_final_digit_parity(self) -> None:
        even, digit = _strategy_outcome(
            contract_type="DIGITEVEN",
            direction="EVEN",
            barrier="",
            entry_quote=Decimal("100.10"),
            exit_quote=Decimal("100.18"),
            exit_digit=8,
        )
        odd, _ = _strategy_outcome(
            contract_type="DIGITODD",
            direction="ODD",
            barrier="",
            entry_quote=Decimal("100.10"),
            exit_quote=Decimal("100.18"),
            exit_digit=8,
        )
        self.assertEqual((even, digit), ("WIN", 8))
        self.assertEqual(odd, "LOSS")

    def test_over_and_under_use_exact_prediction(self) -> None:
        over, _ = _strategy_outcome(
            contract_type="DIGITOVER",
            direction="OVER_2",
            barrier="2",
            entry_quote=Decimal("100.10"),
            exit_quote=Decimal("100.17"),
            exit_digit=7,
        )
        under, _ = _strategy_outcome(
            contract_type="DIGITUNDER",
            direction="UNDER_7",
            barrier="7",
            entry_quote=Decimal("100.10"),
            exit_quote=Decimal("100.13"),
            exit_digit=3,
        )
        self.assertEqual(over, "WIN")
        self.assertEqual(under, "WIN")

    def test_call_and_put_use_quote_direction(self) -> None:
        call, _ = _strategy_outcome(
            contract_type="CALL",
            direction="RISE",
            barrier="",
            entry_quote=Decimal("100.10"),
            exit_quote=Decimal("100.11"),
            exit_digit=1,
        )
        put, _ = _strategy_outcome(
            contract_type="PUT",
            direction="FALL",
            barrier="",
            entry_quote=Decimal("100.10"),
            exit_quote=Decimal("100.09"),
            exit_digit=9,
        )
        self.assertEqual(call, "WIN")
        self.assertEqual(put, "WIN")

    def test_unsupported_row_isolated_instead_of_cross_strategy_settlement(self) -> None:
        with self.assertRaises(ValueError):
            _strategy_outcome(
                contract_type="UNKNOWN",
                direction="EVENING",
                barrier="",
                entry_quote=Decimal("100.10"),
                exit_quote=Decimal("100.11"),
                exit_digit=1,
            )


class IntegrityInstallationContractTests(unittest.TestCase):
    def test_worker_installs_integrity_after_strategy_v2(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        v2 = source.index("install_strategy_v2_runtime()")
        integrity = source.index("install_strategy_settlement_integrity()")
        uniform = source.index("install_uniform_virtual_runtime()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(v2, integrity)
        self.assertLess(integrity, uniform)
        self.assertLess(uniform, bot)

    def test_virtual_parent_is_mandatory_at_repository_boundary(self) -> None:
        source = (ROOT / "app" / "strategy_settlement_integrity.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_ensure_virtual_parent(self, signal)", source)
        self.assertIn("DirectionalSignal(", source)
        self.assertIn("VIRTUAL_TRADE_INSERT_ISOLATED", source)
        self.assertIn("global_execution_continues=true", source)


if __name__ == "__main__":
    unittest.main()
