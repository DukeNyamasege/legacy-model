from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DashboardSessionAndReachabilityTests(unittest.TestCase):
    def test_dashboard_session_and_reachability_contracts(self) -> None:
        source = (ROOT / "dashboard" / "dashboard-v2.js").read_text(encoding="utf-8")
        self.assertIn("foa-session-v2", source)
        self.assertIn("foa-builder-last-good-snapshot-v1", source)
        self.assertIn("LIVE REFRESH DELAYED", source)
        self.assertIn("Opening builder...", source)
        self.assertIn("authenticated", source)

    def test_mobile_css_remains_present(self) -> None:
        css = (ROOT / "dashboard" / "dashboard-v2.css").read_text(encoding="utf-8")
        self.assertIn("@media", css)
        self.assertIn("max-width", css)

    def test_stop_history_backend_is_non_destructive(self) -> None:
        source = (ROOT / "app" / "final_public_controls.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_set_stopped", source)
        self.assertIn("_reset_risk_state", source)
        self.assertNotIn("delete(ManagedAccount)", source)

    def test_deploy_script_preserves_postgres_volume(self) -> None:
        deploy = (ROOT / "scripts" / "deploy_dedicated_backend.sh").read_text(
            encoding="utf-8"
        )
        build = deploy.index("compose build")
        start_database = deploy.index("compose up -d database")
        cutover = deploy.index("compose up -d --force-recreate")
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
        # Action 5 adds the persistent automation_schedules migration. Keep this
        # assertion pinned to the current single Alembic head so release CI cannot
        # silently validate an obsolete schema.
        self.assertIn('alembic heads | grep -q "20260817_0022 (head)"', workflow)
        self.assertNotIn('alembic heads | grep -q "20260812_0021 (head)"', workflow)
        self.assertNotIn('alembic heads | grep -q "20260805_0020 (head)"', workflow)
        self.assertIn("docker build --target api", workflow)
        self.assertIn("Production Netlify frontend build", workflow)


if __name__ == "__main__":
    unittest.main()
