from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SingleGlobalRunPanelTests(unittest.TestCase):
    def test_retired_page_level_run_panel_is_removed_not_restyled(self) -> None:
        cleanup = (ROOT / "dashboard" / "direct-ui-cleanup-v1.js").read_text(encoding="utf-8")
        self.assertIn('document.querySelectorAll(".app-main .run-panel").forEach((page) => page.remove())', cleanup)
        self.assertIn('routeFromHash() !== "trades"', cleanup)
        self.assertIn('window.FOA_FINAL_UI.go(target)', cleanup)
        self.assertIn('.app-main .run-panel{display:none!important}', cleanup)
        self.assertNotIn("transactions-only-page", cleanup)
        self.assertNotIn('page.classList.add("transactions-only-page")', cleanup)

    def test_global_panel_remains_the_only_run_panel_authority(self) -> None:
        shell = (ROOT / "dashboard" / "final-ui-shell-v2.js").read_text(encoding="utf-8")
        cleanup = (ROOT / "dashboard" / "direct-ui-cleanup-v1.js").read_text(encoding="utf-8")
        authority = (ROOT / "dashboard" / "direct-run-panel-authority-v6.js").read_text(encoding="utf-8")
        self.assertIn('class=\"global-run-panel', shell)
        self.assertIn('document.querySelector(".global-run-panel")', authority)
        self.assertIn("The fixed .global-run-panel is the one and only Run panel", cleanup)


if __name__ == "__main__":
    unittest.main()
