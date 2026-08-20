from __future__ import annotations

from pathlib import Path
import unittest

from app.browser_direct_lease_preservation_authority import _clean_automatic_reason


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "app" / "browser_direct_lease_preservation_authority.py"
INSTALLER = ROOT / "app" / "stale_split_basis_reconciliation_authority.py"
RUNTIME_FINALIZER = ROOT / "scripts" / "finalize-runtime-coherence-v1.mjs"
FINANCIAL_FENCE = ROOT / "dashboard" / "direct-financial-fence-v1.js"


class BrowserDirectLeasePreservationTests(unittest.TestCase):
    def test_automatic_reason_cannot_claim_trading_stopped(self) -> None:
        cleaned = _clean_automatic_reason(
            "Trading stopped: authenticated Deriv trading session is not connected"
        )
        self.assertEqual(
            cleaned,
            "authenticated Deriv trading session is not connected",
        )
        self.assertNotIn("stopped", cleaned.lower())

    def test_worker_retry_uses_real_browser_heartbeat_not_retry_time(self) -> None:
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("last_heartbeat_at", source)
        self.assertIn("DIRECT_BROWSER_LEASE_SECONDS", source)
        self.assertIn("row.execution_status = DIRECT_BROWSER_STATUS", source)
        self.assertIn("row.execution_status_updated_at = heartbeat_at", source)
        self.assertNotIn("row.execution_status_updated_at = utc_now()", source)
        self.assertIn("BROWSER_DIRECT_LEASE_PRESERVED", source)
        self.assertIn("BROWSER_DIRECT_STATUS_MUTATION_BLOCKED", source)

    def test_tp_sl_and_manual_stop_remain_higher_priority(self) -> None:
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("lifecycle._terminal_allowed", source)
        self.assertIn("direct_hard_stop_active", source)
        self.assertIn('_TARGET_STOPS = {"take_profit", "stop_loss"}', source)

    def test_browser_lease_authority_is_installed_last(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        lifecycle = "install_tp_sl_manual_only_authority()"
        browser = "install_browser_direct_lease_preservation_authority()"
        self.assertIn(lifecycle, source)
        self.assertIn(browser, source)
        self.assertGreater(source.rfind(browser), source.rfind(lifecycle))

    def test_history_hydration_has_one_owner_and_never_triggers_buy(self) -> None:
        finalizer = RUNTIME_FINALIZER.read_text(encoding="utf-8")
        fence = FINANCIAL_FENCE.read_text(encoding="utf-8")
        self.assertIn("state.hydrationPending > 0", fence)
        self.assertIn("single public history hydration owner", finalizer)
        self.assertIn("!window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1", finalizer)
        self.assertIn("Boolean(tick?.__history_hydration)", finalizer)
        self.assertIn("History hydration builds the statistical window only", finalizer)

    def test_live_market_analysis_waits_for_purchase_ready_transport(self) -> None:
        finalizer = RUNTIME_FINALIZER.read_text(encoding="utf-8")
        for marker in (
            "function executionTransportReady()",
            "state.armed",
            "state.privateWs?.readyState === WebSocket.OPEN",
            "financial.buy_allowed !== false",
            "if (!executionTransportReady() || !state.strategy",
            "secure trade channel arming",
            "execution channel ready",
            "execution_ready: executionTransportReady()",
        ):
            self.assertIn(marker, finalizer)

    def test_running_private_session_failures_retry_instead_of_silently_dying(self) -> None:
        finalizer = RUNTIME_FINALIZER.read_text(encoding="utf-8")
        self.assertIn("running private socket automatic retry", finalizer)
        self.assertIn("restoring secure trade session", finalizer)
        self.assertIn("connectPrivate().catch(() => {})", finalizer)

    def test_transactions_finalizer_forbids_two_dom_writers(self) -> None:
        finalizer = RUNTIME_FINALIZER.read_text(encoding="utf-8")
        self.assertIn("directLedgerOwnsTransactions", finalizer)
        self.assertIn("DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6?.refresh?.()", finalizer)
        self.assertIn("unconditional Transactions renderer survived finalization", finalizer)
        self.assertIn("20260820-single-ledger-v16", finalizer)


if __name__ == "__main__":
    unittest.main()
