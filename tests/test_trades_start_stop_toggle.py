from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TradesStartStopToggleTests(unittest.TestCase):
    def test_toggle_mirrors_live_dashboard_execution_switch(self) -> None:
        source = (ROOT / "dashboard" / "trades-start-stop-toggle.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('lifecycle?.enabled === true', source)
        self.assertIn('"connecting", "reconnecting"', source)
        self.assertIn('fetch(`/me/trading-lifecycle?_=${Date.now()}`', source)
        self.assertIn('"Cache-Control": "no-cache, no-store, must-revalidate"', source)
        self.assertIn('event.target?.closest?.("[data-main-action]")', source)
        self.assertIn('const POLL_MS = 800', source)
        self.assertIn('postLifecycle("/me/stop-trading", {})', source)
        self.assertIn('postLifecycle("/me/resume-trading", { mode: startMode(lifecycle) })', source)
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

    def test_special_all_even_odd_hide_value_immediately(self) -> None:
        source = (ROOT / "dashboard" / "last-digit-special-ui.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('new Set(["all_even", "all_odd"])', source)
        self.assertIn('field.hidden = special', source)
        self.assertIn('field.style.display = special ? "none" : ""', source)
        self.assertIn('input.disabled = special', source)
        self.assertIn('document.addEventListener("change"', source)
        self.assertIn('select[data-builder="lastRule.operator"]', source)
        self.assertIn('input[data-builder="lastRule.value"]', source)
        subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "last-digit-special-ui.js")],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_final_assets_load_after_previous_authorities(self) -> None:
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertGreater(
            index.index('./trades-start-stop-toggle.css'),
            index.index('./final-dashboard-authority.css'),
        )
        self.assertGreater(
            index.index('./trades-start-stop-toggle.js'),
            index.index('./final-dashboard-authority.js'),
        )
        self.assertGreater(
            index.index('./last-digit-special-ui.js'),
            index.index('./trades-start-stop-toggle.js'),
        )

    def test_server_decides_fresh_start_vs_pause_resume(self) -> None:
        frontend = (ROOT / "dashboard" / "trades-start-stop-toggle.js").read_text(
            encoding="utf-8"
        )
        server = (ROOT / "app" / "lifecycle_reset_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('return PAUSED_STATUSES.has(status) || PAUSED_STATUSES.has(life)', frontend)
        self.assertIn('? "continue"', frontend)
        self.assertIn(': "start_again"', frontend)
        self.assertIn('previous in STOPPED_STATUSES', server)
        self.assertIn('requested_mode == "start_again"', server)
        self.assertIn('@app.post("/me/resume-trading")', server)


if __name__ == "__main__":
    unittest.main()
