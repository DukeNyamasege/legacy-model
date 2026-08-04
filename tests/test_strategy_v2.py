from __future__ import annotations

import ast
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.strategy_v2_preferences import (
    STRATEGY_VERSION,
    _decode_payload,
    default_strategy,
    normalize_strategy,
    strategy_catalog_payload,
)

ROOT = Path(__file__).resolve().parents[1]


def _string_literal(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    raise AssertionError(f"String literal {name} was not found in {path}")


class StrategyV2PreferenceTests(unittest.TestCase):
    def test_default_is_explicit_system_strategy(self) -> None:
        selection = default_strategy()
        self.assertEqual(selection.family, "system")
        self.assertEqual(selection.side, "system")
        self.assertEqual(selection.contract_type, "SYSTEM")
        self.assertIsNone(selection.prediction)

    def test_manual_digit_prediction_is_persistent_contract_choice(self) -> None:
        over = normalize_strategy("digits", "over", 2)
        under = normalize_strategy("digits", "under", 7)
        self.assertEqual(over.contract_type, "DIGITOVER")
        self.assertEqual(over.prediction, 2)
        self.assertEqual(over.to_dict()["normal_rule"], "DIGITOVER 2")
        self.assertIn("Same DIGITOVER barrier", over.to_dict()["recovery_rule"])
        self.assertEqual(under.contract_type, "DIGITUNDER")
        self.assertEqual(under.prediction, 7)

    def test_prediction_ranges_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            normalize_strategy("digits", "over", 9)
        with self.assertRaises(ValueError):
            normalize_strategy("digits", "under", 0)
        with self.assertRaises(ValueError):
            normalize_strategy("digits", "over", "two")

    def test_legacy_default_maps_to_system_without_changing_manual_choices(self) -> None:
        legacy_default = json.dumps({"family": "digits", "side": "over"})
        parity = json.dumps({"family": "parity", "side": "even"})
        manual_over = json.dumps(
            {
                "family": "digits",
                "side": "over",
                "prediction": 3,
                "version": STRATEGY_VERSION,
            }
        )
        self.assertEqual(_decode_payload(legacy_default).family, "system")
        self.assertEqual(_decode_payload(parity).contract_type, "DIGITEVEN")
        self.assertEqual(_decode_payload(manual_over).prediction, 3)

    def test_catalog_has_four_clear_options(self) -> None:
        payload = strategy_catalog_payload()
        self.assertEqual(
            set(payload["families"]),
            {"system", "digits", "parity", "direction"},
        )
        self.assertTrue(payload["families"]["digits"]["prediction_required"])
        self.assertIn("two-loss", payload["switching_rule"])


class StrategyV2SourceContractTests(unittest.TestCase):
    def test_manual_virtual_trade_parent_is_created_before_execution(self) -> None:
        source = (ROOT / "app" / "strategy_v2_runtime.py").read_text(
            encoding="utf-8"
        )
        parent = source.index("session.add(")
        candidate_return = source.index("return signal", parent)
        self.assertIn("DirectionalSignal(", source[parent:candidate_return])
        self.assertIn("_ensure_parent_signal(bot, signal, route)", source)
        self.assertIn("VirtualTrade", source)

    def test_system_and_manual_routing_are_separate(self) -> None:
        source = (ROOT / "app" / "strategy_v2_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('ms._routes_for(bot, "system", "system")', source)
        self.assertIn("aidr._enabled_accounts = system_accounts", source)
        self.assertIn("selection.prediction", source)
        self.assertIn('role="SHARED"', source)
        self.assertIn("barrier=str(prediction)", source)
        self.assertIn("ms._queue_non_aidr_signals = queue_v2_signals", source)

    def test_api_accepts_prediction_and_keeps_stop_guard(self) -> None:
        source = (ROOT / "app" / "strategy_v2_api.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("prediction: int | None = None", source)
        self.assertIn("Stop AutoTrade completely", source)
        self.assertIn('"history_preserved": True', source)
        self.assertNotIn("delete(Trade)", source)

    def test_worker_installs_v2_before_atomic_purchase_guard(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        legacy = source.index("install_multi_strategy_runtime()")
        v2 = source.index("install_strategy_v2_runtime()")
        guard = source.index("install_multi_strategy_concurrency_guard()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(legacy, v2)
        self.assertLess(v2, guard)
        self.assertLess(guard, bot)

    def test_dashboard_installs_v2_as_final_strategy_authority(self) -> None:
        api_source = (ROOT / "app" / "api_v3.py").read_text(encoding="utf-8")
        ui_source = (ROOT / "app" / "strategy_v2_ui.py").read_text(
            encoding="utf-8"
        )
        final_ui_source = (ROOT / "app" / "strategy_v2_final_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            api_source.index("install_multi_strategy_api(app)"),
            api_source.index("install_strategy_v2_api(app)"),
        )
        self.assertLess(
            api_source.index("install_multi_strategy_ui(app)"),
            api_source.index("install_strategy_v2_ui(app)"),
        )
        self.assertLess(
            api_source.index("install_dashboard_request_coalescing(app)"),
            api_source.index("install_strategy_v2_final_ui(app)"),
        )
        self.assertIn("broker_script", final_ui_source)
        self.assertIn("_STRATEGY_V2_JS", final_ui_source)
        self.assertIn("FOA_STRATEGY_V2_UI_VERSION:20260804-2", final_ui_source)
        self.assertIn("Prediction digit", ui_source)
        self.assertIn("Start System AutoTrade", ui_source)
        self.assertIn("FOA_STRATEGY_V2_UI_VERSION", ui_source)


class StrategyV2GeneratedJavaScriptTests(unittest.TestCase):
    def test_strategy_v2_javascript_parses(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed in this test environment")
        source = _string_literal(
            ROOT / "app" / "strategy_v2_ui.py",
            "_STRATEGY_V2_JS",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strategy-v2.js"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(
            result.returncode,
            0,
            msg=(result.stdout + "\n" + result.stderr).strip(),
        )


if __name__ == "__main__":
    unittest.main()
