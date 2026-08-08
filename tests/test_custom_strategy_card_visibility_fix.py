from __future__ import annotations

import ast
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "custom_strategy_card_visibility_fix.py"
DATABASE_HARDENING = ROOT / "app" / "database_runtime_hardening.py"


class CustomStrategyCardVisibilityFixTests(unittest.TestCase):
    def test_visibility_compositor_is_installed_after_complete_builder(self) -> None:
        source = DATABASE_HARDENING.read_text(encoding="utf-8")
        self.assertIn("install_custom_strategy_card_visibility_fix", source)
        self.assertLess(
            source.index("install_custom_strategy_final_ui(app)"),
            source.index("install_custom_strategy_card_visibility_fix(app)"),
        )

    def test_visibility_fix_has_late_anchor_and_visible_shell(self) -> None:
        source = MODULE.read_text(encoding="utf-8")
        for marker in (
            "FOA_CUSTOM_STRATEGY_CARD_VISIBILITY_V4",
            ".foa-settings-grid",
            "foa-strategy-selector",
            "foa-custom-strategy-builder",
            "Loading Custom Strategy Builder",
            "MutationObserver",
            "visibility_probe",
            "credentials: \"same-origin\"",
            "complete-builder-v2",
            "anchor-fallback-v1",
        ):
            self.assertIn(marker, source)

    def test_visibility_javascript_has_valid_syntax(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is unavailable")
        source = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        javascript = None
        for statement in tree.body:
            if not isinstance(statement, ast.Assign):
                continue
            if any(
                isinstance(target, ast.Name) and target.id == "_VISIBILITY_JS"
                for target in statement.targets
            ):
                javascript = ast.literal_eval(statement.value)
                break
        self.assertIsInstance(javascript, str)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", encoding="utf-8", delete=False
        ) as handle:
            handle.write(javascript)
            path = handle.name
        try:
            result = subprocess.run(
                [node, "--check", path],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
