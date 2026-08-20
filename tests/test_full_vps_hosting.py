from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FullVpsHostingTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_compose_runs_complete_application_on_vps(self) -> None:
        base = self.text("docker-compose.yml")
        override = self.text("docker-compose.vps.yml")

        self.assertIn("database:", base)
        self.assertIn("api:", base)
        self.assertIn("worker:", base)
        self.assertIn("frontend:", override)
        self.assertIn("uvicorn app.vps_backend_api:app", base)
        self.assertIn("python -m app.custom_strategy_worker", base)
        self.assertIn('"127.0.0.1:8080:8080"', base)
        self.assertIn('"127.0.0.1:8081:80"', override)
        self.assertIn("test2_database:/var/lib/postgresql/data", base)

    def test_caddy_is_same_origin_public_edge(self) -> None:
        source = self.text("Caddyfile")
        self.assertIn("derivadmin.site {", source)
        self.assertIn("handle_path /api/*", source)
        self.assertIn("handle /oauth/*", source)
        self.assertIn("handle /ws/*", source)
        self.assertIn("reverse_proxy 127.0.0.1:8080", source)
        self.assertIn("reverse_proxy 127.0.0.1:8081", source)
        self.assertIn("www.derivadmin.site", source)

    def test_api_bootstrap_is_vps_native(self) -> None:
        backend = self.text("app/vps_backend_api.py")
        core = self.text("app/vps_core_api.py")
        surface = self.text("app/vps_api_surface.py")
        realtime = self.text("app/vps_realtime_gateway.py")

        self.assertIn("from app.vps_core_api import app", backend)
        self.assertIn("install_vps_realtime_gateway(app)", core)
        self.assertIn("install_vps_api_surface(app)", core)
        self.assertIn('app.state.production_hosting = "vps_only"', core)
        self.assertIn('app.state.api_surface = "vps-api-behind-caddy"', surface)
        self.assertIn('"same_origin_vps_websocket"', realtime)
        self.assertIn('"vps-frontend-api-worker-postgres-v1"', realtime)

    def test_realtime_send_after_close_race_is_guarded(self) -> None:
        source = self.text("app/vps_realtime_gateway.py")
        self.assertIn("from starlette.websockets import WebSocketState", source)
        self.assertIn("def _socket_can_send", source)
        self.assertIn("async def _safe_send_json", source)
        self.assertIn("async def _safe_close", source)
        self.assertIn("if not await _safe_send_json(websocket, snapshot):", source)
        self.assertIn('{"type": "heartbeat", "ts": time.time()}', source)
        self.assertIn("if not _socket_can_send(websocket):", source)
        self.assertIn("_normal_disconnect_runtime_error", source)
        self.assertNotIn(
            'await websocket.send_json({"type": "heartbeat"',
            source,
        )

    def test_environment_example_is_current_vps_contract(self) -> None:
        env = self.text(".env.vps.example")
        self.assertIn("PUBLIC_ORIGIN=https://derivadmin.site", env)
        self.assertIn(
            "DERIV_OAUTH_REDIRECT_URL=https://derivadmin.site/oauth/callback",
            env,
        )
        self.assertIn(
            "DASHBOARD_FRONTEND_ORIGINS=https://derivadmin.site,https://www.derivadmin.site",
            env,
        )
        self.assertIn("Host Caddy preserves the public Host header", env)
        self.assertNotIn("FRONTEND_HOSTING_MODE", env)

    def test_full_deploy_preserves_database_and_builds_before_cutover(self) -> None:
        source = self.text("scripts/deploy_full_vps.sh")
        build = source.index("compose build frontend api worker")
        backup = source.index("DATABASE_BACKUP_CREATED")
        cutover = source.index("compose up -d --force-recreate api worker frontend")
        self.assertLess(build, cutover)
        self.assertLess(backup, cutover)
        self.assertIn("pg_dump --format=custom --no-owner --no-privileges", source)
        self.assertIn("alembic upgrade head", source)
        self.assertIn("command -v caddy", source)
        self.assertNotIn("docker compose down -v", source)

    def test_full_vps_connection_resilience_remains_bounded(self) -> None:
        compose = self.text("docker-compose.vps.yml")
        self.assertIn("VPS_ACCOUNT_REFRESH_INTERVAL_SECONDS:-1", compose)
        self.assertIn("VPS_DERIV_HTTP_CONNECTOR_LIMIT:-24", compose)
        self.assertIn("VPS_DERIV_HTTP_LIMIT_PER_HOST:-12", compose)
        self.assertIn("VPS_DERIV_HTTP_CONCURRENCY:-12", compose)
        self.assertIn("VPS_PRIVATE_WS_CONNECT_INTERVAL_SECONDS:-0.15", compose)
        self.assertIn("VPS_PRIVATE_WS_HANDSHAKE_CONCURRENCY:-6", compose)
        self.assertIn("VPS_PRIVATE_WS_BOOTSTRAP_CONCURRENCY:-6", compose)
        self.assertIn("VPS_OTP_HTTP_CONCURRENCY:-8", compose)
        self.assertIn("PRIVATE_WS_RATE_LIMIT_BACKOFF_SECONDS", compose)
        self.assertIn("PRIVATE_WS_MAX_BACKOFF_SECONDS", compose)

    def test_old_split_hosting_files_are_absent(self) -> None:
        obsolete = (
            "app/backend_only_surface.py",
            "scripts/deploy_dedicated_backend.sh",
        )
        for relative in obsolete:
            self.assertFalse((ROOT / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
