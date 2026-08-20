from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BrowserDerivDirectV3Tests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_live_provider_hot_path_is_browser_to_deriv(self) -> None:
        finalizer = self.text("scripts/finalize-oauth-execution-handoff-v1.mjs")
        for marker in (
            "https://api.derivws.com/trading/v1/options/accounts/",
            'Authorization: `Bearer ${auth.accessToken}`',
            '"Deriv-App-ID": auth.derivAppId',
            'credentials: "omit"',
            "requestDirectDerivOtp",
            "connectPrivate",
            "/me/direct-execution/receipt",
        ):
            self.assertIn(marker, finalizer)

        # Legacy server hot-path strings intentionally remain in the build script
        # only as fail-closed assertions against the generated production engine.
        guard = finalizer.split("for (const forbidden of [", 1)[1].split(
            "write(enginePath, engine);", 1
        )[0]
        for forbidden in (
            'apiPath("/me/direct-execution/session")',
            'apiPath("/me/direct-execution/heartbeat")',
            'apiPath("/me/direct-execution/yield")',
            "VPS continuity takeover activated automatically",
        ):
            self.assertIn(forbidden, guard)
        self.assertIn("if (engine.includes(forbidden)) throw new Error", guard)

    def test_bootstrap_exposes_access_token_only(self) -> None:
        api = self.text("app/browser_direct_deriv_transport_v3.py")
        bootstrap = api.split("def browser_direct_bootstrap", 1)[1].split(
            '@app.post("/me/direct-execution/session")', 1
        )[0]
        self.assertIn('"access_token": token', bootstrap)
        self.assertIn('"refresh_token_exposed": False', bootstrap)
        self.assertNotIn('"refresh_token":', bootstrap)
        self.assertIn('"server_otp": False', bootstrap)
        self.assertIn('"server_proposal": False', bootstrap)
        self.assertIn('"server_buy": False', bootstrap)

    def test_vps_receives_two_contract_events_not_tick_stream(self) -> None:
        api = self.text("app/browser_direct_deriv_transport_v3.py")
        finalizer = self.text("scripts/finalize-oauth-execution-handoff-v1.mjs")
        self.assertIn('event not in {"OPEN", "SETTLED"}', api)
        self.assertIn('if (!contractId || !["OPEN", "SETTLED"].includes(event)) return;', finalizer)
        self.assertIn("queueMicrotask(() => sendTradeReceipt(next))", finalizer)
        self.assertIn('"provider_contacted": False', api)

    def test_periodic_browser_checkpoint_and_heartbeat_writes_are_gone(self) -> None:
        checkpoint = self.text("dashboard/direct-continuity-checkpoint-v1.js")
        finalizer = self.text("scripts/finalize-oauth-execution-handoff-v1.mjs")
        self.assertNotIn("setInterval(checkpoint", checkpoint)
        self.assertNotIn("XMLHttpRequest", checkpoint)
        self.assertNotIn("/direct-execution/checkpoint", checkpoint)
        self.assertIn("trade_receipts_only: true", checkpoint)
        self.assertIn("heartbeatOnce(_epoch)", finalizer)

        # There is no heartbeat request in the generated engine. The finalizer
        # names the legacy path solely so the production build fails if it survives.
        guard = finalizer.split("for (const forbidden of [", 1)[1].split(
            "write(enginePath, engine);", 1
        )[0]
        self.assertIn('apiPath("/me/direct-execution/heartbeat")', guard)
        self.assertIn("if (engine.includes(forbidden)) throw new Error", guard)

    def test_browser_direct_accounts_never_promote_to_vps_provider_sessions(self) -> None:
        offload = self.text("app/browser_direct_worker_offload_v3.py")
        installer = self.text("app/stale_split_basis_reconciliation_authority.py")
        self.assertIn("_promote_expired_browser_leases = no_browser_takeover", offload)
        self.assertIn("mode_lock.direct_browser_lease_fresh = _browser_direct_owned", offload)
        self.assertIn("browser_runtime.direct_browser_lease_fresh = _browser_direct_owned", offload)
        self.assertIn("lease_preservation.direct_browser_lease_fresh = _browser_direct_owned", offload)
        self.assertIn("worker_fence.direct_browser_lease_fresh = _browser_direct_owned", offload)
        self.assertIn("install_browser_direct_worker_offload_v3()", installer)
        self.assertGreater(
            installer.rfind("install_browser_direct_worker_offload_v3()"),
            installer.rfind("install_browser_direct_lease_preservation_authority()"),
        )

    def test_stop_clear_and_scheduled_server_execution_remain_separate(self) -> None:
        api = self.text("app/browser_direct_deriv_transport_v3.py")
        worker = self.text("app/browser_direct_worker_offload_v3.py")
        backend = self.text("app/vps_backend_api.py")
        self.assertIn("clear_direct_hard_stop(session, managed_id)", api)
        self.assertIn("run_history_revision:v1:", self.text("app/vps_cross_device_runtime_sync.py"))
        self.assertIn("scheduled_server_execution_preserved=true", worker)
        self.assertIn("Scheduling remains server-owned", backend)
        self.assertIn('production_architecture = "browser_deriv_direct_v3"', backend)

    def test_status_reason_cannot_exceed_database_column(self) -> None:
        authority = self.text("app/browser_direct_lease_preservation_authority.py")
        self.assertIn(")[:160]", authority)
        self.assertNotIn(")[:320]", authority)


if __name__ == "__main__":
    unittest.main()
