from __future__ import annotations

import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.bulk_credential_failure_hardening import (
    is_token_account_binding_failure,
    quarantine_undecryptable_enabled_accounts,
)


ROOT = Path(__file__).resolve().parents[1]


class _Repository:
    def __init__(self, rows):
        self.rows = list(rows)
        self.quarantined: list[tuple[int, str, str]] = []

    def list_managed_accounts(self):
        return list(self.rows)

    def quarantine_managed_account(self, account_id, status, reason):
        self.quarantined.append((int(account_id), str(status), str(reason)))


class BulkCredentialFailureTests(unittest.TestCase):
    def test_deriv_account_binding_rejection_is_permanent_and_account_local(self) -> None:
        self.assertTrue(
            is_token_account_binding_failure(
                "error",
                'Token or account validation failed for account "DOT***750"',
            )
        )
        self.assertTrue(
            is_token_account_binding_failure(
                "credential_error",
                "Credential does not belong to the requested account",
            )
        )
        self.assertFalse(
            is_token_account_binding_failure(
                "error",
                "Provider request timed out",
            )
        )

    def test_only_enabled_unsafe_rows_are_quarantined(self) -> None:
        rows = [
            SimpleNamespace(id=1, enabled=True, token_secret="bad-enabled"),
            SimpleNamespace(id=2, enabled=False, token_secret="bad-disabled"),
            SimpleNamespace(id=3, enabled=True, token_secret="good-enabled"),
            SimpleNamespace(
                id=4,
                enabled=True,
                token_secret="good-binding-failure",
                execution_status="error",
                execution_status_reason="Token or account validation failed for account DOT***750",
            ),
        ]
        repository = _Repository(rows)
        bot = SimpleNamespace(
            encryption_key="test-key",
            repository=repository,
            logger=logging.getLogger("bulk-credential-hardening-test"),
        )

        def decrypt(value, _key):
            if value.startswith("bad"):
                raise ValueError("invalid encrypted payload")
            return {"auth_type": "pat", "account_id": "DOT123"}

        with patch(
            "app.bulk_credential_failure_hardening.decrypt_auth_payload",
            side_effect=decrypt,
        ):
            count = quarantine_undecryptable_enabled_accounts(bot)

        self.assertEqual(count, 2)
        self.assertEqual(len(repository.quarantined), 2)
        quarantined = {
            account_id: (status, reason)
            for account_id, status, reason in repository.quarantined
        }
        self.assertEqual(quarantined[1][0], "credential_decrypt_error")
        self.assertIn("Reconnect this account", quarantined[1][1])
        self.assertEqual(quarantined[4][0], "invalid_account")
        self.assertIn("selected account", quarantined[4][1])
        self.assertNotIn(2, quarantined)
        self.assertNotIn(3, quarantined)

    def test_production_installs_quarantine_after_final_bulk_transport(self) -> None:
        source = (ROOT / "app" / "production_worker_integration.py").read_text(
            encoding="utf-8"
        )
        final_runtime = source.index("install_final_seamless_execution_runtime()")
        credential_guard = source.index("install_bulk_credential_failure_hardening()")
        self.assertLess(final_runtime, credential_guard)
        self.assertIn("shared_credentials_preserved=true", (
            ROOT / "app" / "bulk_credential_failure_hardening.py"
        ).read_text(encoding="utf-8"))

    def test_readiness_audit_does_not_make_disabled_history_a_global_blocker(self) -> None:
        source = (
            ROOT / "scripts" / "audit_bulk_execution_readiness.py"
        ).read_text(encoding="utf-8")
        self.assertIn("decrypt_failed_enabled", source)
        self.assertIn("decrypt_failed_disabled", source)
        self.assertNotIn(
            'elif counters["decrypt_failed"]:',
            source,
        )
        self.assertIn(
            "healthy account execution is unaffected",
            source,
        )


if __name__ == "__main__":
    unittest.main()
