from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ExactBuilderLiveDiagnosticsTests(unittest.TestCase):
    def test_frontend_build_order_is_fail_closed(self) -> None:
        dockerfile = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
        browser_start = dockerfile.rfind("node scripts/finalize-browser-direct-start-v1.mjs")
        preview = dockerfile.rfind("node scripts/prepare-exact-builder-preview-v1.mjs")
        exact = dockerfile.rfind("node scripts/finalize-exact-builder-live-diagnostics-v2.mjs")
        terminal = dockerfile.rfind("node scripts/finalize-terminal-stop-only-v1.mjs")
        self.assertGreater(browser_start, -1)
        self.assertGreater(preview, browser_start)
        self.assertGreater(exact, preview)
        self.assertGreater(terminal, exact)
        for script in (
            "prepare-exact-builder-preview-v1.mjs",
            "finalize-exact-builder-live-diagnostics-v2.mjs",
            "finalize-terminal-stop-only-v1.mjs",
        ):
            self.assertIn(f"node --check scripts/{script}", dockerfile)

    def test_canonical_builder_payload_powers_review(self) -> None:
        prepare = (ROOT / "scripts/prepare-exact-builder-preview-v1.mjs").read_text(encoding="utf-8")
        exact = (ROOT / "scripts/finalize-exact-builder-live-diagnostics-v2.mjs").read_text(encoding="utf-8")
        self.assertIn("function exactStrategyPreview()", prepare)
        self.assertIn("builderSnapshot()", prepare)
        self.assertIn("exactStrategyPreview?.()", exact)

    def test_finalizer_observes_routed_exact_conditions(self) -> None:
        source = (ROOT / "scripts/finalize-exact-builder-live-diagnostics-v2.mjs").read_text(encoding="utf-8")
        for marker in (
            "function conditionDiagnostic(condition, history)",
            "conditionMatches(c, history)",
            'strategyMatches(history, route = activeExecutionRoute(), symbol = "")',
            "route_key",
            "waiting for history",
            "observed",
            "private_reconnect_attempts",
            "diagnostics() {",
            "Waiting for exact Builder history",
            "Exact Builder condition not met",
        ):
            self.assertIn(marker, source)

    def test_only_explicit_terminal_signals_may_stop_remote_browser_run(self) -> None:
        exact = (ROOT / "scripts/finalize-exact-builder-live-diagnostics-v2.mjs").read_text(encoding="utf-8")
        terminal = (ROOT / "scripts/finalize-terminal-stop-only-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("payload?.hard_stop === true || terminalStatus", exact)
        self.assertIn("stopped_take_profit", terminal)
        self.assertIn("stopped_stop_loss", terminal)
        self.assertIn("stopped_manual", terminal)
        self.assertIn('"hard_stopped",', terminal)  # forbidden-marker assertion in final terminal pass
        self.assertIn("generic_server_state=false", terminal)

    def test_release_key_is_cache_busted(self) -> None:
        source = (ROOT / "scripts/finalize-exact-builder-live-diagnostics-v2.mjs").read_text(encoding="utf-8")
        self.assertIn("20260821-exact-builder-diagnostics-v6", source)
        self.assertIn("20260821-exact-builder-review-v6", source)


if __name__ == "__main__":
    unittest.main()
