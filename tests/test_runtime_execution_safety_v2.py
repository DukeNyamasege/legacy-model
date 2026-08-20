from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RuntimeExecutionSafetyV2Tests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_backend_installs_account_global_runtime_sync(self) -> None:
        backend = self.read("app/vps_backend_api.py")
        sync = self.read("app/vps_cross_device_runtime_sync.py")
        self.assertIn("install_vps_cross_device_runtime_sync(app)", backend)
        self.assertIn('@app.get("/me/runtime-sync"', sync)
        self.assertIn('"history_revision"', sync)
        self.assertIn('"execution_status_reason"', sync)
        self.assertIn('"purchase_allowed"', sync)
        self.assertIn("_TRANSIENT_SESSION_RETRY_SECONDS = 45.0", sync)
        self.assertIn("direct_api._provider_otp = _resilient_provider_otp", sync)

    def test_clear_is_history_only_and_cross_device(self) -> None:
        controls = self.read("app/vps_fast_execution_controls.py")
        sync = self.read("app/vps_cross_device_runtime_sync.py")
        self.assertIn("HISTORY ONLY", controls)
        self.assertIn("financial_state_preserved", controls)
        self.assertIn("run_history_revision:v1:", sync)
        self.assertIn("ACCOUNT_HISTORY_CLEAR_REVISION", sync)
        self.assertNotIn("state.recovery_loss_debt = 0.0", controls.split("def fast_clear_personal_trades", 1)[1])

    def test_only_tp_sl_or_manual_hard_stop_are_terminal(self) -> None:
        lifecycle = self.read("app/tp_sl_manual_only_authority.py")
        self.assertIn('_TARGET_STOPS = {"take_profit", "stop_loss"}', lifecycle)
        self.assertIn("direct_hard_stop_active", lifecycle)
        self.assertIn("TP_SL_MANUAL_ONLY_STOP_BLOCKED", lifecycle)
        self.assertIn("TP_SL_MANUAL_ONLY_STALE_AUTO_STOP_REPAIRED", lifecycle)

    def test_final_frontend_safety_gate_runs_after_runtime_coherence(self) -> None:
        dockerfile = self.read("Dockerfile.frontend")
        coherence = dockerfile.rfind("node scripts/finalize-runtime-coherence-v1.mjs")
        safety = dockerfile.rfind("node scripts/finalize-runtime-safety-v2.mjs")
        self.assertGreater(coherence, -1)
        self.assertGreater(safety, coherence)
        self.assertIn("COPY scripts/finalize-runtime-safety-v2.mjs", dockerfile)

    def test_frontend_gate_enforces_cross_device_stop_clear_and_diagnostics(self) -> None:
        finalizer = self.read("scripts/finalize-runtime-safety-v2.mjs")
        for marker in (
            'const STATUS_URL = "/api/me/runtime-sync";',
            "applyRemoteStop(payload)",
            "applyHistoryRevision(payload)",
            "clear_through: clearLocalTradesThrough",
            "NO TRADE PURCHASED AFTER 60 SECONDS",
            "Strategy condition not met",
            "last_execution_error",
            "direct-no-purchase-diagnostic",
            "transaction-empty-v10",
            "raw?.diagnostic === true",
            "52000",
        ):
            self.assertIn(marker, finalizer)

    def test_running_browser_must_not_skip_global_status_poll(self) -> None:
        finalizer = self.read("scripts/finalize-runtime-safety-v2.mjs")
        obsolete = '''  async function readServerStatus() {\\n    if (browserRunning()) {\\n      state.serverActive = false;'''
        self.assertIn(obsolete, finalizer)
        self.assertIn("running browser must still read global status", finalizer)


if __name__ == "__main__":
    unittest.main()
