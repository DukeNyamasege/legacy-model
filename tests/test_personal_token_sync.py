from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKEN_SYNC = ROOT / "app" / "personal_token_sync.py"
ME_FIX = ROOT / "app" / "personal_me_session_fix.py"


class PersonalTokenSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = TOKEN_SYNC.read_text(encoding="utf-8")

    def test_module_compiles_and_request_type_is_globally_resolvable(self) -> None:
        ast.parse(self.source)
        self.assertIn(
            "PersonalApiTokenRequest = base_api.PersonalApiTokenRequest",
            self.source,
        )
        self.assertIn("body: PersonalApiTokenRequest", self.source)

    def test_token_is_validated_by_exact_account_ownership_not_selected_mode(self) -> None:
        self.assertIn(
            "current_provider_account = accounts_by_id.get(current_account_id)",
            self.source,
        )
        self.assertNotIn(
            "and normalize_account_type(item.get(\"account_type\")) == account_type_value",
            self.source,
        )
        self.assertIn("provider_type = _provider_account_type(provider_account)", self.source)
        self.assertIn("_provider_account_is_connectable(current_provider_account)", self.source)

    def test_successful_token_is_synced_to_all_active_returned_accounts(self) -> None:
        self.assertIn(
            "for managed_row in base_api.REPOSITORY.list_managed_accounts()",
            self.source,
        )
        self.assertIn("provider_account = accounts_by_id.get(managed_account_id)", self.source)
        self.assertIn("\"pat_shared_demo_real\": True", self.source)
        self.assertIn("\"pat_verified_scope\": \"trade\"", self.source)
        self.assertIn("PERSONAL_API_TOKEN_VERIFIED_AND_SYNCED", self.source)
        self.assertIn("rejected_linked_accounts", self.source)
        self.assertIn("Deriv Options account is not active", self.source)

    def test_missing_or_rejected_credentials_reopen_the_input_field(self) -> None:
        for status in (
            "credential_error",
            "credential_decrypt_error",
            "token_required",
            "bulk_execution_pat_required",
            "invalid_account",
        ):
            self.assertIn(f'\"{status}\"', self.source)
        self.assertIn(
            "base_api.has_personal_trading_api_token = base_api.has_trading_api_token",
            self.source,
        )
        self.assertIn("_reject_current_account(account, reason)", self.source)

    def test_provider_outage_does_not_destroy_or_reject_a_valid_stored_token(self) -> None:
        self.assertIn("provider_status in {429} or provider_status >= 500", self.source)
        self.assertIn("No credential was changed", self.source)
        self.assertIn("reject_credential", self.source)

    def test_dashboard_explains_one_token_for_demo_and_real(self) -> None:
        self.assertIn("FOA_LINKED_ACCOUNT_TOKEN_SYNC", self.source)
        self.assertIn(
            "One trade-scoped token can authorize linked Demo and Real Options accounts.",
            self.source,
        )
        self.assertIn("X-FOA-Linked-Account-Token-Sync", self.source)

    def test_personal_me_installs_the_final_token_authority(self) -> None:
        source = ME_FIX.read_text(encoding="utf-8")
        self.assertIn(
            "from app.personal_token_sync import install_personal_token_sync",
            source,
        )
        self.assertIn("install_personal_token_sync(app)", source)


if __name__ == "__main__":
    unittest.main()
