from __future__ import annotations

import asyncio
import json
import os
import socket
from datetime import datetime, timezone
from typing import Any

import aiohttp


class NetlifyDashboardSync:
    def __init__(self, repository) -> None:
        self.repository = repository
        self.sync_url = str(os.getenv("NETLIFY_SYNC_URL", "")).strip()
        self.sync_token = str(os.getenv("NETLIFY_SYNC_TOKEN", "")).strip()
        self.sync_interval_seconds = max(
            5, int(os.getenv("NETLIFY_SYNC_INTERVAL_SECONDS", "15"))
        )

    @property
    def enabled(self) -> bool:
        return bool(self.sync_url and self.sync_token)

    def build_snapshot(self) -> dict[str, Any]:
        summary = self.repository.summary()
        return {
            "summary": summary,
            "trades": self.repository.recent_trades(30),
            "accounts": summary.get("accounts", []),
            "runtime": {
                "local_control": False,
                "read_only_dashboard": True,
            },
            "meta": {
                "synced_at": datetime.now(timezone.utc).isoformat(),
                "host_name": socket.gethostname(),
            },
        }

    async def push_once(self) -> None:
        if not self.enabled:
            return
        payload = self.build_snapshot()
        headers = {
            "Authorization": f"Bearer {self.sync_token}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.sync_url, headers=headers, json=payload) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise RuntimeError(f"Netlify sync failed ({response.status}): {text}")

    async def loop(self, logger) -> None:
        if not self.enabled:
            return
        while True:
            try:
                await self.push_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Netlify sync failed: %s", exc)
            await asyncio.sleep(self.sync_interval_seconds)
