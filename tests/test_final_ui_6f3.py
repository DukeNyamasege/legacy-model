from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"


class FinalUi6F3Tests(unittest.TestCase):
    def test_document_uses_premium_bootstrap_before_heavy_runtime(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-premium-boot="pending"', html)
        self.assertIn('frontend-runtime" content="direct-vps-final-ui-6f3"', html)
        self.assertIn('frontend-authority" content="final-ui-shell-v2"', html)
        self.assertIn('/final-premium-6f3.css?v=20260817-6f3-1', html)
        self.assertIn('/final-premium-6f3.js?v=20260817-6f3-1', html)
        self.assertNotIn('<script src="/vps-realtime-client-v2.js?v=20260817-6f2-1" defer>', html)
        self.assertNotIn('<script src="/final-ui-shell-v2.js?v=20260817-6f2-1" defer>', html)
        self.assertNotIn("premium-subscription-action6e", html)
        self.assertNotIn("netlify", html.lower())

    def test_login_to_payment_to_unlock_flow_is_server_authoritative(self) -> None:
        js = (DASHBOARD / "final-premium-6f3.js").read_text(encoding="utf-8")
        for route in (
            'api("/me")',
            'api("/me/premium-access")',
            'api("/me/premium-access/payment-options")',
            'api("/me/accounts")',
            'api("/me/premium-access/mpesa/payments/latest")',
            'api("/me/premium-access/mpesa/stk-push"',
            'api(`/me/premium-access/mpesa/payments/${encodeURIComponent(state.payment.id)}`)',
            'api("/me/premium-access/renewal-status")',
            'api("/me/premium-access/renewal-history?limit=8")',
        ):
            self.assertIn(route, js)
        self.assertIn("KES 250", js)
        self.assertIn("7 days", js)
        self.assertIn("M-PESA · LIPANA", js)
        self.assertIn("DOT & ROT access", js)
        self.assertIn("signed Lipana callback", js)
        self.assertIn("server-side transaction verification", js)
        # The STK response never calls loadFinalApp. Unlock occurs only after the
        # payment polling path sees server activation, then the user reloads.
        self.assertIn("payment?.activated", js)
        self.assertIn("premium?.active", js)
        self.assertNotIn("api.derivws.com", js)
        self.assertNotIn("proposal_open_contract", js)

    def test_unpaid_users_do_not_load_shell_or_realtime(self) -> None:
        js = (DASHBOARD / "final-premium-6f3.js").read_text(encoding="utf-8")
        boot_start = js.index("async function boot()")
        boot = js[boot_start:]
        self.assertIn("await loadPremiumData()", boot)
        self.assertIn("if (state.premium?.local_dev_preview || state.premium?.active)", boot)
        self.assertIn("await loadFinalApp({ realtime: true })", boot)
        self.assertIn("gate();", boot)
        self.assertIn('/vps-realtime-client-v2.js?v=20260817-6f2-1', js)
        self.assertIn('/final-ui-shell-v2.js?v=20260817-6f2-1', js)
        self.assertIn('state.locked = true', js)
        self.assertIn('document.documentElement.dataset.premiumState = "locked"', js)

    def test_pending_uncertain_failed_and_retry_states_are_explicit(self) -> None:
        js = (DASHBOARD / "final-premium-6f3.js").read_text(encoding="utf-8")
        self.assertIn('new Set(["initiating", "pending", "provider_uncertain"])', js)
        self.assertIn('new Set(["failed", "verification_failed"])', js)
        self.assertIn("Do not start another payment", js)
        self.assertIn("Approve the M-Pesa prompt", js)
        self.assertIn("Payment was not completed", js)
        self.assertIn("Try M-Pesa again", js)
        self.assertIn("idempotency_key: idempotencyKey()", js)
        self.assertIn("schedulePoll", js)

    def test_exact_expiry_rechecks_server_and_relocks_runtime(self) -> None:
        js = (DASHBOARD / "final-premium-6f3.js").read_text(encoding="utf-8")
        service = (ROOT / "app" / "premium_access_service.py").read_text(encoding="utf-8")
        renewal = (ROOT / "app" / "premium_renewal_action6d.py").read_text(encoding="utf-8")
        self.assertIn("async function exactExpiryReached()", js)
        self.assertIn('const access = await api("/me/premium-access")', js)
        self.assertIn("location.reload();", js)
        self.assertIn("scheduleExactExpiry", js)
        self.assertIn("timedelta(days=WEEKLY_PERIOD_DAYS)", service)
        self.assertIn("current_period_end <= current", renewal)
        self.assertIn("Premium subscription expired before scheduled start", renewal)

    def test_active_profile_and_reminders_share_final_visual_language(self) -> None:
        js = (DASHBOARD / "final-premium-6f3.js").read_text(encoding="utf-8")
        css = (DASHBOARD / "final-premium-6f3.css").read_text(encoding="utf-8")
        for marker in (
            "Weekly Premium",
            "Exact expiry",
            "Linked accounts",
            "Lipana · M-Pesa",
            "Verified periods",
            "Premium renewal reminder",
        ):
            self.assertIn(marker, js)
        for selector in (
            ".premium-page",
            ".premium-plan-card",
            ".premium-payment-card",
            ".premium-countdown",
            ".premium-profile",
            ".premium-reminder",
        ):
            self.assertIn(selector, css)
        self.assertIn("@media(max-width:720px)", css)
        self.assertIn("@media(max-width:420px)", css)
        self.assertIn("#020714", css)
        self.assertIn("#52e8ff", css)

    def test_mpesa_only_ui_has_no_card_checkout(self) -> None:
        js = (DASHBOARD / "final-premium-6f3.js").read_text(encoding="utf-8")
        self.assertNotIn("Flutterwave", js)
        self.assertNotIn("USD 2", js)
        self.assertNotIn("credit card", js.lower())
        self.assertNotIn("debit card", js.lower())

    def test_backend_premium_gate_and_lipana_verification_remain_authoritative(self) -> None:
        access = (ROOT / "app" / "premium_access_api.py").read_text(encoding="utf-8")
        lipana = (ROOT / "app" / "lipana_mpesa_action6b.py").read_text(encoding="utf-8")
        worker = (ROOT / "app" / "premium_worker_guard.py").read_text(encoding="utf-8")
        self.assertIn('code": "PREMIUM_SUBSCRIPTION_REQUIRED"', access)
        self.assertIn("premium_write_requires_access", access)
        self.assertIn('client.webhooks.verify(payload, signature, secret)', lipana)
        self.assertIn("_verified_provider_transaction", lipana)
        self.assertIn("Lipana transaction amount did not match KES 250", lipana)
        self.assertIn("premium", worker.lower())

    def test_build_declares_one_frontend_authority_and_6f3_admission(self) -> None:
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertIn('frontend_runtime: "full-vps-final-ui-6f3"', build)
        self.assertIn('ui_authority: "final-ui-shell-v2"', build)
        self.assertIn('premium_bootstrap: "final-premium-6f3"', build)
        self.assertIn('premium_runtime_admission: "unpaid-users-do-not-load-shell-or-realtime-v1"', build)
        self.assertIn('premium_unlock_authority: "verified-server-entitlement-only-v1"', build)
        self.assertIn('production_asset_policy: "final-authority-whitelist-only"', build)
        self.assertIn('legacy_ui_shipped: false', build)
        self.assertIn('netlify_runtime_loaded: false', build)
        self.assertIn('"final-premium-6f3.css"', build)
        self.assertIn('"final-premium-6f3.js"', build)

    def test_existing_product_routes_remain_wired_under_premium_admission(self) -> None:
        shell = (DASHBOARD / "final-ui-shell-v2.js").read_text(encoding="utf-8")
        for route in (
            'json("/me/accounts")',
            'json("/me/trades/today?limit=5000")',
            'json("/me/automation-schedules?limit=80")',
            'json("/me/custom-strategy")',
            'json("/me/text-to-strategy/compile"',
            'json("/me/resume-trading"',
            'json("/me/switch-account"',
        ):
            self.assertIn(route, shell)
        for page in ("Strategy Builder", "Text to Strategy", "Strategy Ready", "Schedule Trading", "Live Runs"):
            self.assertIn(page, shell)

    def test_javascript_syntax_is_valid(self) -> None:
        for path in (
            DASHBOARD / "final-premium-6f3.js",
            DASHBOARD / "final-ui-shell-v2.js",
            DASHBOARD / "vps-api-boundary-v2.js",
            DASHBOARD / "vps-realtime-client-v2.js",
            ROOT / "scripts" / "build-vps.mjs",
        ):
            result = subprocess.run(
                ["node", "--check", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=f"{path.name}\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
