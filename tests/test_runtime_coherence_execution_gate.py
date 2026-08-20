from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "scripts" / "finalize-runtime-coherence-v1.mjs"
FINANCIAL_FENCE = ROOT / "dashboard" / "direct-financial-fence-v1.js"
ENGINE_SOURCE = ROOT / "dashboard" / "deriv-direct-execution-v1.js"
CONTINUITY_FINALIZER = ROOT / "scripts" / "finalize-execution-continuity-v1.mjs"


class RuntimeCoherenceExecutionGateTests(unittest.TestCase):
    def test_transactions_have_one_final_dom_authority(self) -> None:
        text = FINALIZER.read_text(encoding="utf-8")
        self.assertIn("directLedgerOwnsTransactions", text)
        self.assertIn("DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6?.refresh?.()", text)
        self.assertIn("unconditional Transactions renderer survived finalization", text)
        self.assertIn("20260820-single-ledger-v16", text)

    def test_financial_fence_blocks_buy_during_history_hydration(self) -> None:
        text = FINANCIAL_FENCE.read_text(encoding="utf-8")
        self.assertIn("state.hydrationPending > 0", text)
        self.assertIn("return Date.now() - state.lastAckAt", text)
        self.assertIn("Direct financial ownership is not active", text)

    def test_runtime_finalizer_removes_double_history_hydration_loop(self) -> None:
        text = FINALIZER.read_text(encoding="utf-8")
        self.assertIn("single public history hydration owner", text)
        self.assertIn("!window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1", text)
        self.assertIn("Boolean(tick?.__history_hydration)", text)
        self.assertIn("History hydration builds the statistical window only", text)

    def test_market_subscriptions_require_purchase_ready_transport(self) -> None:
        text = FINALIZER.read_text(encoding="utf-8")
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
            self.assertIn(marker, text)

    def test_runtime_finalizer_accepts_build_order_readiness_export_shapes(self) -> None:
        finalizer = FINALIZER.read_text(encoding="utf-8")
        source = ENGINE_SOURCE.read_text(encoding="utf-8")
        continuity = CONTINUITY_FINALIZER.read_text(encoding="utf-8")
        canonical = "        open_contracts: state.openContracts.size,\n      };"
        self.assertIn(canonical, source)
        self.assertIn("continuity_repair: true", continuity)
        self.assertIn("last_tick_age_ms:", continuity)
        for marker in (
            "const stateExportContinuity =",
            "const stateExportContinuityWithError =",
            "const stateExportContinuityReady =",
            "const stateExportCurrent =",
            "const stateExportWithError =",
            "const stateExportReady =",
            "engine.includes(stateExportContinuity)",
            "engine.includes(stateExportContinuityWithError)",
            "engine.includes(stateExportCurrent)",
            "engine.includes(stateExportWithError)",
            "no supported pre-finalization or installed shape found",
        ):
            self.assertIn(marker, finalizer)

    def test_running_private_session_failures_retry_automatically(self) -> None:
        text = FINALIZER.read_text(encoding="utf-8")
        self.assertIn("running private socket automatic retry", text)
        self.assertIn("if (state.running && !state.ownerLost)", text)
        self.assertIn("connectPrivate().catch(() => {})", text)
        self.assertIn("restoring secure trade session", text)

    def test_source_still_exposes_exact_bug_shape_so_finalizer_is_fail_closed(self) -> None:
        # The build finalizer intentionally owns this production migration. If the
        # source changes, replaceOne will fail the candidate build until this test
        # and the finalizer are updated together rather than silently shipping a
        # second execution path.
        source = ENGINE_SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            'if (kind === "public" && payload.history && payload.echo_req?.ticks_history)',
            source,
        )
        self.assertIn('sendNoWait("public", { ticks: symbol, subscribe: 1 })', source)


if __name__ == "__main__":
    unittest.main()
