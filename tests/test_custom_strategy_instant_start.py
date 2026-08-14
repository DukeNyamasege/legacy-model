from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.account_execution_session import (
    AccountExecutionPreparationError,
    AccountExecutionSession,
)
import app.custom_strategy_instant_start as instant


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[object, ...]]] = []

    def info(self, message: str, *args: object, **_kwargs: object) -> None:
        self.events.append((message, args))

    def warning(self, message: str, *args: object, **_kwargs: object) -> None:
        self.events.append((message, args))


class _RuntimeRepository:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def list_managed_accounts(self) -> list[object]:
        return list(self.rows)

    def runtime_mode(self) -> str:
        return "demo"


class _HistorySocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.recv_count = 0
        self.send_count_at_first_recv: int | None = None

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        if self.send_count_at_first_recv is None:
            self.send_count_at_first_recv = len(self.sent)
        self.recv_count += 1
        request = list(reversed(self.sent))[self.recv_count - 1]
        symbol = str(request["ticks_history"])
        return json.dumps(
            {
                "req_id": request["req_id"],
                "pip_size": 2,
                "history": {
                    "prices": [100.01, 100.02, 100.03],
                    "times": [1, 2, 3],
                },
                "echo_req": {"ticks_history": symbol},
            }
        )


class CustomStrategyInstantStartTests(unittest.TestCase):
    def test_fast_runtime_admission_does_not_call_provider(self) -> None:
        row = SimpleNamespace(
            id=7,
            enabled=True,
            execution_status="starting",
            token_secret="encrypted",
            label="Demo account",
            stake_amount=0.50,
            take_profit=10.0,
            stop_loss=100.0,
            martingale_enabled=True,
        )
        statuses: list[tuple[int, str, str]] = []
        bot = SimpleNamespace(
            repository=_RuntimeRepository([row]),
            encryption_key="",
            environment="demo",
            logger=_Logger(),
            _set_account_execution_status=lambda managed_id, status, reason="": statuses.append(
                (int(managed_id), str(status), str(reason))
            ),
        )
        payload = {
            "auth_type": "oauth",
            "access_token": "oauth-access-token",
            "scope": "trade application_read",
            "account_id": "VRTC123456",
            "account_type": "demo",
        }

        with patch.object(instant, "decrypt_auth_payload", return_value=payload):
            tokens, profiles = instant._fast_runtime_accounts(bot)

        self.assertEqual(len(tokens), 1)
        profile = profiles[tokens[0]]
        self.assertEqual(profile["account_id"], "VRTC123456")
        self.assertEqual(profile["managed_account_id"], 7)
        self.assertEqual(profile["source"], "custom_strategy_instant_start")
        self.assertEqual(statuses[-1][1], "connecting")
        self.assertIn("connects in background", statuses[-1][2])

    def test_instant_validate_admits_saved_account_without_provider_sweep(self) -> None:
        sync_calls: list[str] = []
        bot = SimpleNamespace(
            repository=_RuntimeRepository([]),
            environment="real",
            tokens=[],
            user_profiles={},
            valid_clients=[],
            logger=_Logger(),
            _sync_clients_with_runtime_accounts=lambda: sync_calls.append("clients"),
            _sync_running_status_after_validation=lambda: sync_calls.append("running"),
        )
        profiles = {
            "runtime-key": {
                "account_id": "VRTC123456",
                "managed_account_id": 7,
            }
        }
        with patch.object(
            instant,
            "_fast_runtime_accounts",
            return_value=(["runtime-key"], profiles),
        ):
            asyncio.run(instant._instant_validate_accounts(bot))

        self.assertEqual(bot.environment, "demo")
        self.assertEqual(bot.valid_clients, [("runtime-key", "VRTC123456")])
        self.assertEqual(sync_calls, ["clients", "running"])

    def test_history_requests_are_batched_before_first_wait(self) -> None:
        history_calls: list[str] = []
        bot = SimpleNamespace(
            symbols=["1HZ10V", "1HZ50V", "1HZ100V"],
            logger=_Logger(),
            _public_history_count=lambda: 100,
            _on_public_history=lambda **kwargs: history_calls.append(str(kwargs["symbol"])),
        )
        socket = _HistorySocket()
        client = SimpleNamespace(ws=socket, bot=bot, next_req_id=1)

        started = time.monotonic()
        asyncio.run(instant._fast_fetch_tick_history(client))
        elapsed = time.monotonic() - started

        self.assertEqual(len(socket.sent), 3)
        self.assertEqual(socket.send_count_at_first_recv, 3)
        self.assertCountEqual(history_calls, bot.symbols)
        self.assertLess(elapsed, 1.0)

    def test_startup_rest_timeout_is_bounded_but_financial_paths_are_not_rewritten(self) -> None:
        calls: list[str] = []

        async def original(
            method: str,
            path: str,
            app_id: str,
            base_url: str,
            token: str | None = None,
            json_data: dict[str, object] | None = None,
        ) -> dict[str, object]:
            del method, app_id, base_url, token, json_data
            calls.append(path)
            await asyncio.sleep(0.03)
            return {"data": {"path": path}}

        async def scenario() -> tuple[dict[str, object], dict[str, object]]:
            with (
                patch.object(instant, "_ORIGINAL_REST_REQUEST", original),
                patch.object(instant, "_STARTUP_REST_TIMEOUT_SECONDS", 0.005),
            ):
                startup = await instant._bounded_startup_rest_request(
                    "GET",
                    "/trading/v1/options/accounts",
                    "app",
                    "https://example.invalid",
                )
                ordinary = await instant._bounded_startup_rest_request(
                    "GET",
                    "/non-startup-path",
                    "app",
                    "https://example.invalid",
                )
            return startup, ordinary

        startup, ordinary = asyncio.run(scenario())
        self.assertEqual(startup["error"]["code"], "STARTUP_TIMEOUT")
        self.assertEqual(ordinary["data"]["path"], "/non-startup-path")
        self.assertEqual(calls, [
            "/trading/v1/options/accounts",
            "/non-startup-path",
        ])

    def test_local_admission_still_cannot_prepare_without_private_websocket(self) -> None:
        token = "runtime-key"
        account_id = "VRTC123456"
        state = {"managed_account_id": 7}
        bot = SimpleNamespace(
            user_profiles={
                token: {
                    "managed_account_id": 7,
                    "account_id": account_id,
                    "api_token": "oauth-access-token",
                }
            },
            sessions={
                token: SimpleNamespace(
                    is_connected=False,
                    account_id=account_id,
                )
            },
            _credential_for_token=lambda value: "oauth-access-token" if value == token else "",
            _client_state_for_token=lambda value, account_id="": state,
        )
        execution = AccountExecutionSession(
            bot=bot,
            token=token,
            account_id=account_id,
            managed_account_id=7,
        )

        with self.assertRaisesRegex(
            AccountExecutionPreparationError,
            "authenticated Deriv trading session is not connected",
        ):
            execution.prepare()

    def test_dashboard_no_longer_uses_old_slow_start_copy(self) -> None:
        source = Path("dashboard/runtime-ux-authority.js").read_text(encoding="utf-8")
        self.assertNotIn(
            "Starting - Connecting execution stream and preparing strategy watcher...",
            source,
        )
        self.assertIn(
            "Market watcher is launching now; execution stream connects in background...",
            source,
        )


if __name__ == "__main__":
    unittest.main()
