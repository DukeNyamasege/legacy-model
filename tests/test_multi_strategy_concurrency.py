from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MultiStrategyConcurrencyContractTests(unittest.TestCase):
    def test_execution_gate_is_installed_after_strategy_router(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        runtime = source.index("install_multi_strategy_runtime()")
        guard = source.index("install_multi_strategy_concurrency_guard()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(runtime, guard)
        self.assertLess(guard, bot)

    def test_gate_rechecks_staleness_and_open_cycle(self) -> None:
        source = (ROOT / "app" / "multi_strategy_concurrency.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("asyncio.Lock()", source)
        self.assertIn("SKIP_STALE_SIGNAL_AT_EXECUTION_GATE", source)
        self.assertIn("pending_contracts_for_current_cycle", source)
        self.assertIn("SKIP_STRATEGY_EXECUTION_GATE_BUSY", source)
        self.assertIn("_multi_strategy_signal_routes", source)
        self.assertIn(".pop(", source)


if __name__ == "__main__":
    unittest.main()
