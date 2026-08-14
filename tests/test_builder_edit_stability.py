from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuilderEditStabilityTests(unittest.TestCase):
    def test_stability_script_prevents_background_remount_during_editing(self) -> None:
        source = (ROOT / "dashboard" / "builder-edit-stability.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("__FOA_BUILDER_EDIT_STABILITY__", source)
        self.assertIn('this?.id === "recovery-spread-control"', source)
        self.assertIn('this?.id === "result-routing-section"', source)
        self.assertIn("isBuilderEditor()", source)
        self.assertIn("allowResultRemountUntil", source)
        self.assertIn("nativeRemove.apply", source)
        self.assertIn("restoreEditingViewport", source)
        self.assertIn("preserveViewportUntil", source)

    def test_split_input_is_free_to_be_temporarily_empty(self) -> None:
        source = (ROOT / "dashboard" / "builder-edit-stability.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("input.cloneNode(true)", source)
        self.assertIn('input.addEventListener("input"', source)
        self.assertIn("if (!raw)", source)
        self.assertIn("commitSplitInput", source)
        self.assertIn('original.dispatchEvent(new Event("change"', source)
        self.assertIn("never on each keystroke", source)

    def test_user_scroll_remains_available_during_and_after_editing(self) -> None:
        source = (ROOT / "dashboard" / "builder-edit-stability.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('window.addEventListener("scroll"', source)
        self.assertIn('window.addEventListener("wheel", releasePostEditViewportProtection', source)
        self.assertIn('window.addEventListener("touchmove", releasePostEditViewportProtection', source)
        self.assertIn("releasePostEditViewportProtection", source)
        self.assertIn("programmaticScroll", source)
        self.assertNotIn("window.setInterval(() =>", source)

    def test_intentional_result_route_structure_change_can_remount(self) -> None:
        source = (ROOT / "dashboard" / "builder-edit-stability.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("isResultStructuralField", source)
        self.assertIn('"tradeType"', source)
        self.assertIn('"analysisMode"', source)
        self.assertIn("allowResultRemountUntil = Date.now() + 220", source)

    def test_netlify_and_fallback_loader_ship_stability_script(self) -> None:
        build = (ROOT / "scripts" / "build-netlify.mjs").read_text(encoding="utf-8")
        loader = (ROOT / "dashboard" / "strategy-edit-authority.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("/builder-edit-stability.js?v=20260814-3", build)
        self.assertIn("/builder-edit-stability.js?v=20260814-3", loader)
        self.assertIn("template-runtime-loader-v6", loader)

    def test_stability_javascript_parses(self) -> None:
        subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "builder-edit-stability.js")],
            check=True,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
