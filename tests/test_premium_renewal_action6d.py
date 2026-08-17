from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.database import Database
from app.models import Base, ManagedAccount
from app.premium_access_models import PremiumCustomer, PremiumCustomerAccount
from app.premium_access_service import (
    WEEKLY_PERIOD_DAYS,
    WEEKLY_PRICE_KES,
    access_payload,
    activate_weekly_access,
    effective_access_state,
    premium_access_period_history,
    record_renewal_failure,
    renewal_reminder_payload,
)
from app import premium_renewal_action6d as renewal


ROOT = Path(__file__).resolve().parents[1]


class PremiumRenewalAction6DTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.database = Database(f"sqlite:///{self.tmp.name}")
        Base.metadata.create_all(self.database.engine)
        self.original_database = renewal.base_api.DATABASE
        renewal.base_api.DATABASE = self.database

    def tearDown(self) -> None:
        renewal.base_api.DATABASE = self.original_database
        self.database.engine.dispose()
        Path(self.tmp.name).unlink(missing_ok=True)

    def _seed_customer(self, *, enabled: bool = True) -> str:
        customer_id = "customer-renewal"
        with self.database.session() as session:
            session.add(
                ManagedAccount(
                    id=41,
                    label="Renewal account",
                    token_secret="encrypted-placeholder",
                    enabled=enabled,
                    execution_status="running" if enabled else "manual_pause",
                )
            )
            session.add(
                PremiumCustomer(
                    id=customer_id,
                    identity_fingerprint="d" * 64,
                    status="unpaid",
                    plan_code="weekly_access",
                    renewal_preference="prompt_again",
                    auto_renew_enabled=False,
                    renewal_provider="lipana",
                )
            )
            session.add(
                PremiumCustomerAccount(
                    customer_id=customer_id,
                    managed_account_id=41,
                    account_hash="e" * 64,
                    account_masked="DOT***041",
                    account_type="demo",
                )
            )
        return customer_id

    def test_plan_is_mpesa_only_and_reminders_use_exact_remaining_time(self) -> None:
        self.assertEqual(WEEKLY_PERIOD_DAYS, 7)
        self.assertEqual(WEEKLY_PRICE_KES, 250.0)
        paid_at = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        customer = PremiumCustomer(
            id="preview",
            identity_fingerprint="a" * 64,
            status="active",
            current_period_start=paid_at,
            current_period_end=paid_at + timedelta(days=7),
            renewal_preference="prompt_again",
            auto_renew_enabled=False,
            renewal_provider="lipana",
        )
        state_24h = effective_access_state(
            customer,
            now=paid_at + timedelta(days=6, minutes=1),
        )
        self.assertEqual(
            renewal_reminder_payload(state_24h)["stage"],
            "twenty_four_hours",
        )
        state_6h = effective_access_state(
            customer,
            now=paid_at + timedelta(days=6, hours=19),
        )
        self.assertEqual(renewal_reminder_payload(state_6h)["stage"], "six_hours")
        state_1h = effective_access_state(
            customer,
            now=paid_at + timedelta(days=6, hours=23, minutes=30),
        )
        self.assertEqual(renewal_reminder_payload(state_1h)["stage"], "one_hour")
        payload = access_payload(state_1h)
        self.assertEqual(set(payload["pricing"]), {"mpesa"})
        self.assertEqual(payload["renewal"]["provider"], "lipana")
        self.assertEqual(payload["renewal"]["payment_method"], "mpesa")
        self.assertFalse(payload["renewal"]["auto_renew_enabled"])

    def test_verified_payment_creates_one_immutable_period_and_replay_does_not_duplicate(self) -> None:
        customer_id = self._seed_customer()
        paid_at = datetime(2026, 8, 17, 13, 15, 33, tzinfo=timezone.utc)
        first = activate_weekly_access(
            self.database,
            customer_id,
            paid_at=paid_at,
            provider="lipana",
            payment_reference="txn-period-1",
            auto_renew_enabled=False,
            renewal_preference="prompt_again",
        )
        self.assertEqual(first.current_period_end, paid_at + timedelta(days=7))
        activate_weekly_access(
            self.database,
            customer_id,
            paid_at=paid_at + timedelta(hours=2),
            provider="lipana",
            payment_reference="txn-period-1",
            auto_renew_enabled=False,
            renewal_preference="prompt_again",
        )
        history = premium_access_period_history(
            self.database,
            customer_id,
            now=paid_at,
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["period_start"], paid_at.isoformat())
        self.assertEqual(
            history[0]["period_end"],
            (paid_at + timedelta(days=7)).isoformat(),
        )

    def test_exact_expiry_sweep_pauses_new_execution_but_not_before_boundary(self) -> None:
        customer_id = self._seed_customer()
        paid_at = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)
        expires_at = paid_at + timedelta(days=7)
        activate_weekly_access(
            self.database,
            customer_id,
            paid_at=paid_at,
            provider="lipana",
            payment_reference="txn-expiry-sweep",
            auto_renew_enabled=False,
            renewal_preference="prompt_again",
        )

        before = renewal.run_premium_expiry_cycle(
            now=expires_at - timedelta(microseconds=1)
        )
        self.assertEqual(before["expired_customers"], 0)
        with self.database.session() as session:
            account = session.get(ManagedAccount, 41)
            self.assertTrue(account.enabled)

        exact = renewal.run_premium_expiry_cycle(now=expires_at)
        self.assertEqual(exact["expired_customers"], 1)
        self.assertEqual(exact["paused_accounts"], 1)
        with self.database.session() as session:
            customer = session.get(PremiumCustomer, customer_id)
            account = session.get(ManagedAccount, 41)
            self.assertEqual(customer.status, "expired")
            self.assertFalse(account.enabled)
            self.assertEqual(account.execution_status, "manual_pause")
            self.assertIn("Renew KES 250", account.execution_status_reason)

    def test_successful_post_expiry_renewal_starts_a_new_exact_seven_day_period(self) -> None:
        customer_id = self._seed_customer()
        first_paid = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
        first_end = first_paid + timedelta(days=7)
        activate_weekly_access(
            self.database,
            customer_id,
            paid_at=first_paid,
            provider="lipana",
            payment_reference="txn-week-1",
            auto_renew_enabled=False,
            renewal_preference="prompt_again",
        )
        renewal.run_premium_expiry_cycle(now=first_end)

        second_paid = first_end + timedelta(minutes=17)
        second = activate_weekly_access(
            self.database,
            customer_id,
            paid_at=second_paid,
            provider="lipana",
            payment_reference="txn-week-2",
            auto_renew_enabled=False,
            renewal_preference="prompt_again",
        )
        self.assertTrue(second.active)
        self.assertEqual(second.current_period_start, second_paid)
        self.assertEqual(second.current_period_end, second_paid + timedelta(days=7))
        history = premium_access_period_history(
            self.database,
            customer_id,
            now=second_paid,
        )
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["status"], "active")
        self.assertEqual(history[1]["status"], "expired")

    def test_failed_renewal_never_extends_expired_period(self) -> None:
        customer_id = self._seed_customer()
        paid_at = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
        expires_at = paid_at + timedelta(days=7)
        activate_weekly_access(
            self.database,
            customer_id,
            paid_at=paid_at,
            provider="lipana",
            payment_reference="txn-before-failure",
            auto_renew_enabled=False,
            renewal_preference="prompt_again",
        )
        failed = record_renewal_failure(
            self.database,
            customer_id,
            failed_at=expires_at + timedelta(minutes=1),
        )
        self.assertFalse(failed.active)
        self.assertEqual(failed.status, "expired")
        self.assertEqual(failed.current_period_end, expires_at)

    def test_action6d_is_installed_between_lipana_and_final_premium_gate(self) -> None:
        entry = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        payment = entry.index("install_lipana_mpesa_action6b(app)")
        renewal_install = entry.index("install_premium_renewal_action6d(app)")
        gate = entry.index("install_premium_access_action6a(app)")
        self.assertLess(payment, renewal_install)
        self.assertLess(renewal_install, gate)

        source = (ROOT / "app" / "premium_renewal_action6d.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("PREMIUM_EXPIRY_SWEEP_INTERVAL_SECONDS", source)
        self.assertIn("Premium subscription expired before scheduled start", source)
        self.assertIn("open_settlement_preserved=true", source)
        self.assertIn("lipana._mark_failed_from_webhook = _premium_lipana_failure", source)

    def test_action6d_migration_is_next_head(self) -> None:
        migration = (
            ROOT
            / "migrations"
            / "versions"
            / "20260817_0025_premium_weekly_renewal_action6d.py"
        ).read_text(encoding="utf-8")
        self.assertIn('revision = "20260817_0025"', migration)
        self.assertIn('down_revision = "20260817_0024"', migration)
        self.assertIn("premium_access_periods", migration)
        self.assertIn("uq_premium_access_period_provider_payment", migration)


if __name__ == "__main__":
    unittest.main()
