from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AuthenticatedHistoryStreamTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_active_trading_uses_one_authenticated_history_and_live_stream(self) -> None:
        finalizer = self.text("scripts/finalize-authenticated-history-stream-v1.mjs")
        for marker in (
            'function sendAuthenticatedHistoryStream(symbol)',
            'ticks_history: symbol',
            'count: required',
            'end: "latest"',
            'style: "ticks"',
            'subscribe: 1',
            'state.marketHistoryRequests.get(reqId)',
            'seedHistory(marketHistory.symbol, prices, times)',
            'state.marketDataKind = "private"',
            'return Promise.resolve(null)',
            'public WebSocket constructor survived finalization',
            'echo_req_dependency=false',
        ):
            self.assertIn(marker, finalizer)

    def test_start_arms_before_authenticated_history_stream(self) -> None:
        finalizer = self.text("scripts/finalize-authenticated-history-stream-v1.mjs")
        self.assertIn('armInBackground(state.epoch, strategy);', finalizer)
        self.assertIn('arm-first manual Start', finalizer)
        self.assertIn('pre-arm public Start path survived finalization', finalizer)
        self.assertIn('start_arm_first=true', finalizer)

    def test_reconciled_start_synchronizes_the_financial_buy_fence(self) -> None:
        finalizer = self.text("scripts/finalize-authenticated-history-stream-v1.mjs")
        self.assertIn('function acceptReconciledArm(epoch, payload)', finalizer)
        self.assertIn('String(payload?.epoch || "") === expectedEpoch', finalizer)
        self.assertIn('const browserOwned = owner === "browser"', finalizer)
        self.assertIn('accept_reconciled_arm: acceptReconciledArm', finalizer)
        self.assertIn('financialFence.accept_reconciled_arm(epoch, payload)', finalizer)
        self.assertIn('Financial ownership fence rejected the reconciled Start epoch', finalizer)
        self.assertIn('reconciled_financial_fence_sync=true', finalizer)

    def test_history_count_covers_primary_and_after_loss_and_caps_at_1000(self) -> None:
        finalizer = self.text("scripts/finalize-authenticated-history-stream-v1.mjs")
        self.assertIn("state.strategy?.conditions", finalizer)
        self.assertIn("state.strategy?.result_routing?.after_loss?.conditions", finalizer)
        self.assertIn("Math.min(1000", finalizer)

    def test_finalizer_is_last_browser_engine_and_fence_mutation(self) -> None:
        dockerfile = self.text("Dockerfile.frontend")
        self.assertIn("COPY scripts/finalize-authenticated-history-stream-v1.mjs", dockerfile)
        self.assertIn("node --check scripts/finalize-authenticated-history-stream-v1.mjs", dockerfile)
        self.assertGreater(
            dockerfile.rfind("node scripts/finalize-authenticated-history-stream-v1.mjs"),
            dockerfile.rfind("node scripts/finalize-fetch-timeout-helper-v1.mjs"),
        )
        finalizer = self.text("scripts/finalize-authenticated-history-stream-v1.mjs")
        self.assertIn("20260823-auth-history-start-v2", finalizer)
        self.assertIn("20260823-reconciled-ownership-v2", finalizer)


if __name__ == "__main__":
    unittest.main()
