from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from guardian.config import GuardianConfig
from guardian.openai_client import GuardianOpenAI
from guardian.telegram import GuardianTelegram


class GuardianOpenAIWiringTests(unittest.TestCase):
    def test_runtime_defaults_fail_closed(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = GuardianConfig.from_env()
        self.assertTrue(config.dry_run)
        self.assertFalse(config.allow_main_push)
        self.assertFalse(config.auto_deploy)

    def test_reviewer_must_be_distinct_from_coding_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repo"
            repository.mkdir()
            (repository / ".git").mkdir()
            (repository / "docker-compose.yml").write_text("services: {}\n")
            (repository / "docker-compose.vps.yml").write_text("services: {}\n")
            environment = {
                "GUARDIAN_REPO_DIR": str(repository),
                "GUARDIAN_STATE_DIR": str(Path(directory) / "state"),
                "OPENAI_API_KEY": "test-key",
                "GUARDIAN_TELEGRAM_BOT_TOKEN": "test-bot-token",
                "GUARDIAN_TELEGRAM_ADMIN_CHAT_ID": "123",
                "GUARDIAN_CODING_MODEL": "same-model",
                "GUARDIAN_REVIEWER_MODEL": "same-model",
            }
            with patch.dict(os.environ, environment, clear=True):
                config = GuardianConfig.from_env()
            with self.assertRaisesRegex(RuntimeError, "distinct"):
                config.validate()

    def test_responses_are_not_stored(self) -> None:
        guardian = object.__new__(GuardianOpenAI)
        guardian.config = SimpleNamespace()
        guardian.project_charter = "charter"
        guardian.strategy_contract = '{"schema_version":1}'
        guardian._consume_ai_call = MagicMock()
        guardian.client = MagicMock()
        guardian.client.responses.create.return_value = SimpleNamespace(
            output_text='{"accepted":true}'
        )

        result = guardian._request(
            model="diagnosis-model",
            instructions="diagnose",
            input_text="redacted evidence",
            schema_name="guardian_test",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"accepted": {"type": "boolean"}},
                "required": ["accepted"],
            },
        )

        self.assertEqual(result, {"accepted": True})
        kwargs = guardian.client.responses.create.call_args.kwargs
        self.assertIs(kwargs["store"], False)
        self.assertTrue(kwargs["text"]["format"]["strict"])
        self.assertIn("AUTHORITATIVE MACHINE-READABLE", kwargs["instructions"])


class GuardianTelegramAuthorizationTests(unittest.TestCase):
    @patch("guardian.telegram.requests.get")
    def test_callback_from_another_chat_is_rejected(self, get: MagicMock) -> None:
        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 7,
                    "callback_query": {
                        "id": "callback",
                        "data": "guardian:approve:1",
                        "message": {"message_id": 3, "chat": {"id": 999}},
                    },
                }
            ],
        }
        telegram = GuardianTelegram(
            SimpleNamespace(
                telegram_bot_token="test-token",
                telegram_admin_chat_id="123",
            )
        )
        handler = MagicMock()

        telegram.poll(handler)

        handler.assert_not_called()
        self.assertEqual(telegram.offset, 8)


if __name__ == "__main__":
    unittest.main()
