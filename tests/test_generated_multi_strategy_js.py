from __future__ import annotations

import ast
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _generated_script() -> str:
    source_path = ROOT / "app" / "multi_strategy_ui.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "_MULTI_STRATEGY_JS"
            for target in statement.targets
        ):
            continue
        value = ast.literal_eval(statement.value)
        if not isinstance(value, str) or not value.strip():
            raise AssertionError("_MULTI_STRATEGY_JS must be a non-empty string")
        return value
    raise AssertionError("_MULTI_STRATEGY_JS was not found")


class GeneratedMultiStrategyJavaScriptTests(unittest.TestCase):
    def test_generated_selector_javascript_parses(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed in this test environment")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "multi-strategy-ui.js"
            path.write_text(_generated_script(), encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(
            result.returncode,
            0,
            msg=(result.stdout + "\n" + result.stderr).strip(),
        )


if __name__ == "__main__":
    unittest.main()
