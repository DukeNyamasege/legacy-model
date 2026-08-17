from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from app.premium_access_api import premium_write_requires_access
from app.premium_access_models import PremiumCustomer
from app.premium_access_service import (
    WEEKLY_PERIOD_DAYS,
    WEEKLY_PRICE_KES,
    access_payload,
    effective_access_state,
    premium_account_hash,
    premium_identity_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]


class PremiumAccessAction6ATests(unittest.TestCase):
    def test_weekly_price_and_exact_seven_day_boundary(self) -> None:
        self.assertEqual(WEEKLY_PERIOD_DAYS, 7)
        self.assertEqual(WEEKLY_PRICE_KES, 250.0)

        paid_at = datetime(2026, 8, 17, 12, 6, 0, tzinfo=timezone.utc)
        expires_at = paid_at + timedelta(days=7)
        customer = PremiumCustomer(
            id="premium-test",
            identity_fingerprint="a" * 64,
            status="active",
            current_period_start=paid_at,
            current_period_end=expires_at,
            renewal_preference="prompt_again",
            auto_renew_enabled=False,
            renewal_provider="lipana",
        )
        before = effective_access_state(
            customer,
            now=expires_at - timedelta(microseconds=1),
        )
        exact = effective_access_state(customer, now=expires_at)
        after = effective_access_state(
            customer,
            now=expires_at + timedelta(seconds=1),
        )
        self.assertTrue(before.active)
        self.assertFalse(exact.active)
        self.assertEqual(exact.status, "expired")
        self.assertFalse(after.active)
        self.assertEqual(after.remaining_seconds, 0)
        self.assertEqual(set(access_payload(before)["pricing"]), {"mpesa"})

    def test_linked_dot_rot_identity_is_stable_and_raw_ids_are_not_storage_keys(self) -> None:
        dot = "VRTC123456"
        rot = "CR123456"
        self.assertEqual(premium_account_hash(dot), premium_account_hash(dot.lower()))
        fingerprint_one = premium_identity_fingerprint([dot, rot])
        fingerprint_two = premium_identity_fingerprint([rot, dot, dot])
        self.assertEqual(fingerprint_one, fingerprint_two)
        self.assertNotIn(dot, fingerprint_one)
        self.assertNotIn(rot, fingerprint_one)

    def test_view_only_gate_blocks_feature_mutations_but_keeps_safety_and_setup(self) -> None:
        self.assertFalse(premium_write_requires_access("GET", "/me/custom-strategy"))
        self.assertTrue(premium_write_requires_access("POST", "/me/custom-strategy"))
        self.assertTrue(premium_write_requires_access("POST", "/me/resume-trading"))
        self.assertTrue(premium_write_requires_access("POST", "/me/auto-trade"))
        self.assertTrue(premium_write_requires_access("POST", "/me/automation-schedules"))
        self.assertFalse(premium_write_requires_access("POST", "/me/stop-trading"))
        self.assertFalse(premium_write_requires_access("POST", "/me/pause-trading"))
        self.assertFalse(premium_write_requires_access("POST", "/me/switch-account"))
        self.assertFalse(
            premium_write_requires_access(
                "POST",
                "/me/automation-preferences/timezone",
            )
        )
        self.assertFalse(
            premium_write_requires_access(
                "POST",
                "/me/automation-schedules/schedule-1/cancel",
            )
        )
        self.assertFalse(
            premium_write_requires_access("POST", "/me/premium-access/checkout")
        )

    def test_database_schema_reserves_provider_fields_without_exposing_card_checkout(self) -> None:
        model = (ROOT / "app" / "premium_access_models.py").read_text(encoding="utf-8")
        migration = (
            ROOT
            / "migrations"
            / "versions"
            / "20260817_0023_premium_access_action6a.py"
        ).read_text(encoding="utf-8")
        for marker in (
            "PremiumCustomer",
            "PremiumCustomerAccount",
            "auto_renew_enabled",
            "renewal_provider",
            "provider_customer_ref",
            "provider_subscription_ref",
            "renewal_failed_at",
        ):
            self.assertIn(marker, model)
        self.assertIn('revision = "20260817_0023"', migration)
        self.assertIn('down_revision = "20260817_0022"', migration)
        self.assertIn("premium_customers", migration)
        self.assertIn("premium_customer_accounts", migration)

    def test_api_gate_is_installed_last_and_uses_mpesa_only_copy(self) -> None:
        entry = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        gate = (ROOT / "app" / "premium_access_api.py").read_text(encoding="utf-8")
        self.assertIn("install_premium_access_action6a(app)", entry)
        self.assertGreater(
            entry.index("install_premium_access_action6a(app)"),
            entry.index("install_premium_renewal_action6d(app)"),
        )
        self.assertIn("PREMIUM_SUBSCRIPTION_REQUIRED", gate)
        self.assertIn("status_code=402", gate)
        self.assertIn("Pay KES 250 via M-Pesa", gate)
        self.assertNotIn("USD 2", gate)
        self.assertNotIn("flutterwave.com", gate)

    def test_worker_checks_entitlement_at_admission_proposal_and_buy(self) -> None:
        source = (ROOT / "app" / "premium_worker_guard.py").read_text(encoding="utf-8")
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        for marker in (
            "premium_runtime_accounts",
            "premium_execute",
            "premium_proposal",
            "premium_buy",
            "settlement_only",
            "premium subscription blocks proposal",
            "premium subscription blocks BUY",
        ):
            self.assertIn(marker, source)
        self.assertIn("install_premium_worker_guard()", worker)
        self.assertGreater(
            worker.index("install_premium_worker_guard()"),
            worker.index("install_telegram_silence()"),
        )
        self.assertIn("premium_settlement_preserved=true", worker)


if __name__ == "__main__":
    unittest.main()
