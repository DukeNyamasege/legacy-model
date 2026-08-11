from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.account_mode_execution_lock import (
    account_allows_new_execution,
    account_lifecycle_from_row,
)


ROOT = Path(__file__).resolve().parents[1]


def row(*, enabled: bool, status: str) -> SimpleNamespace:
    return SimpleNamespace(enabled=enabled, execution_status=status)


class AccountWorkerLifecycleTests(unittest.TestCase):
    def test_account_lifecycle_states_are_explicit(self) -> None:
        self.assertEqual(
            account_lifecycle_from_row(row(enabled=False, status="stopped")),
            "stopped",
        )
        self.assertEqual(
            account_lifecycle_from_row(row(enabled=False, status="manual_pause")),
            "paused",
        )
        self.assertEqual(
            account_lifecycle_from_row(row(enabled=True, status="connecting")),
            "starting",
        )
        self.assertEqual(
            account_lifecycle_from_row(row(enabled=True, status="active")),
            "running",
        )
        self.assertEqual(
            account_lifecycle_from_row(row(enabled=True, status="settlement_only")),
            "settlement",
        )

    def test_only_started_or_running_accounts_allow_new_execution(self) -> None:
        self.assertFalse(account_allows_new_execution(row(enabled=False, status="stopped")))
        self.assertFalse(
            account_allows_new_execution(row(enabled=False, status="manual_pause"))
        )
        self.assertFalse(
            account_allows_new_execution(row(enabled=True, status="settlement_only"))
        )
        self.assertTrue(account_allows_new_execution(row(enabled=True, status="connecting")))
        self.assertTrue(account_allows_new_execution(row(enabled=True, status="active")))

    def test_runtime_loader_gates_before_decrypting_or_validating_accounts(self) -> None:
        source = (ROOT / "enhanced_bot.py").read_text(encoding="utf-8")
        lifecycle_gate = source.index("lifecycle = account_lifecycle_from_row(row)")
        decrypt = source.index("payload = decrypt_auth_payload(row.token_secret")
        self.assertLess(lifecycle_gate, decrypt)
        self.assertIn("elif not account_allows_new_execution(row):", source)
        self.assertIn("Settlement completed; Start Auto Trading is required", source)
        self.assertIn("async def _wait_for_active_execution_account", source)
        self.assertIn("ACCOUNT_EXECUTION_IDLE", source)

    def test_account_scoped_runtime_allows_only_execution_or_settlement_accounts(self) -> None:
        source = (ROOT / "app" / "account_scoped_websocket_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'not account_allows_new_execution(row) and lifecycle != "settlement"',
            source,
        )
        self.assertIn("_refresh_enabled_oauth_rows", source)

    def test_valid_client_sessions_are_keyed_and_not_duplicated(self) -> None:
        source = (ROOT / "enhanced_bot.py").read_text(encoding="utf-8")
        ensure = source[source.index("async def _ensure_sessions_for_valid_clients") :]
        self.assertIn(
            "desired = {token: account_id for token, account_id in self.valid_clients}",
            ensure,
        )
        self.assertIn("if token in self.sessions:", ensure)
        self.assertIn("asyncio.create_task(session.connect_and_run())", ensure)

    def test_public_stream_starts_only_after_active_execution_account_exists(self) -> None:
        source = (ROOT / "enhanced_bot.py").read_text(encoding="utf-8")
        run = source[source.index("async def run(self)") :]
        idle_wait = run.index("await self._wait_for_active_execution_account()")
        public_start = run.index("public_task = asyncio.create_task")
        self.assertLess(idle_wait, public_start)
        self.assertIn('action=stopping_public_stream', source)


if __name__ == "__main__":
    unittest.main()
