from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PerformanceHardeningSourceTests(unittest.TestCase):
    def test_worker_uses_bounded_account_connection_concurrency(self) -> None:
        source = (ROOT / "app" / "rf_dir5_bot.py").read_text(encoding="utf-8")
        self.assertIn("ACCOUNT_CONNECTION_CONCURRENCY", source)
        self.assertIn("asyncio.Semaphore", source)

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
        self.assertIn("FRONTEND_HOSTING_MODE: vps", content)

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
        source = (ROOT / "scripts" / "deploy_dedicated_backend.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('docker compose -f docker-compose.yml "$@"', source)
        self.assertIn("NETLIFY + CONTABO BACKEND DEPLOYMENT", source)
        self.assertIn("compose build api worker", source)
        self.assertIn("compose up -d database", source)
        self.assertIn("pg_dump --format=custom", source)
        self.assertIn("alembic upgrade head", source)
        self.assertIn("compose up -d --force-recreate --remove-orphans --no-deps api", source)
        self.assertIn("Wait for backend health", source)
        self.assertIn("compose up -d --force-recreate --remove-orphans --no-deps worker", source)
        self.assertLess(
            source.index("compose up -d --force-recreate --remove-orphans --no-deps api"),
            source.index("compose up -d --force-recreate --remove-orphans --no-deps worker"),
        )
        self.assertNotIn("docker-compose.vps.yml", source)
        self.assertNotIn("docker volume prune", source)

    def test_compose_rotates_logs_and_disables_tick_spam(self) -> None:
        source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("max-size: ${DOCKER_LOG_MAX_SIZE:-25m}", source)
        self.assertIn("max-file: ${DOCKER_LOG_MAX_FILES:-3}", source)
        self.assertIn("LIVE_TICK_LOG_LINES: ${LIVE_TICK_LOG_LINES:-false}", source)
        self.assertIn("checkpoint_timeout=${POSTGRES_CHECKPOINT_TIMEOUT:-15min}", source)


if __name__ == "__main__":
    unittest.main()
