from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

import enhanced_bot
from app.deriv.http import deriv_headers
from enhanced_bot import TradingBot, sanitize_account_ids


_INSTALLED = False
LOGGER = logging.getLogger(__name__)


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), value)


def _positive_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value)


def _token_fingerprint(token: str) -> str:
    if not token:
        return "public"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _is_rate_limit_html(text: str) -> bool:
    value = str(text or "").lower()
    return "<!doctype html" in value and any(
        marker in value
        for marker in (
            "rate limit",
            "1015",
            "cloudflare",
            "temporarily banned",
        )
    )


def _request_is_trade(path: str) -> bool:
    value = str(path or "").lower()
    return "/contracts/bulk-purchase/" in value


def _request_is_otp(path: str) -> bool:
    return str(path or "").lower().endswith("/otp")


def _request_is_account_list(method: str, path: str) -> bool:
    return (
        str(method).upper() == "GET"
        and str(path).rstrip("/").lower()
        == "/trading/v1/options/accounts"
    )


def _coalesce_key(
    method: str,
    path: str,
    token: str,
    json_data: dict[str, Any] | None,
) -> tuple[str, str, str, str] | None:
    if not _request_is_account_list(method, path):
        return None
    payload = json.dumps(json_data or {}, sort_keys=True, separators=(",", ":"))
    return (
        str(method).upper(),
        str(path),
        _token_fingerprint(token),
        hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12],
    )


@dataclass(slots=True)
class _BrokerConfig:
    connector_limit: int
    limit_per_host: int
    request_concurrency: int
    total_timeout_seconds: float
    connect_timeout_seconds: float
    cache_seconds: float
    retry_base_seconds: float

    @classmethod
    def load(cls) -> "_BrokerConfig":
        return cls(
            connector_limit=_positive_int("DERIV_HTTP_CONNECTOR_LIMIT", 32),
            limit_per_host=_positive_int("DERIV_HTTP_LIMIT_PER_HOST", 16),
            request_concurrency=_positive_int("DERIV_HTTP_CONCURRENCY", 16),
            total_timeout_seconds=_positive_float(
                "DERIV_HTTP_TOTAL_TIMEOUT_SECONDS",
                20.0,
                5.0,
            ),
            connect_timeout_seconds=_positive_float(
                "DERIV_HTTP_CONNECT_TIMEOUT_SECONDS",
                8.0,
                2.0,
            ),
            cache_seconds=_positive_float(
                "DERIV_ACCOUNT_LIST_CACHE_SECONDS",
                8.0,
                0.0,
            ),
            retry_base_seconds=_positive_float(
                "DERIV_HTTP_RETRY_BASE_SECONDS",
                0.35,
                0.05,
            ),
        )


class _DerivRequestBroker:
    """One keep-alive HTTP pool for all Deriv REST traffic in one worker."""

    def __init__(self) -> None:
        self.config = _BrokerConfig.load()
        self._session: aiohttp.ClientSession | None = None
        self._session_loop: asyncio.AbstractEventLoop | None = None
        self._session_lock: asyncio.Lock | None = None
        self._request_slots: asyncio.Semaphore | None = None
        self._inflight_lock: asyncio.Lock | None = None
        self._inflight: dict[tuple[str, str, str, str], asyncio.Task] = {}
        self._cache: dict[
            tuple[str, str, str, str],
            tuple[float, dict[str, Any]],
        ] = {}

    def _ensure_loop_primitives(self) -> None:
        loop = asyncio.get_running_loop()
        if self._session_loop is loop and self._session_lock is not None:
            return
        self._session_loop = loop
        self._session_lock = asyncio.Lock()
        self._inflight_lock = asyncio.Lock()
        self._request_slots = asyncio.Semaphore(self.config.request_concurrency)
        self._inflight = {}
        self._cache = {}
        self._session = None

    async def _http_session(self) -> aiohttp.ClientSession:
        self._ensure_loop_primitives()
        assert self._session_lock is not None
        async with self._session_lock:
            if self._session is not None and not self._session.closed:
                return self._session
            connector = aiohttp.TCPConnector(
                limit=self.config.connector_limit,
                limit_per_host=self.config.limit_per_host,
                ttl_dns_cache=300,
                keepalive_timeout=30,
                enable_cleanup_closed=True,
            )
            timeout = aiohttp.ClientTimeout(
                total=self.config.total_timeout_seconds,
                connect=self.config.connect_timeout_seconds,
                sock_connect=self.config.connect_timeout_seconds,
                sock_read=self.config.total_timeout_seconds,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                raise_for_status=False,
            )
            LOGGER.warning(
                "DERIV_HTTP_BROKER_READY connector_limit=%s limit_per_host=%s "
                "request_concurrency=%s keepalive=true account_read_coalescing=true",
                self.config.connector_limit,
                self.config.limit_per_host,
                self.config.request_concurrency,
            )
            return self._session

    async def close(self) -> None:
        session = self._session
        self._session = None
        if session is not None and not session.closed:
            await session.close()

    def _attempts(self, method: str, path: str) -> int:
        if _request_is_trade(path):
            return 1
        if str(method).upper() == "GET":
            return 3
        if _request_is_otp(path):
            return 2
        return 1

    def _retryable_response(self, response: dict[str, Any]) -> bool:
        error = response.get("error") if isinstance(response, dict) else None
        if not isinstance(error, dict):
            return False
        code = str(error.get("code") or "").strip().upper()
        return code in {
            "RATE_LIMITED",
            "HTTP_429",
            "REQUEST_TIMEOUT",
            "CONNECTION_ERROR",
            "HTTP_502",
            "HTTP_503",
            "HTTP_504",
        }

    async def request(
        self,
        method: str,
        path: str,
        app_id: str,
        base_url: str,
        token: str = "",
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_loop_primitives()
        key = _coalesce_key(method, path, token, json_data)
        if key is None:
            return await self._perform(
                method,
                path,
                app_id,
                base_url,
                token,
                json_data,
            )

        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and cached[0] >= now:
            return copy.deepcopy(cached[1])

        assert self._inflight_lock is not None
        creator = False
        async with self._inflight_lock:
            task = self._inflight.get(key)
            if task is None or task.done():
                creator = True
                task = asyncio.create_task(
                    self._perform(
                        method,
                        path,
                        app_id,
                        base_url,
                        token,
                        json_data,
                    ),
                    name=f"deriv_http_{key[0]}_{key[2]}",
                )
                self._inflight[key] = task

        try:
            response = await asyncio.shield(task)
            if creator and "error" not in response:
                self._cache[key] = (
                    time.monotonic() + self.config.cache_seconds,
                    copy.deepcopy(response),
                )
            return copy.deepcopy(response)
        finally:
            if creator:
                async with self._inflight_lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)

    async def _perform(
        self,
        method: str,
        path: str,
        app_id: str,
        base_url: str,
        token: str,
        json_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        attempts = self._attempts(method, path)
        last_response: dict[str, Any] = {
            "error": {
                "code": "REQUEST_NOT_SENT",
                "message": "Deriv request was not sent",
            }
        }
        for attempt in range(1, attempts + 1):
            last_response = await self._send_once(
                method,
                path,
                app_id,
                base_url,
                token,
                json_data,
            )
            if attempt >= attempts or not self._retryable_response(last_response):
                return last_response
            await asyncio.sleep(
                min(2.0, self.config.retry_base_seconds * (2 ** (attempt - 1)))
            )
            LOGGER.warning(
                "DERIV_HTTP_SAFE_RETRY method=%s path=%s token=%s attempt=%s/%s "
                "trade_request=%s",
                str(method).upper(),
                path,
                _token_fingerprint(token),
                attempt + 1,
                attempts,
                _request_is_trade(path),
            )
        return last_response

    async def _send_once(
        self,
        method: str,
        path: str,
        app_id: str,
        base_url: str,
        token: str,
        json_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        session = await self._http_session()
        assert self._request_slots is not None
        url = f"{base_url.rstrip('/')}{path}"
        headers = deriv_headers(app_id, bearer_token=token or "")
        try:
            async with self._request_slots:
                async with session.request(
                    str(method).upper(),
                    url,
                    headers=headers,
                    json=json_data if str(method).upper() != "GET" else None,
                ) as response:
                    if response.status in {200, 201}:
                        try:
                            payload = await response.json()
                            return payload if isinstance(payload, dict) else {
                                "data": payload
                            }
                        except Exception:
                            text = await response.text()
                            return {
                                "error": {
                                    "code": "INVALID_JSON",
                                    "message": text[:500] or "Deriv returned invalid JSON",
                                }
                            }
                    try:
                        payload = await response.json()
                        if isinstance(payload, dict):
                            return payload
                    except Exception:
                        pass
                    text = await response.text()
                    if response.status == 429 or _is_rate_limit_html(text):
                        return {
                            "error": {
                                "code": "RATE_LIMITED",
                                "message": "Deriv API rate-limited this VPS; request was delayed",
                            }
                        }
                    return {
                        "error": {
                            "code": f"HTTP_{response.status}",
                            "message": text[:500] or f"Deriv returned HTTP {response.status}",
                        }
                    }
        except asyncio.TimeoutError:
            code = (
                "BULK_OUTCOME_UNKNOWN"
                if _request_is_trade(path)
                else "REQUEST_TIMEOUT"
            )
            return {
                "error": {
                    "code": code,
                    "message": (
                        "Bulk purchase response timed out; automatic replay is disabled"
                        if _request_is_trade(path)
                        else "Deriv request timed out"
                    ),
                }
            }
        except (aiohttp.ClientError, OSError) as exc:
            code = (
                "BULK_OUTCOME_UNKNOWN"
                if _request_is_trade(path)
                else "CONNECTION_ERROR"
            )
            return {
                "error": {
                    "code": code,
                    "message": sanitize_account_ids(str(exc)),
                }
            }
        except Exception as exc:
            return {
                "error": {
                    "code": "REQUEST_BROKER_ERROR",
                    "message": sanitize_account_ids(str(exc)),
                }
            }


_BROKER = _DerivRequestBroker()


async def _brokered_rest_request(
    method: str,
    path: str,
    app_id: str,
    base_url: str,
    token: str | None = None,
    json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _BROKER.request(
        method,
        path,
        app_id,
        base_url,
        token or "",
        json_data,
    )


def install_deriv_request_broker() -> None:
    """Replace per-request HTTP sessions with one bounded keep-alive pool."""

    global _INSTALLED
    if _INSTALLED:
        return

    enhanced_bot._rest_request = _brokered_rest_request

    original_run = TradingBot.run

    async def run_with_broker_cleanup(self: TradingBot) -> None:
        try:
            await original_run(self)
        finally:
            await _BROKER.close()

    TradingBot.run = run_with_broker_cleanup
    TradingBot._deriv_request_broker_installed = True
    _INSTALLED = True
    LOGGER.warning(
        "DERIV_REQUEST_BROKER_INSTALLED shared_keepalive=true "
        "account_list_coalescing=true bounded_concurrency=true "
        "trade_timeout_replay=false",
    )
