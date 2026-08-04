from __future__ import annotations

import ast
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _literal(name: str) -> str:
    path = ROOT / "app" / "dashboard_request_coalescing.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    raise AssertionError(f"String literal {name} was not found")


class GeneratedRequestBrokerJavaScriptTests(unittest.TestCase):
    def test_request_broker_parses(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed in this test environment")
        source = _literal("_REQUEST_BROKER_JS")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request-broker.js"
            path.write_text(source, encoding="utf-8")
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

    def test_broker_coalesces_and_aborts_account_switches(self) -> None:
        source = _literal("_REQUEST_BROKER_JS")
        self.assertIn("const inFlight = new Map()", source)
        self.assertIn("abortManagedReads()", source)
        self.assertIn('parts.url.pathname === "/me/switch-account"', source)
        self.assertIn('ignoredQueryKeys', source)
        self.assertIn('new Response(entry.body', source)


if __name__ == "__main__":
    unittest.main()
