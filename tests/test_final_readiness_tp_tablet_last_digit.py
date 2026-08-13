from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from app import custom_strategy_v1 as custom
from app.custom_strategy_last_digit_prediction import (
    install_custom_strategy_last_digit_prediction,
)
from app.session_risk_limits import normalize_stop_loss, normalize_take_profit


ROOT = Path(__file__).resolve().parents[1]


def dynamic_config(trade_type: str = "matches") -> dict:
    return {
        "market_mode": "single",
        "markets": ["1HZ100V"],
        "trade_type": trade_type,
        "prediction": None,
        "duration_ticks": 1,
        "conditions": [
            {
                "kind": "digit_compare",
                "window": 2,
                "operator": ">=",
                "value": 0,
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


class SignedSessionRiskTests(unittest.TestCase):
    def test_tp_is_positive_and_sl_is_negative(self) -> None:
        self.assertEqual(normalize_take_profit(10), 10.0)
        self.assertEqual(normalize_take_profit(-10), 0.0)
        self.assertEqual(normalize_stop_loss(10), -10.0)
        self.assertEqual(normalize_stop_loss(-10), -10.0)
        self.assertEqual(normalize_stop_loss(0), 0.0)

    def test_worker_uses_only_session_profit_and_exact_signed_thresholds(self) -> None:
        source = (ROOT / "app" / "session_risk_stop_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("risk.session_profit", source)
        self.assertIn("session_profit >= take_profit", source)
        self.assertIn("stop_loss < 0 and session_profit <= stop_loss", source)
        self.assertIn("read_session_risk_limits", source)
        self.assertNotIn("profit_today", source)
        self.assertNotIn("account_summary(account_id)", source)
        self.assertNotIn("take_profit - 0.005", source)
        self.assertNotIn("-stop_loss + 0.005", source)

    def test_fresh_start_freezes_settings_and_lifecycle_returns_same_limits(self) -> None:
        api = (ROOT / "app" / "session_risk_api_authority.py").read_text(
            encoding="utf-8"
        )
        builder = (ROOT / "app" / "builder_first_dashboard_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("snapshot_session_risk_limits(session, row)", api)
        self.assertIn('"session_profit": session_profit', api)
        self.assertIn('"take_profit": limits.take_profit', api)
        self.assertIn('"stop_loss": limits.stop_loss', api)
        self.assertIn('"limit_target": limit_target', api)
        self.assertIn("install_session_risk_api_authority(app)", builder)
        self.assertGreater(
            builder.index("install_session_risk_api_authority(app)"),
            builder.index("install_custom_strategy_runtime_api(app)"),
        )

    def test_frontend_renders_signed_stop_loss(self) -> None:
        source = (ROOT / "dashboard" / "signed-risk-limit-display.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("isTp ? Math.abs(rawTarget) : -Math.abs(rawTarget)", source)
        self.assertIn("Session P/L", source)
        self.assertIn("/me/trading-lifecycle", source)


class MatchesDiffersLastDigitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_custom_strategy_last_digit_prediction()

    def test_matches_can_use_qualifying_last_digit_as_prediction(self) -> None:
        config = custom.normalize_custom_strategy(dynamic_config("matches"))
        self.assertIsNone(config["prediction"])
        contract, direction, barrier = custom.contract_for_config(config, last_digit=7)
        self.assertEqual(contract, "DIGITMATCH")
        self.assertEqual(direction, "MATCHES_7")
        self.assertEqual(barrier, "7")

    def test_differs_can_use_qualifying_last_digit_as_prediction(self) -> None:
        config = custom.normalize_custom_strategy(dynamic_config("differs"))
        self.assertIsNone(config["prediction"])
        contract, direction, barrier = custom.contract_for_config(config, last_digit=3)
        self.assertEqual(contract, "DIGITDIFF")
        self.assertEqual(direction, "DIFFERS_3")
        self.assertEqual(barrier, "3")

    def test_worker_installs_dynamic_prediction_before_direct_runtime(self) -> None:
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        self.assertIn("install_custom_strategy_last_digit_prediction()", worker)
        self.assertIn("install_custom_strategy_last_digit_runtime()", worker)
        self.assertLess(
            worker.index("install_custom_strategy_last_digit_prediction()"),
            worker.index("from app.custom_strategy_direct_runtime import"),
        )
        runtime = (ROOT / "app" / "custom_strategy_last_digit_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("signal_last_digit", runtime)
        self.assertIn("virtual_signal_matches_config", runtime)
        self.assertIn("_assert_strategy_exact", runtime)

    def test_ui_exposes_last_digit_and_sends_null_sentinel(self) -> None:
        source = (ROOT / "dashboard" / "matches-differs-last-digit.js").read_text(
            encoding="utf-8"
        )
        self.assertIn(">Last digit</option>", source)
        self.assertIn("payload.prediction = null", source)
        self.assertIn('new Set(["matches", "differs"])', source)
        self.assertIn("config?.prediction === null", source)


class TabletNavigationTests(unittest.TestCase):
    def test_tablet_width_has_independent_navigation_drawer(self) -> None:
        css = (ROOT / "dashboard" / "tablet-navigation-fix.css").read_text(
            encoding="utf-8"
        )
        js = (ROOT / "dashboard" / "tablet-navigation-fix.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("(min-width: 761px) and (max-width: 1024px)", css)
        self.assertIn(".builder-header", css)
        self.assertIn("display: none !important", css)
        self.assertIn("body.foa-mobile-drawer-open .foa-mobile-drawer", css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", css)
        self.assertIn('TABLET_QUERY = "(min-width: 761px) and (max-width: 1024px)"', js)
        self.assertIn("setTabletDrawer(true)", js)
        self.assertIn("[data-mobile-view]", js)

    def test_tablet_fix_does_not_apply_phone_trade_typography(self) -> None:
        css = (ROOT / "dashboard" / "tablet-navigation-fix.css").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(".trade-row", css)
        self.assertNotIn(".trade-head", css)


class PersistentClearHistoryTests(unittest.TestCase):
    def test_server_cutoff_remains_authoritative(self) -> None:
        source = (ROOT / "app" / "global_trade_history_cutoff.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('_CUTOFF_PREFIX = "personal_trade_history_cutoff:v1:"', source)
        self.assertIn("Trade.purchase_time >= cutoff", source)
        self.assertIn("VirtualTrade.created_at >= cutoff", source)
        self.assertIn('"history_visibility_global": True', source)
        self.assertIn('"global_across_sessions": True', source)


class FinalAssetValidationTests(unittest.TestCase):
    def test_final_assets_are_loaded_and_valid_javascript(self) -> None:
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        for asset in (
            "./tablet-navigation-fix.css",
            "./tablet-navigation-fix.js",
            "./matches-differs-last-digit.js",
            "./signed-risk-limit-display.js",
        ):
            self.assertIn(asset, index)
        for name in (
            "tablet-navigation-fix.js",
            "matches-differs-last-digit.js",
            "signed-risk-limit-display.js",
        ):
            subprocess.run(
                ["node", "--check", str(ROOT / "dashboard" / name)],
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
