from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FinalUi6F2Tests(unittest.TestCase):
    def test_direct_vps_document_has_only_6f2_runtime(self) -> None:
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn('frontend-runtime" content="direct-vps-final-ui-6f2"', html)
        self.assertIn('/vps-api-boundary-v2.js?v=20260817-6f2-1', html)
        self.assertIn('/deriv-quill-icons-v2.js?v=2.4.18', html)
        self.assertIn('/vps-realtime-client-v2.js?v=20260817-6f2-1', html)
        self.assertIn('/final-ui-shell-v2.css?v=20260817-6f2-1', html)
        self.assertIn('/final-ui-shell-v2.js?v=20260817-6f2-1', html)
        for retired in (
            "/netlify-api-boundary.js",
            "/netlify-realtime-client.js",
            "/final-ui-shell-v1.css",
            "/final-ui-shell-v1.js",
            "/ui/dashboard-v2.js",
            "automation-home-v1",
            "premium-subscription-action6e",
        ):
            self.assertNotIn(retired, html)

    def test_approved_screens_are_functional_not_scaffolds(self) -> None:
        js = (ROOT / "dashboard" / "final-ui-shell-v2.js").read_text(encoding="utf-8")
        for text in (
            "Home of Automation",
            "Strategy Builder",
            "Text to Strategy",
            "Strategy Ready",
            "Schedule Trading",
            "Choose your timezone",
            "Profile & Settings",
            "Live Runs",
            "250 words",
            "Best possible interpretation",
            "Unsupported or adjusted items",
        ):
            self.assertIn(text, js)
        for route in (
            'json("/me")',
            'json("/me/accounts")',
            'json("/me/trades/today?limit=5000")',
            'json("/me/trading-lifecycle")',
            'json("/me/automation-schedules?limit=80")',
            'json("/me/automation-preferences")',
            'json("/me/custom-strategy")',
            'json("/me/text-to-strategy/compile"',
        ):
            self.assertIn(route, js)
        self.assertNotIn("Coming soon", js)
        self.assertNotIn("mockTrades", js)
        self.assertNotIn("fakeTrades", js)

    def test_new_run_panel_matches_transaction_ledger_contract(self) -> None:
        js = (ROOT / "dashboard" / "final-ui-shell-v2.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "final-ui-shell-v2.css").read_text(encoding="utf-8")
        for text in (
            "Type",
            "Entry / Exit spot",
            "Stake and P/L",
            "Total stake",
            "Total payout",
            "No. of runs",
            "Contracts lost",
            "Contracts won",
            "Total profit/loss",
            "run-account-select",
            "managed_account_id",
        ):
            self.assertIn(text, js)
        for icon in (
            'quill("over")',
            'quill("under")',
            'quill("demoAccount")',
            'quill("realAccount")',
            'quill("usd"',
            'quill("volatility")',
        ):
            self.assertIn(icon, js)
        self.assertIn("entry_tick", js)
        self.assertIn("exit_tick", js)
        self.assertIn("trade.profit", js)
        self.assertIn("trade.payout", js)
        self.assertIn(".run-ledger", css)
        self.assertIn(".run-summary", css)
        self.assertIn(".run-row", css)

    def test_official_deriv_quill_icons_are_pinned_and_exported_locally(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        exporter = (ROOT / "scripts" / "export-deriv-quill-icons-v2.mjs").read_text(encoding="utf-8")
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertEqual(package["dependencies"]["@deriv/quill-icons"], "2.4.18")
        self.assertIn('@deriv/quill-icons/Accounts', exporter)
        self.assertIn('@deriv/quill-icons/Currencies', exporter)
        self.assertIn('@deriv/quill-icons/Markets', exporter)
        self.assertIn('@deriv/quill-icons/TradeTypes', exporter)
        self.assertIn('repository: "deriv-com/quill-icons"', exporter)
        for key in ("over", "under", "demoAccount", "realAccount", "usd", "volatility"):
            self.assertIn(f"{key}:", exporter)
        self.assertIn('deriv_icons: "official-quill-icons-2.4.18-build-time-static-svg"', build)
        self.assertIn('await import("./export-deriv-quill-icons-v2.mjs")', build)

    def test_specific_linked_account_switch_is_selection_only(self) -> None:
        source = (ROOT / "app" / "final_linked_accounts_6f2.py").read_text(encoding="utf-8")
        vps = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/me/accounts")', source)
        self.assertIn('@app.post("/me/switch-account")', source)
        self.assertIn("managed_account_id", source)
        self.assertIn("login_identity_from_payload", source)
        self.assertIn("set_client_session_account", source)
        self.assertIn('"trading_state_mutated": False', source)
        for forbidden in (
            "set_managed_account_enabled(",
            "_reset_risk_state(",
            "write_custom_strategy(",
            "write_strategy(",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("install_final_linked_accounts_6f2(app)", vps)
        self.assertLess(
            vps.index("install_final_linked_accounts_6f2(app)"),
            vps.index("install_premium_access_action6a(app)"),
        )

    def test_browser_never_becomes_deriv_purchase_authority(self) -> None:
        js = (ROOT / "dashboard" / "final-ui-shell-v2.js").read_text(encoding="utf-8")
        boundary = (ROOT / "dashboard" / "vps-api-boundary-v2.js").read_text(encoding="utf-8")
        self.assertIn('json("/me/custom-strategy"', js)
        self.assertIn('json("/me/resume-trading"', js)
        self.assertIn('json("/me/automation-schedules"', js)
        for forbidden in (
            "api.derivws.com",
            "proposal_open_contract",
            '"proposal":',
            '"buy":',
            "new WebSocket(\"wss://ws.derivws.com",
        ):
            self.assertNotIn(forbidden, js)
        self.assertIn("existing recovery toggle", boundary)
        self.assertIn("payload.execution_settings.martingale_enabled = current", boundary)
        self.assertIn("custom_strategy: payload.strategy_snapshot", boundary)

    def test_direct_vps_build_whitelists_only_6f2_assets(self) -> None:
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
        self.assertIn('deployment_topology: "direct-vps-only"', build)
        self.assertIn('ui_authority: "final-ui-shell-v2"', build)
        self.assertIn('run_panel: "deriv-transaction-ledger-v1"', build)
        self.assertIn('linked_account_selector: "specific-linked-options-account-v1"', build)
        self.assertIn('production_asset_policy: "new-shell-whitelist-only"', build)
        self.assertIn('legacy_ui_shipped: false', build)
        self.assertIn('netlify_runtime_loaded: false', build)
        for asset in (
            '"index.html"',
            '"final-ui-shell-v2.css"',
            '"final-ui-shell-v2.js"',
            '"vps-api-boundary-v2.js"',
            '"vps-realtime-client-v2.js"',
        ):
            self.assertIn(asset, build)
        self.assertNotIn('"final-ui-shell-v1.js",', build)
        self.assertNotIn('"vps-api-boundary.js",', build)
        self.assertIn("npm install --ignore-scripts --no-audit --no-fund", dockerfile)
        self.assertIn("RUN node scripts/build-vps.mjs", dockerfile)

    def test_6f2_javascript_is_syntax_valid(self) -> None:
        for path in (
            ROOT / "dashboard" / "final-ui-shell-v2.js",
            ROOT / "dashboard" / "vps-api-boundary-v2.js",
            ROOT / "dashboard" / "vps-realtime-client-v2.js",
            ROOT / "scripts" / "export-deriv-quill-icons-v2.mjs",
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
            self.assertEqual(
                result.returncode,
                0,
                msg=f"{path.name}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
