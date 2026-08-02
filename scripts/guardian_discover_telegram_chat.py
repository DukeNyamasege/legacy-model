#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover private Telegram chat IDs seen by the Guardian bot."
    )
    parser.add_argument(
        "--env-file",
        default="/etc/legacy-model-guardian.env",
        help="Guardian environment file containing GUARDIAN_TELEGRAM_BOT_TOKEN",
    )
    args = parser.parse_args()

    values = load_env(Path(args.env_file))
    token = os.getenv("GUARDIAN_TELEGRAM_BOT_TOKEN", "").strip() or values.get(
        "GUARDIAN_TELEGRAM_BOT_TOKEN", ""
    ).strip()
    if not token:
        raise SystemExit(
            "GUARDIAN_TELEGRAM_BOT_TOKEN is missing. Add it to the protected env file first."
        )

    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"limit": 100, "timeout": 0, "allowed_updates": '["message"]'},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise SystemExit(str(payload.get("description") or "Telegram request failed"))

    private_chats: dict[str, dict[str, str]] = {}
    for update in payload.get("result") or []:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if str(chat.get("type") or "") != "private":
            continue
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            continue
        private_chats[chat_id] = {
            "username": str(chat.get("username") or ""),
            "first_name": str(chat.get("first_name") or ""),
            "last_name": str(chat.get("last_name") or ""),
        }

    if not private_chats:
        print("No private chat was found.")
        print("Open the Guardian bot in Telegram, press Start, send /status, then rerun this command.")
        return 2

    print("Private chats seen by the Guardian bot:")
    for chat_id, details in private_chats.items():
        name = " ".join(
            item for item in (details["first_name"], details["last_name"]) if item
        ).strip()
        username = f"@{details['username']}" if details["username"] else "no username"
        print(f"chat_id={chat_id} username={username} name={name or 'unknown'}")
    print("\nCopy only your numeric chat_id into GUARDIAN_TELEGRAM_ADMIN_CHAT_ID.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
