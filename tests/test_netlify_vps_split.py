from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class NetlifyVpsSplitArchitectureTests(unittest.TestCase):
    def test_netlify_is_production_frontend_without_hardcoded_old_vps(self) -> None:
        config = source("netlify.toml")
        self.assertIn('publish = "dist"', config)
        self.assertNotIn("derivadmin.site", config)
        self.assertNotIn("DASHBOARD_API_BASE_URL =", config)
        self.assertIn("BACKEND_ORIGIN", config)

    def test_build_generates_same_origin_rest_and_direct_websocket_assets(self) -> None:
        build = source("scripts/build-netlify.mjs")
        for marker in (
            "BACKEND_ORIGIN",
            "/api/*",
            "/oauth/*",
            "stream-base-url",
            "netlify-api-boundary.js",
            "custom-runtime-client.js",
            "oauth-direct-runtime.js",
            "netlify-realtime-client.js",
            "mobile-first-compact.css",
            "_redirects",
        ):
            self.assertIn(marker, build)
        self.assertIn('redirects.push("/* /index.html 200")', build)

    def test_browser_boundary_keeps_rest_same_origin_and_bounded(self) -> None:
        js = source("dashboard/netlify-api-boundary.js")
        self.assertIn('const API_PREFIX = "/api"', js)
        self.assertIn("GET_TIMEOUT_MS = 3200", js)
        self.assertIn("WRITE_TIMEOUT_MS = 5200", js)
        self.assertIn("window.EventSource = undefined", js)
        self.assertIn("FOA_NETLIFY_LIVE_CACHE", js)
        self.assertIn('route === "/metrics/summary"', js)

    def test_realtime_is_signed_direct_backend_websocket_with_http_fallback(self) -> None:
        js = source("dashboard/netlify-realtime-client.js")
        self.assertIn('fetch("/me/live-ticket"', js)
        self.assertIn("new WebSocket", js)
        self.assertIn("/ws/me/live?ticket=", js)
        self.assertIn('fetch("/me/live-snapshot"', js)
        self.assertIn("FOA_NETLIFY_LIVE_CACHE", js)
        self.assertIn("FALLBACK_MS = 5000", js)

    def test_backend_realtime_ticket_is_session_bound_and_short_lived(self) -> None:
        py = source("app/netlify_realtime_gateway.py")
        self.assertIn("_TICKET_TTL_SECONDS = 45", py)
        self.assertIn("hmac.compare_digest", py)
        self.assertIn("ClientSession", py)
        self.assertIn("int(client.managed_account_id) != managed_id", py)
        self.assertIn('@app.websocket("/ws/me/live")', py)
        self.assertIn("_origin_allowed", py)
        self.assertIn("legacy_summary_rebuild", py)
        self.assertIn("_FALLBACK_REVISION_SECONDS = 2.0", py)

    def test_execution_never_waits_for_dashboard_delivery(self) -> None:
        bridge = source("app/netlify_worker_bridge.py")
        self.assertIn("_schedule_dashboard_wakeup", bridge)
        self.assertIn("async def nonblocking_dashboard_notify", bridge)
        self.assertIn("ClientTimeout(total=0.8", bridge)
        self.assertIn("fallback_revision_poll=true", bridge)
        self.assertIn("proposal_with_one_safe_retry", bridge)
        self.assertIn("buy_without_ambiguous_retry", bridge)
        self.assertIn("Purchase acknowledgement timed out", bridge)
        self.assertIn("enabled_preserved=true", bridge)

    def test_planned_market_restart_does_not_use_outage_backoff(self) -> None:
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

    def test_backend_proxy_is_provider_neutral(self) -> None:
        caddy = source("Caddyfile")
        self.assertIn("{$BACKEND_DOMAIN", caddy)
        self.assertNotIn("derivadmin.site", caddy)
        env = source(".env.vps.example")
        self.assertIn("FRONTEND_HOSTING_MODE=netlify", env)
        self.assertIn("DASHBOARD_FRONTEND_ORIGINS=", env)
        self.assertIn("DASHBOARD_STREAM_SIGNING_KEY=", env)
        self.assertIn("CLIENT_SESSION_SAMESITE=lax", env)
        self.assertNotIn("derivadmin.site", env)


if __name__ == "__main__":
    unittest.main()
