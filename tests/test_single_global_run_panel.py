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

    def test_desktop_panel_is_fixed_right_quarter_width_drawer(self) -> None:
        layout = (ROOT / "dashboard" / "mobile-layout-authority-v1.js").read_text(encoding="utf-8")
        injector = (ROOT / "scripts" / "inject-frontend-assets.mjs").read_text(encoding="utf-8")

        for marker in (
            "@media(min-width:901px)",
            "position:fixed!important",
            "right:0!important",
            "width:clamp(320px,25vw,460px)!important",
            "height:calc(100dvh - 72px)!important",
            "transform:translateX(calc(100% - 48px))!important",
            'className = "run-panel-reopen-v1"',
            "&gt;</span><b>Run panel</b>",
            "writing-mode:vertical-rl!important",
            "<b>Collapse</b>",
        ):
            self.assertIn(marker, layout)

        self.assertIn("var(--camera-surface", layout)
        self.assertIn("var(--camera-surface-2", layout)
        self.assertIn("var(--camera-text", layout)
        self.assertIn("var(--camera-line-strong", layout)
        self.assertIn("20260819-right-quarter-drawer-v5", layout)
        self.assertIn(
            '["mobile-layout-authority-v1.js", "20260819-right-quarter-drawer-v5"]',
            injector,
        )

    def test_mobile_run_panel_remains_responsive(self) -> None:
        layout = (ROOT / "dashboard" / "mobile-layout-authority-v1.js").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:900px)", layout)
        self.assertIn("width:100%!important", layout)
        self.assertIn("height:calc(100dvh - 72px)!important", layout)
        self.assertIn("safe-area-inset-bottom", layout)


if __name__ == "__main__":
    unittest.main()
