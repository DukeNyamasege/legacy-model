from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from app.global_trade_history_cutoff import _parse_cutoff


ROOT = Path(__file__).resolve().parents[1]


class GlobalTradeHistoryCutoffTests(unittest.TestCase):
    def test_server_clear_is_persistent_visibility_boundary_not_execution_stop(self) -> None:
        source = (ROOT / "app" / "global_trade_history_cutoff.py").read_text(encoding="utf-8")
        self.assertIn('personal_trade_history_cutoff:v1:', source)
        self.assertIn('@app.post("/me/clear-trades")', source)
        self.assertIn('"global_across_sessions": True', source)
        self.assertIn('"execution_preserved": True', source)
        clear_block = source.split('@app.post("/me/clear-trades")', 1)[1].split(
            '@app.get("/me/trades/today")', 1
        )[0]
        self.assertNotIn("delete(Trade)", clear_block)
        self.assertNotIn("_hard_stop", clear_block)
        self.assertNotIn("enabled = False", clear_block)

    def test_old_trade_cannot_reappear_when_it_settles_after_clear(self) -> None:
        source = (ROOT / "app" / "global_trade_history_cutoff.py").read_text(encoding="utf-8")
        self.assertIn("Trade.purchase_time >= cutoff", source)
        self.assertIn("Trade.provider_purchase_time >= cutoff", source)
        self.assertIn("VirtualTrade.created_at >= cutoff", source)
        self.assertNotIn("Trade.settlement_time >= cutoff", source)
        self.assertIn('"history_cleared_at": cutoff_iso', source)
        self.assertIn('"history_visibility_global": True', source)

    def test_final_builder_authority_reasserts_global_history_routes_last(self) -> None:
        source = (ROOT / "app" / "builder_first_dashboard_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("install_global_trade_history_cutoff", source)
        self.assertIn("install_global_trade_history_cutoff(app)", source)

    def test_frontend_clear_calls_server_and_syncs_cutoff_into_existing_filter(self) -> None:
        source = (ROOT / "dashboard" / "global-trade-clear.js").read_text(encoding="utf-8")
        self.assertIn('fetch("/me/clear-trades"', source)
        self.assertIn('body: JSON.stringify({ scope: "all" })', source)
        self.assertIn("history_cleared_at", source)
        self.assertIn("localStorage.setItem(resetKey(payload)", source)
        self.assertIn('event.stopImmediatePropagation()', source)
        self.assertIn('/\\/me\\/trades\\/today', source)
        subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "global-trade-clear.js")],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_cutoff_parser_normalizes_timezone(self) -> None:
        value = _parse_cutoff("2026-08-13T10:00:00+03:00")
        self.assertIsNotNone(value)
        assert value is not None
        self.assertEqual(value.isoformat(), "2026-08-13T07:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
