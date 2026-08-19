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
            ".global-run-panel.global-run-panel{",
            ".global-run-panel.global-run-panel.open{",
            ".global-run-panel.global-run-panel.collapsed{",
            "position:fixed!important",
            "right:0!important",
            "left:auto!important",
            "width:clamp(320px,25vw,460px)!important",
            "height:calc(100dvh - 72px)!important",
            "transform:translateX(calc(100% - 48px))!important",
            'className = "run-panel-reopen-v1"',
            "&gt;</span><b>Run panel</b>",
            "writing-mode:vertical-rl!important",
            "<b>Collapse</b>",
        ):
            self.assertIn(marker, layout)

        self.assertIn('document.getElementById("mobile-layout-authority-v1-style")?.remove()', layout)
        self.assertIn("window.DERIVADMIN_MOBILE_LAYOUT_AUTHORITY_V1?.version === VERSION", layout)
        self.assertNotIn("if (window.__DERIVADMIN_MOBILE_LAYOUT_AUTHORITY_V1__) return", layout)

        self.assertIn("var(--camera-surface", layout)
        self.assertIn("var(--camera-surface-2", layout)
        self.assertIn("var(--camera-text", layout)
        self.assertIn("var(--camera-line-strong", layout)
        self.assertIn("20260819-right-quarter-drawer-v6-right-edge", layout)
        self.assertIn(
            '["mobile-layout-authority-v1.js", "20260819-right-quarter-drawer-v6-right-edge"]',
            injector,
        )

    def test_mobile_run_panel_remains_responsive(self) -> None:
        layout = (ROOT / "dashboard" / "mobile-layout-authority-v1.js").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:900px)", layout)
        self.assertIn("width:100%!important", layout)
        self.assertIn("height:calc(100dvh - 72px)!important", layout)
        self.assertIn("safe-area-inset-bottom", layout)

    def test_mobile_summary_reserves_navigation_lane_above_start_stop(self) -> None:
        usability = (ROOT / "dashboard" / "run-panel-usability-v1.js").read_text(encoding="utf-8")
        injector = (ROOT / "scripts" / "inject-frontend-assets.mjs").read_text(encoding="utf-8")

        for marker in (
            'const VERSION = "20260819-run-panel-usability-v3-mobile-summary-lane"',
            'document.getElementById("run-panel-usability-v2-style")?.remove()',
            'html[data-run-panel-visibility="open"] .bottom-nav',
            'bottom:calc(52px + env(safe-area-inset-bottom, 0px))!important',
            'html[data-run-panel-visibility="open"] .global-run-panel.open .run-panel-sheet',
            'padding-bottom:calc(72px + env(safe-area-inset-bottom, 0px))!important',
            'html[data-run-panel-visibility="open"] .global-run-panel.open .run-panel-stats',
            'min-height:106px!important',
            'min-height:72px!important',
        ):
            self.assertIn(marker, usability)

        self.assertNotIn("if (window.__DERIVADMIN_RUN_PANEL_USABILITY_V2__) return", usability)
        self.assertIn(
            '["run-panel-usability-v1.js", "20260819-mobile-summary-nav-lane-v4"]',
            injector,
        )


if __name__ == "__main__":
    unittest.main()
