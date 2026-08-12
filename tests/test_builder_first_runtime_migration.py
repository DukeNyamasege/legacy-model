from __future__ import annotations

import unittest
from pathlib import Path

from app.account_mode_execution_lock import account_allows_new_execution


ROOT = Path(__file__).resolve().parents[1]


class BuilderFirstRuntimeMigrationTests(unittest.TestCase):
    def test_fresh_deployment_migration_stops_every_existing_runtime(self) -> None:
        migration = (
            ROOT / "migrations" / "versions" / "20260812_0021_stop_all_account_runtimes.py"
        ).read_text(encoding="utf-8")

        self.assertIn("UPDATE managed_accounts", migration)
        self.assertIn("enabled = FALSE", migration)
        self.assertIn("execution_status = 'stopped'", migration)
        self.assertIn("Builder-first migration: Auto Trading is OFF", migration)
        self.assertIn("UPDATE bot_state", migration)
        self.assertIn("status = 'STOPPED'", migration)
        self.assertIn("DELETE FROM trader_leases", migration)
        self.assertIn("Re-enabling trading must remain an explicit user action", migration)

    def test_idle_verifier_can_fail_closed_and_report_zero_runtime_registry(self) -> None:
        verifier = (ROOT / "scripts" / "verify_builder_runtime_idle.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"--stop-all"', verifier)
        self.assertIn("update(ManagedAccount).values(", verifier)
        self.assertIn('execution_status="stopped"', verifier)
        self.assertIn("delete(TraderLease)", verifier)
        self.assertIn('"active_auto_trading_accounts"', verifier)
        self.assertIn('"active_execution_leases"', verifier)
        self.assertIn('"runtime_registry": 0', verifier)
        self.assertIn('return 0 if report["ready_for_user_start"] else 1', verifier)

    def test_deployment_applies_stop_all_before_worker_cutover_and_verifies_idle(self) -> None:
        deploy = (ROOT / "scripts" / "deploy_vps.sh").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        migration_index = deploy.index("python -m scripts.verify_builder_runtime_idle --stop-all")
        worker_start_index = deploy.index("compose up -d --force-recreate worker")
        idle_verify_index = deploy.index("Builder-first runtime is not idle after deployment")

        self.assertLess(migration_index, worker_start_index)
        self.assertLess(worker_start_index, idle_verify_index)
        self.assertIn('CUSTOM_STRATEGY_ONLY_RUNTIME: "true"', deploy)
        self.assertIn(
            "CUSTOM_STRATEGY_ONLY_RUNTIME: ${CUSTOM_STRATEGY_ONLY_RUNTIME:-true}",
            compose,
        )
        self.assertIn("Runtime supervisor    : IDLE (zero active account runtimes)", deploy)

    def test_auto_trading_off_never_allows_new_execution(self) -> None:
        class Row:
            def __init__(self, *, enabled: bool, status: str) -> None:
                self.enabled = enabled
                self.execution_status = status

        for status in ("stopped", "disabled", "inactive", "manual_pause", "settlement_only"):
            with self.subTest(status=status):
                self.assertFalse(
                    account_allows_new_execution(Row(enabled=False, status=status))
                )

    def test_start_and_stop_routes_are_the_only_account_lifecycle_authority(self) -> None:
        lifecycle = (ROOT / "app" / "lifecycle_reset_authority.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('@app.post("/me/auto-trade")', lifecycle)
        self.assertIn("if bool(body.enabled):", lifecycle)
        self.assertIn('base_api.ResumeTradeRequest(mode="start_again")', lifecycle)
        self.assertIn("return authoritative_stop(request)", lifecycle)
        self.assertIn("row.execution_status = \"connecting\"", lifecycle)
        self.assertIn("row.execution_status = \"stopped\"", lifecycle)
        self.assertIn("Auto trading stopped. Trade history is retained", lifecycle)

    def test_stopped_accounts_cannot_be_promoted_by_worker_validation(self) -> None:
        lock = (ROOT / "app" / "account_mode_execution_lock.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("STOPPED -> STARTING -> RUNNING -> STOPPING -> STOPPED", lock)
        self.assertIn('current_lifecycle in {"stopped", "paused", "settlement"}', lock)
        self.assertIn("status in AUTO_PROMOTION_STATUSES", lock)
        self.assertIn("return account_lifecycle_from_row(row) in {\"starting\", \"running\"}", lock)

    def test_worker_waits_for_an_explicit_active_account_before_public_stream(self) -> None:
        bot = (ROOT / "enhanced_bot.py").read_text(encoding="utf-8")
        run = bot[bot.index("async def run(self)") :]

        self.assertLess(
            run.index("await self._wait_for_active_execution_account()"),
            run.index("public_task = asyncio.create_task"),
        )
        self.assertIn("ACCOUNT_EXECUTION_IDLE reason=no_active_auto_trading_accounts", bot)
        self.assertIn("ACCOUNT_EXECUTION_IDLE reason=all_accounts_stopped", bot)
        self.assertIn("action=stopping_public_stream", bot)

    def test_custom_strategy_only_bootstrap_skips_legacy_rf_aidr_installers(self) -> None:
        worker = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")

        self.assertIn('custom_strategy_only = _env_flag("CUSTOM_STRATEGY_ONLY_RUNTIME", True)', worker)
        self.assertIn("CUSTOM_STRATEGY_ONLY_BOOTSTRAP", worker)

        strict_index = worker.index("install_strict_streak_guard()")
        aidr_index = worker.index("install_ai_digit_recovery_v1_strategy()")
        first_guard = worker.rfind("if not custom_strategy_only:", 0, strict_index)
        second_guard = worker.rfind("if not custom_strategy_only:", 0, aidr_index)
        self.assertGreater(first_guard, -1)
        self.assertGreater(second_guard, -1)
        self.assertIn("legacy_rf=false legacy_aidr=false", worker)

    def test_production_integration_disables_shared_legacy_clocks_by_default(self) -> None:
        production = (ROOT / "app" / "production_worker_integration.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('os.getenv("CUSTOM_STRATEGY_ONLY_RUNTIME", "true")', production)
        self.assertIn("LEGACY_SHARED_STRATEGY_CLOCK_DISABLED", production)
        self.assertIn("rf_aidr_system_execution=false", production)

        clock_index = production.index("install_final_shared_system_strategy_clock()")
        multi_index = production.index("install_final_multi_strategy_execution()")
        clock_guard = production.rfind("else:", 0, clock_index)
        multi_guard = production.rfind("if not custom_strategy_only:", 0, multi_index)
        self.assertGreater(clock_guard, -1)
        self.assertGreater(multi_guard, -1)

    def test_custom_on_tick_does_not_call_legacy_tick_handler_by_default(self) -> None:
        runtime = (ROOT / "app" / "custom_strategy_runtime.py").read_text(encoding="utf-8")
        custom_on_tick = runtime[runtime.index("async def custom_on_tick") :]

        self.assertIn("if custom_strategy_only_runtime_enabled():", custom_on_tick)
        self.assertLess(
            custom_on_tick.index("await _custom_only_on_tick(self, tick_data)"),
            custom_on_tick.index("await original_on_tick(self, tick_data)"),
        )
        self.assertIn("Maintain market state without invoking RF/AIDR/system scanners", runtime)
        self.assertIn("bot.repository.record_tick(", runtime)
        self.assertIn("settle_due_virtual_trades(", runtime)
        self.assertIn("_schedule_matches(self, tick_data)", runtime)

    def test_custom_conditions_remain_the_only_purchase_source(self) -> None:
        runtime = (ROOT / "app" / "custom_strategy_runtime.py").read_text(encoding="utf-8")

        self.assertIn("matches = _group_matches(", runtime)
        self.assertIn("if not matches:", runtime)
        self.assertIn("return", runtime[runtime.index("if not matches:") : runtime.index("inflight: set[int]")])
        self.assertIn("signal = build_custom_signal(", runtime)
        self.assertIn("family=\"custom\"", runtime)
        self.assertIn("_execute_custom_group(", runtime)

    def test_no_duplicate_custom_runtime_task_for_one_account_per_tick(self) -> None:
        runtime = (ROOT / "app" / "custom_strategy_runtime.py").read_text(encoding="utf-8")

        self.assertIn("_custom_strategy_inflight_ids", runtime)
        self.assertIn("ids = {int(value) for value in raw_ids if int(value) not in inflight}", runtime)
        self.assertIn("inflight.update(ids)", runtime)
        self.assertIn("inflight.discard(managed_id)", runtime)
        self.assertIn("_custom_strategy_seen_ticks", runtime)
        self.assertIn("if tick_key in seen:", runtime)

    def test_fatal_account_conditions_are_scoped_to_the_account(self) -> None:
        guard = (ROOT / "app" / "credential_quarantine_runtime_guard.py").read_text(
            encoding="utf-8"
        )
        hardening = (ROOT / "app" / "bulk_credential_failure_hardening.py").read_text(
            encoding="utf-8"
        )
        lock = (ROOT / "app" / "account_mode_execution_lock.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("PRE_VALIDATION_CREDENTIAL_QUARANTINE_COMPLETE", guard)
        self.assertIn("InvalidToken", hardening)
        self.assertIn("global_execution_continues=true", hardening)
        self.assertIn("stop_mode_account", lock)
        self.assertIn("Hard stop one account mode without touching the sibling", lock)


if __name__ == "__main__":
    unittest.main()
