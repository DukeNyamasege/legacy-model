from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.multi_strategy_ui import _MULTI_STRATEGY_JS


class GeneratedMultiStrategyJavaScriptTests(unittest.TestCase):
    def test_generated_selector_javascript_parses(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed in this test environment")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "multi-strategy-ui.js"
            path.write_text(_MULTI_STRATEGY_JS, encoding="utf-8")
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
