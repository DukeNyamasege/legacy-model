from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from app.dashboard_live_events import _runtime_state


ROOT = Path(__file__).resolve().parents[1]


class DashboardLivePerformanceTests(TestCase):
    def test_runtime_state_is_read_only_mapping(self) -> None:
        self.assertEqual(_runtime_state(enabled=True, status="starting"), "STARTING")
        self.assertEqual(
            _runtime_state(enabled=True, status="waiting_for_condition"),
            "WAITING_FOR_CONDITION",
        )
        self.assertEqual(_runtime_state(enabled=True, status="executing"), "EXECUTING")
        self.assertEqual(_runtime_state(enabled=True, status="running"), "RUNNING")
        self.assertEqual(_runtime_state(enabled=False, status="running"), "STOPPED")
        self.assertEqual(_runtime_state(enabled=False, status="credential_error"), "ERROR")

    def test_live_event_stream_never_stops_account(self) -> None:
        source = (ROOT / "app" / "dashboard_live_events.py").read_text(encoding="utf-8")
        self.assertIn('"/me/live-events"', source)
        self.assertIn("text/event-stream", source)
        self.assertIn("await request.is_disconnected()", source)
        self.assertIn("await asyncio.sleep(0.75)", source)
        self.assertNotIn("row.enabled =", source)
        self.assertNotIn("stop-trading", source)
        self.assertNotIn("auto-trade", source)

    def test_final_browser_uses_vps_websocket_primary_and_bounded_rest_fallback(self) -> None:
        source = (ROOT / "dashboard" / "vps-realtime-client-v2.js").read_text(
            encoding="utf-8"
        )
        premium = (ROOT / "dashboard" / "final-premium-6f3.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('fetch("/me/live-ticket"', source)
        self.assertIn('new WebSocket(`${streamBase}/ws/me/live?ticket=', source)
        self.assertIn('fetch("/me/live-snapshot"', source)
        self.assertIn("const FALLBACK_MS = 5000", source)
        self.assertIn("Math.min(8000, 450 *", source)
        self.assertIn("document.hidden", source)
        self.assertIn("visibilitychange", source)
        self.assertIn("window.DERIVADMIN_LIVE_CACHE", source)
        self.assertNotIn("innerHTML", source)
        # 6F-3 does not load the realtime transport for unpaid users.
        self.assertIn('/vps-realtime-client-v2.js?v=20260817-6f2-1', premium)
        self.assertIn("if (realtime && !state.realtimeLoaded)", premium)
        self.assertIn("if (state.premium?.local_dev_preview || state.premium?.active)", premium)

    def test_live_dashboard_respects_server_clear_trades_cutoff(self) -> None:
        cutoff = (ROOT / "app" / "final_trade_history_cutoff_authority.py").read_text(
            encoding="utf-8"
        )
        final_ui = (ROOT / "dashboard" / "final-ui-shell-v2.js").read_text(
            encoding="utf-8"
        )
        realtime = (ROOT / "dashboard" / "vps-realtime-client-v2.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("performance._fast_trade_payload = cutoff_fast_trade_payload", cutoff)
        self.assertIn("gateway._fast_trade_payload = cutoff_fast_trade_payload", cutoff)
        self.assertIn("performance._clear_response_caches()", cutoff)
        self.assertIn("await gateway._HUB.publish()", cutoff)
        self.assertIn("const summary = state.trades?.summary || {}", final_ui)
        self.assertIn("raw.trades || null", realtime)
        self.assertIn("document.dispatchEvent(new CustomEvent(\"foa:vps-live\"", realtime)
        self.assertFalse((ROOT / "dashboard" / "custom-runtime-client.js").exists())
        self.assertFalse((ROOT / "dashboard" / "dashboard-v2.js").exists())

    def test_builder_installs_live_stream_after_runtime_routes(self) -> None:
        source = (ROOT / "app" / "builder_first_dashboard_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("install_dashboard_live_events(app)", source)
        self.assertIn('X-FOA-Live-Dashboard', source)
        self.assertIn("final-readiness-1", source)
        self.assertIn("install_session_risk_api_authority(app)", source)


if __name__ == "__main__":
    import unittest

    unittest.main()
