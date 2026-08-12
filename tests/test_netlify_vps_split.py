from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class NetlifyVpsSplitArchitectureTests(unittest.TestCase):
    def test_netlify_is_authoritative_static_frontend(self) -> None:
        config = source("netlify.toml")
        build = source("scripts/build-netlify.mjs")
        self.assertIn('command = "npm run build"', config)
        self.assertIn('publish = "dist"', config)
        self.assertIn("BACKEND_ORIGIN", build)
        self.assertIn("DASHBOARD_WS_BASE_URL", build)
        self.assertIn("/api/*", build)
        self.assertIn("/oauth/*", build)
        self.assertIn("netlify-api-boundary.js", build)
        self.assertIn("netlify-realtime-client.js", build)

    def test_backend_has_signed_realtime_boundary(self) -> None:
        gateway = source("app/netlify_realtime_gateway.py")
        backend = source("app/netlify_backend_api.py")
        self.assertIn("/me/live-ticket", gateway)
        self.assertIn("/me/live-snapshot", gateway)
        self.assertIn("/ws/me/live", gateway)
        self.assertIn("DASHBOARD_STREAM_SIGNING_KEY", gateway)
        self.assertIn("install_netlify_realtime_gateway(app)", backend)
        self.assertIn('production_frontend_host = "netlify"', backend)

    def test_realtime_ticket_does_not_expose_deriv_credential(self) -> None:
        gateway = source("app/netlify_realtime_gateway.py")
        ticket_section = gateway.split("def netlify_live_ticket", 1)[1].split(
            '@app.get("/me/live-snapshot"', 1
        )[0]
        self.assertIn('"sid": session_hash_value', ticket_section)
        self.assertIn('"mid": int(account["id"])', ticket_section)
        self.assertNotIn("access_token", ticket_section)
        self.assertNotIn("refresh_token", ticket_section)
        self.assertNotIn("token_secret", ticket_section)

    def test_netlify_browser_runtime_uses_direct_wss_and_bounded_fallback(self) -> None:
        realtime = source("dashboard/netlify-realtime-client.js")
        boundary = source("dashboard/netlify-api-boundary.js")
        self.assertIn("new WebSocket", realtime)
        self.assertIn("/me/live-ticket", realtime)
        self.assertIn("/ws/me/live", realtime)
        self.assertIn("/me/live-snapshot", realtime)
        self.assertIn("FALLBACK_MS", realtime)
        self.assertIn("netlify-same-origin-rest-v2-optimistic-lifecycle", boundary)
        self.assertIn("GET_TIMEOUT_MS", boundary)
        self.assertIn("WRITE_TIMEOUT_MS", boundary)

    def test_worker_bridge_separates_financial_execution_from_dashboard_delivery(self) -> None:
        bridge = source("app/netlify_worker_bridge.py")
        worker = source("app/custom_strategy_worker.py")
        self.assertIn("install_netlify_worker_bridge", worker)
        self.assertIn("best-effort", bridge.lower())
        self.assertIn("Purchase acknowledgement timed out", bridge)
        self.assertIn("purchase_retry=false", bridge)
        self.assertIn("Auto Trading remains enabled", bridge)

    def test_planned_public_market_restart_uses_fast_path(self) -> None:
        resilience = source("app/public_websocket_resilience.py")
        self.assertIn("PUBLIC_WS_PLANNED_RESTART_SECONDS", resilience)
        self.assertIn("custom_market_set_changed", resilience)
        self.assertIn("planned_restart=true", resilience.replace("%s", "true"))
        self.assertIn("if planned_restart:", resilience)

    def test_vps_runs_backend_only_services(self) -> None:
        compose = source("docker-compose.yml")
        self.assertIn("app.netlify_backend_api:app", compose)
        self.assertIn("app.custom_strategy_worker", compose)
        self.assertIn("FRONTEND_HOSTING_MODE", compose)
        self.assertIn("DASHBOARD_STREAM_SIGNING_KEY", compose)
        self.assertNotIn("frontend:\n", compose)
        worker = source("app/custom_strategy_worker.py")
        self.assertIn("install_netlify_worker_bridge()", worker)

    def test_backend_proxy_targets_selected_contabo_host(self) -> None:
        caddy = source("Caddyfile")
        self.assertIn("api.derivadmin.site", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:8080", caddy)
        env = source(".env.vps.example")
        self.assertIn("FRONTEND_HOSTING_MODE=netlify", env)
        self.assertIn("DASHBOARD_FRONTEND_ORIGINS=", env)
        self.assertIn("DASHBOARD_STREAM_SIGNING_KEY=", env)
        self.assertIn("CLIENT_SESSION_SAMESITE=lax", env)
        self.assertIn("api.derivadmin.site", env)
        self.assertIn("https://derivadmin.site/oauth/callback", env)
        self.assertIn("DERIV_TRADING_ENABLED=false", env)
        self.assertIn("ALLOW_REAL_TRADING=false", env)


if __name__ == "__main__":
    unittest.main()
