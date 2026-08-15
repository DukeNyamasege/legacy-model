from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from app.final_trade_history_cutoff_authority import _row_visible_after_cutoff


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "app" / "final_trade_history_cutoff_authority.py"
BACKEND = ROOT / "app" / "netlify_backend_api.py"
KPI_JS = ROOT / "dashboard" / "virtual-kpi-neutrality.js"


class ClearTradesUnboundedKpiTests(unittest.TestCase):
    def test_cutoff_uses_trade_open_time_not_settlement_time(self) -> None:
        cutoff = datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc)
        old_actual = {
            "trade_kind": "actual",
            "purchase_time": "2026-08-15T05:59:59+00:00",
            "settlement_time": "2026-08-15T06:00:10+00:00",
        }
        new_actual = {
            "trade_kind": "actual",
            "purchase_time": "2026-08-15T06:00:01+00:00",
        }
        old_virtual = {
            "trade_kind": "virtual",
            "is_virtual": True,
            "created_at": "2026-08-15T05:59:59+00:00",
            "settled_at": "2026-08-15T06:00:10+00:00",
        }
        new_virtual = {
            "trade_kind": "virtual",
            "is_virtual": True,
            "created_at": "2026-08-15T06:00:01+00:00",
        }
        self.assertFalse(_row_visible_after_cutoff(old_actual, cutoff))
        self.assertTrue(_row_visible_after_cutoff(new_actual, cutoff))
        self.assertFalse(_row_visible_after_cutoff(old_virtual, cutoff))
        self.assertTrue(_row_visible_after_cutoff(new_virtual, cutoff))

    def test_server_aggregate_is_unbounded_and_cutoff_filtered(self) -> None:
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("func.count(Trade.id)", source)
        self.assertIn("Trade.purchase_time >= cutoff", source)
        self.assertIn("Trade.provider_purchase_time >= cutoff", source)
        self.assertIn("VirtualTrade.created_at >= cutoff", source)
        self.assertNotIn("limit(100)", source)
        self.assertNotIn("limit(5000)", source)
        self.assertIn('"unbounded_post_cutoff_database_aggregate"', source)

    def test_realtime_and_rest_use_same_post_clear_authority(self) -> None:
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("performance._fast_trade_payload = cutoff_fast_trade_payload", source)
        self.assertIn("gateway._fast_trade_payload = cutoff_fast_trade_payload", source)
        self.assertIn("performance._me_payload = cutoff_me_payload", source)
        self.assertIn("gateway._me_payload = cutoff_me_payload", source)
        self.assertIn("performance._clear_response_caches()", source)
        self.assertIn("await gateway._HUB.publish()", source)

    def test_final_backend_installs_cutoff_authority_last(self) -> None:
        source = BACKEND.read_text(encoding="utf-8")
        surface = source.index("install_backend_only_surface(app)")
        cutoff = source.index("install_final_trade_history_cutoff_authority(app)")
        self.assertLess(surface, cutoff)

    def test_frontend_uses_server_summary_not_bounded_rows(self) -> None:
        source = KPI_JS.read_text(encoding="utf-8")
        self.assertIn("function summaryMetrics(payload)", source)
        self.assertIn("summaryMetrics(payload) || rowFallbackMetrics(me, payload)", source)
        self.assertIn("Never derive KPI totals from rows.length", source)


if __name__ == "__main__":
    unittest.main()
