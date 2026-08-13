from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TradesStartStopToggleTests(unittest.TestCase):
    def test_toggle_uses_authoritative_lifecycle_state(self) -> None:
        source = (ROOT / "dashboard" / "trades-start-stop-toggle.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('fetch("/me/trading-lifecycle"', source)
        self.assertIn('postLifecycle("/me/stop-trading", {})', source)
        self.assertIn('postLifecycle("/me/resume-trading", { mode: "continue" })', source)
        self.assertIn('button.dataset.tradingState = state', source)
        self.assertIn('event.stopImmediatePropagation()', source)
        subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "trades-start-stop-toggle.js")],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_button_labels_are_opposites(self) -> None:
        css = (ROOT / "dashboard" / "trades-start-stop-toggle.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-trading-state="stopped"', css)
        self.assertIn('content: "Start Trading"', css)
        self.assertIn('data-trading-state="running"', css)
        self.assertIn('content: "Stop Trading"', css)

    def test_toggle_assets_load_after_previous_final_authority(self) -> None:
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertGreater(
            index.index('./trades-start-stop-toggle.css'),
            index.index('./final-dashboard-authority.css'),
        )
        self.assertGreater(
            index.index('./trades-start-stop-toggle.js'),
            index.index('./final-dashboard-authority.js'),
        )

    def test_server_decides_fresh_start_vs_pause_resume(self) -> None:
        source = (ROOT / "app" / "lifecycle_reset_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('previous in STOPPED_STATUSES', source)
        self.assertIn('requested_mode == "start_again"', source)
        self.assertIn('mode="start_again"', source)
        self.assertIn('@app.post("/me/resume-trading")', source)


if __name__ == "__main__":
    unittest.main()
