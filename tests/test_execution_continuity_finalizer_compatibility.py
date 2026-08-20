from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ExecutionContinuityFinalizerCompatibilityTests(unittest.TestCase):
    def test_current_provider_aware_ledger_is_accepted(self) -> None:
        finalizer = (ROOT / "scripts" / "finalize-execution-continuity-v1.mjs").read_text(
            encoding="utf-8"
        )
        ledger = (ROOT / "dashboard" / "direct-transaction-ledger-v6.js").read_text(
            encoding="utf-8"
        )

        current_shape = (
            'const exit = settled ? '
            '(spotDigit(row.exit_spot, row.symbol, row.actual_last_digit) ?? "—") : "OPEN";'
        )
        self.assertIn(current_shape, ledger)
        self.assertIn("const legacyEntryExit =", finalizer)
        self.assertIn("const providerAwareExit =", finalizer)
        self.assertIn("ledger.includes(legacyEntryExit)", finalizer)
        self.assertIn("ledger.includes(providerAwareExit)", finalizer)
        self.assertIn(
            "neither legacy nor provider-aware ledger shape found",
            finalizer,
        )

    def test_old_unconditional_entry_exit_replacement_is_gone(self) -> None:
        finalizer = (ROOT / "scripts" / "finalize-execution-continuity-v1.mjs").read_text(
            encoding="utf-8"
        )
        obsolete = (
            'if (!ledger.includes("spotDigit(row.entry_spot, row.symbol, row.entry_digit)")) '
            'ledger = replaceOne('
        )
        self.assertNotIn(obsolete, finalizer)


if __name__ == "__main__":
    unittest.main()
