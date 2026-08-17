from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.database import Database
from app.models import Base
from app.premium_access_models import PremiumCustomer
from app.premium_access_service import activate_weekly_access, effective_access_state
from app import lipana_mpesa_action6b as lipana


ROOT = Path(__file__).resolve().parents[1]


class LipanaPureContractTests(unittest.TestCase):
    def test_kenyan_phone_normalization_is_strict(self) -> None:
        self.assertEqual(lipana.normalize_kenyan_mpesa_phone("0712 345 678"), "+254712345678")
        self.assertEqual(lipana.normalize_kenyan_mpesa_phone("254712345678"), "+254712345678")
        self.assertEqual(lipana.normalize_kenyan_mpesa_phone("+254112345678"), "+254112345678")
        with self.assertRaises(ValueError):
            lipana.normalize_kenyan_mpesa_phone("+12025550123")

    def test_success_and_failure_events_accept_current_lipana_names(self) -> None:
        self.assertEqual(lipana._event_kind("transaction.success"), "success")
        self.assertEqual(lipana._event_kind("payment.success"), "success")
        self.assertEqual(lipana._event_kind("transaction.failed"), "failed")
        self.assertEqual(lipana._event_kind("payment.cancelled"), "failed")
        self.assertEqual(lipana._event_kind("payout.success"), "other")

    def test_provider_transaction_must_match_exact_weekly_amount(self) -> None:
        class Transactions:
            def retrieve(self, transaction_id: str):
                return {
                    "transactionId": transaction_id,
                    "amount": 250,
                    "currency": "KES",
                    "status": "success",
                }

        class Client:
            transactions = Transactions()

        verified = lipana._verified_provider_transaction(Client(), "txn_123")
        self.assertEqual(verified["amount"], 250)

        class WrongAmountTransactions:
            def retrieve(self, transaction_id: str):
                return {
                    "transactionId": transaction_id,
                    "amount": 249,
                    "currency": "KES",
                    "status": "success",
                }

        class WrongClient:
            transactions = WrongAmountTransactions()

        with self.assertRaisesRegex(ValueError, "KES 250"):
            lipana._verified_provider_transaction(WrongClient(), "txn_123")


class PremiumPaymentIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.database = Database(f"sqlite:///{self.tmp.name}")
        Base.metadata.create_all(self.database.engine)
        with self.database.session() as session:
            session.add(
                PremiumCustomer(
                    id="customer-1",
                    identity_fingerprint="f" * 64,
                    status="unpaid",
                    plan_code="weekly_access",
                    renewal_preference="automatic_if_supported",
                    auto_renew_enabled=True,
                )
            )

    def tearDown(self) -> None:
        self.database.engine.dispose()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_same_provider_transaction_can_never_grant_a_second_week(self) -> None:
        paid_at = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        first = activate_weekly_access(
            self.database,
            "customer-1",
            paid_at=paid_at,
            provider="lipana",
            payment_reference="txn_same",
            auto_renew_enabled=False,
            renewal_preference="prompt_again",
        )
        self.assertTrue(first.active)
        self.assertEqual(first.current_period_end, paid_at + timedelta(days=7))

        replay = activate_weekly_access(
            self.database,
            "customer-1",
            paid_at=paid_at + timedelta(hours=5),
            provider="lipana",
            payment_reference="txn_same",
            auto_renew_enabled=False,
            renewal_preference="prompt_again",
        )
        self.assertEqual(replay.current_period_end, paid_at + timedelta(days=7))

        with self.database.session() as session:
            customer = session.get(PremiumCustomer, "customer-1")
            self.assertIsNotNone(customer)
            self.assertEqual(customer.renewal_preference, "prompt_again")
            self.assertFalse(customer.auto_renew_enabled)
            self.assertEqual(customer.current_period_start, paid_at)
            self.assertEqual(customer.current_period_end, paid_at + timedelta(days=7))

    def test_expiry_is_still_exact_after_lipana_activation(self) -> None:
        paid_at = datetime(2026, 8, 17, 9, 22, 30, tzinfo=timezone.utc)
        activate_weekly_access(
            self.database,
            "customer-1",
            paid_at=paid_at,
            provider="lipana",
            payment_reference="txn_expiry",
            auto_renew_enabled=False,
            renewal_preference="prompt_again",
        )
        with self.database.session() as session:
            customer = session.get(PremiumCustomer, "customer-1")
            self.assertTrue(
                effective_access_state(
                    customer,
                    now=paid_at + timedelta(days=7) - timedelta(microseconds=1),
                ).active
            )
            expired = effective_access_state(
                customer,
                now=paid_at + timedelta(days=7),
            )
            self.assertFalse(expired.active)
            self.assertEqual(expired.status, "expired")


class LipanaSourceSafetyTests(unittest.TestCase):
    def test_official_sdk_is_pinned_and_server_reverification_is_required(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        source = (ROOT / "app" / "lipana_mpesa_action6b.py").read_text(encoding="utf-8")
        self.assertIn("lipana==1.0.1", requirements)
        self.assertIn('request.headers.get("x-lipana-signature")', source)
        self.assertIn("client.webhooks.verify(payload, signature, secret)", source)
        self.assertIn("client.transactions.retrieve", source)
        self.assertIn("amount_minor != AMOUNT_MINOR", source)
        self.assertIn('renewal_preference="prompt_again"', source)
        self.assertIn('auto_renew_enabled=False', source)

    def test_payment_model_never_persists_raw_phone_or_webhook_body(self) -> None:
        models = (ROOT / "app" / "premium_payment_models.py").read_text(encoding="utf-8")
        self.assertIn("phone_hash", models)
        self.assertIn("phone_masked", models)
        self.assertNotIn("phone_number:", models)
        self.assertNotIn("raw_phone", models)
        self.assertNotIn("raw_payload", models)
        self.assertIn("event_digest", models)

    def test_vps_environment_documents_sandbox_and_callback_without_secrets(self) -> None:
        env = (ROOT / ".env.vps.example").read_text(encoding="utf-8")
        self.assertIn("LIPANA_ENVIRONMENT=sandbox", env)
        self.assertIn("LIPANA_SECRET_KEY=", env)
        self.assertIn("LIPANA_WEBHOOK_SECRET=", env)
        self.assertIn("https://derivadmin.site/api/webhooks/lipana", env)

    def test_action6b_routes_are_installed_before_final_premium_gate(self) -> None:
        source = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        payment = source.index("install_lipana_mpesa_action6b(app)")
        gate = source.index("install_premium_access_action6a(app)")
        self.assertLess(payment, gate)


if __name__ == "__main__":
    unittest.main()
