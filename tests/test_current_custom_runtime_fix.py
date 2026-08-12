from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from app.account_execution_session import AccountExecutionSession
from app.account_scoped_websocket_runtime import _promote_embedded_oauth_payload
from app.custom_strategy_current_runtime_fix import (
    _active_account_unresolved_rows,
    _proposal_on_exact_account_session,
)
from app.real_demo_trading_support import _account_purchase_token_from_payload


ROOT = Path(__file__).resolve().parents[1]


class _PrivateSession:
    def __init__(self) -> None:
        self.account_id = "DOT1000422"
        self.is_connected = True
        self.requests: list[dict] = []

    async def send_request(self, payload: dict) -> dict:
        self.requests.append(dict(payload))
        return {
            "proposal": {
                "id": "private-session-proposal-1234567890",
                "ask_price": 1.0,
                "payout": 1.9,
            }
        }


class _PublicClient:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def send_request(self, payload: dict) -> dict:
        self.requests.append(dict(payload))
        raise AssertionError("Custom Strategy proposal must not use public WebSocket")


class _ProposalBot:
    def __init__(self) -> None:
        self.key = "fingerprint:DOT1000422"
        self.user_profiles = {
            self.key: {
                "managed_account_id": 592,
                "account_id": "DOT1000422",
                "api_token": "oauth-trade-token",
            }
        }
        self.clients = {
            self.key: {
                "managed_account_id": 592,
                "base_stake": 1.0,
            }
        }
        self.sessions = {self.key: _PrivateSession()}
        self.public_client = _PublicClient()
        self.currency = "USD"
        self.app_markup_percentage = 0.0

    def _credential_for_token(self, token: str) -> str:
        return str(self.user_profiles[token]["api_token"])

    def _client_state_for_token(self, token: str, *, account_id: str | None = None):
        return self.clients[token]


class CurrentProposalSessionRegressionTests(TestCase):
    def test_proposal_is_created_on_exact_private_account_session(self) -> None:
        bot = _ProposalBot()
        execution = AccountExecutionSession(
            bot=bot,
            token=bot.key,
            account_id="DOT1000422",
            managed_account_id=592,
        )
        signal = SimpleNamespace(
            contract_type="DIGITOVER",
            barrier="2",
            duration_ticks=1,
            symbol="1HZ50V",
        )

        economics = asyncio.run(
            _proposal_on_exact_account_session(
                execution,
                signal,
                stake=1.0,
                predicted_probability=0.70,
            )
        )

        self.assertEqual(economics.proposal_id, "private-session-proposal-1234567890")
        self.assertEqual(bot.public_client.requests, [])
        self.assertEqual(len(bot.sessions[bot.key].requests), 1)
        self.assertEqual(bot.sessions[bot.key].requests[0]["proposal"], 1)
        self.assertEqual(bot.sessions[bot.key].requests[0]["underlying_symbol"], "1HZ50V")
        self.assertEqual(bot.sessions[bot.key].requests[0]["barrier"], "2")

    def test_runtime_fix_replaces_public_proposal_method(self) -> None:
        source = (ROOT / "app" / "custom_strategy_current_runtime_fix.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("AccountExecutionSession.proposal = _proposal_on_exact_account_session", source)
        self.assertIn("private_session.send_request(self._proposal_request", source)
        self.assertNotIn("self.bot.public_client.send_request", source)


class CurrentRuntimeFailClosedRegressionTests(TestCase):
    def test_failure_removes_account_from_hot_path_immediately(self) -> None:
        source = (ROOT / "app" / "custom_strategy_current_runtime_fix.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("runtime.pop(int(managed_id), None)", source)
        self.assertIn("_custom_direct_inflight", source)
        self.assertIn("task.cancel()", source)

    def test_private_ready_bypasses_inherited_rf_contract_validation(self) -> None:
        source = (ROOT / "app" / "custom_strategy_current_runtime_fix.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("TradingBot._on_private_session_ready(self, session)", source)
        self.assertNotIn("_validate_account_contracts", source)
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        self.assertIn("install_custom_strategy_current_runtime_fix()", worker)

    def test_historical_unresolved_rows_are_scoped_to_active_account(self) -> None:
        bot = SimpleNamespace(valid_clients=[("key", "DOT1000422")])
        rows = [
            SimpleNamespace(account_id_masked="DOT***422", contract_id="111"),
            SimpleNamespace(account_id_masked="DOT***999", contract_id="222"),
            SimpleNamespace(account_id_masked="CR***123", contract_id="333"),
        ]
        relevant = _active_account_unresolved_rows(bot, rows)
        self.assertEqual([row.contract_id for row in relevant], ["111"])


class OAuthExecutionCredentialRegressionTests(TestCase):
    def test_legacy_oauth_plus_pat_row_is_promoted_back_to_oauth(self) -> None:
        payload = {
            "auth_type": "pat",
            "access_token": "legacy-pat",
            "pat_verified_at": "2026-08-01T00:00:00+00:00",
            "oauth_access_token": "fresh-oauth-access",
            "oauth_refresh_token": "fresh-oauth-refresh",
            "oauth_expires_at": "2099-01-01T00:00:00+00:00",
            "oauth_scope": "trade application_read",
            "account_id": "DOT1000422",
            "account_type": "demo",
        }
        promoted = _promote_embedded_oauth_payload(payload)
        self.assertEqual(promoted["auth_type"], "oauth")
        self.assertEqual(promoted["access_token"], "fresh-oauth-access")
        self.assertEqual(promoted["pat_token"], "legacy-pat")
        self.assertIn("trade", promoted["scope"].split())

    def test_private_websocket_prefers_oauth_over_legacy_pat_fallback(self) -> None:
        payload = {
            "auth_type": "pat",
            "access_token": "legacy-pat",
            "oauth_access_token": "fresh-oauth-access",
            "oauth_scope": "trade application_read",
        }
        self.assertEqual(
            _account_purchase_token_from_payload(payload),
            "fresh-oauth-access",
        )

    def test_api_authority_keeps_oauth_primary_for_future_logins(self) -> None:
        source = (ROOT / "app" / "oauth_direct_account_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('merged["auth_type"] = "oauth"', source)
        self.assertIn('merged["pat_token"] = legacy_pat', source)
        self.assertIn("has_account_execution_credential", source)
        api_v3 = (ROOT / "app" / "api_v3.py").read_text(encoding="utf-8")
        self.assertIn("install_oauth_direct_account_authority()", api_v3)


if __name__ == "__main__":
    unittest.main()
