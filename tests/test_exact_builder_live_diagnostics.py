from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ExactBuilderLiveDiagnosticsTests(unittest.TestCase):
    def test_frontend_build_runs_exact_diagnostics_last(self) -> None:
        dockerfile = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
        browser_start = dockerfile.rfind("node scripts/finalize-browser-direct-start-v1.mjs")
        exact = dockerfile.rfind("node scripts/finalize-exact-builder-live-diagnostics-v1.mjs")
        self.assertGreater(browser_start, -1)
        self.assertGreater(exact, browser_start)
        self.assertIn("node --check scripts/finalize-exact-builder-live-diagnostics-v1.mjs", dockerfile)

    def test_finalizer_uses_canonical_builder_payload_for_review(self) -> None:
        source = (ROOT / "scripts/finalize-exact-builder-live-diagnostics-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("function exactStrategyPreview()", source)
        self.assertIn("builderSnapshot()", source)
        self.assertIn("exactStrategyPreview?.()", source)
        self.assertIn("savedSummary(exactStrategy", source)

    def test_finalizer_exposes_exact_condition_observations(self) -> None:
        source = (ROOT / "scripts/finalize-exact-builder-live-diagnostics-v1.mjs").read_text(encoding="utf-8")
        for marker in (
            "function conditionDiagnostic(condition, history)",
            "waiting for history",
            "observed",
            "private_reconnect_attempts",
            "diagnostics() {",
            "Waiting for exact Builder history",
            "Exact Builder condition not met",
        ):
            self.assertIn(marker, source)

    def test_only_terminal_stop_signals_may_stop_remote_browser_run(self) -> None:
        source = (ROOT / "scripts/finalize-exact-builder-live-diagnostics-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("payload?.hard_stop === true || terminalStatus", source)
        self.assertIn("generic enabled=false remote stop survived", source)
        self.assertIn("stopped_take_profit", source)
        self.assertIn("stopped_stop_loss", source)
        self.assertIn("stopped_manual", source)

    def test_release_key_is_cache_busted(self) -> None:
        source = (ROOT / "scripts/finalize-exact-builder-live-diagnostics-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("20260820-exact-builder-diagnostics-v5", source)
        self.assertIn("20260820-exact-builder-review-v5", source)


if __name__ == "__main__":
    unittest.main()
