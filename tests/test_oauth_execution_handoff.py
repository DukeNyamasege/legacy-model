from __future__ import annotations

from pathlib import Path
import unittest

import app.vps_direct_execution_api as direct_api


ROOT = Path(__file__).resolve().parents[1]


class OAuthExecutionHandoffTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_oauth_refresh_preserves_account_identity(self) -> None:
        payload = {
            "auth_type": "oauth",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": "2026-08-20T00:00:00+00:00",
            "scope": "trade application_read",
            "account_id": "DOT123456",
            "account_type": "demo",
            "auth_source": "deriv_oauth",
        }
        updated = direct_api._apply_refreshed_oauth(
            payload,
            {
                "auth_type": "oauth",
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_at": "2026-08-21T00:00:00+00:00",
                "scope": "trade application_read",
                "token_type": "Bearer",
            },
        )
        self.assertEqual(updated["access_token"], "new-access")
        self.assertEqual(updated["refresh_token"], "new-refresh")
        self.assertEqual(updated["account_id"], "DOT123456")
        self.assertEqual(updated["account_type"], "demo")
        self.assertEqual(updated["auth_source"], "deriv_oauth")

    def test_pat_wrapper_keeps_pat_while_refreshing_embedded_oauth(self) -> None:
        payload = {
            "auth_type": "pat",
            "access_token": "pat-value",
            "pat_token": "pat-value",
            "oauth_access_token": "old-oauth",
            "oauth_refresh_token": "old-refresh",
            "oauth_expires_at": "2026-08-20T00:00:00+00:00",
            "oauth_scope": "trade application_read",
            "account_id": "DOT123456",
        }
        updated = direct_api._apply_refreshed_oauth(
            payload,
            {
                "access_token": "new-oauth",
                "refresh_token": "new-refresh",
                "expires_at": "2026-08-21T00:00:00+00:00",
                "scope": "trade application_read",
            },
        )
        self.assertEqual(updated["access_token"], "pat-value")
        self.assertEqual(updated["pat_token"], "pat-value")
        self.assertEqual(updated["oauth_access_token"], "new-oauth")
        self.assertEqual(updated["oauth_refresh_token"], "new-refresh")

    def test_provider_auth_rejection_is_recognized_for_one_refresh_retry(self) -> None:
        self.assertTrue(direct_api._provider_rejected_login("Invalid or missing authentication credentials"))
        self.assertTrue(direct_api._provider_rejected_login("Unauthorized token"))
        self.assertFalse(direct_api._provider_rejected_login("Invalid account ID format"))

    def test_session_route_reuses_login_and_yield_never_stops_account(self) -> None:
        source = self.read("app/vps_direct_execution_api.py")
        for marker in (
            '"authorization": "existing_deriv_login_reused"',
            '"second_login_required": False',
            '@app.post("/me/direct-execution/yield")',
            '"auto_trading_continues": True',
            'row.execution_status_updated_at = now - timedelta(',
            'DIRECT_BROWSER_LEASE_SECONDS + 1.0',
            '_fresh_oauth_payload(session, row, _auth_payload(row))',
            'force=True',
        ):
            self.assertIn(marker, source)

        yield_section = source.split('def yield_direct_execution', 1)[1].split(
            '@app.post("/me/direct-execution/stop")', 1
        )[0]
        self.assertNotIn("row.enabled = False", yield_section)
        self.assertNotIn('row.execution_status = "stopped"', yield_section)

    def test_browser_heartbeat_requires_healthy_private_trade_channel(self) -> None:
        finalizer = self.read("scripts/finalize-oauth-execution-handoff-v1.mjs")
        for marker in (
            "yieldUnhealthyBrowserExecution",
            'apiPath("/me/direct-execution/yield")',
            "state.privateWs?.readyState !== WebSocket.OPEN",
            "state.privateUnavailableSince",
            "heartbeatOnce(state.epoch)",
            "browser_financial_owner_healthy",
            "VPS continuity takeover activated automatically",
        ):
            self.assertIn(marker, finalizer)
        self.assertIn(
            "Never renew it",
            finalizer,
        )

    def test_finalizer_is_last_financial_ownership_gate(self) -> None:
        docker = self.read("Dockerfile.frontend")
        self.assertIn("COPY scripts/finalize-oauth-execution-handoff-v1.mjs", docker)
        self.assertIn("node --check scripts/finalize-oauth-execution-handoff-v1.mjs", docker)
        self.assertIn("node scripts/finalize-oauth-execution-handoff-v1.mjs", docker)
        sticky = docker.rfind("node scripts/finalize-sticky-stake-v1.mjs")
        handoff = docker.rfind("node scripts/finalize-oauth-execution-handoff-v1.mjs")
        self.assertGreater(sticky, -1)
        self.assertGreater(handoff, sticky)


if __name__ == "__main__":
    unittest.main()
