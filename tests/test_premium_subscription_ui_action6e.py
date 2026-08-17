from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PremiumSubscriptionUiAction6ETests(unittest.TestCase):
    def test_ui_is_mpesa_only_and_uses_existing_server_authorities(self) -> None:
        js = (ROOT / "dashboard" / "premium-subscription-action6e.js").read_text(
            encoding="utf-8"
        )
        for marker in (
            'api("/me/premium-access")',
            'api("/me/premium-access/payment-options")',
            'api("/me/premium-access/mpesa/stk-push"',
            '/me/premium-access/mpesa/payments/${encodeURIComponent(state.payment.id)}',
            'api("/me/premium-access/mpesa/payments/latest")',
            'api("/me/premium-access/renewal-history?limit=12")',
            "Premium Access Required",
            "Renew Premium with M-Pesa",
            "Payment verified. Premium is active.",
            "No Premium time was added.",
            "Manual M-Pesa after expiry",
            "KES 250",
            "7 days",
            "Lipana",
        ):
            self.assertIn(marker, js)

        self.assertNotIn("flutterwave", js.lower())
        self.assertNotIn("card payment", js.lower())
        self.assertNotIn("localStorage", js)
        self.assertIn("sessionStorage", js)
        self.assertIn("idempotency_key: newIdempotencyKey()", js)

    def test_stk_prompt_never_counts_as_payment_success_by_itself(self) -> None:
        js = (ROOT / "dashboard" / "premium-subscription-action6e.js").read_text(
            encoding="utf-8"
        )
        success = js.split("function paymentIsSuccess", 1)[1].split(
            "function paymentIsFailure", 1
        )[0]
        self.assertIn("premium?.active", success)
        self.assertIn("payment?.activated", success)
        self.assertIn('=== "success"', success)
        self.assertNotIn('payload?.success', success)

        submit = js.split("async function submitPayment", 1)[1].split(
            "function resetPaymentFlow", 1
        )[0]
        self.assertIn("paymentIsSuccess(payload)", submit)
        self.assertIn("schedulePoll(800)", submit)

    def test_exact_expiry_countdown_is_display_only_and_rechecks_server(self) -> None:
        js = (ROOT / "dashboard" / "premium-subscription-action6e.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("const end = asTime(state.premium?.expires_at);", js)
        self.assertIn("if (state.premium?.active && seconds <= 0 && !state.loading) refreshPremium(true);", js)
        self.assertIn("PASSIVE_REFRESH_MS = 30000", js)
        self.assertIn("POLL_MS = 2500", js)
        self.assertIn("provider_uncertain", js)
        self.assertIn("Do not send another payment", js)

    def test_profile_and_reminder_views_are_present_without_replacing_main_shell(self) -> None:
        js = (ROOT / "dashboard" / "premium-subscription-action6e.js").read_text(
            encoding="utf-8"
        )
        css = (ROOT / "dashboard" / "premium-subscription-action6e.css").read_text(
            encoding="utf-8"
        )
        for marker in (
            "foa-premium-profile-card",
            "Premium payment history",
            "Verified periods only",
            "Premium renewal reminder",
            "data-premium-open-profile",
            "foa:automation-route",
            ".foa-automation-bell",
        ):
            self.assertIn(marker, js)
        for marker in (
            ".foa-premium-overlay",
            ".foa-premium-profile-card",
            ".foa-premium-reminder",
            ".foa-premium-countdown",
            "@media (max-width: 420px)",
        ):
            self.assertIn(marker, css)

    def test_full_vps_build_installs_action6e_assets_and_mpesa_only_metadata(self) -> None:
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertIn("/premium-subscription-action6e.css?v=20260817-1", build)
        self.assertIn("/premium-subscription-action6e.js?v=20260817-2", build)
        self.assertIn('premium_ui: "mpesa-weekly-subscription-action6e-v1"', build)
        self.assertIn('premium_prices: "KES250-mpesa-only-v1"', build)
        self.assertIn('premium_payment: "lipana-stk-verified-webhook-v1"', build)
        self.assertNotIn('premium_prices: "KES250-or-USD2-v1"', build)

    def test_javascript_syntax_is_valid(self) -> None:
        result = subprocess.run(
            [
                "node",
                "--check",
                str(ROOT / "dashboard" / "premium-subscription-action6e.js"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
