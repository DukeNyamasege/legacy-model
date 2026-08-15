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
        builder_authority = api_v3.index("install_builder_first_dashboard_authority(app)")
        self.assertLess(final_ui, session_resilience)
        self.assertLess(session_resilience, head_compat)
        self.assertLess(head_compat, builder_authority)
        self.assertLess(api_v3.index("install_database_runtime_hardening(app)"), builder_authority)

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

    def test_builder_first_dashboard_authority_serves_final_shell_last(self) -> None:
        source = (ROOT / "app" / "builder_first_dashboard_authority.py").read_text(
            encoding="utf-8"
        )
        for marker in (
            'base_api.ROOT / "dashboard" / "index.html"',
            'base_api.ROOT / "dashboard" / name',
            "X-FOA-Builder-First-Dashboard",
            "FOA_SIMPLIFIED_DASHBOARD_COMPAT",
            '"/ui/dashboard-v2.css"',
            '"/ui/dashboard-v2.js"',
            '"/ui/dashboard-actions-v2.js"',
            '"/ui/simplified-dashboard.js"',
            "_remove_route(app, path, \"GET\")",
            "_remove_route(app, path, \"HEAD\")",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("AI Digit Recovery V1", source)
        self.assertNotIn("Start Even AutoTrade", source)

    def test_simplified_dashboard_file_is_builder_first_compatibility_stub(self) -> None:
        source = (ROOT / "dashboard" / "simplified-dashboard.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("FOA_SIMPLIFIED_DASHBOARD_COMPAT", source)
        self.assertIn("builder-first", source)
        self.assertIn("/ui/dashboard-v2.js", source)
        self.assertNotIn("AI Digit Recovery V1", source)
        self.assertNotIn("Start Even AutoTrade", source)

    def test_startup_token_sync_cannot_replace_builder_first_assets(self) -> None:
        source = (ROOT / "app" / "personal_token_sync.py").read_text(
            encoding="utf-8"
        )
        startup = source.split(
            "async def finalize_linked_account_token_sync() -> None:", 1
        )[1].split("app.state.personal_token_sync_installed", 1)[0]
        authority_guard = startup.index(
            '"builder_first_dashboard_authority_installed"'
        )
        legacy_asset_install = startup.index("_install_final_dashboard_scripts(app)")
        self.assertLess(authority_guard, legacy_asset_install)
        self.assertIn("return", startup[authority_guard:legacy_asset_install])

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

    def test_full_vps_replaces_old_smoke_surface_while_netlify_remains_rollback(self) -> None:
        self.assertFalse((ROOT / "scripts" / "production_smoke.py").exists())
        self.assertFalse((ROOT / "scripts" / "deploy_vps.sh").exists())
        self.assertTrue((ROOT / "docker-compose.vps.yml").exists())
        self.assertTrue((ROOT / "scripts" / "deploy_full_vps.sh").exists())
        self.assertTrue((ROOT / "scripts" / "install_full_vps_caddy.sh").exists())

        build = (ROOT / "scripts" / "build-netlify.mjs").read_text(encoding="utf-8")
        self.assertIn("BACKEND_ORIGIN", build)
        self.assertIn("netlify-api-boundary.js", build)
        self.assertIn("netlify-realtime-client.js", build)
        self.assertIn("/api/*", build)
        self.assertIn("/oauth/*", build)

        vps_build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertIn("full-vps-same-origin-v1", vps_build)
        self.assertIn('await rm(resolve(output, "_redirects"), { force: true });', vps_build)

        deploy = (ROOT / "scripts" / "deploy_full_vps.sh").read_text(encoding="utf-8")
        self.assertIn("FULL CONTABO VPS DEPLOYMENT", deploy)
        self.assertIn("compose build frontend api worker", deploy)
        self.assertIn("alembic upgrade head", deploy)
        self.assertIn("Database   : Docker named volume preserved", deploy)

    def test_final_dashboard_actions_route_uses_current_actions_source(self) -> None:
        final_ui = (ROOT / "app" / "final_virtual_history_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('base_api.ROOT / "dashboard" / "dashboard-actions-v2.js"', final_ui)

        actions = (ROOT / "dashboard" / "dashboard-actions-v2.js").read_text(
            encoding="utf-8"
        )
        for marker in ("foa-action-loader", "foa-final-trade-row", "clear-trades"):
            self.assertIn(marker, actions)

    def test_contabo_deploy_builds_before_backend_cutover_and_preserves_database_volume(self) -> None:
        deploy = (ROOT / "scripts" / "deploy_dedicated_backend.sh").read_text(
            encoding="utf-8"
        )
        build = deploy.index("compose build api worker")
        start_database = deploy.index("compose up -d database")
        cutover = deploy.index("compose up -d --force-recreate --remove-orphans api worker")
        self.assertLess(build, start_database)
        self.assertLess(start_database, cutover)
        self.assertIn("pg_dump --format=custom --no-owner --no-privileges", deploy)
        self.assertIn("alembic upgrade head", deploy)
        self.assertIn("PostgreSQL named volume preserved", deploy)
        self.assertNotIn("docker-compose.vps.yml", deploy)
        self.assertNotIn("docker volume prune", deploy)

    def test_github_release_gate_workflow_exists(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release-gate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Release Gate", workflow)
        self.assertIn("pull_request", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("python -m compileall -q app scripts", workflow)
        self.assertIn("python -m unittest -q tests.test_stop_history_and_mobile_ui", workflow)
        self.assertIn("sh -n scripts/deploy_dedicated_backend.sh", workflow)
        self.assertIn("docker compose -f docker-compose.yml config --quiet", workflow)
        self.assertNotIn("scripts/deploy_vps.sh", workflow)
        self.assertIn("docker-compose.vps.yml", workflow)
        self.assertIn("Production full VPS frontend build", workflow)
        self.assertIn("Build frontend image", workflow)
        self.assertIn('alembic heads | grep -q "20260812_0021 (head)"', workflow)
        self.assertNotIn('alembic heads | grep -q "20260805_0020 (head)"', workflow)
        self.assertIn("docker build --target api", workflow)
        self.assertIn("Production Netlify frontend build", workflow)


if __name__ == "__main__":
    unittest.main()
