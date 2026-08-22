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

    def test_history_count_covers_primary_and_after_loss_and_caps_at_1000(self) -> None:
        finalizer = self.text("scripts/finalize-authenticated-history-stream-v1.mjs")
        self.assertIn("state.strategy?.conditions", finalizer)
        self.assertIn("state.strategy?.result_routing?.after_loss?.conditions", finalizer)
        self.assertIn("Math.min(1000", finalizer)

    def test_finalizer_is_last_browser_engine_mutation(self) -> None:
        dockerfile = self.text("Dockerfile.frontend")
        self.assertIn("COPY scripts/finalize-authenticated-history-stream-v1.mjs", dockerfile)
        self.assertIn("node --check scripts/finalize-authenticated-history-stream-v1.mjs", dockerfile)
        self.assertGreater(
            dockerfile.rfind("node scripts/finalize-authenticated-history-stream-v1.mjs"),
            dockerfile.rfind("node scripts/finalize-fetch-timeout-helper-v1.mjs"),
        )
        self.assertIn("20260823-auth-history-stream-v1", finalizer := self.text("scripts/finalize-authenticated-history-stream-v1.mjs"))


if __name__ == "__main__":
    unittest.main()
