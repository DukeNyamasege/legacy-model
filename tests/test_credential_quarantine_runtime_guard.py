from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app.credential_quarantine_runtime_guard as guard
from enhanced_bot import TradingBot


class CredentialQuarantineRuntimeGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_validate = TradingBot.validate_accounts
        self.original_installed = guard._INSTALLED

    def tearDown(self) -> None:
        TradingBot.validate_accounts = self.original_validate
        guard._INSTALLED = self.original_installed

    def test_guard_reloads_runtime_accounts_after_quarantine_before_validation(self) -> None:
        events: list[str] = []

        async def base_validate(self, *args, **kwargs):
            events.append("validate")
            return "ok"

        TradingBot.validate_accounts = base_validate
        guard._INSTALLED = False
        guard.install_credential_quarantine_runtime_guard()

        fake = SimpleNamespace(
            logger=logging.getLogger("credential-runtime-guard-test"),
            _load_runtime_accounts=lambda: events.append("reload"),
        )
        with patch(
            "app.credential_quarantine_runtime_guard.quarantine_undecryptable_enabled_accounts",
            side_effect=lambda _bot: events.append("sweep") or 3,
        ):
            result = asyncio.run(TradingBot.validate_accounts(fake))

        self.assertEqual(result, "ok")
        self.assertEqual(events, ["sweep", "reload", "validate"])

    def test_guard_does_not_reload_when_sweep_is_clean(self) -> None:
        events: list[str] = []

        async def base_validate(self, *args, **kwargs):
            events.append("validate")
            return "ok"

        TradingBot.validate_accounts = base_validate
        guard._INSTALLED = False
        guard.install_credential_quarantine_runtime_guard()

        fake = SimpleNamespace(
            logger=logging.getLogger("credential-runtime-guard-test-clean"),
            _load_runtime_accounts=lambda: events.append("reload"),
        )
        with patch(
            "app.credential_quarantine_runtime_guard.quarantine_undecryptable_enabled_accounts",
            return_value=0,
        ):
            result = asyncio.run(TradingBot.validate_accounts(fake))

        self.assertEqual(result, "ok")
        self.assertEqual(events, ["validate"])

    def test_production_installs_guard_after_custom_runtime_layers(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        source = (root / "app" / "production_worker_integration.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("install_credential_quarantine_runtime_guard", source)
        self.assertLess(
            source.index("install_custom_strategy_aidr_isolation()"),
            source.index("install_credential_quarantine_runtime_guard()"),
        )


if __name__ == "__main__":
    unittest.main()
