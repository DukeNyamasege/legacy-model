"""Deriv digit strategy bot migrated to the New Deriv APIs:

- Uses the official public WS endpoint for unauthenticated market data.
- Fetches and caches symbol precision (pip_size) dynamically.
- Performs multi-account copy trading via the new REST bulk-purchase endpoint.
- Validates tokens and maps account IDs dynamically on startup via REST.
- Uses account-specific OTPs for secure authenticated WebSocket connections.
- Monitores open contracts via proposal_open_contract subscriptions (no polling).
- Exposes OAuth 2.0 PKCE helper flow on the command-line (--login).
- Daily risk management, stop-loss, and take-profit per copier.
- Configurable two-run recovery sizing with a hard maximum stake.
- Tick-based cooldown and global locking.
"""

import sys
import os
import json
import asyncio
import time
import traceback
import hashlib
import logging
import uuid
import re
import socket
import subprocess
import math
from contextlib import suppress
from pathlib import Path
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Set

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError
from dotenv import load_dotenv

from app.config import load_test2_config
from app.database import Database
from app.model.bayesian_probability import BayesianProbability
from app.model.feature_builder import build_features
from app.model.hmm_regime import ThreeStateHmm
from app.model.model_store import persist_model_metadata
from app.oauth_client import refresh_access_token, token_is_expiring
from app.repositories.test2_repository import Test2Repository
from app.strategy.cooldown import AdaptiveCooldown
from app.strategy.decision_engine import DecisionEngine, parse_proposal_economics
from app.strategy.over2_strategy import (
    TEST2_BARRIER,
    TEST2_PATTERN_RANGES,
    TEST2_TRIGGER,
    validate_contract_parameters,
)
from app.strategy.signal_detector import CandidateSignal, Over2SignalDetector
from app.token_store import decrypt_auth_payload, decrypt_token, encrypt_auth_payload

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

# Fix Unicode output on Windows terminals
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def load_config(config_path: str) -> Dict[str, Any]:
    return load_test2_config(config_path).model_dump()


def decrypt_tokens(tokens: List[str], key: str) -> List[str]:
    """Decrypt tokens using a URL-safe Base64 key if encryption is set."""
    if not key:
        return tokens
    if not HAS_CRYPTOGRAPHY:
        logging.getLogger("deriv_bot").warning("cryptography library not installed. Processing tokens as plaintext.")
        return tokens
    try:
        f = Fernet(key.encode("utf-8"))
        decrypted = []
        for t in tokens:
            try:
                decrypted.append(f.decrypt(t.encode("utf-8")).decode("utf-8"))
            except Exception:
                decrypted.append(t)
        return decrypted
    except Exception as e:
        logging.getLogger("deriv_bot").error("Token decryption failed: %s. Using tokens as plaintext.", e)
        return tokens


def load_tokens(tokens_path: str) -> List[str]:
    p = Path(tokens_path)
    if not p.exists():
        env_tokens = os.getenv("DERIV_TOKENS", "")
        if env_tokens:
            return [
                token.strip()
                for token in re.split(r"[\r\n,]+", env_tokens)
                if token.strip()
            ]
        env_token = os.getenv("DERIV_TOKEN")
        return [env_token] if env_token else []

    tokens: List[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        tokens.append(s)

    seen = set()
    uniq: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def legacy_global_tokens_enabled() -> bool:
    return os.getenv("COPYTRADING_ALLOW_LEGACY_GLOBAL_TOKENS", "false").lower() in {
        "1",
        "true",
        "yes",
    }


def load_user_profiles(users_path: str) -> Dict[str, Dict[str, Any]]:
    path = Path(users_path)
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    records = raw.get("users", []) if isinstance(raw, dict) else []
    profiles: Dict[str, Dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token", "")).strip()
        if not token:
            continue
        profiles[token] = {
            "id": str(item.get("id", token_tag(token))),
            "name": str(item.get("name", token_tag(token))),
            "enabled": bool(item.get("enabled", True)),
            "account_id": str(item.get("account_id", "")).strip(),
        }
    return profiles


def token_tag(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]


def mask_account_id(account_id: str) -> str:
    value = str(account_id)
    return f"{value[:3]}***{value[-3:]}" if len(value) > 6 else "***"


ACCOUNT_ID_PATTERN = re.compile(r"\b[A-Z]{2,6}\d{3,}\b")


def sanitize_account_ids(message: Any) -> str:
    value = str(message or "")
    return ACCOUNT_ID_PATTERN.sub(lambda match: mask_account_id(match.group(0)), value)


def today_local_iso() -> str:
    return datetime.now().date().isoformat()


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "bot": {"cooldown_ticks_remaining": 0}, "clients": {}, "unresolved_contracts": []}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("state must be a JSON object")
        # Migrate legacy version 4 to version 1 for crash resilience
        if loaded.get("version") == 4:
            migration = loaded
            migration.setdefault("version", 1)
            # Move clients/unresolved_contracts into version 1 format
            migration["clients"] = migration.get("clients", {})
            migration["unresolved_contracts"] = migration.get("unresolved_contracts", [])
            return migration
        loaded.setdefault("version", 1)
        loaded.setdefault("clients", {})
        loaded.setdefault("unresolved_contracts", [])
        bot_state = loaded.get("bot")
        if not isinstance(bot_state, dict):
            bot_state = {}
        bot_state.setdefault("cooldown_ticks_remaining", 0)
        loaded["bot"] = bot_state
        return loaded
    except Exception:
        return {"version": 1, "bot": {"cooldown_ticks_remaining": 0}, "clients": {}, "unresolved_contracts": []}


def detect_digit_streak_signal(
    last_digits: List[str],
    streak_length: int,
    ) -> Optional[Tuple[str, str, str]]:
    """Compatibility helper for the five-digit BIN22001 Over-2 signal."""
    required = len(TEST2_PATTERN_RANGES)
    if len(last_digits) < required:
        return None

    window = [int(d) for d in last_digits[-required:]]
    if all(
        lower <= digit <= upper
        for digit, (lower, upper) in zip(
            window, TEST2_PATTERN_RANGES, strict=True
        )
    ):
        return "DIGITOVER", TEST2_BARRIER, TEST2_TRIGGER
    return None


def optional_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def optional_epoch_datetime(value: Any) -> Optional[datetime]:
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return None
    if epoch <= 0:
        return None
    try:
        return datetime.fromtimestamp(epoch, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


async def _rest_request(
    method: str,
    path: str,
    app_id: str,
    base_url: str,
    token: Optional[str] = None,
    json_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Perform a REST API request to the Deriv API."""
    url = f"{base_url.rstrip('/')}{path}"
    headers = {
        "Deriv-App-ID": str(app_id),
        "Content-Type": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with aiohttp.ClientSession() as session:
            if method.upper() == "POST":
                async with session.post(url, headers=headers, json=json_data) as resp:
                    if resp.status in {200, 201}:
                        return await resp.json()
                    else:
                        try:
                            err_body = await resp.json()
                            return err_body
                        except Exception:
                            text = await resp.text()
                            return {"error": {"message": text, "code": f"HTTP_{resp.status}"}}
            else:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        try:
                            err_body = await resp.json()
                            return err_body
                        except Exception:
                            text = await resp.text()
                            return {"error": {"message": text, "code": f"HTTP_{resp.status}"}}
    except Exception as e:
        return {"error": {"message": str(e), "code": "CONNECTION_ERROR"}}


class ConnectionStaleError(Exception):
    """Raised when the tick stream goes silent for too long."""


def scan_source_for_hardcoded_tokens(root: Path) -> None:
    token_pattern = re.compile(r"\bpat_[A-Za-z0-9_-]{24,}\b")
    source_suffixes = {".py", ".yaml", ".yml", ".json", ".toml", ".md"}
    excluded_names = {"tokens.txt", "users.json", ".env"}
    excluded_parts = {
        ".git",
        ".venv",
        "archives",
        "data",
        "exports",
        "analysis",
        "__pycache__",
    }
    tracked_paths: list[Path] = []
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
            text=False,
        )
        tracked_paths = [
            root / item.decode("utf-8")
            for item in result.stdout.split(b"\0")
            if item
        ]
    except (OSError, subprocess.SubprocessError):
        tracked_paths = list(root.rglob("*"))

    offenders = []
    for path in tracked_paths:
        if (
            not path.is_file()
            or path.name in excluded_names
            or path.name.startswith(".runtime_")
            or path.suffix.lower() not in source_suffixes
            or any(part in excluded_parts for part in path.parts)
        ):
            continue
        try:
            if token_pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(path.relative_to(root)))
        except OSError:
            continue
    if offenders:
        raise RuntimeError(
            "Startup security scan found a hard-coded Deriv PAT in source files: "
            + ", ".join(offenders)
        )


class _EnsureExtraFieldsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k in ("token", "token_tag", "contract_id", "stake"):
            if not hasattr(record, k):
                setattr(record, k, "-")
        return True


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        first_word = message.split(" ", 1)[0].strip(":").upper() if message else "LOG"
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "run_id": os.getenv("TEST_RUN_ID", "bin22001"),
            "deployment_id": os.getenv("DEPLOYMENT_ID", "local"),
            "worker_id": getattr(record, "worker_id", "-"),
            "signal_id": getattr(record, "signal_id", "-"),
            "proposal_id": getattr(record, "proposal_id", "-"),
            "contract_id": getattr(record, "contract_id", "-"),
            "event_type": getattr(record, "event_type", first_word),
            "strategy_version": "2.2.0-over2-rising-22001",
            "model_version": "2.2.0-over2-rising-22001",
            "masked_account_id": getattr(record, "masked_account_id", "-"),
            "token_tag": getattr(record, "token_tag", "-"),
            "stake": getattr(record, "stake", "-"),
            "message": message,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


class LiveConsoleHandler(logging.StreamHandler):
    def __init__(self, stream=None):
        super().__init__(stream)
        self._status_text = ""

    def _erase_status(self) -> None:
        if self._status_text:
            self.stream.write("\r" + (" " * len(self._status_text)) + "\r")
            self.flush()

    def set_status(self, text: str) -> None:
        text = text[:220]
        self._erase_status()
        self._status_text = text
        if self._status_text:
            self.stream.write(self._status_text)
            self.flush()

    def clear_status(self) -> None:
        self._erase_status()
        self._status_text = ""

    def emit(self, record: logging.LogRecord) -> None:
        self._erase_status()
        try:
            super().emit(record)
        finally:
            if self._status_text:
                self.stream.write(self._status_text)
                self.flush()


def setup_logging(level: str, log_file: str) -> logging.Logger:
    logger = logging.getLogger("deriv_bot")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
    logger.handlers.clear()

    fmt_console = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s | token=%(token_tag)s contract=%(contract_id)s stake=%(stake)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = LiveConsoleHandler(sys.stdout)
    sh.setFormatter(fmt_console)
    sh.addFilter(_EnsureExtraFieldsFilter())
    logger.addHandler(sh)
    setattr(logger, "live_console_handler", sh)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(JsonLogFormatter())
    fh.addFilter(_EnsureExtraFieldsFilter())
    logger.addHandler(fh)

    logger.propagate = False
    return logger


class PublicMarketDataClient:
    """Manages the unauthenticated public WebSocket connection for ticks and symbol info."""
    def __init__(self, bot: 'TradingBot'):
        self.bot = bot
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.next_req_id = 1
        self.listen_task: Optional[asyncio.Task] = None

    async def connect_and_run(self) -> None:
        attempt = 0
        url = self.bot.public_ws_url
        while self.bot.is_running:
            try:
                self.bot.logger.info("Connecting to public market WebSocket: %s", url)
                async with websockets.connect(url) as ws:
                    self.ws = ws
                    self.is_connected = True
                    attempt = 0
                    self.bot._on_public_connection_established()
                    self.bot.logger.info("Public WebSocket connection established")

                    # Fetch and cache pip size (symbol precision)
                    await self._fetch_precision()

                    # Subscribe to symbol ticks
                    await self._subscribe_ticks()

                    self.bot._mark_tick_received()

                    # Handle messages
                    async for msg in ws:
                        await self._on_message(msg)

            except (ConnectionClosed, OSError, Exception) as e:
                self.is_connected = False
                self.ws = None
                attempt += 1
                self.bot.logger.warning("Public WebSocket error: %s. Reconnecting in %ss...", e, self.bot.reconnect_delay_seconds)

            await asyncio.sleep(min(30, self.bot.reconnect_delay_seconds * (1.5 ** attempt)))

    async def send_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Send a request over the public WebSocket and wait for its response using req_id."""
        if not self.ws or not self.is_connected:
            return {"error": {"message": "Public WebSocket is not connected", "code": "NOT_CONNECTED"}}

        req_id = self.next_req_id
        self.next_req_id += 1
        req["req_id"] = req_id

        fut = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = fut

        try:
            await self.ws.send(json.dumps(req))
            return await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            return {"error": {"message": "Request timed out", "code": "TIMEOUT"}}
        except Exception as e:
            return {"error": {"message": str(e), "code": "ERROR"}}
        finally:
            self.pending_requests.pop(req_id, None)

    async def _fetch_precision(self) -> None:
        self.bot.logger.info("Retrieving symbol details for %s...", self.bot.symbol)
        if not self.ws:
            return
        req_id = self.next_req_id
        self.next_req_id += 1
        await self.ws.send(
            json.dumps({"active_symbols": "brief", "req_id": req_id})
        )
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=10.0)
            resp = json.loads(raw)
        except (asyncio.TimeoutError, json.JSONDecodeError) as exc:
            self.bot.logger.error("Failed to fetch active symbols: %s", exc)
            return
        if "error" in resp:
            self.bot.logger.error("Failed to fetch active symbols: %s", resp["error"].get("message"))
            return

        symbols = resp.get("active_symbols", [])
        matched = False
        for sym in symbols:
            if sym.get("underlying_symbol") == self.bot.symbol:
                pip_size_val = sym.get("pip_size", 0.01)

                # Convert pip_size float to decimal places
                if isinstance(pip_size_val, (int, float)):
                    if pip_size_val < 1:
                        s = f"{pip_size_val:.10f}".rstrip('0')
                        self.bot.pip_size = len(s.split('.')[1]) if '.' in s else 2
                    else:
                        self.bot.pip_size = int(pip_size_val)
                else:
                    self.bot.pip_size = 2

                matched = True
                self.bot.logger.info("Cached %s precision: %s decimal places", self.bot.symbol, self.bot.pip_size)
                break

        if not matched:
            self.bot.logger.warning("Symbol %s not found in active symbols. Defaulting to 2 decimals.", self.bot.symbol)
            self.bot.pip_size = 2

    async def _subscribe_ticks(self) -> None:
        req = {
            "ticks": self.bot.symbol,
            "subscribe": 1
        }
        await self.ws.send(json.dumps(req))
        self.bot.logger.info("Subscribed to %s ticks on public connection", self.bot.symbol)

    async def _on_message(self, msg_str: str) -> None:
        try:
            data = json.loads(msg_str)
        except Exception:
            return

        req_id = data.get("req_id")
        if req_id and req_id in self.pending_requests:
            self.pending_requests[req_id].set_result(data)
            return

        msg_type = data.get("msg_type")
        if msg_type == "tick":
            await self.bot._on_tick(data)


class ClientSession:
    """Manages the authenticated WebSocket connection and contract monitoring for a single copier account."""
    def __init__(self, token: str, account_id: str, bot: 'TradingBot'):
        self.token = token
        self.account_id = account_id
        self.bot = bot
        self.token_tag = token_tag(token)
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.active_subscriptions: Dict[int, str] = {}  # contract_id -> subscription_id
        self.pending_contracts: Set[int] = set()       # contract_ids being monitored
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.next_req_id = 910000
        self.task: Optional[asyncio.Task] = None
        self.reconcile_task: Optional[asyncio.Task] = None

    async def get_otp_url(self) -> Optional[str]:
        app_id = self.bot.app_id
        base_url = self.bot.rest_base_url
        path = f"/trading/v1/options/accounts/{self.account_id}/otp"
        res = await _rest_request("POST", path, app_id, base_url, token=self.token)
        if "error" in res:
            self.bot.logger.error("Failed to get OTP: %s", res["error"].get("message"), extra={"token_tag": self.token_tag})
            return None
        return res.get("data", {}).get("url")

    async def connect_and_run(self) -> None:
        attempt = 0
        while self.bot.is_running:
            try:
                # 1. Fetch short-lived OTP WebSocket URL
                url = await self.get_otp_url()
                if not url:
                    await asyncio.sleep(self.bot.reconnect_delay_seconds)
                    continue

                self.bot.logger.info(
                    "Connecting to private WebSocket for account %s...",
                    mask_account_id(self.account_id),
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
                async with websockets.connect(url) as ws:
                    self.ws = ws
                    self.is_connected = True
                    self.pending_requests.clear()
                    attempt = 0
                    self.bot.logger.info(
                        "Private WebSocket connected for account %s",
                        mask_account_id(self.account_id),
                        extra={
                            "token_tag": self.token_tag,
                            "masked_account_id": mask_account_id(self.account_id),
                        },
                    )
                    await self.ws.send(
                        json.dumps({"balance": 1, "subscribe": 1, "req_id": 900001})
                    )

                    # Restore subscriptions for any unresolved contracts
                    for cid in list(self.pending_contracts):
                        await self.subscribe_contract(cid)

                    # Start ping keep-alive
                    ping_task = asyncio.create_task(self._ping_loop())
                    self.reconcile_task = asyncio.create_task(self._reconcile_contracts_loop())
                    try:
                        async for msg in ws:
                            await self._on_message(msg)
                    finally:
                        if self.reconcile_task:
                            self.reconcile_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await self.reconcile_task
                            self.reconcile_task = None
                        ping_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await ping_task

            except (ConnectionClosed, OSError, Exception) as e:
                self.is_connected = False
                self.ws = None
                attempt += 1
                self.bot.logger.warning(
                    "Private connection lost for account %s: %s. Reconnecting...",
                    mask_account_id(self.account_id),
                    e,
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )

            await asyncio.sleep(min(30, self.bot.reconnect_delay_seconds * (1.5 ** attempt)))

    async def _ping_loop(self) -> None:
        while self.ws and self.is_connected:
            try:
                await self.ws.send(json.dumps({"ping": 1}))
            except Exception:
                break
            await asyncio.sleep(30)

    async def send_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        if not self.ws or not self.is_connected:
            return {"error": {"message": "Private WebSocket is not connected", "code": "NOT_CONNECTED"}}

        req_id = self.next_req_id
        self.next_req_id += 1
        req["req_id"] = req_id
        fut = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = fut
        try:
            await self.ws.send(json.dumps(req))
            return await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            return {"error": {"message": "Request timed out", "code": "TIMEOUT"}}
        except Exception as e:
            return {"error": {"message": str(e), "code": "ERROR"}}
        finally:
            self.pending_requests.pop(req_id, None)

    async def request_contract_snapshot(self, contract_id: int) -> Dict[str, Any]:
        return await self.send_request(
            {
                "proposal_open_contract": 1,
                "contract_id": int(contract_id),
            }
        )

    async def refresh_balance_snapshot(self) -> Dict[str, Any]:
        return await self.send_request({"balance": 1})

    async def _reconcile_contracts_loop(self) -> None:
        await asyncio.sleep(max(1, self.bot.settle_wait_seconds))
        while self.ws and self.is_connected and self.bot.is_running:
            for cid in list(self.pending_contracts):
                if self.bot._contract_age_seconds(cid) < self.bot.settle_wait_seconds:
                    continue
                snapshot = await self.request_contract_snapshot(cid)
                if "error" in snapshot:
                    self.bot.logger.warning(
                        "Contract reconciliation request failed for %s: %s",
                        cid,
                        snapshot["error"].get("message"),
                        extra={"token_tag": self.token_tag, "contract_id": str(cid)},
                    )
                    continue
                contract = snapshot.get("proposal_open_contract")
                if not contract:
                    continue
                await self.bot.handle_contract_update(self.token, int(cid), contract)
            await asyncio.sleep(max(1, self.bot.reconciliation_poll_seconds))

    async def subscribe_contract(self, contract_id: int) -> None:
        if not self.ws or not self.is_connected:
            return
        req = {
            "proposal_open_contract": 1,
            "contract_id": int(contract_id),
            "subscribe": 1,
            "req_id": int(contract_id)
        }
        try:
            await self.ws.send(json.dumps(req))
            self.bot.logger.info("Subscribed to contract %s updates", contract_id, extra={"token_tag": self.token_tag})
        except Exception as e:
            self.bot.logger.error("Failed to subscribe to contract %s: %s", contract_id, e, extra={"token_tag": self.token_tag})

    async def unsubscribe_contract(self, subscription_id: str) -> None:
        if not self.ws or not self.is_connected:
            return
        try:
            await self.ws.send(json.dumps({"forget": subscription_id}))
        except Exception:
            pass

    async def _on_message(self, msg_str: str) -> None:
        try:
            data = json.loads(msg_str)
        except Exception:
            return

        req_id = data.get("req_id")
        if req_id and req_id in self.pending_requests:
            future = self.pending_requests[req_id]
            if not future.done():
                future.set_result(data)

        msg_type = data.get("msg_type")
        if msg_type == "balance":
            if not self.bot._store_account_balance_payload(
                self.account_id,
                data.get("balance", {}),
                token=self.token,
            ):
                self.bot.logger.warning(
                    "Ignored malformed balance update",
                    extra={"token_tag": self.token_tag},
                )
            return
        if msg_type == "proposal_open_contract":
            contract = data.get("proposal_open_contract")
            if not contract:
                return

            contract_id = contract.get("contract_id")
            if not contract_id:
                return

            sub_id = data.get("subscription", {}).get("id")
            if sub_id:
                self.active_subscriptions[int(contract_id)] = sub_id

            await self.bot.handle_contract_update(self.token, int(contract_id), contract)


class TradingBot:
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or os.getenv("DERIV_BOT_CONFIG", "config.yaml")
        load_dotenv(Path(self.config_path).resolve().parent / ".env")
        scan_source_for_hardcoded_tokens(Path(self.config_path).resolve().parent)
        self.test2_config = load_test2_config(self.config_path)
        self.cfg = self.test2_config.model_dump()

        self.app_id = str(self.cfg["deriv"].get("app_id", "71937"))
        self.app_markup_percentage = float(
            self.cfg["deriv"].get("app_markup_percentage", 0.0) or 0.0
        )
        self.environment = str(self.cfg["deriv"].get("environment", "demo")).lower()
        self.public_ws_url = str(self.cfg["deriv"].get("public_ws_url", "wss://api.derivws.com/trading/v1/options/ws/public"))
        self.rest_base_url = str(self.cfg["deriv"].get("rest_base_url", "https://api.derivws.com"))
        self.trading_enabled = bool(self.cfg["deriv"].get("trading_enabled", True))
        self.encryption_key = str(self.cfg["deriv"].get("token_encryption_key", ""))

        self.symbol = self.cfg["strategy"].get("symbol", "1HZ100V")
        self.contract_type = str(self.cfg["strategy"]["contract_type"])
        self.contract_barrier = str(self.cfg["strategy"]["prediction"])
        self.duration = int(self.cfg["strategy"]["duration"])
        self.duration_unit = str(self.cfg["strategy"]["duration_unit"])
        self.currency = str(self.cfg["strategy"]["currency"])
        self.pattern_length = int(self.cfg["strategy"].get("pattern_length", 3))
        self.max_tick_silence_seconds = max(5, int(self.cfg["trade"].get("max_tick_silence_seconds", 45)))
        self.reconnect_delay_seconds = max(1, int(self.cfg["trade"].get("reconnect_delay_seconds", 10)))
        self.settle_wait_seconds = max(1, int(self.cfg["trade"].get("settle_wait_seconds", 2)))
        self.settlement_sla_seconds = max(
            0.1,
            float(self.cfg["trade"].get("settlement_sla_seconds", 2.0)),
        )
        self.max_open_trade_seconds = max(2, int(self.cfg["trade"].get("max_open_trade_seconds", 6)))
        self.reconciliation_poll_seconds = max(1, int(self.cfg["trade"].get("reconciliation_poll_seconds", 2)))
        self.watchdog_poll_interval_seconds = 5.0

        validate_contract_parameters(
            contract_type=self.contract_type,
            barrier=self.contract_barrier,
            symbol=self.symbol,
            stake=float(self.cfg["strategy"]["initial_stake"]),
            duration=self.duration,
            duration_unit=self.duration_unit,
        )
        if not self.test2_config.execution.require_rising_ticks:
            raise RuntimeError("Rising-only entry policy must remain enabled.")

        self.logger = setup_logging(
            self.cfg["logging"].get("level", "INFO"),
            self.cfg["logging"].get("file", "trading_bot.log"),
        )
        self.logger.info("RISING_POLICY_ACTIVE mode=strict_last_three_quotes")
        self.logger.info(
            "CONTRACT_TIMING_STANDARD duration=1_tick settlement_sla_seconds=%.1f "
            "reconciliation_after_seconds=%s",
            self.settlement_sla_seconds,
            self.settle_wait_seconds,
        )
        self.logger.info(
            "APP_MARKUP_EXPECTED percentage=%.2f source=registered_app_or_direct_buy "
            "verification=settled_contract_and_markup_statistics",
            self.app_markup_percentage,
        )
        if self.environment == "demo" and self.app_markup_percentage > 0:
            self.logger.warning(
                "APP_MARKUP_DEMO_MODE percentage=%.2f; demo contracts can validate "
                "integration fields, but paid markup revenue requires Deriv real-account "
                "eligibility.",
                self.app_markup_percentage,
            )

        self.database = Database(self.test2_config.database_url)
        self.database.create_schema()
        self.repository = Test2Repository(self.database, self.test2_config)
        self.environment = self.repository.runtime_mode()
        self.tokens, self.user_profiles = self._load_runtime_accounts()
        if not self.tokens:
            self.logger.warning(
                "No accounts have joined auto trading yet. Bot will start in watch mode "
                "and will begin trading when a user joins from the dashboard."
            )

        self.is_running = True
        self.is_trading_locked = False
        self.pip_size = 2
        self.last_tick_received_at = 0.0
        self.tick_sequence = 0
        self.connection_session_id = ""
        self.pending_signal: Optional[CandidateSignal] = None
        self.ticks_history = deque(maxlen=50)
        self.live_ticks_history = deque(maxlen=7)
        self.tick_sequence = self.repository.current_tick_sequence()
        historical_digits = self.repository.recent_digits(limit=6000)
        self.raw_tick_digits = deque(historical_digits, maxlen=10000)

        signal_cfg = self.test2_config.signal
        self.signal_detector = Over2SignalDetector(
            run_id=self.test2_config.model.run_id,
            trigger_name=signal_cfg.trigger_name,
            pattern_ranges=signal_cfg.pattern_ranges,
            overlapping_signals_allowed=signal_cfg.overlapping_signals_allowed,
            require_pattern_reset=signal_cfg.require_pattern_reset,
        )
        bayes_cfg = self.test2_config.bayesian
        self.bayesian = BayesianProbability(
            prior_alpha=bayes_cfg.prior_alpha,
            prior_beta=bayes_cfg.prior_beta,
            credible_interval=bayes_cfg.credible_interval,
            minimum_completed_trades=bayes_cfg.minimum_completed_trades,
        )
        wins, losses = self.repository.completed_outcomes()
        self.bayesian.restore(wins, losses)
        hmm_cfg = self.test2_config.hmm
        self.hmm = ThreeStateHmm(hmm_cfg.minimum_training_ticks)
        if self.hmm.train(list(self.raw_tick_digits)):
            self._persist_hmm_metadata()
        execution_cfg = self.test2_config.execution
        self.decision_engine = DecisionEngine(
            reject_if_new_tick_arrives=execution_cfg.reject_if_new_tick_arrives,
            maximum_signal_age_ms=execution_cfg.maximum_signal_age_ms,
            maximum_proposal_age_ms=execution_cfg.maximum_proposal_age_ms,
            bayesian_mode=bayes_cfg.mode,
            bayesian_confidence_threshold=bayes_cfg.minimum_probability_edge_confidence,
            hmm_mode=hmm_cfg.mode,
            favourable_state=hmm_cfg.favourable_state,
            favourable_state_threshold=hmm_cfg.minimum_favourable_state_probability,
        )
        cooldown_cfg = self.test2_config.cooldown
        self.cooldown = AdaptiveCooldown(
            after_win_ticks=cooldown_cfg.after_win_ticks,
            after_loss_ticks=cooldown_cfg.after_loss_ticks,
            after_three_consecutive_losses_ticks=cooldown_cfg.after_three_consecutive_losses_ticks,
            after_five_consecutive_losses_ticks=cooldown_cfg.after_five_consecutive_losses_ticks,
        )
        self.recovery_cfg = self.test2_config.recovery

        self.state_path = Path(self.cfg["files"]["state"])
        self.state_doc = load_state(self.state_path)
        self.cooldown_ticks_remaining = 0
        saved_bot_state = self.state_doc.get("bot", {})
        self.regime_outcomes = deque(
            [
                str(value).upper()
                for value in saved_bot_state.get("regime_outcomes", [])
                if str(value).upper() in {"WIN", "LOSS"}
            ],
            maxlen=self.recovery_cfg.rolling_window_trades,
        )
        self.shadow_outcomes = deque(
            [
                str(value).upper()
                for value in saved_bot_state.get("shadow_outcomes", [])
                if str(value).upper() in {"WIN", "LOSS"}
            ],
            maxlen=self.recovery_cfg.rolling_window_trades,
        )
        self.regime_guard_paused = bool(saved_bot_state.get("regime_guard_paused", False))
        self.regime_guard_reason = str(saved_bot_state.get("regime_guard_reason", ""))
        self.regime_consecutive_losses = int(saved_bot_state.get("regime_consecutive_losses", 0))
        self.shadow_consecutive_wins = int(saved_bot_state.get("shadow_consecutive_wins", 0))
        self.pending_shadow_signals: List[Dict[str, Any]] = []

        self.clients: Dict[str, Dict[str, Any]] = self._init_clients_from_state()
        self.sessions: Dict[str, ClientSession] = {}
        self.valid_clients: List[Tuple[str, str]] = [] # list of (token, account_id) pairs
        self.unresolved_contracts_from_state: Set[int] = set()

        # Trade cycle monitoring variables
        self.pending_contracts_for_current_cycle: Set[int] = set()
        self.cycle_outcomes: List[str] = []
        self.contract_signal_ids: Dict[int, str] = {}
        self.pending_by_signal: Dict[str, Set[int]] = {}
        self.outcomes_by_signal: Dict[str, List[str]] = {}
        self.pending_contract_started_at: Dict[int, datetime] = {}
        self.delayed_contracts_logged: Set[int] = set()

        self._watchdog_task: Optional[asyncio.Task] = None
        self._lease_task: Optional[asyncio.Task] = None
        self._background_tasks: Set[asyncio.Task] = set()
        self.worker_id = str(uuid.uuid4())
        self.lease_key = ""
        self.public_client = PublicMarketDataClient(self)
        self._managed_accounts_revision = self.repository.managed_accounts_revision()
        self._runtime_mode_cache = self.environment
        if self.regime_guard_paused:
            if not self.recovery_cfg.regime_guard_enabled:
                self._set_regime_guard(False, "REGIME_GUARD_DISABLED")
            elif self._shadow_resume_ready():
                self._set_regime_guard(False, "SHADOW_SIGNAL_HEALTH_RECOVERED_ON_STARTUP")
        self._save_state()

    def _load_global_token_accounts(self) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
        tokens_file = self.cfg["files"]["tokens"]
        tokens_file = os.getenv("DERIV_TOKENS_FILE", tokens_file)
        raw_tokens = load_tokens(tokens_file)
        tokens = decrypt_tokens(raw_tokens, self.encryption_key)
        users_file = self.cfg["files"].get("users", "users.json")
        users_file = os.getenv("DERIV_USER_FILE", users_file)
        profiles = load_user_profiles(users_file)
        for token in tokens:
            profile = profiles.setdefault(
                token,
                {
                    "id": token_tag(token),
                    "name": "Global bot account",
                    "enabled": True,
                    "account_id": "",
                },
            )
            profile.setdefault("auth_type", "global_token")
            profile.setdefault("source", "global")
        return tokens, profiles

    def _load_runtime_accounts(self) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
        managed_accounts = self.repository.list_managed_accounts()
        if not managed_accounts and legacy_global_tokens_enabled():
            tokens, profiles = self._load_global_token_accounts()
        else:
            tokens = []
            profiles = {}

        def add_runtime_token(token: str, profile: Dict[str, Any]) -> None:
            if token in profiles:
                profiles[token].update({k: v for k, v in profile.items() if v not in {"", None}})
                return
            tokens.append(token)
            profiles[token] = profile

        if managed_accounts:
            for row in managed_accounts:
                if not row.enabled:
                    continue
                try:
                    payload = decrypt_auth_payload(row.token_secret, self.encryption_key)
                except Exception as exc:
                    self.logger.error("Managed token %s could not be decrypted: %s", row.id, exc)
                    continue
                auth_type = str(payload.get("auth_type", "pat")).strip() or "pat"
                if auth_type == "oauth" and token_is_expiring(payload):
                    refresh_token_value = str(payload.get("refresh_token", "")).strip()
                    if not refresh_token_value:
                        self.logger.error(
                            "Managed OAuth account %s is missing a refresh token",
                            row.id,
                        )
                        continue
                    try:
                        refreshed = refresh_access_token(
                            client_id=str(self.test2_config.deriv.oauth_client_id or self.app_id),
                            refresh_token=refresh_token_value,
                        )
                    except Exception as exc:
                        self.logger.error(
                            "Managed OAuth account %s could not refresh its token: %s",
                            row.id,
                            exc,
                        )
                        continue
                    payload.update(refreshed)
                    token_secret = encrypt_auth_payload(payload, self.encryption_key)
                    try:
                        self.repository.update_managed_account(
                            int(row.id),
                            label=row.label or f"Account {row.id}",
                            token_secret=token_secret,
                            enabled=bool(row.enabled),
                        )
                    except Exception as exc:
                        self.logger.error(
                            "Managed OAuth account %s could not persist refreshed token: %s",
                            row.id,
                            exc,
                        )
                        continue
                token = self._purchase_token_from_payload(payload)
                if not token:
                    self.logger.error(
                        "Managed account %s is missing a Deriv API token/PAT required "
                        "for REST bulk purchase; OAuth login alone cannot execute contracts.",
                        row.id,
                    )
                    continue
                add_runtime_token(
                    token,
                    {
                        "id": str(row.id),
                        "name": row.label or f"Account {row.id}",
                        "enabled": True,
                        "account_id": str(payload.get("account_id", "")).strip(),
                        "auth_type": "pat" if auth_type == "oauth" else auth_type,
                        "source": "private",
                    },
                )
            if tokens:
                return tokens, profiles
            self.logger.warning(
                "Managed accounts are configured, but none are enabled and valid; "
                "staying in watch mode until a user joins auto trading."
            )
            return [], {}

        if not legacy_global_tokens_enabled():
            self.logger.warning(
                "No managed PAT accounts are enabled; legacy token-file trading is disabled."
            )
            return [], {}

        return tokens, profiles

    def _persist_hmm_metadata(self) -> None:
        model_id = f"hmm-{self.test2_config.model.run_id}-{self.tick_sequence}"
        first_sequence = max(1, self.tick_sequence - len(self.raw_tick_digits) + 1)
        try:
            metadata = persist_model_metadata(
                "model_artifacts",
                model_id=model_id,
                model_version=self.test2_config.model.version,
                training_run_id=self.test2_config.model.run_id,
                training_tick_range=(first_sequence, self.tick_sequence),
                observation_count=len(self.raw_tick_digits),
                state_mappings={
                    "0": "MEAN_REVERSION",
                    "1": "NEUTRAL_RANDOM",
                    "2": "CONTINUATION",
                },
                validation_metrics={"framework_ready": True},
            )
        except OSError as exc:
            self.logger.warning("HMM metadata persistence skipped: %s", exc)
            return
        self.repository.record_model_artifact(
            model_type="HMM",
            model_version=self.test2_config.model.version,
            storage_location=f"model_artifacts/{model_id}.json",
            metadata=metadata,
            checksum=metadata["checksum"],
        )

    def _init_clients_from_state(self) -> Dict[str, Dict[str, Any]]:
        today = today_local_iso()
        clients_doc = self.state_doc.get("clients", {})
        base_stake = float(self.cfg["strategy"]["initial_stake"])
        clients_doc_by_user_id: Dict[str, Dict[str, Any]] = {}
        clients_doc_by_account_id: Dict[str, Dict[str, Any]] = {}

        for value in clients_doc.values():
            if not isinstance(value, dict):
                continue
            user_id = str(value.get("user_id", "")).strip()
            account_id = str(value.get("account_id", "")).strip()
            if user_id and user_id not in clients_doc_by_user_id:
                clients_doc_by_user_id[user_id] = value
            if account_id and account_id not in clients_doc_by_account_id:
                clients_doc_by_account_id[account_id] = value

        clients: Dict[str, Dict[str, Any]] = {}
        for token in self.tokens:
            tag = token_tag(token)
            profile = self.user_profiles.get(token, {})
            user_id = str(profile.get("id", tag)).strip() or tag
            account_id = str(profile.get("account_id", "")).strip()
            existing = (
                clients_doc.get(tag)
                or clients_doc_by_user_id.get(user_id)
                or clients_doc_by_account_id.get(account_id)
                or {}
            )
            clients[token] = self._build_client_state(
                token=token,
                profile=profile,
                existing=existing,
                today=today,
                base_stake=base_stake,
            )
        return clients

    def _build_client_state(
        self,
        *,
        token: str,
        profile: Dict[str, Any],
        existing: Dict[str, Any],
        today: str,
        base_stake: float,
    ) -> Dict[str, Any]:
        tag = token_tag(token)
        user_id = str(profile.get("id", tag)).strip() or tag
        account_id = str(profile.get("account_id", existing.get("account_id", ""))).strip()
        st = {
            "token_tag": tag,
            "user_id": user_id,
            "name": str(profile.get("name", existing.get("name", tag))),
            "account_id": account_id,
            "total_profit": float(existing.get("total_profit", 0.0)),
            "profit_today": float(existing.get("profit_today", 0.0)),
            "current_stake": float(existing.get("current_stake", base_stake)),
            "day": str(existing.get("day", today)),
            "total_trades": int(existing.get("total_trades", 0)),
            "wins": int(existing.get("wins", 0)),
            "losses": int(existing.get("losses", 0)),
            "last_result": str(existing.get("last_result", "idle")),
            "last_profit": float(existing.get("last_profit", 0.0)),
            "loss_streak": int(existing.get("loss_streak", 0)),
            "recovery_loss_pool": float(existing.get("recovery_loss_pool", 0.0)),
            "recovery_wins_remaining": max(
                1,
                int(existing.get("recovery_wins_remaining", self.recovery_runs)),
            ),
            "last_profit_ratio": float(existing.get("last_profit_ratio", 0.0)),
            "oscar_debt": float(
                existing.get(
                    "oscar_debt",
                    existing.get("recovery_loss_pool", 0.0),
                )
            ),
            "oscar_win_streak": int(existing.get("oscar_win_streak", 0)),
            "single_recovery_pending": bool(
                existing.get("single_recovery_pending", False)
            ),
            "single_recovery_active": bool(
                existing.get("single_recovery_active", False)
            ),
        }
        if st["day"] != today:
            st["profit_today"] = 0.0
            st["day"] = today
        return st

    def _sync_clients_with_runtime_accounts(self) -> None:
        today = today_local_iso()
        base_stake = float(self.cfg["strategy"]["initial_stake"])
        existing_by_user_id: Dict[str, Dict[str, Any]] = {}
        existing_by_account_id: Dict[str, Dict[str, Any]] = {}
        existing_by_tag: Dict[str, Dict[str, Any]] = {}

        for state in self.clients.values():
            user_id = str(state.get("user_id", "")).strip()
            account_id = str(state.get("account_id", "")).strip()
            tag = str(state.get("token_tag", "")).strip()
            if user_id and user_id not in existing_by_user_id:
                existing_by_user_id[user_id] = state
            if account_id and account_id not in existing_by_account_id:
                existing_by_account_id[account_id] = state
            if tag and tag not in existing_by_tag:
                existing_by_tag[tag] = state

        next_clients: Dict[str, Dict[str, Any]] = {}
        for token in self.tokens:
            profile = self.user_profiles.get(token, {})
            tag = token_tag(token)
            user_id = str(profile.get("id", tag)).strip() or tag
            account_id = str(profile.get("account_id", "")).strip()
            existing = (
                self.clients.get(token)
                or existing_by_user_id.get(user_id)
                or existing_by_account_id.get(account_id)
                or existing_by_tag.get(tag)
                or {}
            )
            next_clients[token] = self._build_client_state(
                token=token,
                profile=profile,
                existing=existing,
                today=today,
                base_stake=base_stake,
            )
        self.clients = next_clients

    def _client_state_for_token(
        self,
        token: str,
        *,
        account_id: str = "",
    ) -> Dict[str, Any]:
        state = self.clients.get(token)
        if state is not None:
            return state

        profile = self.user_profiles.get(token, {})
        user_id = str(profile.get("id", "")).strip()
        if user_id:
            for existing in self.clients.values():
                if str(existing.get("user_id", "")).strip() == user_id:
                    return existing

        account_id = str(account_id or profile.get("account_id", "")).strip()
        if account_id:
            for existing in self.clients.values():
                if str(existing.get("account_id", "")).strip() == account_id:
                    return existing

        raise KeyError(token)

    def _save_state(self) -> None:
        doc = {
            "version": 6,
            "bot": {
                "run_id": self.test2_config.model.run_id,
                "cooldown_ticks_remaining": self.cooldown_ticks_remaining,
                "environment": self.environment,
                "symbol": self.symbol,
                "is_trading_locked": self.is_trading_locked,
                "pending_contract_count": len(self.pending_contracts_for_current_cycle),
                "last_tick_received_at": self.last_tick_received_at,
                "regime_guard_paused": self.regime_guard_paused,
                "regime_guard_reason": self.regime_guard_reason,
                "regime_consecutive_losses": self.regime_consecutive_losses,
                "regime_outcomes": list(self.regime_outcomes),
                "shadow_outcomes": list(self.shadow_outcomes),
                "shadow_consecutive_wins": self.shadow_consecutive_wins,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "clients": {},
            "unresolved_contracts": []
        }
        for token, st in self.clients.items():
            doc["clients"][st["token_tag"]] = {
                "user_id": st["user_id"],
                "name": st["name"],
                "account_id": st.get("account_id", ""),
                "total_profit": st["total_profit"],
                "profit_today": st["profit_today"],
                "current_stake": st["current_stake"],
                "day": st["day"],
                "total_trades": st["total_trades"],
                "wins": st["wins"],
                "losses": st["losses"],
                "last_result": st["last_result"],
                "last_profit": st["last_profit"],
                "loss_streak": st["loss_streak"],
                "recovery_loss_pool": st["recovery_loss_pool"],
                "recovery_wins_remaining": st["recovery_wins_remaining"],
                "last_profit_ratio": st["last_profit_ratio"],
                "oscar_debt": st["oscar_debt"],
                "oscar_win_streak": st["oscar_win_streak"],
                "single_recovery_pending": st["single_recovery_pending"],
                "single_recovery_active": st["single_recovery_active"],
            }

        unresolved = []
        for token, session in self.sessions.items():
            for cid in session.pending_contracts:
                unresolved.append({
                    "token_tag": token_tag(token),
                    "contract_id": cid,
                    "account_id_masked": f"{session.account_id[:3]}***{session.account_id[-3:]}",
                })
        doc["unresolved_contracts"] = unresolved
        _atomic_write_json(self.state_path, doc)

    def _spawn_background_task(self, coro: Any, *, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)

        def _on_done(done_task: asyncio.Task) -> None:
            self._background_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.logger.error("Background task %s failed: %s", name, exc)

        task.add_done_callback(_on_done)

    def _cooldown_remaining_ticks(self) -> int:
        return max(0, int(self.cooldown.state.ticks_remaining))

    def _is_cooldown_active(self) -> bool:
        return self._cooldown_remaining_ticks() > 0

    def _cooldown_note(self) -> str:
        remaining = self._cooldown_remaining_ticks()
        return f"trade=COOLDOWN {remaining} ticks"

    @property
    def base_stake(self) -> float:
        return float(self.cfg["strategy"]["initial_stake"])

    def _last_three_ticks_rising(self) -> bool:
        if len(self.ticks_history) < 3:
            return False
        quotes = [float(item["quote"]) for item in list(self.ticks_history)[-3:]]
        return quotes[0] < quotes[1] < quotes[2]

    @property
    def recovery_stake_cap(self) -> float:
        return float(self.recovery_cfg.maximum_stake)

    @property
    def recovery_runs(self) -> int:
        return max(1, int(getattr(self.recovery_cfg, "recovery_runs", 2)))

    def _round_stake_up(self, value: float) -> float:
        return math.ceil(max(self.base_stake, value) * 100.0 - 1e-9) / 100.0

    def _recovery_stake_for_debt(
        self,
        debt: float,
        profit_ratio: float,
        wins_remaining: int | None = None,
    ) -> float:
        debt = max(0.0, float(debt))
        ratio = max(0.0, float(profit_ratio))
        if debt <= 1e-9 or ratio <= 1e-9:
            return self.base_stake

        remaining = max(1, int(wins_remaining or self.recovery_runs))
        stake = self._round_stake_up((debt / remaining) / ratio)
        return min(self.recovery_stake_cap, stake)

    def _planned_stake_for_accounts(self, profit_ratio: float) -> float:
        required = self.base_stake
        for token, account_id in self.valid_clients:
            state = self._client_state_for_token(token, account_id=account_id)
            debt = max(
                0.0,
                float(state.get("recovery_loss_pool", state.get("oscar_debt", 0.0))),
            )
            if state.get("single_recovery_pending") or debt > 0:
                required = max(
                    required,
                    self._recovery_stake_for_debt(
                        debt,
                        profit_ratio,
                        int(state.get("recovery_wins_remaining", self.recovery_runs)),
                    ),
                )
        return round(required, 2)

    def _update_client_recovery_state(
        self,
        state: Dict[str, Any],
        *,
        outcome: str,
        profit: float,
    ) -> None:
        settled_stake = float(state.get("current_stake", self.base_stake))
        if outcome == "win":
            state["loss_streak"] = 0
            state["oscar_win_streak"] = 0
            state["single_recovery_active"] = False
            prior_debt = max(
                0.0,
                float(state.get("recovery_loss_pool", state.get("oscar_debt", 0.0))),
            )
            if prior_debt <= 1e-9:
                state["oscar_debt"] = 0.0
                state["recovery_loss_pool"] = 0.0
                state["recovery_wins_remaining"] = self.recovery_runs
                state["single_recovery_pending"] = False
                state["current_stake"] = self.base_stake
                return

            recovered = max(0.0, float(profit))
            remaining_debt = round(max(0.0, prior_debt - recovered), 2)
            remaining_wins = max(
                0,
                int(state.get("recovery_wins_remaining", self.recovery_runs)) - 1,
            )
            if remaining_debt <= 0.01:
                state["oscar_debt"] = 0.0
                state["recovery_loss_pool"] = 0.0
                state["recovery_wins_remaining"] = self.recovery_runs
                state["single_recovery_pending"] = False
                state["current_stake"] = self.base_stake
                return

            state["oscar_debt"] = remaining_debt
            state["recovery_loss_pool"] = remaining_debt
            state["recovery_wins_remaining"] = max(1, remaining_wins)
            state["single_recovery_pending"] = True
            state["current_stake"] = self._recovery_stake_for_debt(
                remaining_debt,
                float(state.get("last_profit_ratio", 0.0)),
                state["recovery_wins_remaining"],
            )
            return

        state["loss_streak"] = int(state.get("loss_streak", 0)) + 1
        state["oscar_win_streak"] = 0
        state["single_recovery_active"] = False
        prior_debt = max(
            0.0,
            float(state.get("recovery_loss_pool", state.get("oscar_debt", 0.0))),
        )
        loss_amount = abs(profit) if profit < 0 else settled_stake
        debt = round(prior_debt + loss_amount, 2)
        state["oscar_debt"] = round(debt, 2)
        state["recovery_loss_pool"] = round(debt, 2)
        state["recovery_wins_remaining"] = self.recovery_runs
        state["single_recovery_pending"] = True
        state["current_stake"] = self._recovery_stake_for_debt(
            debt,
            float(state.get("last_profit_ratio", 0.0)),
            state["recovery_wins_remaining"],
        )

    def _win_rate(self, outcomes: deque) -> float:
        if not outcomes:
            return 0.0
        return sum(value == "WIN" for value in outcomes) / len(outcomes)

    def _recent_shadow_outcomes(self) -> List[str]:
        sample_count = self.recovery_cfg.shadow_min_samples
        return list(self.shadow_outcomes)[-sample_count:]

    def _shadow_resume_ready(self) -> bool:
        recent = self._recent_shadow_outcomes()
        if len(recent) < self.recovery_cfg.shadow_min_samples:
            return False
        return (
            self._win_rate(deque(recent))
            >= self.recovery_cfg.resume_above_shadow_win_rate
            and self.shadow_consecutive_wins
            >= self.recovery_cfg.shadow_consecutive_wins_required
        )

    def _set_regime_guard(self, paused: bool, reason: str = "") -> None:
        if self.regime_guard_paused == paused and self.regime_guard_reason == reason:
            return
        self.regime_guard_paused = paused
        self.regime_guard_reason = reason
        if paused:
            self.shadow_outcomes.clear()
            self.shadow_consecutive_wins = 0
            self.logger.warning("REGIME_GUARD_PAUSED reason=%s", reason)
        else:
            self.logger.info("REGIME_GUARD_RESUMED reason=%s", reason or "signal_health_recovered")
            self.regime_consecutive_losses = 0
        self._save_state()

    def _record_real_cycle_outcome(self, outcome: str) -> None:
        if not self.recovery_cfg.regime_guard_enabled:
            return
        normalized = "WIN" if outcome == "win" else "LOSS"
        self.regime_outcomes.append(normalized)
        if normalized == "LOSS":
            self.regime_consecutive_losses += 1
        else:
            self.regime_consecutive_losses = 0

        if self.regime_consecutive_losses >= self.recovery_cfg.pause_after_consecutive_losses:
            self._set_regime_guard(
                True,
                f"{self.regime_consecutive_losses}_CONSECUTIVE_LOSSES",
            )
            return

        if len(self.regime_outcomes) >= self.recovery_cfg.rolling_window_trades:
            win_rate = self._win_rate(self.regime_outcomes)
            if win_rate < self.recovery_cfg.pause_below_win_rate:
                self._set_regime_guard(
                    True,
                    f"ROLLING_WIN_RATE_{win_rate:.2f}",
                )

    def _record_shadow_outcome(self, outcome: str, signal_id: str) -> None:
        normalized = "WIN" if outcome == "win" else "LOSS"
        self.shadow_outcomes.append(normalized)
        if normalized == "WIN":
            self.shadow_consecutive_wins += 1
        else:
            self.shadow_consecutive_wins = 0
        recent = self._recent_shadow_outcomes()
        win_rate = self._win_rate(deque(recent))
        self.logger.info(
            "REGIME_SHADOW_RESULT signal_id=%s outcome=%s shadow_samples=%s shadow_win_rate=%.2f",
            signal_id,
            normalized,
            len(recent),
            win_rate,
        )
        if self.regime_guard_paused and self._shadow_resume_ready():
            self._set_regime_guard(False, "SHADOW_SIGNAL_HEALTH_RECOVERED")
        else:
            self._save_state()

    def _evaluate_pending_shadow_signals(self, final_digit: int) -> None:
        if not self.pending_shadow_signals:
            return
        remaining: List[Dict[str, Any]] = []
        for item in self.pending_shadow_signals:
            if int(item["tick_sequence"]) >= self.tick_sequence:
                remaining.append(item)
                continue
            outcome = "win" if final_digit > int(self.contract_barrier) else "loss"
            self._record_shadow_outcome(outcome, str(item["signal_id"]))
        self.pending_shadow_signals = remaining

    def _record_regime_guard_signal(self, signal: CandidateSignal, digits_display: str) -> None:
        self.repository.record_candidate(signal)
        self.repository.mark_signal(signal.signal_id, status="SKIP_REGIME_GUARD")
        self.pending_shadow_signals.append(
            {
                "signal_id": signal.signal_id,
                "tick_sequence": signal.tick_sequence,
            }
        )
        self.logger.warning(
            "SIGNAL_SKIPPED signal_id=%s digits=[%s] trigger=%s status=SKIP_REGIME_GUARD reason=%s",
            signal.signal_id,
            digits_display,
            signal.trigger_name,
            self.regime_guard_reason,
        )
        self._render_live_ticks(note="trade=SHADOW_GUARD")

    def _record_blocked_signal(
        self,
        signal: CandidateSignal,
        *,
        status: str,
        digits_display: str,
        note: str,
    ) -> None:
        self.repository.record_candidate(signal)
        self.repository.mark_signal(signal.signal_id, status=status)
        self.logger.info(
            "SIGNAL_SKIPPED signal_id=%s digits=[%s] trigger=%s status=%s",
            signal.signal_id,
            digits_display,
            signal.trigger_name,
            status,
        )
        self._render_live_ticks(note=note)

    def _build_candidate_signal_from_ticks(
        self,
        ticks: List[Dict[str, Any]],
        *,
        connection_session_id: str,
        tick_sequence: int,
    ) -> CandidateSignal:
        required = len(TEST2_PATTERN_RANGES)
        window = ticks[-required:]
        trigger_digits = tuple(int(item["last_digit"]) for item in window)
        newest = window[-1]
        return CandidateSignal(
            signal_id=str(uuid.uuid4()),
            run_id=self.test2_config.model.run_id,
            symbol=self.symbol,
            contract_type=self.contract_type,
            barrier=self.contract_barrier,
            trigger_name=TEST2_TRIGGER,
            trigger_digits=trigger_digits,
            signal_tick_epoch=int(newest["epoch"]),
            signal_tick_id=str(newest["tick_id"]),
            signal_last_digit=int(newest["last_digit"]),
            generated_at=datetime.now(timezone.utc).isoformat(),
            generated_monotonic=time.monotonic(),
            connection_session_id=connection_session_id,
            tick_sequence=tick_sequence,
        )

    def _consume_cooldown_tick(self) -> None:
        ended = self.cooldown.observe_tick()
        self.cooldown_ticks_remaining = self.cooldown.state.ticks_remaining
        if ended:
            self.logger.info(
                "COOLDOWN_ENDED reason=%s",
                self.cooldown.state.reason,
            )
        self._save_state()

    def _register_trade_cycle_outcome(self, outcome: str) -> None:
        state = self.cooldown.register_outcome(outcome)
        self.cooldown_ticks_remaining = state.ticks_remaining
        self.logger.info(
            "COOLDOWN_STARTED result=%s reason=%s cooldown_ticks=%s",
            str(outcome).upper(),
            state.reason,
            self.cooldown_ticks_remaining,
        )
        self._save_state()

    def _get_live_console_handler(self) -> Optional[LiveConsoleHandler]:
        handler = getattr(self.logger, "live_console_handler", None)
        return handler if isinstance(handler, LiveConsoleHandler) else None

    def _render_live_ticks(self, note: str = "") -> None:
        handler = self._get_live_console_handler()
        if handler is None or not self.live_ticks_history:
            return

        symbol = self.symbol
        digits_display = " | ".join(t["last_digit"] for t in self.live_ticks_history)
        quotes_display = " | ".join(t["display"] for t in self.live_ticks_history)
        state = note
        if not state:
            if self.is_trading_locked:
                state = "trade=ACTIVE"
            elif self._is_cooldown_active():
                state = self._cooldown_note()
            else:
                state = "trade=WATCHING"
        handler.set_status(
            f"LIVE {symbol} | digits=[{digits_display}] | ticks=[{quotes_display}] | {state}"
        )

    def _clear_live_ticks(self) -> None:
        handler = self._get_live_console_handler()
        if handler is not None:
            handler.clear_status()

    def _mark_tick_received(self) -> None:
        self.last_tick_received_at = time.monotonic()

    def _on_public_connection_established(self) -> None:
        previous = self.connection_session_id
        self.connection_session_id = str(uuid.uuid4())
        if previous and self.pending_signal and not self.pending_signal.consumed:
            self.repository.mark_signal(
                self.pending_signal.signal_id,
                status="SKIP_CONNECTION_UNHEALTHY",
                stale=True,
            )
            self.pending_signal = None

    def _reset_session_runtime_state(self) -> None:
        self.is_trading_locked = False
        self.last_tick_received_at = 0.0
        self.ticks_history.clear()
        self.live_ticks_history.clear()
        self.pending_contract_started_at.clear()
        self.delayed_contracts_logged.clear()
        self._clear_live_ticks()

    def _contract_age_seconds(self, contract_id: int) -> float:
        opened_at = self.pending_contract_started_at.get(int(contract_id))
        if not opened_at:
            return 0.0
        return max(0.0, (datetime.now(timezone.utc) - opened_at).total_seconds())

    def _find_session_for_contract(
        self, contract_id: int
    ) -> Optional[Tuple[str, ClientSession]]:
        for token, session in self.sessions.items():
            if contract_id in session.pending_contracts:
                return token, session
        return None

    async def _reconcile_pending_contract(self, contract_id: int, reason: str) -> None:
        match = self._find_session_for_contract(contract_id)
        if match is None:
            self.logger.warning(
                "Contract %s still pending but no live session owns it",
                contract_id,
                extra={"contract_id": str(contract_id)},
            )
            return
        token, session = match
        snapshot = await session.request_contract_snapshot(contract_id)
        if "error" in snapshot:
            self.logger.warning(
                "Contract reconciliation failed for %s after %s seconds (%s): %s",
                contract_id,
                int(self._contract_age_seconds(contract_id)),
                reason,
                snapshot["error"].get("message"),
                extra={"token_tag": token_tag(token), "contract_id": str(contract_id)},
            )
            return
        contract = snapshot.get("proposal_open_contract")
        if not contract:
            return
        if contract.get("is_sold"):
            await self.handle_contract_update(token, int(contract_id), contract)
            return
        if contract_id not in self.delayed_contracts_logged:
            self.delayed_contracts_logged.add(contract_id)
            self.logger.warning(
                "OPEN_CONTRACT_DELAYED contract_id=%s age_seconds=%s reason=%s status=%s",
                contract_id,
                int(self._contract_age_seconds(contract_id)),
                reason,
                contract.get("status", "unknown"),
                extra={"token_tag": token_tag(token), "contract_id": str(contract_id)},
            )

    async def validate_accounts(self) -> None:
        """Fetch and validate account IDs REST-side, sorting demo and real accounts."""
        self.environment = self.repository.runtime_mode()
        self.tokens, self.user_profiles = self._load_runtime_accounts()
        valid = []
        account_indexes: Dict[str, int] = {}
        for token in self.tokens:
            tag = token_tag(token)
            profile = self.user_profiles.get(token, {})
            preferred_account_id = str(profile.get("account_id", "")).strip()
            self.logger.info("Validating account for token...", extra={"token_tag": tag})

            path = "/trading/v1/options/accounts"
            resp = await _rest_request("GET", path, self.app_id, self.rest_base_url, token=token)

            if "error" in resp:
                self.logger.error("Account verification failed: %s", resp["error"].get("message"), extra={"token_tag": tag})
                continue

            accounts = resp.get("data", [])
            matched = None
            if preferred_account_id:
                matched = next((acc for acc in accounts if acc.get("account_id") == preferred_account_id), None)
                if matched and matched.get("account_type") != self.environment:
                    self.logger.error(
                        "Configured account %s does not match environment %s",
                        mask_account_id(preferred_account_id),
                        self.environment,
                        extra={"token_tag": tag},
                    )
                    matched = None
            if matched is None:
                for acc in accounts:
                    if acc.get("account_type") == self.environment:
                        matched = acc
                        break

            if not matched:
                self.logger.error("No valid options %s account found for token", self.environment, extra={"token_tag": tag})
                continue

            account_id = matched["account_id"]
            profile["account_id"] = account_id
            existing_index = account_indexes.get(account_id)
            if existing_index is not None:
                existing_token, _ = valid[existing_index]
                if (
                    not self._bulk_purchase_token_capable(existing_token)
                    and self._bulk_purchase_token_capable(token)
                ):
                    valid[existing_index] = (token, account_id)
                    self.logger.warning(
                        "Duplicate login/token for account %s replaced with bulk-capable token",
                        mask_account_id(account_id),
                        extra={
                            "token_tag": tag,
                            "masked_account_id": mask_account_id(account_id),
                        },
                    )
                else:
                    self.logger.warning(
                        "Duplicate login/token for account %s ignored; account already has one trading slot",
                        mask_account_id(account_id),
                        extra={
                            "token_tag": tag,
                            "masked_account_id": mask_account_id(account_id),
                        },
                    )
                continue
            account_indexes[account_id] = len(valid)
            self.repository.update_account_balance(
                account_id=account_id,
                balance=float(matched.get("balance", 0.0)),
                currency=str(matched.get("currency", "USD")),
                status=str(matched.get("status", "active")),
            )
            self.logger.info(
                "Successfully validated %s account: %s",
                self.environment,
                mask_account_id(account_id),
                extra={
                    "token_tag": tag,
                    "masked_account_id": mask_account_id(account_id),
                },
            )
            valid.append((token, account_id))

        self.valid_clients = valid
        if not self.valid_clients:
            self.logger.warning(
                "No valid Options %s accounts are currently enabled; worker will keep watching.",
                self.environment,
            )
            return
        self._sync_running_status_after_validation()

    def _sync_running_status_after_validation(self) -> None:
        status, pause_reason = self.repository.control_state()
        if status == "MANUAL_PAUSE" and pause_reason != "COPY_PURCHASE_PARTIAL":
            self.logger.warning(
                "Clearing stale MANUAL_PAUSE status reason=%s after validating PAT-ready accounts.",
                pause_reason or "none",
            )
            self.repository.set_status("RUNNING")
            return
        if status not in {"MANUAL_PAUSE", "EMERGENCY_STOP"}:
            self.repository.set_status("RUNNING")

    async def _ensure_sessions_for_valid_clients(self) -> None:
        for token, account_id in self.valid_clients:
            if token in self.sessions:
                continue
            session = ClientSession(token, account_id, self)
            self.sessions[token] = session
            session.task = asyncio.create_task(session.connect_and_run())

    async def _refresh_runtime_accounts_if_needed(self) -> None:
        current_revision = self.repository.managed_accounts_revision()
        current_mode = self.repository.runtime_mode()
        if (
            current_revision == self._managed_accounts_revision
            and current_mode == self._runtime_mode_cache
        ):
            return
        self._managed_accounts_revision = current_revision
        self._runtime_mode_cache = current_mode
        await self.validate_accounts()
        self._sync_clients_with_runtime_accounts()
        await self._ensure_sessions_for_valid_clients()

    async def _on_tick(self, tick_data: Dict[str, Any]) -> None:
        tick = tick_data["tick"]
        quote = float(tick["quote"])
        display_value = f"{quote:.{self.pip_size}f}"
        last_digit = display_value[-1]
        epoch = int(tick["epoch"])
        tick_id = str(tick.get("id") or f"{epoch}:{display_value}")
        self._mark_tick_received()
        self.tick_sequence += 1

        tick_snapshot = {
            "quote": quote,
            "display": display_value,
            "last_digit": last_digit,
            "epoch": epoch,
            "tick_id": tick_id,
        }
        self.live_ticks_history.append(tick_snapshot)
        self.ticks_history.append(tick_snapshot)
        self.raw_tick_digits.append(int(last_digit))
        self.repository.record_tick(
            sequence_id=self.tick_sequence,
            symbol=str(tick.get("symbol") or self.symbol),
            epoch=epoch,
            tick_id=tick_id,
            quote=quote,
            final_digit=int(last_digit),
            connection_session_id=self.connection_session_id,
        )
        self._evaluate_pending_shadow_signals(int(last_digit))
        self._render_live_ticks()

        hmm_cfg = self.test2_config.hmm
        if (
            hmm_cfg.enabled
            and len(self.raw_tick_digits) >= hmm_cfg.minimum_training_ticks
            and (
                not self.hmm.trained
                or self.tick_sequence % hmm_cfg.retrain_every_ticks == 0
            )
        ):
            if self.hmm.train(list(self.raw_tick_digits)):
                self._persist_hmm_metadata()

        digits_display = " | ".join(
            t["last_digit"] for t in list(self.ticks_history)[-self.pattern_length :]
        )
        self.logger.debug("tick %s last_digits=[%s]", display_value, digits_display)
        last_digits = [t["last_digit"] for t in list(self.ticks_history)]
        raw_match = detect_digit_streak_signal(last_digits, self.pattern_length)
        if raw_match is not None:
            self.logger.info(
                "RAW_MATCH_DETECTED digits=[%s] trigger=%s tick_sequence=%s",
                digits_display,
                raw_match[2],
                self.tick_sequence,
            )

        signal = self.signal_detector.observe(
            list(self.ticks_history),
            connection_session_id=self.connection_session_id,
            tick_sequence=self.tick_sequence,
        )
        if raw_match is not None and signal is None:
            signal = self._build_candidate_signal_from_ticks(
                list(self.ticks_history),
                connection_session_id=self.connection_session_id,
                tick_sequence=self.tick_sequence,
            )
            self.logger.warning(
                "RAW_MATCH_RECOVERED digits=[%s] tick_sequence=%s detector_returned_none",
                digits_display,
                self.tick_sequence,
            )

        if self.is_trading_locked:
            if signal is not None:
                self._record_blocked_signal(
                    signal,
                    status="SKIP_TRADING_LOCK",
                    digits_display=digits_display,
                    note="trade=TRADING_LOCK",
                )
            return

        if self._is_cooldown_active():
            if signal is not None:
                self._record_blocked_signal(
                    signal,
                    status="SKIP_COOLDOWN",
                    digits_display=digits_display,
                    note=self._cooldown_note(),
                )
            self._consume_cooldown_tick()
            self._render_live_ticks(note=self._cooldown_note() if self._is_cooldown_active() else "trade=WATCHING")
            return

        status, pause_reason = self.repository.control_state()
        if status == "MANUAL_PAUSE" and pause_reason != "COPY_PURCHASE_PARTIAL":
            self.logger.warning(
                "Clearing stale MANUAL_PAUSE status reason=%s before evaluating signal.",
                pause_reason or "none",
            )
            self.repository.set_status("RUNNING")
            status = "RUNNING"
        if status in {"STOPPED", "MANUAL_PAUSE", "EMERGENCY_STOP"}:
            if signal is not None:
                blocked_status = {
                    "STOPPED": "SKIP_STOPPED",
                    "MANUAL_PAUSE": "SKIP_MANUAL_PAUSE",
                    "EMERGENCY_STOP": "SKIP_EMERGENCY_STOP",
                }[status]
                self._record_blocked_signal(
                    signal,
                    status=blocked_status,
                    digits_display=digits_display,
                    note=f"trade={status}",
                )
            else:
                self._render_live_ticks(note=f"trade={status}")
            return

        if signal is None:
            return

        if self.test2_config.execution.require_rising_ticks and not self._last_three_ticks_rising():
            self._record_blocked_signal(
                signal,
                status="SKIP_NOT_RISING",
                digits_display=digits_display,
                note="trade=NOT_RISING",
            )
            return

        if self.recovery_cfg.regime_guard_enabled and self.regime_guard_paused:
            self._record_regime_guard_signal(signal, digits_display)
            return

        self.repository.record_candidate(signal)
        self.pending_signal = signal
        self.is_trading_locked = True
        self._render_live_ticks(note=f"CANDIDATE {signal.trigger_name}")
        self.logger.info(
            "SIGNAL_CREATED signal_id=%s digits=[%s] trigger=%s contract_type=%s barrier=%s",
            signal.signal_id,
            digits_display,
            signal.trigger_name,
            signal.contract_type,
            signal.barrier,
        )
        self._spawn_background_task(
            self._purchase_for_multiple_accounts(signal),
            name=f"purchase_{signal.signal_id}",
        )

    async def _purchase_for_multiple_accounts(
        self,
        signal: CandidateSignal,
    ) -> None:
        """Evaluate one Test 2 candidate and submit a new-API bulk purchase."""
        try:
            await self._refresh_runtime_accounts_if_needed()
            base_stake = self.base_stake
            validate_contract_parameters(
                contract_type=signal.contract_type,
                barrier=signal.barrier,
                symbol=signal.symbol,
                stake=base_stake,
                duration=self.duration,
                duration_unit=self.duration_unit,
            )
            self.logger.info(
                "Validating candidate via proposal request signal_id=%s",
                signal.signal_id,
            )
            proposal_requested = time.monotonic()
            self.repository.mark_signal(
                signal.signal_id,
                status="PROPOSAL_REQUESTED",
                proposal_requested=True,
            )
            (
                prop_resp,
                proposal_requested,
                proposal_received,
            ) = await self._send_proposal_request(signal, base_stake)
            if "error" in prop_resp:
                self.logger.error("Proposal validation rejected: %s", prop_resp["error"].get("message"))
                self.repository.mark_signal(
                    signal.signal_id,
                    status="SKIP_INVALID_PROPOSAL",
                    proposal_received=True,
                )
                return

            echo = prop_resp.get("echo_req", {})
            for key, expected in {
                "contract_type": signal.contract_type,
                "barrier": signal.barrier,
                "underlying_symbol": signal.symbol,
            }.items():
                if key in echo and str(echo[key]) != expected:
                    self.repository.mark_signal(
                        signal.signal_id,
                        status="SKIP_INVALID_PROPOSAL",
                        proposal_received=True,
                    )
                    self.logger.error("Proposal echo mismatch for %s", key)
                    return

            preliminary = self.bayesian.snapshot(0.60, self.test2_config.bayesian.safety_margin_probability)
            try:
                economics = parse_proposal_economics(
                    prop_resp,
                    stake=base_stake,
                    predicted_probability=preliminary.posterior_mean,
                    requested_monotonic=proposal_requested,
                    received_monotonic=proposal_received,
                    app_markup_percentage=self.app_markup_percentage,
                )
            except Exception as exc:
                self.repository.mark_signal(
                    signal.signal_id,
                    status="SKIP_INVALID_PROPOSAL",
                    proposal_received=True,
                )
                self.logger.error("Proposal validation rejected: %s", exc)
                return
            profit_ratio = economics.potential_profit / economics.stake
            target_stake = self._planned_stake_for_accounts(profit_ratio)
            if abs(target_stake - base_stake) > 1e-9:
                (
                    prop_resp,
                    proposal_requested,
                    proposal_received,
                ) = await self._send_proposal_request(signal, target_stake)
                if "error" in prop_resp:
                    self.logger.error("Proposal validation rejected: %s", prop_resp["error"].get("message"))
                    self.repository.mark_signal(
                        signal.signal_id,
                        status="SKIP_INVALID_PROPOSAL",
                        proposal_received=True,
                    )
                    return
                try:
                    economics = parse_proposal_economics(
                        prop_resp,
                        stake=target_stake,
                        predicted_probability=preliminary.posterior_mean,
                        requested_monotonic=proposal_requested,
                        received_monotonic=proposal_received,
                        app_markup_percentage=self.app_markup_percentage,
                    )
                except Exception as exc:
                    self.repository.mark_signal(
                        signal.signal_id,
                        status="SKIP_INVALID_PROPOSAL",
                        proposal_received=True,
                    )
                    self.logger.error("Proposal validation rejected: %s", exc)
                    return
            self.repository.record_proposal(signal, economics)

            bayesian = self.bayesian.snapshot(
                economics.break_even_probability,
                self.test2_config.bayesian.safety_margin_probability,
            )
            features = build_features(list(self.raw_tick_digits))
            hmm = self.hmm.infer(features)
            decision = self.decision_engine.decide(
                signal=signal,
                economics=economics,
                bayesian=bayesian,
                hmm=hmm,
                current_tick_sequence=self.tick_sequence,
                connection_session_id=self.connection_session_id,
                connection_healthy=(
                    self.public_client.is_connected
                    and bool(self.sessions)
                    and all(session.is_connected for session in self.sessions.values())
                ),
                pattern_reset_required=False,
            )
            self.repository.record_decision(
                decision,
                hmm=hmm,
                bayesian=bayesian,
            )
            self.logger.info(
                "MODEL_DECISION signal_id=%s action=%s expected_value=%.5f "
                "posterior_mean=%.5f hmm_state=%s",
                signal.signal_id,
                decision.final_action,
                decision.expected_value,
                decision.posterior_mean,
                decision.hmm_state,
            )
            if decision.final_action != "PURCHASE":
                self.repository.mark_signal(
                    signal.signal_id,
                    status=decision.final_action,
                    stale=decision.final_action == "SKIP_STALE_SIGNAL",
                    proposal_received=True,
                )
                return

            if not self.trading_enabled:
                self.repository.mark_signal(signal.signal_id, status="OBSERVATION_ONLY")
                self.logger.info("Trading is DISABLED; candidate recorded as observation only")
                return

            if (
                self.test2_config.execution.reject_if_new_tick_arrives
                and self.tick_sequence != signal.tick_sequence
            ):
                self.repository.mark_signal(
                    signal.signal_id,
                    status="SKIP_STALE_SIGNAL",
                    stale=True,
                )
                return
            status, pause_reason = self.repository.control_state()
            if status == "MANUAL_PAUSE" and pause_reason != "COPY_PURCHASE_PARTIAL":
                self.logger.warning(
                    "Clearing stale MANUAL_PAUSE status reason=%s before purchase.",
                    pause_reason or "none",
                )
                self.repository.set_status("RUNNING")
                status = "RUNNING"
            if status in {"MANUAL_PAUSE", "EMERGENCY_STOP"}:
                skip_status = "SKIP_EMERGENCY_STOP" if status == "EMERGENCY_STOP" else "SKIP_MANUAL_PAUSE"
                self.repository.mark_signal(signal.signal_id, status=skip_status)
                return
            if not self.repository.consume_signal(signal.signal_id):
                self.repository.mark_signal(signal.signal_id, status="SKIP_DUPLICATE")
                return
            signal.consumed = True

            eligible_accounts = self._eligible_purchase_accounts()
            if not eligible_accounts:
                self.repository.mark_signal(signal.signal_id, status="SKIP_NO_ENABLED_ACCOUNTS")
                self.logger.info(
                    "Skipping purchase for signal %s because no copier accounts are enabled.",
                    signal.signal_id,
                )
                return
            enabled_managed_accounts = self._enabled_managed_account_ids()
            eligible_account_ids = {account_id for _, account_id in eligible_accounts}
            missing_enabled_accounts = sorted(enabled_managed_accounts - eligible_account_ids)
            if missing_enabled_accounts:
                self.repository.mark_signal(signal.signal_id, status="SKIP_COPY_GROUP_INCOMPLETE")
                self.repository.set_status(
                    "MANUAL_PAUSE",
                    "COPY_GROUP_INCOMPLETE",
                )
                self.logger.critical(
                    "COPY_GROUP_INCOMPLETE enabled_accounts=%s eligible_accounts=%s missing=%s; "
                    "pausing before purchase.",
                    len(enabled_managed_accounts),
                    len(eligible_account_ids),
                    [mask_account_id(account_id) for account_id in missing_enabled_accounts],
                )
                return
            stake_amount = round(float(economics.stake), 2)
            profit_ratio = economics.potential_profit / economics.stake
            bulk_incompatible_accounts = self._bulk_purchase_incompatible_accounts(
                eligible_accounts
            )
            if (
                bulk_incompatible_accounts
                and not self._sequential_private_fallback_allowed(len(eligible_accounts))
            ):
                self.repository.mark_signal(
                    signal.signal_id,
                    status="SKIP_BULK_REQUIRES_PAT",
                )
                self.logger.error(
                    "BULK_PURCHASE_REQUIRES_PAT signal_id=%s account_count=%s "
                    "oauth_accounts=%s; Deriv REST bulk-purchase requires end-user "
                    "PAT tokens for copied accounts. Bot remains RUNNING and no "
                    "contract was opened.",
                    signal.signal_id,
                    len(eligible_accounts),
                    [mask_account_id(account_id) for account_id in bulk_incompatible_accounts],
                )
                return

            purchase_requested_at = datetime.now(timezone.utc)
            self.repository.mark_signal(
                signal.signal_id,
                status="PURCHASE_REQUESTED",
                purchase_requested=True,
                ticks_between=self.tick_sequence - signal.tick_sequence,
            )
            self.logger.info(
                "PURCHASE_REQUESTED signal_id=%s account_count=%s proposal_id=%s",
                signal.signal_id,
                len(eligible_accounts),
                economics.proposal_id,
            )
            transactions: List[Dict[str, Any]] = []
            if bulk_incompatible_accounts:
                self.logger.warning(
                    "Using private WebSocket fallback for bulk-incompatible account_count=%s "
                    "signal_id=%s",
                    len(eligible_accounts),
                    signal.signal_id,
                )
                transactions = await self._purchase_via_private_sessions(
                    signal=signal,
                    economics=economics,
                    eligible_accounts=eligible_accounts,
                    stake_amount=stake_amount,
                )
            else:
                bulk_path = f"/trading/v1/options/contracts/bulk-purchase/{self.environment}"
                contract_parameters = self._contract_parameters(
                    signal,
                    stake_amount,
                    symbol_key="underlying_symbol",
                )
                req_body = {
                    "contract_parameters": contract_parameters,
                    "accounts": [
                        {"token": token, "account_id": account_id}
                        for token, account_id in eligible_accounts
                    ],
                }
                resp = await _rest_request(
                    "POST",
                    bulk_path,
                    self.app_id,
                    self.rest_base_url,
                    token=None,
                    json_data=req_body,
                )

                if "error" in resp:
                    error_message = sanitize_account_ids(resp["error"].get("message"))
                    self.logger.error("REST Bulk Purchase request failed: %s", error_message)
                    self.repository.mark_signal(signal.signal_id, status="PURCHASE_FAILED_BULK_REQUIRED")
                    if not self._sequential_private_fallback_allowed(len(eligible_accounts)):
                        self.logger.error(
                            "BULK_PURCHASE_FAILED_NO_CONTRACTS signal_id=%s account_count=%s; "
                            "bot remains RUNNING because no contract was opened and copy-trade "
                            "consistency is intact. error=%s",
                            signal.signal_id,
                            len(eligible_accounts),
                            error_message,
                        )
                    return
                transactions = resp.get("data", {}).get("transactions", [])
                if resp.get("errors"):
                    messages = "; ".join(
                        sanitize_account_ids(
                            err.get("message", "Unknown bulk-purchase error")
                        )
                        for err in resp.get("errors", [])
                    )
                    self.logger.error("REST Bulk Purchase validation failed: %s", messages)
                    if not transactions:
                        if not self._sequential_private_fallback_allowed(len(eligible_accounts)):
                            self.repository.mark_signal(
                                signal.signal_id,
                                status="PURCHASE_FAILED_BULK_REQUIRED",
                            )
                            self.logger.error(
                                "BULK_PURCHASE_FAILED_NO_CONTRACTS signal_id=%s account_count=%s; "
                                "sequential private fallback is disabled to avoid mismatched 1-tick "
                                "outcomes, but bot remains RUNNING because no contract was opened. "
                                "errors=%s",
                                signal.signal_id,
                                len(eligible_accounts),
                                messages,
                            )
                            return
                        self.logger.info(
                            "Falling back to private WebSocket buy for signal_id=%s",
                            signal.signal_id,
                        )
                        transactions = await self._purchase_via_private_sessions(
                            signal=signal,
                            economics=economics,
                            eligible_accounts=eligible_accounts,
                            stake_amount=stake_amount,
                        )
            signal_contracts: Set[int] = set()
            self.outcomes_by_signal[signal.signal_id] = []

            for tx in transactions:
                account_id = tx.get("account_id")
                token = next((t for t, acc in eligible_accounts if acc == account_id), None)
                if not token:
                    continue

                st = self._client_state_for_token(token, account_id=str(account_id or ""))
                tag = st["token_tag"]

                if "error" in tx:
                    self.logger.error(
                        "Purchase failed for account %s: %s",
                        mask_account_id(account_id),
                        tx["error"].get("message"),
                        extra={
                            "token_tag": tag,
                            "masked_account_id": mask_account_id(account_id),
                        },
                    )
                    continue

                contract_id = tx.get("contract_id")
                transaction_id = tx.get("transaction_id")
                if contract_id:
                    st["current_stake"] = stake_amount
                    st["last_profit_ratio"] = profit_ratio
                    st["single_recovery_active"] = (
                        bool(st.get("single_recovery_pending", False))
                        or stake_amount > base_stake + 1e-9
                    )
                    if st["single_recovery_active"]:
                        st["single_recovery_pending"] = False
                    contract_id = int(contract_id)
                    self.pending_contracts_for_current_cycle.add(contract_id)
                    signal_contracts.add(contract_id)
                    self.contract_signal_ids[contract_id] = signal.signal_id
                    session = self.sessions[token]
                    session.pending_contracts.add(contract_id)
                    self.pending_contract_started_at[contract_id] = purchase_requested_at
                    self.repository.register_purchase(
                        signal_id=signal.signal_id,
                        contract_id=str(contract_id),
                        transaction_id=str(transaction_id or contract_id),
                        account_id=str(account_id),
                        purchase_time=purchase_requested_at,
                        aligned_with_signal=True,
                        buy_price=optional_float(tx.get("buy_price")),
                        payout=optional_float(tx.get("payout")),
                        provider_purchase_time=optional_epoch_datetime(
                            tx.get("purchase_time")
                        ),
                        provider_start_time=optional_epoch_datetime(
                            tx.get("start_time")
                        ),
                        contract_duration=self.duration,
                        contract_duration_unit=self.duration_unit,
                    )

                    self.logger.info(
                        "PURCHASE_CONFIRMED signal_id=%s contract_type=%s barrier=%s trigger=%s",
                        signal.signal_id,
                        signal.contract_type,
                        signal.barrier,
                        signal.trigger_name,
                        extra={"token_tag": tag, "contract_id": str(contract_id), "stake": f"{stake_amount:.2f}"}
                    )
                    await session.subscribe_contract(contract_id)
                    await self._refresh_account_balance_snapshot(token, str(account_id))

            self.pending_by_signal[signal.signal_id] = signal_contracts
            self._save_state()

            if not signal_contracts:
                self.repository.mark_signal(signal.signal_id, status="PURCHASE_FAILED")
                self.logger.warning("No contracts were purchased successfully")
                return

            purchased_account_ids = {
                str(tx.get("account_id", "")).strip()
                for tx in transactions
                if tx.get("contract_id") and "error" not in tx
            }
            missing_purchases = sorted(eligible_account_ids - purchased_account_ids)
            if missing_purchases:
                self.repository.mark_signal(
                    signal.signal_id,
                    status="PURCHASE_PARTIAL",
                    purchase_confirmed=True,
                )
                self.repository.set_status(
                    "MANUAL_PAUSE",
                    "COPY_PURCHASE_PARTIAL",
                )
                self.logger.critical(
                    "COPY_PURCHASE_PARTIAL signal_id=%s purchased=%s expected=%s missing=%s; "
                    "pausing to prevent further inconsistent copy trading.",
                    signal.signal_id,
                    len(purchased_account_ids),
                    len(eligible_account_ids),
                    [mask_account_id(account_id) for account_id in missing_purchases],
                )
            else:
                self.repository.mark_signal(
                    signal.signal_id,
                    status="PURCHASE_CONFIRMED",
                    purchase_confirmed=True,
                )
            if missing_purchases:
                asyncio.create_task(
                    self._cycle_timeout_watchdog(signal.signal_id, list(signal_contracts))
                )
                return

            asyncio.create_task(
                self._cycle_timeout_watchdog(signal.signal_id, list(signal_contracts))
            )

        except Exception as e:
            self.logger.error("Unexpected error during multi-trade: %s", e)
            traceback.print_exc()
            self.repository.mark_signal(signal.signal_id, status="PURCHASE_FAILED")
        finally:
            self.is_trading_locked = False
            self.pending_signal = None

    async def _purchase_via_private_sessions(
        self,
        *,
        signal: CandidateSignal,
        economics: Any,
        eligible_accounts: List[Tuple[str, str]],
        stake_amount: float,
    ) -> List[Dict[str, Any]]:
        transactions: List[Dict[str, Any]] = []
        for token, account_id in eligible_accounts:
            session = self.sessions.get(token)
            st = self._client_state_for_token(token, account_id=account_id)
            extra = {
                "token_tag": st["token_tag"],
                "masked_account_id": mask_account_id(account_id),
                "stake": f"{stake_amount:.2f}",
            }
            if not session or not session.is_connected:
                message = "Private WebSocket is not connected"
                self.logger.error(
                    "Private buy skipped for account %s: %s",
                    mask_account_id(account_id),
                    message,
                    extra=extra,
                )
                transactions.append(
                    {"account_id": account_id, "error": {"message": message}}
                )
                continue

            response = await session.send_request(
                self._direct_buy_request(signal, stake_amount, economics)
            )
            if "error" in response:
                message = response["error"].get("message", "Unknown buy error")
                self.logger.error(
                    "Private buy failed for account %s: %s",
                    mask_account_id(account_id),
                    message,
                    extra=extra,
                )
                transactions.append(
                    {"account_id": account_id, "error": {"message": message}}
                )
                continue

            buy = response.get("buy", {})
            contract_id = buy.get("contract_id")
            transaction_id = buy.get("transaction_id") or buy.get(
                "transaction_ids", {}
            ).get("buy")
            if not contract_id:
                message = "Buy response did not include a contract_id"
                self.logger.error(
                    "Private buy failed for account %s: %s",
                    mask_account_id(account_id),
                    message,
                    extra=extra,
                )
                transactions.append(
                    {"account_id": account_id, "error": {"message": message}}
                )
                continue

            transactions.append(
                {
                    "account_id": account_id,
                    "contract_id": contract_id,
                    "transaction_id": transaction_id or contract_id,
                    "buy_price": buy.get("buy_price"),
                    "payout": buy.get("payout"),
                    "purchase_time": buy.get("purchase_time"),
                    "start_time": buy.get("start_time"),
                }
            )
        return transactions

    async def _send_proposal_request(
        self,
        signal: CandidateSignal,
        stake_amount: float,
    ) -> Tuple[Dict[str, Any], float, float]:
        proposal_requested = time.monotonic()
        prop_resp = await self.public_client.send_request(
            self._proposal_request(signal, stake_amount)
        )
        proposal_received = time.monotonic()
        return prop_resp, proposal_requested, proposal_received

    def _proposal_request(
        self,
        signal: CandidateSignal,
        stake_amount: float,
    ) -> Dict[str, Any]:
        return self._contract_parameters(
            signal,
            stake_amount,
            symbol_key="underlying_symbol",
        ) | {
            "proposal": 1,
        }

    def _direct_buy_request(
        self,
        signal: CandidateSignal,
        stake_amount: float,
        economics: Any,
    ) -> Dict[str, Any]:
        parameters = self._contract_parameters(
            signal,
            stake_amount,
            symbol_key="underlying_symbol",
        )
        if self.app_markup_percentage > 0:
            parameters["app_markup_percentage"] = round(
                self.app_markup_percentage,
                2,
            )
        expected_markup = max(
            0.0,
            float(economics.payout)
            * max(0.0, self.app_markup_percentage)
            / 100.0,
        )
        # price is a ceiling, not the requested stake. Round upward so a
        # fractional-cent payout-based markup cannot reject a valid buy.
        maximum_price = math.ceil(
            (float(stake_amount) + expected_markup) * 100.0 - 1e-9
        ) / 100.0
        return {
            "buy": "1",
            "price": maximum_price,
            "parameters": parameters,
        }

    def _contract_parameters(
        self,
        signal: CandidateSignal,
        stake_amount: float,
        *,
        symbol_key: str,
    ) -> Dict[str, Any]:
        return {
            "amount": stake_amount,
            "basis": "stake",
            "contract_type": signal.contract_type,
            "currency": self.currency,
            "duration": self.duration,
            "duration_unit": self.duration_unit,
            "barrier": signal.barrier,
            symbol_key: signal.symbol,
        }

    def _copytrading_master_account_id(self) -> str:
        configured = os.getenv("COPYTRADING_MASTER_ACCOUNT_ID", "").strip()
        if configured:
            return configured
        return self.valid_clients[0][1] if self.valid_clients else ""

    def _enabled_managed_account_ids(self) -> Set[str]:
        account_ids: Set[str] = set()
        for row in self.repository.list_managed_accounts():
            if not row.enabled:
                continue
            try:
                payload = decrypt_auth_payload(row.token_secret, self.encryption_key)
            except Exception:
                continue
            if not self._purchase_token_from_payload(payload):
                continue
            account_id = str(payload.get("account_id", "")).strip()
            if account_id:
                account_ids.add(account_id)
        return account_ids

    def _purchase_token_from_payload(self, payload: Dict[str, Any]) -> str:
        explicit_pat = str(payload.get("pat_token", "")).strip()
        if explicit_pat:
            return explicit_pat
        auth_type = str(payload.get("auth_type", "pat")).strip().lower() or "pat"
        access_token = str(payload.get("access_token", "")).strip()
        if auth_type != "oauth":
            return access_token
        return ""

    def _auth_type_for_token(self, token: str) -> str:
        profile = self.user_profiles.get(token, {})
        return str(profile.get("auth_type", "pat")).strip().lower() or "pat"

    def _bulk_purchase_token_capable(self, token: str) -> bool:
        return self._auth_type_for_token(token) != "oauth"

    def _bulk_purchase_incompatible_accounts(
        self, accounts: List[Tuple[str, str]]
    ) -> List[str]:
        return [
            account_id
            for token, account_id in accounts
            if not self._bulk_purchase_token_capable(token)
        ]

    def _eligible_purchase_accounts(self) -> List[Tuple[str, str]]:
        accounts = list(self.valid_clients)
        unique_accounts: List[Tuple[str, str]] = []
        seen_account_ids: Set[str] = set()
        for token, account_id in accounts:
            if account_id in seen_account_ids:
                self.logger.warning(
                    "Duplicate eligible account %s removed before purchase",
                    mask_account_id(account_id),
                    extra={
                        "token_tag": token_tag(token),
                        "masked_account_id": mask_account_id(account_id),
                    },
                )
                continue
            seen_account_ids.add(account_id)
            unique_accounts.append((token, account_id))
        accounts = unique_accounts
        include_master = os.getenv("COPYTRADING_INCLUDE_MASTER", "true").lower() in {
            "1",
            "true",
            "yes",
        }
        if include_master or len(accounts) <= 1:
            return accounts
        master_account_id = self._copytrading_master_account_id()
        copiers = [
            (token, account_id)
            for token, account_id in accounts
            if account_id != master_account_id
        ]
        return copiers or accounts

    def _sequential_private_fallback_allowed(self, account_count: int) -> bool:
        if account_count <= 1:
            return True
        return os.getenv("COPYTRADING_ALLOW_SEQUENTIAL_PRIVATE_FALLBACK", "false").lower() in {
            "1",
            "true",
            "yes",
        }

    def _store_account_balance_payload(
        self,
        account_id: str,
        balance_payload: Dict[str, Any],
        *,
        token: str = "",
    ) -> bool:
        matched_account_id = str(account_id or "").strip()
        if not matched_account_id:
            return False
        try:
            self.repository.update_account_balance(
                account_id=matched_account_id,
                balance=float(balance_payload["balance"]),
                currency=str(balance_payload.get("currency", self.currency)),
                status=str(balance_payload.get("status", "active")),
            )
            return True
        except (AttributeError, KeyError, TypeError, ValueError):
            self.logger.warning(
                "Ignored malformed balance snapshot for account %s",
                mask_account_id(matched_account_id),
                extra={"token_tag": token_tag(token)},
            )
            return False

    async def _refresh_account_balance_snapshot(
        self,
        token: str,
        account_id: str,
    ) -> None:
        matched_account_id = str(account_id or "").strip()
        if not matched_account_id:
            return

        session = self.sessions.get(token)
        if session and session.is_connected:
            response = await session.refresh_balance_snapshot()
            if "error" not in response:
                if self._store_account_balance_payload(
                    matched_account_id,
                    response.get("balance", {}),
                    token=token,
                ):
                    return

        response = await _rest_request(
            "GET",
            "/trading/v1/options/accounts",
            self.app_id,
            self.rest_base_url,
            token=token,
        )
        if "error" in response:
            self.logger.warning(
                "Balance refresh failed for account %s: %s",
                mask_account_id(matched_account_id),
                response["error"].get("message"),
                extra={"token_tag": token_tag(token)},
            )
            return

        accounts = response.get("data", [])
        matched = next(
            (
                row
                for row in accounts
                if str(row.get("account_id", "")).strip() == matched_account_id
            ),
            None,
        )
        if not matched:
            return

        self._store_account_balance_payload(
            matched_account_id,
            matched,
            token=token,
        )

    async def _cycle_timeout_watchdog(
        self, signal_id: str, contract_ids: List[int]
    ) -> None:
        """Enforces a timeout in case contract settlement updates are not received."""
        await asyncio.sleep(float(self.max_open_trade_seconds))
        pending = self.pending_by_signal.get(signal_id, set())
        timed_out = [cid for cid in contract_ids if cid in pending]
        if timed_out:
            self.logger.warning(
                "Settlement updates delayed for signal_id=%s contract_count=%s; "
                "forcing reconciliation",
                signal_id,
                len(timed_out),
            )
            for cid in timed_out:
                await self._reconcile_pending_contract(cid, "timeout_watchdog")

    async def handle_contract_update(self, token: str, contract_id: int, contract: Dict[str, Any]) -> None:
        status = contract.get("status", "unknown")
        if status not in {"won", "lost", "sold", "cancelled"}:
            return # not settled

        session = self.sessions.get(token)
        account_id = session.account_id if session else ""
        st = self._client_state_for_token(token, account_id=account_id)
        tag = st["token_tag"]
        extra = {"token_tag": tag, "contract_id": str(contract_id), "stake": f"{st['current_stake']:.2f}"}

        # Prevent duplicate processing
        if contract_id not in self.pending_contracts_for_current_cycle and contract_id not in self.unresolved_contracts_from_state:
            return

        profit = float(contract.get("profit", 0.0))
        buy_price = optional_float(contract.get("buy_price"))
        payout = optional_float(contract.get("payout"))
        app_markup_amount = optional_float(contract.get("app_markup_amount"))
        commission = optional_float(contract.get("commission"))
        provider_purchase_time = optional_epoch_datetime(contract.get("purchase_time"))
        provider_start_time = optional_epoch_datetime(
            contract.get("date_start") or contract.get("start_time")
        )
        provider_expiry_time = optional_epoch_datetime(contract.get("date_expiry"))
        provider_settlement_time = optional_epoch_datetime(
            contract.get("sell_time")
            or contract.get("exit_spot_time")
            or contract.get("date_expiry")
        )
        entry_value = contract.get("entry_tick", contract.get("entry_spot"))
        exit_value = contract.get("exit_tick", contract.get("exit_spot"))
        try:
            entry_tick = float(entry_value) if entry_value is not None else None
        except (TypeError, ValueError):
            entry_tick = None
        try:
            exit_tick = float(exit_value) if exit_value is not None else None
        except (TypeError, ValueError):
            exit_tick = None
        exit_digit = None
        if exit_tick is not None:
            exit_digit = int(f"{exit_tick:.{self.pip_size}f}"[-1])

        if status == "won":
            outcome = "win"
        elif status == "lost":
            outcome = "loss"
        else:
            outcome = "win" if profit > 0 else "loss"
        if not self.repository.settle_trade(
            contract_id=str(contract_id),
            profit=profit,
            outcome=outcome,
            entry_tick=entry_tick,
            exit_tick=exit_tick,
            exit_digit=exit_digit,
            buy_price=buy_price,
            payout=payout,
            app_markup_amount=app_markup_amount,
            commission=commission,
            provider_purchase_time=provider_purchase_time,
            provider_start_time=provider_start_time,
            provider_expiry_time=provider_expiry_time,
            provider_settlement_time=provider_settlement_time,
        ):
            return

        self.logger.info(
            "CONTRACT_ECONOMICS account=%s buy_price=%s payout=%s profit=%.2f "
            "app_markup_amount=%s commission=%s",
            mask_account_id(account_id),
            f"{buy_price:.2f}" if buy_price is not None else "unavailable",
            f"{payout:.2f}" if payout is not None else "unavailable",
            profit,
            f"{app_markup_amount:.4f}" if app_markup_amount is not None else "unavailable",
            f"{commission:.4f}" if commission is not None else "unavailable",
            extra=extra,
        )
        lifecycle_seconds = self._contract_age_seconds(contract_id)
        provider_lifecycle_seconds = None
        if provider_purchase_time and provider_settlement_time:
            provider_lifecycle_seconds = max(
                0.0,
                (provider_settlement_time - provider_purchase_time).total_seconds(),
            )
        self.logger.info(
            "CONTRACT_TIMING account=%s contract_id=%s duration=1_tick "
            "lifecycle_seconds=%.3f provider_lifecycle_seconds=%s sla_seconds=%.1f "
            "sla_status=%s",
            mask_account_id(account_id),
            contract_id,
            lifecycle_seconds,
            (
                f"{provider_lifecycle_seconds:.3f}"
                if provider_lifecycle_seconds is not None
                else "unavailable"
            ),
            self.settlement_sla_seconds,
            "MET" if lifecycle_seconds <= self.settlement_sla_seconds else "LATE",
            extra=extra,
        )
        if self.app_markup_percentage > 0 and not (app_markup_amount and app_markup_amount > 0):
            self.logger.warning(
                "APP_MARKUP_NOT_CONFIRMED account=%s contract_id=%s expected_percentage=%.2f "
                "reported_app_markup_amount=%s; verify Registered Apps markup and "
                "/control/markup-statistics",
                mask_account_id(account_id),
                contract_id,
                self.app_markup_percentage,
                "unavailable" if app_markup_amount is None else f"{app_markup_amount:.4f}",
                extra=extra,
            )
        elif app_markup_amount is not None:
            self.logger.info(
                "APP_MARKUP_CONFIRMED account=%s contract_id=%s amount=%.4f",
                mask_account_id(account_id),
                contract_id,
                app_markup_amount,
                extra=extra,
            )

        st["total_profit"] = float(st["total_profit"]) + profit
        st["profit_today"] = float(st["profit_today"]) + profit
        st["total_trades"] = int(st.get("total_trades", 0)) + 1
        st["last_profit"] = profit

        if status == "won":
            self.logger.info("WIN profit=%.2f", profit, extra=extra)
            st["wins"] = int(st.get("wins", 0)) + 1
        elif status == "lost":
            self.logger.info("LOSS profit=%.2f", profit, extra=extra)
            st["losses"] = int(st.get("losses", 0)) + 1
        elif status in {"sold", "cancelled"}:
            self.logger.info("SETTLED status=%s profit=%.2f", status, profit, extra=extra)
            if outcome == "win":
                st["wins"] = int(st.get("wins", 0)) + 1
            else:
                st["losses"] = int(st.get("losses", 0)) + 1
        else:
            outcome = "loss"
            st["losses"] = int(st.get("losses", 0)) + 1
        st["last_result"] = outcome

        self._update_client_recovery_state(st, outcome=outcome, profit=profit)

        self.logger.info(
            "total_profit=%.2f profit_today=%.2f next_stake=%.2f",
            st["total_profit"],
            st["profit_today"],
            st["current_stake"],
            extra=extra,
        )

        # Cleanup subscription
        session = self.sessions.get(token)
        if session:
            sub_id = session.active_subscriptions.pop(contract_id, None)
            if sub_id:
                await session.unsubscribe_contract(sub_id)
            session.pending_contracts.discard(contract_id)
            await self._refresh_account_balance_snapshot(token, session.account_id)

        self.unresolved_contracts_from_state.discard(contract_id)
        self.pending_contracts_for_current_cycle.discard(contract_id)
        self.pending_contract_started_at.pop(contract_id, None)
        self.delayed_contracts_logged.discard(contract_id)

        signal_id = self.contract_signal_ids.pop(contract_id, "")
        if signal_id:
            pending = self.pending_by_signal.setdefault(signal_id, set())
            pending.discard(contract_id)
            self.outcomes_by_signal.setdefault(signal_id, []).append(outcome)
            if not pending:
                outcomes = self.outcomes_by_signal.pop(signal_id, [outcome])
                self.pending_by_signal.pop(signal_id, None)
                cycle_outcome = "win" if all(value == "win" for value in outcomes) else "loss"
                self.bayesian.update(cycle_outcome == "win")
                self._record_real_cycle_outcome(cycle_outcome)
                self._register_trade_cycle_outcome(cycle_outcome)
                self.logger.info(
                    "CONTRACT_SETTLED signal_id=%s result=%s exit_digit=%s",
                    signal_id,
                    cycle_outcome.upper(),
                    exit_digit,
                )

        self._save_state()

    async def _watchdog_loop(self) -> None:
        await asyncio.sleep(min(self.max_tick_silence_seconds, self.watchdog_poll_interval_seconds))
        refresh_interval = max(5, int(os.getenv("ACCOUNT_REFRESH_INTERVAL_SECONDS", "10")))
        refresh_timer = 0
        while self.is_running:
            if self.last_tick_received_at > 0:
                silence = time.monotonic() - self.last_tick_received_at
                if silence > self.max_tick_silence_seconds:
                    raise ConnectionStaleError(f"No tick received for {silence:.1f} seconds")
            refresh_timer += self.watchdog_poll_interval_seconds
            if refresh_timer >= refresh_interval:
                refresh_timer = 0
                try:
                    await self._refresh_runtime_accounts_if_needed()
                except Exception as exc:
                    self.logger.warning("Account refresh failed: %s", exc)
            await asyncio.sleep(self.watchdog_poll_interval_seconds)

    async def _lease_heartbeat_loop(self) -> None:
        while self.is_running and self.lease_key:
            acquired = self.repository.acquire_lease(
                lease_key=self.lease_key,
                worker_id=self.worker_id,
                host_name=socket.gethostname(),
                process_id=os.getpid(),
                deployment_id=os.getenv("DEPLOYMENT_ID", "local"),
            )
            if not acquired:
                self.logger.critical("TRADER_LOCK_LOST lease_key=%s", self.lease_key)
                self.repository.set_status("EMERGENCY_STOP", "TRADER_LOCK_LOST")
                self.is_running = False
                return
            self.repository.heartbeat(self.connection_session_id)
            await asyncio.sleep(10)

    async def _stop_watchdog(self) -> None:
        task = self._watchdog_task
        self._watchdog_task = None
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def run(self) -> None:
        attempt = 0
        while self.is_running:
            should_retry = True
            public_task: Optional[asyncio.Task] = None
            try:
                attempt += 1
                if attempt > 1:
                    self.logger.warning("reconnect attempt #%s", attempt - 1)

                self._reset_session_runtime_state()

                # Validate and retrieve account IDs REST-side
                await self.validate_accounts()
                account_scope = hashlib.sha256(
                    ",".join(
                        sorted(account_id for _, account_id in self.valid_clients)
                    ).encode()
                ).hexdigest()[:16]
                self.lease_key = (
                    f"{self.test2_config.model.run_id}:{self.environment}:{account_scope}"
                )
                acquired = self.repository.acquire_lease(
                    lease_key=self.lease_key,
                    worker_id=self.worker_id,
                    host_name=socket.gethostname(),
                    process_id=os.getpid(),
                    deployment_id=os.getenv("DEPLOYMENT_ID", "local"),
                )
                if not acquired:
                    raise SystemExit(
                        "Another healthy Test 2 worker already owns the trader lease"
                    )
                if self._lease_task is None or self._lease_task.done():
                    self._lease_task = asyncio.create_task(self._lease_heartbeat_loop())
                    self.logger.info("TRADER_LOCK_ACQUIRED lease_key=%s", self.lease_key)

                # Initialize private sessions before reconciling unresolved DB trades.
                for token, account_id in self.valid_clients:
                    session = ClientSession(token, account_id, self)
                    self.sessions[token] = session

                for trade in self.repository.unresolved_contracts():
                    cid = int(trade.contract_id)
                    matched_token = None
                    for token, account_id in self.valid_clients:
                        masked = f"{account_id[:3]}***{account_id[-3:]}"
                        if masked == trade.account_id_masked:
                            matched_token = token
                            break
                    if matched_token is None:
                        self.logger.error(
                            "Unresolved contract %s has no matching configured account; manual review required",
                            cid,
                            extra={"contract_id": str(cid)},
                        )
                        continue
                    self.sessions[matched_token].pending_contracts.add(cid)
                    self.pending_contract_started_at[cid] = trade.purchase_time
                    self.unresolved_contracts_from_state.add(cid)
                    self.pending_contracts_for_current_cycle.add(cid)
                    self.contract_signal_ids[cid] = trade.signal_id
                    self.pending_by_signal.setdefault(trade.signal_id, set()).add(cid)

                for session in self.sessions.values():
                    session.task = asyncio.create_task(session.connect_and_run())

                status, _ = self.repository.control_state()
                if status == "RECONNECTING":
                    self.repository.set_status("RUNNING")

                # Start public market data WebSocket client
                public_task = asyncio.create_task(self.public_client.connect_and_run())

                # Start watchdog
                self._watchdog_task = asyncio.create_task(self._watchdog_loop())
                # Keep loop alive running watchdog
                await self._watchdog_task

            except ConnectionStaleError as e:
                self.logger.warning("Watchdog alert: %s", e)
            except (ConnectionClosedError, OSError, ConnectionResetError) as e:
                self.logger.warning("Connection lost: %s", e)
            except KeyboardInterrupt:
                self.logger.info("Bot stopped by user")
                should_retry = False
            except Exception as e:
                self.logger.error("Unexpected error in run loop: %s", e)
                traceback.print_exc()
            finally:
                await self._stop_watchdog()
                if public_task and not public_task.done():
                    public_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await public_task
                for task in list(self._background_tasks):
                    task.cancel()
                for task in list(self._background_tasks):
                    with suppress(asyncio.CancelledError):
                        await task
                self._background_tasks.clear()
                # Stop private client WS tasks
                for session in self.sessions.values():
                    if session.task and not session.task.done():
                        session.task.cancel()
                for session in self.sessions.values():
                    if session.task:
                        with suppress(asyncio.CancelledError):
                            await session.task
                self.sessions.clear()
                self._reset_session_runtime_state()

            if not should_retry or not self.is_running:
                break

            self.logger.info("Waiting %ss before reconnecting...", self.reconnect_delay_seconds)
            self.repository.set_status("RECONNECTING", "PUBLIC_TICK_STREAM_RECOVERY")
            await asyncio.sleep(self.reconnect_delay_seconds)

        if self._lease_task and not self._lease_task.done():
            self._lease_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._lease_task
        if self.lease_key:
            self.repository.release_lease(self.lease_key, self.worker_id)
        self.repository.set_status("STOPPED")


def run_oauth_flow(client_id: str, redirect_uri: str) -> None:
    """OAuth 2.0 PKCE helper flow."""
    import secrets
    import base64
    import urllib.parse
    import requests

    code_verifier = secrets.token_urlsafe(64)
    sha_hash = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(sha_hash).decode('utf-8').replace('=', '')
    state = secrets.token_hex(16)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "trade",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }

    auth_url = "https://auth.deriv.com/oauth2/auth?" + urllib.parse.urlencode(params)

    print("\n=== Deriv OAuth 2.0 Login Helper ===")
    print("1. Open this URL in your browser to log in:")
    print(auth_url)
    print("\n2. Paste the redirect callback URL below:")

    try:
        user_url = input("\nRedirect URL: ").strip()
        parsed = urllib.parse.urlparse(user_url)
        query = urllib.parse.parse_qs(parsed.query)

        code = query.get("code", [None])[0]
        ret_state = query.get("state", [None])[0]

        if not code:
            print("Error: No code found in callback URL.")
            return
        if ret_state != state:
            print("Error: State mismatch!")
            return

        print("\nExchanging code for token...")
        token_url = "https://auth.deriv.com/oauth2/token"
        data = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri
        }

        resp = requests.post(token_url, data=data)
        if resp.status_code == 200:
            token_data = resp.json()
            secret_directory = Path("secrets")
            secret_directory.mkdir(parents=True, exist_ok=True)
            secret_path = secret_directory / "oauth_access.token"
            secret_path.write_text(
                str(token_data.get("access_token", "")), encoding="utf-8"
            )
            print("\nOAuth access token saved to secrets/oauth_access.token.")
        else:
            print("Error:", resp.status_code, resp.text)
    except KeyboardInterrupt:
        print("\nFlow cancelled.")


if __name__ == "__main__":
    if "--login" in sys.argv:
        config_path = os.getenv("DERIV_BOT_CONFIG", "config.yaml")
        cfg = load_config(config_path)
        c_id = os.getenv("DERIV_OAUTH_CLIENT_ID") or cfg["deriv"].get("oauth_client_id")
        r_uri = os.getenv("DERIV_OAUTH_REDIRECT_URL") or cfg["deriv"].get("oauth_redirect_url")
        if not c_id:
            c_id = input("Enter your Client ID: ").strip()
        if not r_uri:
            r_uri = input("Enter Redirect URI: ").strip()
        run_oauth_flow(c_id, r_uri)
        sys.exit(0)

    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    bot = TradingBot()
    try:
        loop.run_until_complete(bot.run())
    except KeyboardInterrupt:
        bot._clear_live_ticks()
        print("\n⏹️ Bot stopped by user.")
    finally:
        bot._clear_live_ticks()
        loop.close()
