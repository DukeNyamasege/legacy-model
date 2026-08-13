from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExactStrategyExecutionAuthorityTests(unittest.TestCase):
    def test_worker_installs_exact_authority_after_private_session_fix(self) -> None:
        source = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        self.assertIn("install_exact_strategy_execution_authority", source)
        self.assertLess(
            source.index("install_custom_strategy_current_runtime_fix()"),
            source.index("install_exact_strategy_execution_authority()"),
        )
        self.assertLess(
            source.index("install_exact_strategy_execution_authority()"),
            source.index("install_seamless_execution_recovery()"),
        )

    def test_every_condition_is_rechecked_before_real_or_virtual_entry(self) -> None:
        source = (ROOT / "app" / "exact_strategy_execution_authority.py").read_text(
            encoding="utf-8"
        )
        canonical = (ROOT / "app" / "custom_strategy_v1.py").read_text(encoding="utf-8")
        self.assertIn("evaluate_custom_strategy(", source)
        self.assertIn("market_selected(config, symbol)", source)
        self.assertIn("contract_for_config(config)", source)
        self.assertIn("custom_strategy_fingerprint(config)", source)
        self.assertGreaterEqual(source.count("_assert_strategy_exact(item, signal)"), 2)
        self.assertIn("return all(", canonical)
        self.assertIn("for condition in normalized[\"conditions\"]", canonical)

    def test_late_signal_is_skipped_instead_of_bought_on_a_later_digit(self) -> None:
        source = (ROOT / "app" / "exact_strategy_execution_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("current_sequence != expected_sequence", source)
        self.assertIn("exact-entry trigger tick expired before purchase", source)
        self.assertIn("CUSTOM_STRATEGY_EXACT_ENTRY_SKIPPED", source)
        self.assertIn('status="EXPIRED_BEFORE_ENTRY"', source)
        self.assertIn("financial_purchase=false", source)
        self.assertIn("AccountExecutionSession.proposal = _proposal_with_exact_tick", source)
        self.assertIn("AccountExecutionSession.buy_proposal = _buy_with_exact_tick", source)
        self.assertIn("# This is the last local guard before the private WebSocket BUY request.", source)

    def test_virtual_hook_uses_exact_signal_tick_and_never_settles_late(self) -> None:
        source = (ROOT / "app" / "exact_strategy_execution_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("int(signal.tick_sequence)", source)
        self.assertIn("+ max(1, int(getattr(signal, \"duration_ticks\", 1) or 1))", source)
        self.assertIn("CUSTOM_VIRTUAL_EXACT_ENTRY", source)
        self.assertIn("amount_charged=0 payout=0 profit_loss=0", source)
        self.assertIn('row.result = "VIRTUAL_STALE"', source)
        self.assertIn("observation discarded instead of", source)
        self.assertIn("int(exit_sequence) == current_sequence", source)

    def test_parity_summary_uses_the_exact_selected_window_and_operator(self) -> None:
        source = (ROOT / "dashboard" / "last-digit-special-ui.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('const parityText = operator === "all_even" ? "all even" : "all odd"', source)
        self.assertIn("`When the last ${windowSize} digits are ${parityText}`", source)
        self.assertIn("syncSummary();", source)
        subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "last-digit-special-ui.js")],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
