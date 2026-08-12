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

    def test_browser_uses_sse_primary_and_bounded_fallback(self) -> None:
        source = (ROOT / "dashboard" / "custom-runtime-client.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('new EventSource("/me/live-events"', source)
        self.assertIn("GET_TIMEOUT_MS = 4500", source)
        self.assertIn("POST_TIMEOUT_MS = 12000", source)
        self.assertIn("nativeSetInterval", source)
        self.assertIn("20000", source)
        self.assertIn('route === "/metrics/summary"', source)
        self.assertIn("refreshSummaryInBackground", source)
        self.assertIn("foa-nonblocking-loader-style", source)
        self.assertNotIn('["/me", 30000]', source)

    def test_live_dashboard_respects_clear_trades_reset(self) -> None:
        source = (ROOT / "dashboard" / "custom-runtime-client.js").read_text(
            encoding="utf-8"
        )
        builder = (ROOT / "dashboard" / "dashboard-v2.js").read_text(encoding="utf-8")
        self.assertIn('TRADE_RESET_PREFIX = "foa-trade-session-reset-v1"', source)
        self.assertIn("function tradeResetTime()", source)
        self.assertIn("function visibleLiveTrades()", source)
        self.assertIn("function visibleLiveMetrics()", source)
        self.assertIn('event.target?.closest?.("[data-clear-local-trades]")', source)
        self.assertIn('storageSet(tradeResetKey(), new Date().toISOString())', builder)
        self.assertIn("const resetMetrics = visibleLiveMetrics()", source)
        self.assertIn("const rows = visibleLiveTrades()", source)

    def test_builder_installs_live_stream_after_runtime_routes(self) -> None:
        source = (ROOT / "app" / "builder_first_dashboard_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("install_dashboard_live_events(app)", source)
        self.assertIn('X-FOA-Live-Dashboard', source)
        self.assertIn("live-dashboard-authority-5", source)


if __name__ == "__main__":
    import unittest

    unittest.main()
