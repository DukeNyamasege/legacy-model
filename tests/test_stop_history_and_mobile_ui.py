from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StopHistoryContractTests(unittest.TestCase):
    def test_stop_resets_recovery_without_marking_history_reset(self) -> None:
        source = (ROOT / "app" / "lifecycle_reset_authority.py").read_text(
            encoding="utf-8"
        )
        stop_block = source.split('@app.post("/me/stop-trading")', 1)[1].split(
            '@app.post("/me/pause-trading")', 1
        )[0]
        self.assertIn("mark_history_reset=False", stop_block)
        self.assertIn('"history_preserved": True', stop_block)
        self.assertIn("Trade history is retained", stop_block)

    def test_only_explicit_clear_marks_history_reset(self) -> None:
        source = (ROOT / "app" / "lifecycle_reset_authority.py").read_text(
            encoding="utf-8"
        )
        clear_block = source.split('@app.post("/me/clear-trades")', 1)[1]
        self.assertIn("mark_history_reset=True", clear_block)
        start_block = source.split("def _start(", 1)[1].split(
            "def install_lifecycle_reset_authority", 1
        )[0]
        self.assertNotIn("_write_reset_marker", start_block)

    def test_daily_trade_stream_has_no_stop_session_filter(self) -> None:
        source = (ROOT / "app" / "final_personal_trade_stream.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("_trade_belongs_to_session", source)
        self.assertNotIn("created <= reset_at", source)
        self.assertIn('"history_preserved_across_stop": True', source)
        self.assertIn("actual_rows = session.execute", source)


class CompactMobileUIContractTests(unittest.TestCase):
    def test_compact_mobile_css_is_installed_after_stable_dashboard(self) -> None:
        settings_guard = (ROOT / "app" / "dashboard_settings_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from app.mobile_compact_ui import install_mobile_compact_ui", settings_guard)
        self.assertIn("install_mobile_compact_ui(app)", settings_guard)

    def test_narrow_phone_typography_is_materially_smaller(self) -> None:
        source = (ROOT / "app" / "mobile_compact_ui.py").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 760px)", source)
        self.assertIn("@media (max-width: 420px)", source)
        self.assertIn("font-size: 9.5px !important", source)
        self.assertIn("font-size: 16px !important", source)
        self.assertIn("min-height: 44px !important", source)
        self.assertIn("foa-top-actions", source)
        self.assertIn("X-FOA-Mobile-UI-Version", source)


class DashboardSessionAndReachabilityTests(unittest.TestCase):
    def test_returning_session_bootstrap_is_final_root_authority(self) -> None:
        api_v3 = (ROOT / "app" / "api_v3.py").read_text(encoding="utf-8")
        final_ui = api_v3.index("install_final_virtual_history_ui(app)")
        session_resilience = api_v3.index("install_dashboard_session_resilience(app)")
        head_compat = api_v3.index("install_head_request_compat(app)")
        self.assertLess(final_ui, session_resilience)
        self.assertLess(session_resilience, head_compat)

        source = (ROOT / "app" / "dashboard_session_resilience.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("window.FOA_BOOT_SESSION=", source)
        self.assertIn("base_api.get_current_account(request)", source)
        self.assertIn('marker = \'<script src="/ui/dashboard-v2.js\'', source)

    def test_dashboard_js_uses_safe_session_marker_and_fast_route_switch(self) -> None:
        source = (ROOT / "dashboard" / "dashboard-v2.js").read_text(encoding="utf-8")
        self.assertIn('session: "foa-session-v2"', source)
        self.assertIn("window.FOA_BOOT_SESSION", source)
        self.assertIn("booting: !BOOT_SESSION?.authenticated", source)
        self.assertIn("function switchMode(mode)", source)
        self.assertIn('refresh(false, "Refreshing dashboard...")', source)

    def test_head_compatibility_covers_static_and_health_routes(self) -> None:
        head = (ROOT / "app" / "head_request_compat.py").read_text(encoding="utf-8")
        for path in (
            "/ui/dashboard-v2.css",
            "/ui/dashboard-actions-v2.js",
            "/health",
            "/health/live",
            "/runtime",
        ):
            self.assertIn(path, head)
        database = (ROOT / "app" / "database_runtime_hardening.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('@app.head("/health/database"', database)

    def test_health_readiness_uses_lightweight_worker_heartbeat(self) -> None:
        repository = (ROOT / "app" / "repositories" / "test2_repository.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def worker_heartbeat", repository)
        self.assertIn("select(BotState.last_heartbeat)", repository)

        api = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
        ready_block = api.split('def health_ready() -> dict:', 1)[1].split(
            '@app.get("/status")', 1
        )[0]
        self.assertIn("REPOSITORY.worker_heartbeat()", ready_block)
        self.assertNotIn("REPOSITORY.summary()", ready_block)

    def test_vps_smoke_tracks_current_dashboard_and_keeps_healthy_api_available(self) -> None:
        smoke = (ROOT / "scripts" / "production_smoke.py").read_text(encoding="utf-8")
        self.assertIn("READINESS_PROBE_TIMEOUT_SECONDS = 30.0", smoke)
        self.assertIn("/ui/dashboard-v2.js", smoke)
        self.assertIn("foa-session-v2", smoke)
        self.assertIn("window.FOA_BOOT_SESSION", smoke)
        self.assertIn("mobile_input_zoom_guard", smoke)

        deploy = (ROOT / "scripts" / "deploy_vps.sh").read_text(encoding="utf-8")
        self.assertIn("API_DATABASE_HEALTHY=true", deploy)
        self.assertIn("leaving it running to avoid a public 502", deploy)

    def test_vps_deploy_gates_release_before_live_cutover(self) -> None:
        deploy = (ROOT / "scripts" / "deploy_vps.sh").read_text(encoding="utf-8")
        main_flow = deploy.split('echo "2a. Verify System and Custom Martingale stake calculations"', 1)[1]
        gate = main_flow.index("run_release_gate")
        stop_live = main_flow.index('echo "5. Stop old API and worker only after the release gate passes"')
        self.assertLess(gate, stop_live)

        self.assertIn("legacy-model-preflight-", deploy)
        self.assertIn("DERIV_ENVIRONMENT: demo", deploy)
        self.assertIn('DERIV_TRADING_ENABLED: "false"', deploy)
        self.assertIn('TELEGRAM_NOTIFICATIONS_SUSPENDED: "true"', deploy)
        self.assertIn("Release gate smoke test failed. Production was not changed.", deploy)
        self.assertIn("Production cutover failed before containers were replaced", deploy)
        self.assertIn("Verify live PostgreSQL and create a pre-migration backup before cutover", deploy)
        self.assertIn("compose ps --status running -q database", deploy)
        cutover_section = main_flow.split('echo "5. Stop old API and worker only after the release gate passes"', 1)[0]
        self.assertNotIn("compose stop worker api", cutover_section)
        self.assertNotIn("recreate_database_container", deploy)

    def test_github_release_gate_workflow_exists(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release-gate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Release Gate", workflow)
        self.assertIn("pull_request", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("python -m compileall -q app scripts", workflow)
        self.assertIn("python -m unittest -q tests.test_stop_history_and_mobile_ui", workflow)
        self.assertIn("sh -n scripts/deploy_vps.sh scripts/update_vps.sh", workflow)
        self.assertIn("docker build --target api", workflow)


if __name__ == "__main__":
    unittest.main()
