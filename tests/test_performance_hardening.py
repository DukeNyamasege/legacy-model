from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PerformanceHardeningSourceTests(unittest.TestCase):
    def test_worker_uses_bounded_account_connection_concurrency(self) -> None:
        source = (ROOT / "app" / "vps_low_latency_runtime.py").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")
        self.assertIn("class _VpsBootstrapScheduler", source)
        self.assertIn("asyncio.Condition", source)
        self.assertIn('VPS_PRIVATE_WS_BOOTSTRAP_CONCURRENCY', source)
        self.assertIn("otp_and_wss_atomic=true", source)
        self.assertIn("VPS_PRIVATE_WS_BOOTSTRAP_CONCURRENCY", compose)

    def test_provider_requests_use_shared_broker(self) -> None:
        source = (ROOT / "app" / "deriv_request_broker.py").read_text(encoding="utf-8")
        self.assertIn("aiohttp.TCPConnector", source)
        self.assertIn("limit_per_host", source)
        self.assertIn("ClientSession", source)

    def test_public_websocket_resilience_has_reconnect(self) -> None:
        source = (ROOT / "app" / "public_websocket_resilience.py").read_text(encoding="utf-8")
        self.assertIn("reconnect", source.lower())
        self.assertIn("backoff", source.lower())

    def test_private_websocket_hardening_has_rate_limit_controls(self) -> None:
        source = (ROOT / "app" / "private_websocket_rate_limit.py").read_text(encoding="utf-8")
        self.assertIn("concurrency", source.lower())
        self.assertIn("interval", source.lower())

    def test_vps_runtime_contains_latency_controls(self) -> None:
        source = (ROOT / "app" / "vps_low_latency_runtime.py").read_text(encoding="utf-8")
        self.assertIn("low_latency", source.lower())
        self.assertIn("bounded", source.lower())

    def test_compose_uses_persistent_database_volume(self) -> None:
        source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("test2_database", source)
        self.assertIn("/var/lib/postgresql/data", source)

    def test_full_vps_compose_contains_frontend(self) -> None:
        full_vps = ROOT / "docker-compose.vps.yml"
        self.assertTrue(full_vps.exists())
        content = full_vps.read_text(encoding="utf-8")
        self.assertIn("frontend:", content)
        self.assertIn("dockerfile: Dockerfile.frontend", content)
        self.assertIn('"127.0.0.1:8081:80"', content)

    def test_repository_cleanup_is_safe_and_volume_preserving(self) -> None:
        source = (ROOT / "scripts" / "cleanup_repository_state.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("git fetch origin --prune", source)
        self.assertIn("git branch --merged main", source)
        self.assertIn("docker image prune -a -f", source)
        self.assertIn("docker builder prune -a -f", source)
        self.assertIn("-e .env", source)
        self.assertIn("-e deploy-backups/", source)
        self.assertNotIn("docker system prune", source)
        self.assertNotIn("docker volume prune", source)

    def test_contabo_deploy_is_single_compose_and_preserves_database(self) -> None:
        source = (ROOT / "scripts" / "deploy_full_vps.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("-f docker-compose.yml", source)
        self.assertIn("-f docker-compose.vps.yml", source)
        self.assertIn("FULL CONTABO VPS DEPLOYMENT", source)
        self.assertIn("compose build frontend api worker", source)
        self.assertIn("compose up -d database", source)
        self.assertIn("pg_dump --format=custom", source)
        self.assertIn("alembic upgrade head", source)
        self.assertIn("compose up -d --force-recreate --no-deps api", source)
        self.assertIn("Wait for API health", source)
        self.assertIn("compose up -d --force-recreate --remove-orphans --no-deps worker frontend", source)
        self.assertLess(
            source.index("compose up -d --force-recreate --no-deps api"),
            source.index("compose up -d --force-recreate --remove-orphans --no-deps worker frontend"),
        )
        self.assertNotIn("docker volume prune", source)

    def test_compose_rotates_logs_and_disables_tick_spam(self) -> None:
        source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("max-size: ${DOCKER_LOG_MAX_SIZE:-25m}", source)
        self.assertIn("max-file: ${DOCKER_LOG_MAX_FILES:-3}", source)
        self.assertIn("LIVE_TICK_LOG_LINES: ${LIVE_TICK_LOG_LINES:-false}", source)
        self.assertIn("checkpoint_timeout=${POSTGRES_CHECKPOINT_TIMEOUT:-15min}", source)


if __name__ == "__main__":
    unittest.main()
