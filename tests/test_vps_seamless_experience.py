from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VPSSeamlessExperienceTests(unittest.TestCase):
    def test_full_vps_build_installs_preloaded_recovery_layer(self) -> None:
        source = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertIn("full-vps-same-origin-v2", source)
        self.assertIn("netlify-api-boundary.js?v=20260816-vps3", source)
        self.assertIn("vps-seamless-experience.js?v=20260816-3", source)
        self.assertIn("vps-seamless-experience.css?v=20260816-3", source)
        self.assertIn("dashboardMarker", source)

    def test_full_vps_does_not_use_old_3_2_second_netlify_read_sla(self) -> None:
        source = (ROOT / "dashboard" / "netlify-api-boundary.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('FRONTEND_RUNTIME.startsWith("full-vps-same-origin")', source)
        self.assertIn("const GET_TIMEOUT_MS = FULL_VPS ? 6500 : 3200", source)
        self.assertIn("const GET_RETRY_COUNT = FULL_VPS ? 1 : 0", source)
        self.assertIn("safeGetMethod(method)", source)
        self.assertIn("const live = cachedPayload(sourcePath)", source)
        self.assertIn("full-vps-same-origin-rest-v3-resilient", source)
        self.assertIn("Writes\n  // are never retried automatically", source)

    def test_frontend_recovers_authenticated_shell_from_signed_snapshot(self) -> None:
        source = (ROOT / "dashboard" / "vps-seamless-experience.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('const SESSION_KEY = "foa-session-v2"', source)
        self.assertIn("rememberAuthenticatedSession(payload)", source)
        self.assertIn("recoverAuthenticatedShell(payload)", source)
        self.assertIn('document.querySelector(".foa-landing-v2, .public-builder")', source)
        self.assertIn("window.location.reload()", source)
        self.assertIn("AUTH_RECOVERY_KEY", source)
        self.assertIn("lastRealtimeAt = Date.now()", source)

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
        self.assertIn('condition_not_met: ["Scanning", "wait"]', source)
        self.assertIn('condition_met: ["Matched", "met"]', source)
        self.assertNotIn("Live Strategy Monitor", source)
        self.assertNotIn("foa-vps-feed-row", source)

    def test_strategy_scanner_is_tiny_and_stable_on_mobile(self) -> None:
        source = (ROOT / "dashboard" / "vps-seamless-experience.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("min-height: 24px", source)
        self.assertIn("padding: 4px 7px", source)
        self.assertIn("font-size: 10px", source)
        self.assertIn("text-overflow: ellipsis", source)
        self.assertIn("min-height: 22px", source)
        self.assertNotIn(".foa-vps-monitor-head", source)
        self.assertNotIn(".foa-vps-feed-row", source)

    def test_trades_control_does_not_restore_800ms_http_polling(self) -> None:
        source = (ROOT / "dashboard" / "trades-start-stop-toggle.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("const FALLBACK_POLL_MS = 10000", source)
        self.assertIn("foa:vps-live-snapshot", source)
        self.assertNotIn("const POLL_MS = 800", source)
        self.assertIn("cachedLifecycle() || await readLifecycle()", source)

    def test_worker_reports_exact_saved_scan_condition_without_financial_side_effect(self) -> None:
        source = (ROOT / "app" / "vps_seamless_worker.py").read_text(encoding="utf-8")
        self.assertIn("describe_condition", source)
        self.assertIn("_scan_description", source)
        self.assertIn('f"Scanning {market_scope} for {criteria}."', source)
        self.assertIn('f"Matched on {symbol}: {criteria}."', source)
        self.assertIn("condition_not_met", source)
        self.assertIn("condition_met", source)
        self.assertIn("trade_preparing", source)
        self.assertIn("trade_open", source)
        self.assertIn("_PENDING_LATEST", source)
        self.assertIn("execution_unaffected=true", source)
        self.assertNotIn("session.add(", source)
        self.assertNotIn("session.commit(", source)

    def test_api_event_bus_is_memory_only_and_advances_signed_realtime_revision(self) -> None:
        source = (ROOT / "app" / "vps_realtime_events.py").read_text(encoding="utf-8")
        self.assertIn("/control/internal/vps-runtime-events", source)
        self.assertIn('"runtime_events"', source)
        self.assertIn("_EVENT_REVISIONS", source)
        self.assertIn("live_snapshot_with_runtime_revision", source)
        self.assertIn('f"{base_revision}|vps-events:{_event_revision(managed_id)}"', source)
        self.assertIn(
            "realtime_gateway._live_snapshot = live_snapshot_with_runtime_revision",
            source,
        )
        self.assertIn('"frontend": "vps_nginx_static"', source)
        self.assertIn('"rest_transport": "vps_same_origin_caddy"', source)
        self.assertIn('"storage": "ephemeral_api_memory"', source)
        self.assertNotIn("INSERT INTO", source)
        self.assertNotIn("session.add(", source)
        self.assertNotIn("session.commit(", source)

    def test_legacy_vps_recovery_keeps_oauth_refresh_and_settlement_guard(self) -> None:
        source = (ROOT / "app" / "vps_execution_start_recovery.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("token_is_expiring", source)
        self.assertIn("refresh_access_token", source)
        self.assertIn("asyncio.to_thread", source)
        self.assertIn("if getattr(session, \"pending_contracts\", set()):", source)
        self.assertIn("_provider_backoff_active(row)", source)
        self.assertIn("sibling_sessions_rebuilt=false", source)
        self.assertNotIn("validate_accounts()", source)

    def test_final_connection_authority_overrides_timer_recycle_before_monitor(self) -> None:
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        stability = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        manual = worker.index("install_custom_strategy_manual_stop_guard()")
        continuity = worker.index("install_final_execution_continuity()")
        connection = worker.index("install_custom_strategy_connection_stampede_guard()")
        recovery = worker.index("install_vps_execution_start_recovery()")
        final_stability = worker.index("install_custom_strategy_connection_stability_fix()")
        monitor = worker.index("install_vps_seamless_worker()")
        self.assertLess(manual, monitor)
        self.assertLess(continuity, recovery)
        self.assertLess(connection, recovery)
        self.assertLess(recovery, final_stability)
        self.assertLess(final_stability, monitor)
        self.assertIn("stalled_execution_recovery=live_session_owned", worker)
        self.assertIn("forced_recycle=false", worker)
        self.assertIn("public_reconnect_owner=public_websocket_resilience", worker)
        self.assertIn(
            "vps_recovery._stalled_execution_watchdog = _stable_execution_watchdog",
            stability,
        )
        self.assertIn("session_recycle=false", stability)
        self.assertIn("live_strategy_monitor=ephemeral_docker_event_bus", worker)

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
