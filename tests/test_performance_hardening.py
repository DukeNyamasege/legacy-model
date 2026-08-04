from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ApiPerformanceSourceTests(unittest.TestCase):
    def test_final_performance_authorities_are_installed_last(self) -> None:
        source = (ROOT / "app" / "api_v3.py").read_text(encoding="utf-8")
        account = source.rindex("install_api_performance_hardening(app)")
        browser = source.rindex("install_dashboard_request_coalescing(app)")
        health = source.rindex("install_fast_integration_health(app)")
        database = source.rindex("install_database_runtime_hardening(app)")
        self.assertLess(account, browser)
        self.assertLess(browser, health)
        self.assertLess(health, database)

    def test_health_check_never_forces_dashboard_rebuild(self) -> None:
        source = (ROOT / "app" / "fast_integration_health.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"forced_dashboard_rebuild": False', source)
        self.assertIn("base_api.dashboard_summary(account_type=mode)", source)
        self.assertNotIn("force=True", source)
        self.assertIn("elapsed_ms < 5000.0", source)

    def test_startup_latency_is_visible_but_not_a_false_readiness_failure(self) -> None:
        source = (ROOT / "app" / "fast_integration_health.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"critical": False', source)
        self.assertIn('warnings.append("latency_budget")', source)
        self.assertNotIn('failures.append("latency_budget")', source)
        self.assertIn('"health_profile": "cached-nonblocking-v2"', source)
        self.assertIn('"warnings": warnings', source)
        self.assertIn('if failures:', source)

    def test_snapshot_rebuilds_are_bounded_before_startup(self) -> None:
        health = (ROOT / "app" / "fast_integration_health.py").read_text(
            encoding="utf-8"
        )
        throttle = (ROOT / "app" / "dashboard_snapshot_throttle.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("install_dashboard_snapshot_throttle()", health)
        self.assertIn('DASHBOARD_REBUILD_MIN_INTERVAL_SECONDS", "15"', throttle)
        self.assertIn("return min(120.0, max(5.0, configured))", throttle)
        self.assertIn("base_api._build_dashboard_snapshot = _bounded_snapshot_build", throttle)
        self.assertIn("_MODE_LOCKS", throttle)

    def test_personal_reads_do_not_wait_for_deriv(self) -> None:
        source = (ROOT / "app" / "api_performance_hardening.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("base_api.schedule_personal_account_refresh(account)", source)
        self.assertNotIn("load_options_accounts(", source)
        self.assertIn("_SESSION_TOUCH_SECONDS = 60.0", source)
        self.assertIn('"performance_profile": "fast-personal-v1"', source)

    def test_personal_history_is_bounded_and_account_scoped(self) -> None:
        source = (ROOT / "app" / "api_performance_hardening.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("limit: int = Query(default=200, ge=25, le=500)", source)
        self.assertIn("Trade.managed_account_id == managed_id", source)
        self.assertIn('"account_generation"', source)
        self.assertIn('"truncated": total + virtual_total > len(rows)', source)

    def test_switch_returns_exact_target_generation(self) -> None:
        source = (ROOT / "app" / "api_performance_hardening.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("stale_requests_cancelled_by_ui", source)
        self.assertIn('f"{int(target[\'managed_account_id\'])}:{target_type}"', source)

    def test_request_broker_executes_before_dashboard_boot(self) -> None:
        source = (ROOT / "app" / "dashboard_request_coalescing.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('return _REQUEST_BROKER_JS + "\\n" + source', source)
        self.assertNotIn("source += _REQUEST_BROKER_JS", source)
        self.assertIn("abortManagedReads()", source)


class DatabaseWritePressureTests(unittest.TestCase):
    def test_worker_installs_tick_buffer_before_bot(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        install = source.index("install_tick_persistence_buffer()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(install, bot)

    def test_tick_buffer_uses_bulk_conflict_safe_inserts(self) -> None:
        source = (ROOT / "app" / "tick_persistence_buffer.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('TICK_PERSIST_BATCH_SIZE", "50"', source)
        self.assertIn('TICK_PERSIST_FLUSH_SECONDS", "1.0"', source)
        self.assertIn("on_conflict_do_nothing", source)
        self.assertIn("state.rows = rows + state.rows", source)
        self.assertIn('dialect.name == "postgresql"', source)
        self.assertIn("original_record_tick(", source)

    def test_duplicate_provider_callbacks_are_idempotent(self) -> None:
        worker = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        source = (ROOT / "app" / "trade_registration_idempotency.py").read_text(
            encoding="utf-8"
        )
        install = worker.index("install_trade_registration_idempotency()")
        bot = worker.index("bot = RFDir5TradingBot()")
        self.assertLess(install, bot)
        self.assertIn("on_conflict_do_nothing()", source)
        self.assertIn("DUPLICATE_PURCHASE_REGISTRATION_IGNORED", source)
        self.assertIn("Test2Repository.register_purchase = idempotent_register_purchase", source)

    def test_personal_query_indexes_are_migrated(self) -> None:
        source = (
            ROOT / "alembic" / "versions" / "20260804_0019_performance_indexes.py"
        ).read_text(encoding="utf-8")
        self.assertIn("ix_trades_managed_purchase_time_v2", source)
        self.assertIn("ix_virtual_managed_created_at_v2", source)
        self.assertIn('down_revision = "20260726_0018"', source)


class VpsCleanupSourceTests(unittest.TestCase):
    def test_update_cleans_before_after_and_on_failed_deploy(self) -> None:
        source = (ROOT / "scripts" / "update_vps.sh").read_text(encoding="utf-8")
        pre_cleanup = source.index("cleanup_vps_artifacts.sh pre-deploy")
        deploy = source.index("sh ./scripts/deploy_vps.sh")
        diagnostics = source.rindex("sh scripts/diagnose_vps_performance.sh")
        post_cleanup = source.index("cleanup_vps_artifacts.sh post-deploy")
        failed_cleanup = source.index("cleanup_vps_artifacts.sh failed-deploy")
        self.assertLess(pre_cleanup, deploy)
        self.assertGreater(diagnostics, deploy)
        self.assertGreater(post_cleanup, deploy)
        self.assertGreater(failed_cleanup, deploy)
        self.assertIn('flock -n 9 || fail "Another VPS update is already running"', source)

    def test_cleanup_keeps_only_container_referenced_images(self) -> None:
        source = (ROOT / "scripts" / "cleanup_vps_artifacts.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("legacy-model-preflight-", source)
        self.assertIn("DEPLOYMENT_LOCK_HELD", source)
        self.assertIn("docker image prune -a -f", source)
        self.assertIn("docker builder prune -a -f", source)
        self.assertIn("snapshot_running_containers", source)
        self.assertIn("assert_running_containers_preserved", source)
        self.assertIn("{{.State.Running}}", source)
        self.assertIn("actual_image_id", source)
        self.assertNotIn("assert_running_images_preserved", source)
        self.assertNotIn("docker image inspect \"$image_id\"", source)
        self.assertNotIn("docker system prune -a", source)
        self.assertNotIn("docker volume prune", source)
        self.assertIn("test2_database", source)
        self.assertIn("test2_models", source)

    def test_compose_rotates_logs_and_disables_tick_spam(self) -> None:
        source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("max-size: ${DOCKER_LOG_MAX_SIZE:-25m}", source)
        self.assertIn("max-file: ${DOCKER_LOG_MAX_FILES:-3}", source)
        self.assertIn("LIVE_TICK_LOG_LINES: ${LIVE_TICK_LOG_LINES:-false}", source)
        self.assertIn("checkpoint_timeout=${POSTGRES_CHECKPOINT_TIMEOUT:-15min}", source)


if __name__ == "__main__":
    unittest.main()
