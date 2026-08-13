from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LastDigitSpecialUiTests(unittest.TestCase):
    def test_value_field_is_removed_for_all_even_and_all_odd(self) -> None:
        source = (ROOT / "dashboard" / "last-digit-special-ui.js").read_text(encoding="utf-8")
        self.assertIn('new Set(["all_even", "all_odd"])', source)
        self.assertIn('field.style.display = special ? "none" : ""', source)
        self.assertIn('input.disabled = special', source)
        self.assertIn('document.addEventListener("change"', source)
        subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "last-digit-special-ui.js")],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_ui_guard_is_loaded_last(self) -> None:
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn('./last-digit-special-ui.js', index)
        self.assertGreater(
            index.index('./last-digit-special-ui.js'),
            index.index('./trades-start-stop-toggle.js'),
        )


if __name__ == "__main__":
    unittest.main()
