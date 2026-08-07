from __future__ import annotations

import ast
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from app.manual_martingale_v2 import (
    MULTIPLIER_MODE,
    SPLIT_MODE,
    SYSTEM_MODE,
    multiplier_recovery_stake,
    normalize_manual_martingale_settings,
)
from app.manual_martingale_v2_hardening import split_recovery_stake_by_parts


ROOT = Path(__file__).resolve().parents[1]


class ManualMartingaleV2Tests(unittest.TestCase):
    def test_modes_and_limits_normalize(self) -> None:
        self.assertEqual(normalize_manual_martingale_settings({"mode": "system"})["mode"], SYSTEM_MODE)
        custom = normalize_manual_martingale_settings(
            {"mode": "multiplier", "multiplier": 1.5, "split_count": 9}
        )
        self.assertEqual(custom["mode"], MULTIPLIER_MODE)
        self.assertEqual(custom["multiplier"], 1.5)
        self.assertEqual(custom["split_count"], 3)
        split = normalize_manual_martingale_settings(
            {"mode": "split", "multiplier": 99, "split_count": 1}
        )
        self.assertEqual(split["mode"], SPLIT_MODE)
        self.assertEqual(split["multiplier"], 10.0)
        self.assertEqual(split["split_count"], 1)

    def test_multiplier_level_uses_actual_loss_count(self) -> None:
        stake, level = multiplier_recovery_stake(
            base_stake=1.00,
            consecutive_losses=1,
            multiplier=2.0,
        )
        self.assertEqual((stake, level), (2.00, 1))
        stake, level = multiplier_recovery_stake(
            base_stake=1.00,
            consecutive_losses=2,
            multiplier=1.5,
        )
        self.assertEqual((stake, level), (2.25, 2))

    def test_three_way_split_genuinely_reduces_one_shot_stake(self) -> None:
        part, full = split_recovery_stake_by_parts(
            base_stake=1.00,
            recovery_debt=1.00,
            proposal_profit_ratio=0.85,
            remaining_parts=3,
        )
        self.assertEqual(full, 1.25)
        self.assertEqual(part, 0.42)
        self.assertLess(part, 1.00)
        self.assertGreaterEqual(part, 0.35)

    def test_one_way_split_is_exact_recovery_target(self) -> None:
        part, full = split_recovery_stake_by_parts(
            base_stake=1.00,
            recovery_debt=1.00,
            proposal_profit_ratio=0.85,
            remaining_parts=1,
        )
        self.assertEqual(part, full)
        self.assertEqual(full, 1.25)

    def test_system_strategy_is_explicitly_excluded_from_manual_override(self) -> None:
        source = (ROOT / "app" / "manual_martingale_v2.py").read_text(encoding="utf-8")
        self.assertIn('if family == "system" or str(settings["mode"]) == SYSTEM_MODE:', source)
        self.assertIn("The System Strategy is deliberately untouchable here.", source)

    def test_final_worker_installs_manual_policy_after_execution_authorities(self) -> None:
        source = (ROOT / "app" / "production_worker_integration.py").read_text(encoding="utf-8")
        final_execution = source.index("install_final_multi_strategy_execution()")
        manual_policy = source.index("install_manual_martingale_v2_worker()")
        split_hardening = source.index("install_manual_martingale_v2_hardening()")
        self.assertLess(final_execution, manual_policy)
        self.assertLess(manual_policy, split_hardening)

    def test_ui_contains_explicit_wins_losses_and_three_manual_choices(self) -> None:
        source = (ROOT / "app" / "trading_controls_final_ui.py").read_text(encoding="utf-8")
        self.assertIn('"Wins"', source)
        self.assertIn('"Losses"', source)
        self.assertIn("1. System Martingale", source)
        self.assertIn("2. Custom Multiplier", source)
        self.assertIn("3. Split System Martingale", source)
        self.assertIn('value="1"', source)
        self.assertIn('value="2"', source)
        self.assertIn('value="3"', source)
        self.assertIn("foa-six-trade-kpis", source)

    def test_inline_final_ui_javascript_has_valid_syntax(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is unavailable")
        source = (ROOT / "app" / "trading_controls_final_ui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        javascript = None
        for statement in tree.body:
            if not isinstance(statement, ast.Assign):
                continue
            if any(isinstance(target, ast.Name) and target.id == "_EXTRA_JS" for target in statement.targets):
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
