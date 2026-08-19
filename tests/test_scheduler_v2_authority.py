from __future__ import annotations

import unittest
from pathlib import Path

from app.automation_scheduler_action5 import local_schedule_to_utc


ROOT = Path(__file__).resolve().parents[1]


class SchedulerV2AuthorityContract(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_second_precision_survives_timezone_conversion(self) -> None:
        local, utc_value = local_schedule_to_utc(
            "2026-08-21",
            "19:00:37",
            "Africa/Nairobi",
        )
        self.assertTrue(local.startswith("2026-08-21T19:00:37+03:00"))
        self.assertEqual(utc_value.isoformat(), "2026-08-21T16:00:37+00:00")

    def test_server_create_rejects_past_and_near_past_schedules(self) -> None:
        source = self.text("app/automation_scheduler_action5.py")
        self.assertIn("scheduled_utc <= utc_now() + timedelta(seconds=5)", source)
        self.assertIn("Scheduled time must be at least a few seconds in the future", source)
        self.assertIn("time: str = Field(min_length=5, max_length=8)", source)

    def test_due_schedule_is_an_explicit_start_and_clears_previous_hard_stop(self) -> None:
        source = self.text("app/automation_scheduler_v2_authority.py")
        self.assertIn("clear_direct_hard_stop", source)
        self.assertIn('schedule.status == "starting"', source)
        self.assertIn("clear_direct_hard_stop(session, int(schedule.managed_account_id))", source)
        self.assertIn("return original_apply(schedule_id)", source)

    def test_scheduler_runs_subsecond_and_keeps_worker_purchase_authority(self) -> None:
        source = self.text("app/automation_scheduler_v2_authority.py")
        base = self.text("app/automation_scheduler_action5.py")
        self.assertIn('float(os.getenv("AUTOMATION_SCHEDULER_INTERVAL_SECONDS", "0.25"))', source)
        self.assertIn("max(\n            0.10", source)
        self.assertIn("await asyncio.to_thread(action5.run_scheduler_cycle)", source)
        self.assertIn('"execution_authority": "existing_custom_strategy_worker"', base)
        self.assertNotIn("proposal_open_contract", source)
        self.assertNotIn("requests.post", source)

    def test_completed_schedule_exposes_profit_runs_and_stop_reason(self) -> None:
        source = self.text("app/automation_scheduler_v2_authority.py")
        for marker in (
            "Session result:",
            "result_profit",
            "result_runs",
            "result_wins",
            "result_losses",
            "result_label",
            "Take profit hit",
            "Stop loss hit",
            "Stopped by trader",
            "Trade.purchase_time >= started",
        ):
            self.assertIn(marker, source)

    def test_frontend_finalizer_adds_explicit_seconds_future_guard_history_and_state_export(self) -> None:
        source = self.text("scripts/finalize-scheduler-v2.mjs")
        for marker in (
            'id="s-second" type="number" min="0" max="59" step="1"',
            "exactScheduleTime",
            "normalizedScheduleTime",
            "scheduleWallClockIsFuture",
            "Date.now() + 5000",
            "state.schedules?.items || state.schedules?.schedules || []",
            "SCHEDULE HISTORY",
            "result_profit",
            "result_label",
            "state: () => state",
            "derivadmin:scheduled-runtime",
            "owner === \"server\" || owner === \"server_takeover\"",
            "}, 1000);",
        ):
            self.assertIn(marker, source)

    def test_mobile_scheduler_clock_and_results_cannot_overflow(self) -> None:
        ui = self.text("dashboard/scheduler-v2-ui.js")
        self.assertIn(".schedule-clock-grid", ui)
        self.assertIn("grid-template-columns:1.15fr .9fr .55fr 1.2fr", ui)
        self.assertIn("@media(max-width:700px)", ui)
        self.assertIn("@media(max-width:390px)", ui)
        self.assertIn(".schedule-result", ui)
        self.assertIn("max-width:100%", ui)

    def test_frontend_build_runs_scheduler_then_execution_continuity(self) -> None:
        docker = self.text("Dockerfile.frontend")
        self.assertIn("COPY scripts/finalize-scheduler-v2.mjs", docker)
        self.assertIn("COPY scripts/finalize-execution-continuity-v1.mjs", docker)
        self.assertIn("node scripts/finalize-scheduler-v2.mjs", docker)
        self.assertIn("node scripts/finalize-execution-continuity-v1.mjs", docker)
        self.assertIn("cp dashboard/scheduler-v2-ui.js", docker)
        self.assertIn("20260818-unified-ledger-v10-virtual", docker)
        self.assertIn("20260819-live-fix-v2", docker)
        self.assertIn("node --check dist/final-ui-shell-v2.js", docker)

    def test_vps_entrypoint_installs_scheduler_v2_after_action5(self) -> None:
        entry = self.text("app/vps_backend_api.py")
        first = entry.index("install_automation_scheduler_action5(app)")
        second = entry.index("install_automation_scheduler_v2_authority()")
        self.assertGreater(second, first)
        self.assertIn("hybrid_browser_direct_v2_global_recovery_policy", entry)


if __name__ == "__main__":
    unittest.main()
