from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"


class FinalUi6F3Tests(unittest.TestCase):
    def test_document_loads_testing_controller_without_loading_heavy_shell_directly(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-premium-boot="pending"', html)
        self.assertIn('frontend-runtime" content="direct-vps-final-ui-6f3"', html)
        self.assertIn('frontend-authority" content="final-ui-shell-v2"', html)
        self.assertIn('/final-premium-6f3.css?v=20260817-6f3-1', html)
        self.assertIn('/final-premium-6f3.js?v=20260819-block-workspace-v13', html)
        self.assertIn('/public-testing-runtime-v1.js?v=20260819-banner-removed-v6', html)
        self.assertNotIn('<script src="/vps-realtime-client-v2.js?v=20260817-6f2-1" defer>', html)
        self.assertNotIn('<script src="/final-ui-shell-v2.js?v=20260817-6f2-1" defer>', html)
        self.assertNotIn("premium-subscription-action6e", html)
        self.assertNotIn("netlify", html.lower())

    def test_future_mpesa_premium_flow_is_retained(self) -> None:
        js = (DASHBOARD / "final-premium-6f3.js").read_text(encoding="utf-8")
        for route in (
            'api("/me")', 'api("/me/premium-access")', 'api("/me/premium-access/payment-options")',
            'api("/me/accounts")', 'api("/me/premium-access/mpesa/payments/latest")',
            'api("/me/premium-access/mpesa/stk-push"',
            'api(`/me/premium-access/mpesa/payments/${encodeURIComponent(state.payment.id)}`)',
            'api("/me/premium-access/renewal-status")', 'api("/me/premium-access/renewal-history?limit=8")',
        ):
            self.assertIn(route, js)
        for marker in (
            "KES 250", "7 days", "M-PESA · LIPANA", "DOT & ROT access",
            "signed Lipana callback", "server-side transaction verification", "payment?.activated", "premium?.active",
        ):
            self.assertIn(marker, js)
        self.assertIn("const TESTING_FREE_ACCESS = true;", js)
        self.assertNotIn("api.derivws.com", js)
        self.assertNotIn("proposal_open_contract", js)

    def test_public_testing_controller_is_access_only(self) -> None:
        js = (DASHBOARD / "public-testing-runtime-v1.js").read_text(encoding="utf-8")
        for marker in (
            'window.fetch("/api/me/public-testing-access"', "public_testing_free_access",
            ".premium-reminder", "premium use only", "pay kes 250",
            'execution_authority: "none"', "deriv-direct-execution-v2",
        ):
            self.assertIn(marker, js)
        for forbidden in (
            "wss://", "proposal_open_contract", "ticks: symbol",
            '"[data-run-start]"', '"[data-run-execution-toggle]"',
            '"[data-builder-trade]"', '"[data-start-trading]"',
            'data-run-tab="transactions"', "/me/resume-trading", "/me/stop-trading",
        ):
            self.assertNotIn(forbidden, js)

    def test_testing_price_banner_is_removed_from_the_ui(self) -> None:
        shell = (DASHBOARD / "final-ui-shell-v2.js").read_text(encoding="utf-8")
        testing = (DASHBOARD / "public-testing-runtime-v1.js").read_text(encoding="utf-8")
        mobile_css = (DASHBOARD / "mobile-reference-ui.css").read_text(encoding="utf-8")
        theme = (DASHBOARD / "tutorial-camera-theme-v1.css").read_text(encoding="utf-8")
        combined = "\n".join((shell, testing, mobile_css, theme))
        self.assertNotIn("paid-soon-banner", combined)
        self.assertNotIn("DerivAdmin is free during this testing phase", combined)
        self.assertNotIn("Weekly access will soon be KES 250", combined)

    def test_public_testing_runtime_has_no_deriv_market_or_trading_authority(self) -> None:
        js = (DASHBOARD / "public-testing-runtime-v1.js").read_text(encoding="utf-8")
        self.assertIn("NO Deriv tick mirror", js)
        self.assertIn("deriv-direct-execution-v2", js)
        self.assertNotIn("wss://", js)
        self.assertNotIn("ticks: symbol", js)
        self.assertNotIn('"buy":', js)
        self.assertNotIn("proposal_open_contract", js)
        self.assertNotIn("access_token", js)
        self.assertNotIn("pat_token", js)

    def test_public_testing_bypass_covers_http_worker_expiry_and_schedule_start(self) -> None:
        access = (ROOT / "app" / "public_testing_access.py").read_text(encoding="utf-8")
        vps = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.vps.example").read_text(encoding="utf-8")
        self.assertIn('os.getenv("PUBLIC_TESTING_FREE_ACCESS", "true")', access)
        self.assertIn('os.environ["PREMIUM_ACCESS_ENFORCEMENT"] = "false"', access)
        self.assertIn("scheduler._apply_schedule_strategy = original", access)
        self.assertIn("renewal.run_premium_expiry_cycle = free_testing_expiry_cycle", access)
        self.assertIn('@app.get("/me/public-testing-access"', access)
        self.assertIn("apply_public_testing_premium_bypass()", vps)
        self.assertIn("apply_public_testing_scheduler_bypass()", vps)
        self.assertIn("install_public_testing_access_api(app)", vps)
        self.assertIn("if not public_testing:", worker)
        self.assertIn("install_premium_worker_guard()", worker)
        self.assertIn("PUBLIC_TESTING_FREE_ACCESS=true", env_example)
        self.assertIn("PREMIUM_ACCESS_ENFORCEMENT=true", env_example)

    def test_exact_paid_expiry_logic_remains_available_for_later_launch(self) -> None:
        js = (DASHBOARD / "final-premium-6f3.js").read_text(encoding="utf-8")
        service = (ROOT / "app" / "premium_access_service.py").read_text(encoding="utf-8")
        renewal = (ROOT / "app" / "premium_renewal_action6d.py").read_text(encoding="utf-8")
        self.assertIn("async function exactExpiryReached()", js)
        self.assertIn('const access = await api("/me/premium-access")', js)
        self.assertIn("scheduleExactExpiry", js)
        self.assertIn("timedelta(days=WEEKLY_PERIOD_DAYS)", service)
        self.assertIn("current_period_end <= current", renewal)
        self.assertIn("Premium subscription expired before scheduled start", renewal)

    def test_backend_premium_and_lipana_verification_are_not_deleted(self) -> None:
        access = (ROOT / "app" / "premium_access_api.py").read_text(encoding="utf-8")
        lipana = (ROOT / "app" / "lipana_mpesa_action6b.py").read_text(encoding="utf-8")
        worker = (ROOT / "app" / "premium_worker_guard.py").read_text(encoding="utf-8")
        self.assertIn('code": "PREMIUM_SUBSCRIPTION_REQUIRED"', access)
        self.assertIn("premium_write_requires_access", access)
        self.assertIn('client.webhooks.verify(payload, signature, secret)', lipana)
        self.assertIn("_verified_provider_transaction", lipana)
        self.assertIn("Lipana transaction amount did not match KES 250", lipana)
        self.assertIn("premium", worker.lower())

    def test_build_switches_browser_admission_with_same_testing_flag(self) -> None:
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")
        self.assertIn("PUBLIC_TESTING_FREE_ACCESS", build)
        self.assertIn("testingFlagMarker", build)
        self.assertIn('"public-testing-runtime-v1.js"', build)
        self.assertIn('run_default_tab: "transactions-on-start-v1"', build)
        self.assertIn('instant_run: "one-click-save-if-needed-then-resume-worker-v1"', build)
        self.assertIn('journal_analysis: "live-public-deriv-tick-observability-mirror-v1"', build)
        self.assertIn('journal_financial_authority: "backend-private-websocket-only"', build)
        self.assertIn("ARG PUBLIC_TESTING_FREE_ACCESS=true", dockerfile)
        self.assertIn("PUBLIC_TESTING_FREE_ACCESS: ${PUBLIC_TESTING_FREE_ACCESS:-true}", compose)

    def test_existing_strategy_schedule_and_purchase_flow_remain_wired(self) -> None:
        shell = (DASHBOARD / "final-ui-shell-v2.js").read_text(encoding="utf-8")
        runtime = (ROOT / "app" / "custom_strategy_runtime_api.py").read_text(encoding="utf-8")
        direct = (ROOT / "app" / "custom_strategy_direct_runtime.py").read_text(encoding="utf-8")
        execution = (ROOT / "app" / "account_execution_session.py").read_text(encoding="utf-8")
        for route in (
            'json("/me/accounts")', 'json("/me/trades/today?limit=5000")',
            'json("/me/automation-schedules?limit=80")', 'json("/me/custom-strategy")',
            'json("/me/text-to-strategy/compile"', 'json("/me/resume-trading"', 'json("/me/switch-account"',
        ):
            self.assertIn(route, shell)
        self.assertIn('@app.post("/me/resume-trading")', runtime)
        self.assertIn('row.execution_status = "starting"', runtime)
        self.assertIn("evaluate_custom_strategy", direct)
        self.assertIn("_execute_for_account", direct)
        self.assertIn("execute_real", direct)
        self.assertIn('"proposal": 1', execution)
        self.assertIn('"buy": str(economics.proposal_id)', execution)
        self.assertIn("PURCHASE_CONFIRMED", execution)

    def test_javascript_syntax_is_valid(self) -> None:
        for path in (
            DASHBOARD / "final-premium-6f3.js", DASHBOARD / "final-ui-shell-v2.js",
            DASHBOARD / "public-testing-runtime-v1.js", DASHBOARD / "vps-api-boundary-v2.js",
            DASHBOARD / "vps-realtime-client-v2.js", ROOT / "scripts" / "build-vps.mjs",
        ):
            result = subprocess.run(["node", "--check", str(path)], cwd=ROOT, capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(result.returncode, 0, msg=f"{path.name}\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
