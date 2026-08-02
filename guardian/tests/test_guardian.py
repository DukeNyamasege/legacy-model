from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from guardian.runtime import GuardianRuntime
from guardian.security import redact, safe_repo_path, sanitize_test_command, validate_diff
from guardian.store import GuardianStore


class GuardianSecurityTests(unittest.TestCase):
    def test_redacts_keys_tokens_and_account_ids(self) -> None:
        text = (
            "Authorization: Bearer secret-value\n"
            "OPENAI_API_KEY=sk-example-secret-value\n"
            "TELEGRAM token=123456789:abcdefghijklmnopqrstuvwxyzABCDE\n"
            "account CR1234567"
        )
        result = redact(text)
        self.assertNotIn("secret-value", result)
        self.assertNotIn("sk-example", result)
        self.assertNotIn("123456789:abcdefghijklmnopqrstuvwxyz", result)
        self.assertNotIn("CR1234567", result)
        self.assertIn("[REDACTED]", result)

    def test_protected_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                safe_repo_path(root, ".env")
            with self.assertRaises(ValueError):
                safe_repo_path(root, "../outside.txt")
            allowed = safe_repo_path(root, "guardian/example.py")
            self.assertEqual(allowed, root / "guardian/example.py")

    def test_destructive_patch_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_diff("+ docker compose down -v")
        with self.assertRaises(ValueError):
            validate_diff("+++ b/.env\n+OPENAI_API_KEY=secret")

    def test_test_command_allowlist(self) -> None:
        self.assertEqual(
            sanitize_test_command("python -m compileall -q guardian"),
            "python -m compileall -q guardian",
        )
        with self.assertRaises(ValueError):
            sanitize_test_command("curl https://example.com")


class GuardianStoreTests(unittest.TestCase):
    def test_incident_lifecycle_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GuardianStore(Path(directory) / "guardian.sqlite3")
            incident_id = store.create_incident(
                fingerprint="abc",
                category="error",
                severity="warning",
                title="Example",
                summary="Example incident",
                evidence="traceback",
                analysis={"needs_code_change": True},
                base_commit="a" * 40,
            )
            self.assertIsNotNone(incident_id)
            self.assertTrue(
                store.transition(
                    int(incident_id),
                    expected=("proposed",),
                    target="approved",
                    approved_by="123",
                )
            )
            self.assertFalse(
                store.transition(
                    int(incident_id),
                    expected=("proposed",),
                    target="rejected",
                )
            )
            incident = store.incident(int(incident_id))
            self.assertEqual(incident["status"], "approved")
            self.assertTrue(incident["analysis"]["needs_code_change"])


class GuardianRuntimeTests(unittest.TestCase):
    def test_fingerprint_normalizes_numbers(self) -> None:
        health = {"http": {"ok": False}, "services": []}
        first = GuardianRuntime.incident_fingerprint(
            "worker error contract 12345 at line 88", health
        )
        second = GuardianRuntime.incident_fingerprint(
            "worker error contract 98765 at line 99", health
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
