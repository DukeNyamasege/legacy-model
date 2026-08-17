from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from app.final_trade_history_cutoff_authority import _row_visible_after_cutoff


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "app" / "final_trade_history_cutoff_authority.py"
BACKEND = ROOT / "app" / "netlify_backend_api.py"
FINAL_UI_JS = ROOT / "dashboard" / "final-ui-shell-v2.js"
PREMIUM_JS = ROOT / "dashboard" / "final-premium-6f3.js"
VPS_REALTIME_JS = ROOT / "dashboard" / "vps-realtime-client-v2.js"
INDEX = ROOT / "dashboard" / "index.html"


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

    def test_retired_client_side_kpi_reset_overlay_is_removed(self) -> None:
        self.assertFalse((ROOT / "dashboard" / "custom-runtime-client.js").exists())
        self.assertFalse((ROOT / "dashboard" / "dashboard-v2.js").exists())
        index = INDEX.read_text(encoding="utf-8")
        self.assertNotIn("custom-runtime-client.js", index)
        self.assertNotIn("dashboard-v2.js", index)
        # Clear-history visibility is server-authoritative now, not browser-local.
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("_row_visible_after_cutoff", source)
        self.assertIn("cutoff_fast_trade_payload", source)

    def test_new_direct_vps_home_uses_server_summary_as_single_kpi_source(self) -> None:
        shell = FINAL_UI_JS.read_text(encoding="utf-8")
        premium = PREMIUM_JS.read_text(encoding="utf-8")
        realtime = VPS_REALTIME_JS.read_text(encoding="utf-8")
        index = INDEX.read_text(encoding="utf-8")

        metrics = shell.split("function metrics()", 1)[1].split("function home()", 1)[0]
        self.assertIn("const summary = state.trades?.summary || {}", metrics)
        self.assertIn("const meStats = state.me?.stats || {}", metrics)
        self.assertIn("summary.total ?? meStats.trades", metrics)
        self.assertIn("summary.wins ?? meStats.wins", metrics)
        self.assertIn("summary.losses ?? meStats.losses", metrics)
        self.assertIn("summary.profit ?? meStats.profit", metrics)
        self.assertNotIn("state.trades?.trades", metrics)

        self.assertIn("/me/live-snapshot", realtime)
        self.assertIn("raw.trades || null", realtime)
        self.assertIn("window.DERIVADMIN_LIVE_CACHE", realtime)
        self.assertNotIn("querySelectorAll", realtime)
        self.assertNotIn("innerHTML", realtime)

        # F3 holds these heavy F2 modules behind Premium admission rather than
        # loading them directly from index.html.
        self.assertNotIn('<script src="/vps-realtime-client-v2.js?v=20260817-6f2-1" defer>', index)
        self.assertNotIn('<script src="/final-ui-shell-v2.js?v=20260817-6f2-1" defer>', index)
        self.assertIn("vps-realtime-client-v2.js?v=20260817-6f2-1", premium)
        self.assertIn("final-ui-shell-v2.js?v=20260817-6f2-1", premium)
        self.assertIn("if (state.premium?.local_dev_preview || state.premium?.active)", premium)
        self.assertNotIn("final-ui-shell-v1.js", index)
        self.assertNotIn("virtual-kpi-neutrality.js", index)
        self.assertNotIn("netlify-realtime-client.js", index)


if __name__ == "__main__":
    unittest.main()
