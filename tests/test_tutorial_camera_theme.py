from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TutorialCameraThemeContract(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_dark_and_light_are_mid_tone_camera_palettes(self) -> None:
        css = self.text("dashboard/tutorial-camera-theme-v1.css")
        self.assertIn('html[data-theme="dark"]', css)
        self.assertIn('html[data-theme="light"]', css)
        self.assertIn('--camera-bg: #07111f', css)
        self.assertIn('--camera-bg: #e9f0f6', css)
        self.assertIn('--camera-text: #f1f6fb', css)
        self.assertIn('--camera-text: #12263a', css)
        self.assertIn('--camera-cyan: #4bc2d9', css)
        self.assertIn('--camera-cyan: #167f9d', css)

    def test_recorded_surfaces_share_one_palette(self) -> None:
        css = self.text("dashboard/tutorial-camera-theme-v1.css")
        for marker in (
            ".topbar",
            ".bottom-nav",
            ".account-switch-summary",
            ".builder-panel",
            ".schedule-form",
            ".global-run-panel",
            ".run-panel-sheet",
            ".run-panel-stats",
            ".transaction-row",
            ".direct-confirm-card",
            "#derivadmin-soft-loader",
        ):
            self.assertIn(marker, css)
        self.assertIn("background: var(--camera-surface)", css)
        self.assertIn("color: var(--camera-muted)", css)

    def test_profit_loss_and_execution_states_remain_distinct(self) -> None:
        css = self.text("dashboard/tutorial-camera-theme-v1.css")
        self.assertIn("--camera-green: #43d3a0", css)
        self.assertIn("--camera-red: #ff7182", css)
        self.assertIn("--camera-green: #087f5b", css)
        self.assertIn("--camera-red: #c84459", css)
        self.assertIn('data-single-run-state="start"', css)
        self.assertIn('data-single-run-state="stop"', css)
        self.assertIn("#167d60", css)
        self.assertIn("#d94a5e", css)

    def test_frontend_loads_camera_palette_last_in_head(self) -> None:
        docker = self.text("Dockerfile.frontend")
        self.assertIn("cp dashboard/tutorial-camera-theme-v1.css dist/tutorial-camera-theme-v1.css", docker)
        self.assertIn("20260818-camera-theme-v1", docker)
        self.assertIn("grep -q -- '--camera-bg' dist/tutorial-camera-theme-v1.css", docker)
        self.assertIn("const camera='<link rel=", docker)
        self.assertIn("h.replace('</head>',camera+", docker)


if __name__ == "__main__":
    unittest.main()
