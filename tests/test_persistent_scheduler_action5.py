from __future__ import annotations

from pathlib import Path
import unittest

from app.automation_scheduler_action5 import (
    canonical_strategy_snapshot,
    local_schedule_to_utc,
)


ROOT = Path(__file__).resolve().parents[1]


class PersistentSchedulerAction5Tests(unittest.TestCase):
    def test_nairobi_wall_clock_converts_to_exact_utc(self) -> None:
        local, utc_value = local_schedule_to_utc(
            "2026-08-21",
            "19:00",
            "Africa/Nairobi",
        )
        self.assertTrue(local.startswith("2026-08-21T19:00:00+03:00"))
        self.assertEqual(utc_value.isoformat(), "2026-08-21T16:00:00+00:00")

    def test_nonexistent_dst_wall_clock_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist"):
            local_schedule_to_utc(
                "2026-03-08",
                "02:30",
                "America/New_York",
            )

    def test_builder_snapshot_freezes_strategy_risk_and_recovery(self) -> None:
        frozen = canonical_strategy_snapshot(
            {
                "builder": {
                    "strategyMode": "combined",
                    "marketMode": "selected",
                    "markets": ["1HZ100V", "R_100"],
                    "lastRule": {"window": 3, "operator": "<=", "value": 6},
                    "percentageRule": {
                        "target": "over",
                        "value": 2,
                        "window": 1000,
                        "operator": ">",
                        "threshold": 72,
                    },
                    "tickDirectionRule": {
                        "enabled": False,
                        "window": 3,
                        "direction": "rising",
                    },
                    "trade": {
                        "group": "over_under",
                        "side": "over",
                        "prediction": 2,
                    },
                    "reanalyze": {
                        "mode": "after_every_trade",
                        "losses": 1,
                        "wins": 1,
                    },
                    "money": {
                        "stake": 0.5,
                        "takeProfit": 10,
                        "stopLoss": 20,
                        "martingale": 1.5,
                        "ticks": 1,
                    },
                    "virtualHook": {
                        "enabled": True,
                        "enterAfterLosses": 2,
                        "exitAfterConsecutiveWins": 2,
                    },
                },
                "result": {
                    "routingEnabled": False,
                    "recoveryMode": "split",
                    "splitCount": 2,
                },
            },
            stake=0.75,
            take_profit=12.0,
            stop_loss=8.0,
        )
        config = frozen["custom_strategy"]
        self.assertEqual(config["trade_type"], "over")
        self.assertEqual(config["prediction"], 2)
        self.assertEqual(len(config["conditions"]), 2)
        self.assertEqual(config["market_mode"], "selected")
        self.assertEqual(frozen["execution_settings"]["stake_amount"], 0.75)
        self.assertEqual(frozen["execution_settings"]["take_profit"], 12.0)
        self.assertEqual(frozen["execution_settings"]["stop_loss"], 8.0)
        self.assertEqual(frozen["martingale"]["mode"], "split")
        self.assertEqual(frozen["martingale"]["split_count"], 2)
        self.assertFalse(frozen["result_routing"]["enabled"])

    def test_result_based_after_loss_route_is_frozen(self) -> None:
        frozen = canonical_strategy_snapshot(
            {
                "builder": {
                    "strategyMode": "percentage",
                    "marketMode": "all",
                    "markets": [],
                    "percentageRule": {
                        "target": "over",
                        "value": 1,
                        "window": 1000,
                        "operator": ">",
                        "threshold": 80,
                    },
                    "tickDirectionRule": {"enabled": False},
                    "trade": {"side": "over", "prediction": 1},
                    "reanalyze": {"mode": "after_every_trade"},
                    "money": {"martingale": 2.1, "ticks": 1},
                    "virtualHook": {
                        "enabled": True,
                        "enterAfterLosses": 2,
                        "exitAfterConsecutiveWins": 1,
                    },
                },
                "result": {
                    "routingEnabled": True,
                    "recoveryMode": "multiplier",
                    "splitCount": 1,
                    "afterLoss": {
                        "tradeType": "over",
                        "prediction": 4,
                        "durationTicks": 1,
                        "analysisMode": "last_digit",
                        "lastRule": {
                            "window": 5,
                            "operator": "<=",
                            "value": 5,
                        },
                        "tickDirectionRule": {"enabled": False},
                    },
                },
            },
            stake=0.5,
            take_profit=10,
            stop_loss=20,
        )
        routing = frozen["result_routing"]
        self.assertTrue(routing["enabled"])
        self.assertEqual(routing["after_loss"]["trade_type"], "over")
        self.assertEqual(routing["after_loss"]["prediction"], 4)
        self.assertEqual(routing["after_loss"]["conditions"][0]["window"], 5)

    def test_persistent_model_and_migration_are_restart_safe(self) -> None:
        model = (ROOT / "app" / "automation_schedule_models.py").read_text(
            encoding="utf-8"
        )
        migration = (
            ROOT
            / "migrations"
            / "versions"
            / "20260817_0022_persistent_automation_schedules.py"
        ).read_text(encoding="utf-8")
        self.assertIn("class AutomationSchedule(Base)", model)
        self.assertIn("status: Mapped[str]", model)
        self.assertIn("claim_expires_at", model)
        self.assertIn("scheduled_for_utc", model)
        self.assertIn('revision = "20260817_0022"', migration)
        self.assertIn('down_revision = "20260812_0021"', migration)
        self.assertIn("ix_automation_schedule_due", migration)

    def test_scheduler_uses_existing_execution_authority_and_overlap_policies(self) -> None:
        source = (ROOT / "app" / "automation_scheduler_action5.py").read_text(
            encoding="utf-8"
        )
        for marker in (
            'VALID_OVERLAP_POLICIES = {"wait", "skip", "replace"}',
            "write_custom_strategy",
            "_write_custom_martingale",
            "write_result_routing",
            "_reset_risk_state",
            "_set_stopped(session, account)",
            'account.execution_status = "starting"',
            "account.enabled = True",
            "existing_custom_strategy_worker",
            "existing_session_risk_stop_authority",
            "with_for_update(skip_locked=True)",
            "AUTOMATION_SCHEDULE_LATE_GRACE_SECONDS",
            "execution_requires_new_token",
            "execution_token_was_rejected",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("stop_account(", source)
        self.assertNotIn('requests.post("https://api.deriv', source)
        self.assertNotIn("proposal_open_contract", source)

    def test_scheduler_api_is_server_owned_and_cancel_is_account_scoped(self) -> None:
        source = (ROOT / "app" / "automation_scheduler_action5.py").read_text(
            encoding="utf-8"
        )
        entry = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/me/automation-schedules")', source)
        self.assertIn('@app.post("/me/automation-schedules")', source)
        self.assertIn(
            '@app.post("/me/automation-schedules/{schedule_id}/cancel")',
            source,
        )
        self.assertIn(
            'int(row.managed_account_id) != int(account["id"])',
            source,
        )
        self.assertIn(
            "app.router.lifespan_context = automation_scheduler_lifespan",
            source,
        )
        self.assertNotIn("add_event_handler(", source)
        self.assertIn("install_automation_scheduler_action5(app)", entry)
        self.assertLess(
            entry.index("from app.netlify_backend_api import app"),
            entry.index("from app.automation_scheduler_action5 import"),
        )

    def test_telegram_schedule_notifications_are_private_only(self) -> None:
        source = (ROOT / "app" / "automation_scheduler_action5.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("telegram_admin._send_private_sync", source)
        self.assertIn("SCHEDULED TRADING SESSION STARTED", source)
        self.assertIn("SCHEDULED TRADING SESSION FINISHED", source)
        self.assertIn("SCHEDULED TRADING SESSION CANCELLED", source)
        self.assertNotIn("/publish", source)
        self.assertNotIn("send_channel", source)

    def test_retired_action5_frontend_is_not_a_runtime_authority(self) -> None:
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        final_ui = (ROOT / "dashboard" / "final-ui-shell-v1.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("automation-scheduler-action5.js", html)
        self.assertNotIn("automation-scheduler-action5.css", html)
        self.assertIn("/me/automation-schedules?limit=20", final_ui)
        self.assertIn("final-ui-shell-v1", html)

    def test_vps_build_exposes_full_built_in_strategy_snapshots(self) -> None:
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertIn(
            '"builtIns: BUILT_INS.map((item) => clone(item)),"',
            build,
        )
        self.assertIn(
            'schedule_built_ins: "full-frozen-template-snapshots-v1"',
            build,
        )
        self.assertIn(
            "refusing an unsafe Action 5 build",
            build,
        )

    def test_vps_build_marks_scheduler_persistent_without_restoring_old_ui(self) -> None:
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertIn('ui_authority: "final-ui-shell-v1"', build)
        self.assertIn('legacy_ui_loaded: false', build)
        self.assertIn('schedule_execution: "persistent-server-scheduler-existing-worker-authority-v1"', build)
        self.assertIn('schedule_persistence: "postgres-restart-safe-exactly-once-claim-v1"', build)
        self.assertNotIn("/automation-scheduler-action5.css?v=20260817-1", build)
        self.assertNotIn("/automation-scheduler-action5.js?v=20260817-1", build)
        scheduler = (ROOT / "app" / "automation_scheduler_action5.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("docker compose", scheduler)
        self.assertNotIn("deploy_full_vps.sh", scheduler)


if __name__ == "__main__":
    unittest.main()
