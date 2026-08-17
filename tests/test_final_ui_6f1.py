from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FinalUi6F1Tests(unittest.TestCase):
    def test_source_document_has_one_direct_vps_ui_authority(self) -> None:
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn('frontend-runtime" content="direct-vps-final-ui-6f1"', html)
        self.assertIn('/vps-api-boundary.js?v=20260817-1', html)
        self.assertIn('/vps-realtime-client.js?v=20260817-6f1-2', html)
        self.assertIn('/final-ui-shell-v1.css?v=20260817-6f1-1', html)
        self.assertIn('/final-ui-shell-v1.js?v=20260817-6f1-1', html)
        self.assertIn('id="derivadmin-root"', html)

        forbidden = (
            "/netlify-api-boundary.js",
            "/netlify-realtime-client.js",
            "/ui/dashboard-v2.css",
            "/ui/dashboard-v2.js",
            "/ui/dashboard-actions-v2.js",
            "automation-home-v1",
            "text-to-strategy-v1",
            "strategy-ready-v1",
            "timezone-schedule-v1",
            "automation-scheduler-action5",
            "premium-subscription-action6e",
            "final-dashboard-authority",
            "mobile-topbar-compact",
            "tablet-navigation-fix",
        )
        for marker in forbidden:
            self.assertNotIn(marker, html)

    def test_new_shell_matches_approved_home_information_architecture(self) -> None:
        js = (ROOT / "dashboard" / "final-ui-shell-v1.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "final-ui-shell-v1.css").read_text(encoding="utf-8")

        for text in (
            "DerivAdmin",
            "Home of Automation",
            "Strategy Builder",
            "Text to Strategy",
            "Schedule Trading",
            "My Automation",
            "Strategy Library",
            "Built-in",
            "My Strategies",
            "AI Generated",
            "Balance",
            "Runs",
            "P/L",
            "Wins",
            "Losses",
        ):
            self.assertIn(text, js)

        for route in ('"home"', '"builder"', '"ai"', '"schedule"', '"profile"'):
            self.assertIn(route, js)

        self.assertIn('api("/me")', js)
        self.assertIn('api("/me/trades/today?limit=100")', js)
        self.assertIn('api("/me/trading-lifecycle")', js)
        self.assertIn('api("/me/automation-schedules?limit=20")', js)
        self.assertIn('api("/me/premium-access")', js)
        self.assertIn('"/me/switch-account"', js)
        self.assertIn('data-account-mode="${mode}"', js)
        self.assertIn('"demo", "real"', js)

        self.assertIn("--da-bg: #010816", css)
        self.assertIn("--da-blue: #078fff", css)
        self.assertIn("--da-cyan: #12d9ff", css)
        self.assertIn("--da-purple: #9b5cff", css)
        self.assertIn(".da-bottom-nav", css)
        self.assertIn(".da-feature", css)
        self.assertIn(".da-greeting-card", css)
        self.assertIn("@media (max-width: 760px)", css)

    def test_direct_vps_build_ships_only_new_shell_assets(self) -> None:
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        package = (ROOT / "package.json").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")

        self.assertIn('deployment_topology: "direct-vps-only"', build)
        self.assertIn('ui_authority: "final-ui-shell-v1"', build)
        self.assertIn('production_asset_policy: "new-shell-whitelist-only"', build)
        self.assertIn('legacy_ui_shipped: false', build)
        self.assertIn('netlify_runtime_loaded: false', build)
        self.assertIn('const productionAssets = [', build)
        for asset in (
            '"index.html"',
            '"final-ui-shell-v1.css"',
            '"final-ui-shell-v1.js"',
            '"vps-api-boundary.js"',
            '"vps-realtime-client.js"',
        ):
            self.assertIn(asset, build)
        self.assertNotIn('cp(resolve(root, "dashboard")', build)
        self.assertNotIn("build-netlify.mjs", build)
        self.assertNotIn("npm run build:netlify", package)
        self.assertIn('"build": "node scripts/build-vps.mjs"', package)
        self.assertNotIn("build-netlify.mjs", dockerfile)
        self.assertIn("RUN node scripts/build-vps.mjs", dockerfile)

    def test_vps_realtime_is_data_transport_only(self) -> None:
        source = (ROOT / "dashboard" / "vps-realtime-client.js").read_text(encoding="utf-8")
        self.assertIn("/me/live-ticket", source)
        self.assertIn("/ws/me/live?ticket=", source)
        self.assertIn("/me/live-snapshot", source)
        self.assertIn("window.DERIVADMIN_LIVE_CACHE", source)
        self.assertIn('CustomEvent("derivadmin:live-snapshot"', source)
        self.assertNotIn('querySelectorAll(".builder-stat")', source)
        self.assertNotIn("innerHTML", source)

    def test_same_origin_vps_edge_and_database_safety_remain(self) -> None:
        caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        compose_vps = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")
        deploy = (ROOT / "scripts" / "deploy_full_vps.sh").read_text(encoding="utf-8")

        self.assertIn("handle_path /api/*", caddy)
        self.assertIn("handle /oauth/*", caddy)
        self.assertIn("handle /ws/*", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:8081", caddy)
        self.assertIn("test2_database:/var/lib/postgresql/data", compose)
        self.assertIn('"127.0.0.1:8081:80"', compose_vps)
        self.assertNotIn("docker volume prune", deploy)
        self.assertNotIn("docker system prune --volumes", deploy)
        self.assertNotIn("docker compose down -v", deploy)

    def test_new_ui_javascript_and_vps_build_are_valid_javascript(self) -> None:
        for path in (
            ROOT / "dashboard" / "final-ui-shell-v1.js",
            ROOT / "dashboard" / "vps-realtime-client.js",
            ROOT / "dashboard" / "vps-api-boundary.js",
            ROOT / "scripts" / "build-vps.mjs",
        ):
            result = subprocess.run(
                ["node", "--check", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f"{path.name}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
