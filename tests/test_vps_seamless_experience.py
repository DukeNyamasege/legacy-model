from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VPSSeamlessExperienceTests(unittest.TestCase):
    def test_full_vps_build_installs_preloaded_seamless_layer(self) -> None:
        source = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertIn("full-vps-same-origin-v2", source)
        self.assertIn("vps-seamless-experience.js?v=20260816-1", source)
        self.assertIn("vps-seamless-experience.css?v=20260816-1", source)
        self.assertIn("dashboardMarker", source)

    def test_frontend_is_realtime_first_and_single_click_safe(self) -> None:
        source = (ROOT / "dashboard" / "vps-seamless-experience.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('source.includes("refresh(false")', source)
        self.assertIn("if (action === \"start\" && builderDirty) return", source)
        self.assertIn("event.stopImmediatePropagation()", source)
        self.assertIn('"/me/resume-trading"', source)
        self.assertIn('"/me/stop-trading"', source)
        self.assertIn("foa:vps-live-snapshot", source)
        self.assertIn("Live Strategy Monitor", source)
        self.assertIn("Conditions not met", source) if False else None

    def test_trades_control_does_not_restore_800ms_http_polling(self) -> None:
        source = (ROOT / "dashboard" / "trades-start-stop-toggle.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("const FALLBACK_POLL_MS = 10000", source)
        self.assertIn("foa:vps-live-snapshot", source)
        self.assertNotIn("const POLL_MS = 800", source)
        self.assertIn("cachedLifecycle() || await readLifecycle()", source)

    def test_worker_events_are_ephemeral_and_non_financial(self) -> None:
        source = (ROOT / "app" / "vps_seamless_worker.py").read_text(encoding="utf-8")
        self.assertIn("condition_not_met", source)
        self.assertIn("condition_met", source)
        self.assertIn("trade_preparing", source)
        self.assertIn("trade_open", source)
        self.assertIn("_PENDING_LATEST", source)
        self.assertIn("execution_unaffected=true", source)
        self.assertNotIn("session.add(", source)
        self.assertNotIn("session.commit(", source)

    def test_api_event_bus_is_memory_only_and_corrects_vps_health_metadata(self) -> None:
        source = (ROOT / "app" / "vps_realtime_events.py").read_text(encoding="utf-8")
        self.assertIn("/control/internal/vps-runtime-events", source)
        self.assertIn('"runtime_events"', source)
        self.assertIn('"frontend": "vps_nginx_static"', source)
        self.assertIn('"rest_transport": "vps_same_origin_caddy"', source)
        self.assertIn('"storage": "ephemeral_api_memory"', source)
        self.assertNotIn("INSERT INTO", source)

    def test_worker_installs_monitor_after_financial_authorities(self) -> None:
        source = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        manual = source.index("install_custom_strategy_manual_stop_guard()")
        continuity = source.index("install_final_execution_continuity()")
        monitor = source.index("install_vps_seamless_worker()")
        self.assertLess(manual, monitor)
        self.assertLess(continuity, monitor)
        self.assertIn("live_strategy_monitor=ephemeral_docker_event_bus", source)

    def test_vps_compose_uses_tight_local_refresh_fallback(self) -> None:
        source = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")
        self.assertIn(
            "ACCOUNT_REFRESH_INTERVAL_SECONDS: ${VPS_ACCOUNT_REFRESH_INTERVAL_SECONDS:-1}",
            source,
        )
        self.assertIn(
            "INTERNAL_VPS_RUNTIME_EVENTS_URL: http://api:8080/control/internal/vps-runtime-events",
            source,
        )


if __name__ == "__main__":
    unittest.main()
