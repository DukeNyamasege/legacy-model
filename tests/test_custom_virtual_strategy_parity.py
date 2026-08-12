from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.custom_strategy_settlement import virtual_signal_matches_config


ROOT = Path(__file__).resolve().parents[1]


def over_config(prediction: int, *, market: str = "1HZ100V", duration: int = 1) -> dict:
    return {
        "version": "custom-strategy-v2",
        "configured": True,
        "market_mode": "single",
        "markets": [market],
        "trade_type": "over",
        "prediction": prediction,
        "duration_ticks": duration,
        "conditions": [
            {
                "kind": "digit_compare",
                "window": 2,
                "operator": ">=",
                "value": 3,
            }
        ],
        "match": "all",
        "reanalyze": {"mode": "after_every_trade", "losses": 1, "wins": 1},
        "virtual_hook_enabled": True,
        "virtual_hook": {
            "enabled": True,
            "enter_after_losses": 2,
            "exit_after_consecutive_wins": 1,
        },
    }


def signal(*, prediction: int, market: str = "1HZ100V", duration: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=market,
        contract_type="DIGITOVER",
        direction=f"OVER_{prediction}",
        barrier=str(prediction),
        duration_ticks=duration,
    )


class CustomVirtualStrategyParityTests(unittest.TestCase):
    def test_over_2_virtual_observation_must_remain_over_2(self) -> None:
        config = over_config(2)
        self.assertTrue(virtual_signal_matches_config(config, signal(prediction=2)))
        self.assertFalse(virtual_signal_matches_config(config, signal(prediction=7)))

    def test_changing_strategy_to_over_7_makes_over_7_the_virtual_contract(self) -> None:
        config = over_config(7)
        self.assertTrue(virtual_signal_matches_config(config, signal(prediction=7)))
        self.assertFalse(virtual_signal_matches_config(config, signal(prediction=2)))

    def test_market_and_duration_must_match_saved_strategy(self) -> None:
        config = over_config(2, market="R_50", duration=5)
        self.assertTrue(
            virtual_signal_matches_config(
                config,
                signal(prediction=2, market="R_50", duration=5),
            )
        )
        self.assertFalse(
            virtual_signal_matches_config(
                config,
                signal(prediction=2, market="1HZ100V", duration=5),
            )
        )
        self.assertFalse(
            virtual_signal_matches_config(
                config,
                signal(prediction=2, market="R_50", duration=1),
            )
        )

    def test_runtime_passes_exact_signal_into_virtual_trade_and_installs_settlement(self) -> None:
        runtime = (ROOT / "app" / "custom_strategy_direct_runtime.py").read_text(encoding="utf-8")
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        settlement = (ROOT / "app" / "custom_strategy_settlement.py").read_text(encoding="utf-8")

        self.assertIn("signal=signal", runtime)
        self.assertIn("install_custom_strategy_settlement()", worker)
        self.assertIn("virtual_signal_matches_config(config, signal)", settlement)
        self.assertIn("RFDir5Repository._protection_payload = _protection_payload_for_config", settlement)
        self.assertIn("using the exact saved strategy", settlement)
        self.assertNotIn("OVER-4", settlement)


if __name__ == "__main__":
    unittest.main()
