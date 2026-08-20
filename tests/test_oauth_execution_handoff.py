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

    def test_browser_bootstrap_never_exposes_refresh_token(self) -> None:
        source = self.read("app/browser_direct_deriv_transport_v3.py")
        bootstrap = source.split('def browser_direct_bootstrap', 1)[1].split(
            '@app.post("/me/direct-execution/session")', 1
        )[0]
        for marker in (
            '"access_token": token',
            '"deriv_app_id"',
            '"api_base": "https://api.derivws.com"',
            '"server_otp": False',
            '"server_proposal": False',
            '"server_buy": False',
            '"refresh_token_exposed": False',
        ):
            self.assertIn(marker, bootstrap)
        self.assertNotIn('"refresh_token":', bootstrap)
        self.assertIn("oauth_trade_access_token(payload)", bootstrap)

    def test_browser_calls_deriv_otp_directly_and_not_vps_session(self) -> None:
        finalizer = self.read("scripts/finalize-oauth-execution-handoff-v1.mjs")
        for marker in (
            "https://api.derivws.com/trading/v1/options/accounts/",
            'Authorization: `Bearer ${auth.accessToken}`',
            '"Deriv-App-ID": auth.derivAppId',
            'credentials: "omit"',
            'apiPath("/me/direct-execution/bootstrap")',
            'apiPath("/me/direct-execution/receipt")',
            "sendTradeReceipt",
            "browser reconnecting directly",
        ):
            self.assertIn(marker, finalizer)
        for forbidden in (
            'apiPath("/me/direct-execution/session")',
            'apiPath("/me/direct-execution/heartbeat")',
            'apiPath("/me/direct-execution/yield")',
            "VPS continuity takeover activated automatically",
        ):
            self.assertNotIn(forbidden, finalizer)

    def test_server_heartbeat_and_takeover_are_retired(self) -> None:
        api = self.read("app/browser_direct_deriv_transport_v3.py")
        worker = self.read("app/browser_direct_worker_offload_v3.py")
        checkpoint = self.read("dashboard/direct-continuity-checkpoint-v1.js")
        for marker in (
            '"heartbeat_required": False',
            '"takeover_requested": False',
            '"server_trade_transport": False',
            '"live_server_provider_requests"',
        ):
            self.assertIn(marker, api)
        self.assertIn("_promote_expired_browser_leases = no_browser_takeover", worker)
        self.assertIn("provider_requests=false", worker)
        self.assertIn("browser_direct_takeover=false", worker)
        self.assertNotIn("setInterval(checkpoint, 5000)", checkpoint)
        self.assertNotIn("/api/me/direct-execution/checkpoint", checkpoint)
        self.assertIn("trade_receipts_only: true", checkpoint)

    def test_manual_stop_still_uses_server_hard_stop_control(self) -> None:
        engine = self.read("dashboard/deriv-direct-execution-v1.js")
        hard_stop = self.read("dashboard/direct-hard-stop-fence-v1.js")
        api = self.read("app/browser_direct_deriv_transport_v3.py")
        self.assertIn('/me/direct-execution/stop', engine)
        self.assertIn("Trading is stopped; BUY blocked locally", hard_stop)
        self.assertIn("clear_direct_hard_stop(session, managed_id)", api)
        self.assertIn("Direct execution stopped", self.read("app/vps_direct_execution_api.py"))

    def test_finalizer_is_last_transport_gate(self) -> None:
        docker = self.read("Dockerfile.frontend")
        self.assertIn("COPY scripts/finalize-oauth-execution-handoff-v1.mjs", docker)
        self.assertIn("node --check scripts/finalize-oauth-execution-handoff-v1.mjs", docker)
        self.assertIn("node scripts/finalize-oauth-execution-handoff-v1.mjs", docker)
        sticky = docker.rfind("node scripts/finalize-sticky-stake-v1.mjs")
        direct_v3 = docker.rfind("node scripts/finalize-oauth-execution-handoff-v1.mjs")
        self.assertGreater(sticky, -1)
        self.assertGreater(direct_v3, sticky)


if __name__ == "__main__":
    unittest.main()
