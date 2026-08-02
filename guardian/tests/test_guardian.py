from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from guardian.patcher import GuardianPatcher
from guardian.privacy import sanitize_strategy_metrics
from guardian.runtime import GuardianRuntime
from guardian.security import (
    redact,
    safe_repo_path,
    sanitize_test_command,
    validate_diff,
)
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
            with self.assertRaises(ValueError):
                safe_repo_path(root, "scripts/deploy_vps.sh")
            with self.assertRaises(ValueError):
                safe_repo_path(root, "app/aidr_strategy_contract.json")
            with self.assertRaises(ValueError):
                safe_repo_path(root, "alembic/versions/001.py")
            allowed = safe_repo_path(root, "guardian/example.py")
            self.assertEqual(allowed, root / "guardian/example.py")

    def test_destructive_patch_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_diff("+ docker compose down -v")
        with self.assertRaises(ValueError):
            validate_diff("+++ b/.env\n+OPENAI_API_KEY=secret")
        with self.assertRaises(ValueError):
            validate_diff("--- a/config.yaml\n+++ b/config.yaml")

    def test_test_command_allowlist(self) -> None:
        self.assertEqual(
            sanitize_test_command("python -m compileall -q guardian"),
            "python -m compileall -q guardian",
        )
        with self.assertRaises(ValueError):
            sanitize_test_command("curl https://example.com")
        with self.assertRaises(ValueError):
            sanitize_test_command("docker compose config")


class GuardianCommissioningDefaultsTests(unittest.TestCase):
    def test_example_cannot_push_or_deploy_by_default(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        values: dict[str, str] = {}
        for raw_line in (repository / ".env.guardian.example").read_text(
            encoding="utf-8"
        ).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()

        self.assertEqual(values.get("GUARDIAN_DRY_RUN"), "true")
        self.assertEqual(values.get("GUARDIAN_ALLOW_MAIN_PUSH"), "false")
        self.assertEqual(values.get("GUARDIAN_AUTO_DEPLOY"), "false")


class GuardianPatchScopeTests(unittest.TestCase):
    def test_patch_can_only_touch_diagnosed_existing_file_or_new_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as repository_directory, tempfile.TemporaryDirectory() as worktree_directory:
            repository = Path(repository_directory)
            worktree = Path(worktree_directory)
            (repository / "app").mkdir()
            (worktree / "app").mkdir()
            (repository / "app" / "diagnosed.py").write_text("OLD = True\n")
            (repository / "app" / "unrelated.py").write_text("SAFE = True\n")
            (worktree / "app" / "diagnosed.py").write_text("OLD = True\n")
            (worktree / "app" / "unrelated.py").write_text("SAFE = True\n")

            patcher = object.__new__(GuardianPatcher)
            patcher.config = SimpleNamespace(repo_dir=repository)
            exact, parents = patcher._patch_scope(["app/diagnosed.py"])

            changed = patcher._write_files(
                worktree,
                {
                    "files": [
                        {"path": "app/diagnosed.py", "content": "OLD = False\n"},
                        {"path": "app/new_helper.py", "content": "HELPER = True\n"},
                        {"path": "tests/test_fix.py", "content": "def test_fix():\n    assert True\n"},
                    ]
                },
                exact_scope=exact,
                new_file_parents=parents,
            )
            self.assertEqual(
                changed,
                ["app/diagnosed.py", "app/new_helper.py", "tests/test_fix.py"],
            )

            with self.assertRaises(ValueError):
                patcher._write_files(
                    worktree,
                    {
                        "files": [
                            {"path": "app/unrelated.py", "content": "SAFE = False\n"}
                        ]
                    },
                    exact_scope=exact,
                    new_file_parents=parents,
                )


class GuardianPrivacyTests(unittest.TestCase):
    def test_strategy_metrics_remove_private_rows(self) -> None:
        payload = {
            "total_registered_traders": 10,
            "trading_now": 3,
            "system_performance": {
                "today": {
                    "total_trades": 20,
                    "wins": 12,
                    "losses": 8,
                    "profit": -1.25,
                }
            },
            "accounts": [
                {"account_id": "CR1234567", "balance": 500.0, "profit": 20.0}
            ],
            "oauth_token": "secret",
            "user_email": "private@example.com",
        }
        result = sanitize_strategy_metrics(payload)
        self.assertEqual(result["total_registered_traders"], 10)
        self.assertEqual(result["system_performance"]["today"]["wins"], 12)
        self.assertNotIn("accounts", result)
        self.assertNotIn("oauth_token", result)
        self.assertNotIn("user_email", result)


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
            self.assertIsNone(
                store.create_incident(
                    fingerprint="abc",
                    category="error",
                    severity="warning",
                    title="Duplicate",
                    summary="Duplicate active incident",
                    evidence="traceback",
                    analysis={"needs_code_change": True},
                    base_commit="a" * 40,
                )
            )
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

    def test_restart_fails_closed_for_working_incident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guardian.sqlite3"
            store = GuardianStore(path)
            incident_id = store.create_incident(
                fingerprint="restart",
                category="error",
                severity="critical",
                title="Interrupted",
                summary="Work was interrupted",
                evidence="traceback",
                analysis={"needs_code_change": True},
                base_commit="b" * 40,
            )
            self.assertTrue(
                store.transition(
                    int(incident_id),
                    expected=("proposed",),
                    target="working",
                )
            )
            reopened = GuardianStore(path)
            self.assertEqual(len(reopened.interrupted_remediations), 1)
            self.assertEqual(
                reopened.incident(int(incident_id))["status"],
                "interrupted",
            )


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

    def test_compose_ps_accepts_json_array(self) -> None:
        payload = json.dumps(
            [
                {
                    "Service": "api",
                    "State": "running",
                    "Health": "healthy",
                },
                {
                    "Service": "worker",
                    "State": "running",
                    "Health": "",
                },
            ]
        )
        services = GuardianRuntime._parse_compose_ps(payload)
        self.assertEqual([item["service"] for item in services], ["api", "worker"])

    def test_compose_ps_accepts_json_lines(self) -> None:
        payload = "\n".join(
            (
                json.dumps({"Service": "database", "State": "running"}),
                json.dumps({"Service": "api", "State": "running"}),
            )
        )
        services = GuardianRuntime._parse_compose_ps(payload)
        self.assertEqual(len(services), 2)


if __name__ == "__main__":
    unittest.main()
