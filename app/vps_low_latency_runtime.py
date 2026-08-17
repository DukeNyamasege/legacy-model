from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import suppress
from typing import Any

import websockets

import enhanced_bot as base_runtime
from app import custom_strategy_connection_stampede_guard as stampede
from app import custom_strategy_direct_runtime as direct_runtime
from app import custom_strategy_instant_start as instant
from app import deriv_request_broker as request_broker
from app import private_websocket_rate_limit as private_ws
from app.account_mode_execution_lock import (
    account_allows_new_execution,
    account_lifecycle_from_row,
)
from app.account_scoped_websocket_runtime import _promote_embedded_oauth_payload
from app.rf_dir5_bot import RFDir5TradingBot
from app.token_store import decrypt_auth_payload
from enhanced_bot import (
    ClientSession,
    is_permanent_credential_error,
    mask_account_id,
    normalize_account_type,
    runtime_account_key,
    sanitize_account_ids,
)


LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_ORIGINAL_ADMIT: Any = None
_ORIGINAL_WAKE: Any = None

# Ordinary network/handshake faults must not make one account sleep for minutes.
# Provider rate limits are intentionally NOT capped by this value; the existing
# 60-300 second rate-limit circuit remains authoritative for actual 429/1015.
_TRANSIENT_BACKOFF_MAX_SECONDS = 12.0
_MARKET_SELECTION_RECHECK_SECONDS = 2.0
_OTP_BOOTSTRAP_TIMEOUT_SECONDS = 8.0
_PRIVATE_WS_OPEN_TIMEOUT_SECONDS = 20.0
_CONTRACT_SNAPSHOT_OTP_TIMEOUT_SECONDS = 8.0
_CONTRACT_SNAPSHOT_OPEN_TIMEOUT_SECONDS = 12.0
_CONTRACT_SNAPSHOT_RESPONSE_TIMEOUT_SECONDS = 5.0
_PRIORITY_SECONDS = 45.0


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), value)


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _low_latency_fast_runtime_accounts(
    bot: RFDir5TradingBot,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Build all runnable accounts locally without redundant status writes."""

    tokens: list[str] = []
    profiles: dict[str, dict[str, Any]] = {}
    seen_accounts: set[str] = set()

    for row in bot.repository.list_managed_accounts():
        lifecycle = account_lifecycle_from_row(row)
        if not account_allows_new_execution(row) and lifecycle != "settlement":
            continue

        managed_id = int(_row_value(row, "id"))
        try:
            payload = decrypt_auth_payload(
                str(_row_value(row, "token_secret", "") or ""),
                bot.encryption_key,
            )
            payload = _promote_embedded_oauth_payload(payload)
        except Exception:
            bot._set_account_execution_status(
                managed_id,
                "credential_error",
                "Stored Deriv credential could not be read for this account.",
            )
            continue

        account_id = str(payload.get("account_id") or "").strip()
        account_type = normalize_account_type(
            payload.get("account_type") or payload.get("environment"),
            bot.environment,
        )
        credential = instant._credential_from_saved_payload(payload)
        if not account_id or not credential:
            bot._set_account_execution_status(
                managed_id,
                "token_required",
                "Authenticated trade permission is required before execution can connect.",
            )
            continue
        if account_id in seen_accounts:
            bot._set_account_execution_status(
                managed_id,
                "duplicate",
                "This Deriv account is already represented by another active row.",
            )
            continue

        seen_accounts.add(account_id)
        runtime_key = runtime_account_key(credential, account_id)
        profiles[runtime_key] = {
            "id": str(managed_id),
            "name": str(_row_value(row, "label", "") or f"Account {managed_id}"),
            "enabled": True,
            "account_id": account_id,
            "account_type": account_type,
            "auth_type": str(payload.get("auth_type") or "oauth").strip().lower(),
            "source": "custom_strategy_instant_start",
            "managed_account_id": managed_id,
            "stake_amount": float(_row_value(row, "stake_amount", 0.50) or 0.50),
            "take_profit": float(_row_value(row, "take_profit", 0.0) or 0.0),
            "stop_loss": float(_row_value(row, "stop_loss", 0.0) or 0.0),
            "martingale_enabled": bool(_row_value(row, "martingale_enabled", True)),
            "settlement_only": lifecycle == "settlement",
            "api_token": credential,
        }
        tokens.append(runtime_key)

        current_status = str(
            _row_value(row, "execution_status", "") or ""
        ).strip().lower()
        if lifecycle != "settlement" and current_status in {"starting", "validating"}:
            bot._set_account_execution_status(
                managed_id,
                "connecting",
                "Market watcher is active; authenticated execution connection is starting",
            )

    return tokens, profiles


def _low_latency_select_saved_strategy_markets(
    bot: RFDir5TradingBot,
    profiles: dict[str, dict[str, Any]],
) -> None:
    """Select the market set once, not once per account admission."""

    managed_ids: set[int] = set()
    for profile in profiles.values():
        try:
            managed_ids.add(int(profile.get("managed_account_id")))
        except (TypeError, ValueError):
            continue
    if not managed_ids:
        return

    now = time.monotonic()
    last_scan = float(getattr(bot, "_vps_market_selection_last_scan", 0.0) or 0.0)
    last_ids = getattr(bot, "_vps_market_selection_ids", None)
    if (
        last_ids == frozenset(managed_ids)
        and list(getattr(bot, "symbols", []) or [])
        and now - last_scan < _MARKET_SELECTION_RECHECK_SECONDS
    ):
        return

    configs: dict[int, dict[str, Any]] = {}
    active_runtime = getattr(bot, "_custom_direct_accounts", {})
    if isinstance(active_runtime, dict) and managed_ids.issubset(set(active_runtime)):
        configs = {
            int(managed_id): dict(getattr(active_runtime[managed_id], "config", {}) or {})
            for managed_id in managed_ids
        }
    else:
        try:
            configs = direct_runtime._load_configs_for_ids(bot, managed_ids)
        except Exception as exc:
            bot.logger.warning(
                "CUSTOM_INSTANT_MARKET_SELECTION_DEFERRED error_type=%s",
                type(exc).__name__,
            )
            return

    requested = direct_runtime._required_symbols(list(configs.values()))
    bot._vps_market_selection_last_scan = now
    bot._vps_market_selection_ids = frozenset(managed_ids)
    if not requested:
        return

    signature = tuple(requested)
    previous = tuple(getattr(bot, "_vps_market_selection_signature", ()) or ())
    bot.symbols = list(requested)
    bot.symbol = str(requested[0])
    bot._vps_market_selection_signature = signature
    if signature != previous:
        bot.logger.info(
            "CUSTOM_INSTANT_MARKETS_READY count=%s symbols=%s "
            "private_session_required_for_buy=true deduplicated=true",
            len(requested),
            ",".join(requested),
        )


def _low_latency_admit_one_runtime_account(
    bot: RFDir5TradingBot,
    managed_id: int,
) -> str:
    """Reuse an already-admitted runtime instead of rebuilding it on every sweep."""

    existing = stampede._runtime_token_for_account(bot, int(managed_id))
    if existing:
        row = bot.repository.managed_account(int(managed_id))
        if row is None:
            return ""
        lifecycle = account_lifecycle_from_row(row)
        if not account_allows_new_execution(row) and lifecycle != "settlement":
            return ""
        status = str(_row_value(row, "execution_status", "") or "").strip().lower()
        if lifecycle != "settlement" and status in {"starting", "validating"}:
            bot._set_account_execution_status(
                int(managed_id),
                "connecting",
                "Account already admitted; private execution connection is starting",
            )
        return str(existing)

    original = _ORIGINAL_ADMIT
    if original is None:
        return ""
    return str(original(bot, int(managed_id)) or "")


class _VpsBootstrapScheduler:
    """Bound OTP+WSS as one atomic lane and prioritize urgent account recovery."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._condition = asyncio.Condition()
        self._active = 0
        self._urgent_waiters = 0

    async def acquire(self, urgent: bool) -> None:
        async with self._condition:
            if urgent:
                self._urgent_waiters += 1
            try:
                await self._condition.wait_for(
                    lambda: self._active < self.limit
                    and (urgent or self._urgent_waiters == 0)
                )
                self._active += 1
            finally:
                if urgent:
                    self._urgent_waiters = max(0, self._urgent_waiters - 1)

    async def release(self) -> None:
        async with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()


def _scheduler_for(session: ClientSession) -> _VpsBootstrapScheduler:
    bot = session.bot
    scheduler = getattr(bot, "_vps_private_bootstrap_scheduler", None)
    if isinstance(scheduler, _VpsBootstrapScheduler):
        return scheduler
    scheduler = _VpsBootstrapScheduler(
        _env_int("VPS_PRIVATE_WS_BOOTSTRAP_CONCURRENCY", 6, minimum=2)
    )
    bot._vps_private_bootstrap_scheduler = scheduler
    bot.logger.warning(
        "PRIVATE_WS_BOOTSTRAP_SCHEDULER_ACTIVE concurrency=%s "
        "otp_and_wss_atomic=true urgent_priority=true",
        scheduler.limit,
    )
    return scheduler


def _priority_wake_private_connection(session: ClientSession) -> None:
    session._vps_private_priority_until = time.monotonic() + _PRIORITY_SECONDS
    original = _ORIGINAL_WAKE
    if original is not None:
        original(session)


def _session_is_urgent(session: ClientSession) -> bool:
    if bool(getattr(session, "pending_contracts", set())):
        return True
    return float(getattr(session, "_vps_private_priority_until", 0.0) or 0.0) > time.monotonic()


def _normalize_deriv_error(response: dict[str, Any]) -> dict[str, Any] | None:
    error = response.get("error") if isinstance(response, dict) else None
    if isinstance(error, dict):
        normalized = dict(error)
        try:
            normalized["status"] = int(normalized.get("status") or 0)
        except (TypeError, ValueError):
            pass
        return normalized

    errors = response.get("errors") if isinstance(response, dict) else None
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        return None
    first = dict(errors[0])
    try:
        status = int(first.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    code = str(first.get("code") or "").strip()
    if status == 429:
        code = "RATE_LIMITED"
    elif status in {401, 403}:
        code = f"HTTP_{status}"
    elif status >= 500:
        code = f"HTTP_{status}"
    return {
        "status": status,
        "code": code or (f"HTTP_{status}" if status else "DERIV_ERROR"),
        "message": str(first.get("message") or "Deriv OTP request failed"),
    }


def _vps_broker_retryable_response(
    self: request_broker._DerivRequestBroker,
    response: dict[str, Any],
) -> bool:
    error = _normalize_deriv_error(response)
    if not isinstance(error, dict):
        return False
    code = str(error.get("code") or "").strip().upper()
    try:
        status = int(error.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    return status in {429, 500, 502, 503, 504} or code in {
        "RATE_LIMITED",
        "HTTP_429",
        "HTTP_500",
        "HTTP_502",
        "HTTP_503",
        "HTTP_504",
        "REQUEST_TIMEOUT",
        "CONNECTION_ERROR",
    }


async def _vps_get_otp_url(self: ClientSession) -> str | None:
    """Read current Deriv errors correctly and keep OTP REST bounded."""

    self._vps_otp_rate_limit_seconds = 0.0
    semaphore = getattr(self.bot, "_otp_semaphore", None)
    if semaphore is None:
        semaphore = asyncio.Semaphore(
            _env_int("VPS_OTP_HTTP_CONCURRENCY", 8, minimum=2)
        )
        self.bot._otp_semaphore = semaphore

    path = f"/trading/v1/options/accounts/{self.account_id}/otp"
    async with semaphore:
        response = await base_runtime._rest_request(
            "POST",
            path,
            self.bot.app_id,
            self.bot.rest_base_url,
            token=self.credential,
        )

    error = _normalize_deriv_error(response)
    if isinstance(error, dict):
        code = str(error.get("code") or "DERIV_ERROR").strip()
        message = sanitize_account_ids(
            str(error.get("message") or "Deriv OTP request failed")
        )
        try:
            status = int(error.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        rate_limited = status == 429 or code.upper() in {"RATE_LIMITED", "HTTP_429"}
        if rate_limited:
            self._vps_otp_rate_limit_seconds = 60.0
            self.bot.logger.warning(
                "PRIVATE_WS_OTP_PROVIDER_ERROR account=%s status=%s code=%s "
                "rate_limited=true global_backoff_seconds=60",
                mask_account_id(self.account_id),
                status or "unknown",
                code,
                extra={"token_tag": self.token_tag},
            )
            return None

        permanent = status in {401, 403} or is_permanent_credential_error(error)
        self.bot._set_account_execution_status(
            self.managed_account_id,
            "credential_error" if permanent else "reconnecting",
            message,
        )
        self.bot.logger.warning(
            "PRIVATE_WS_OTP_PROVIDER_ERROR account=%s status=%s code=%s "
            "permanent=%s message=%s",
            mask_account_id(self.account_id),
            status or "unknown",
            code,
            str(permanent).lower(),
            message,
            extra={"token_tag": self.token_tag},
        )
        if permanent:
            self.bot.valid_clients = [
                item for item in self.bot.valid_clients if item[0] != self.token
            ]
        return None

    url = str((response.get("data") or {}).get("url") or "").strip()
    if not url:
        self.bot.logger.warning(
            "PRIVATE_WS_OTP_PROVIDER_ERROR account=%s status=unknown "
            "code=OTP_URL_MISSING permanent=false message=Deriv response had no WebSocket URL",
            mask_account_id(self.account_id),
            extra={"token_tag": self.token_tag},
        )
        return None
    return url


async def _connect_with_happy_eyeballs(url: str, **kwargs: Any):
    try:
        return await websockets.connect(
            url,
            happy_eyeballs_delay=0.25,
            interleave=1,
            **kwargs,
        )
    except TypeError as exc:
        text = str(exc).lower()
        if "happy_eyeballs" not in text and "interleave" not in text:
            raise
        return await websockets.connect(url, **kwargs)


async def _low_latency_open_websocket(
    gate: private_ws._PrivateConnectionGate,
    url: str,
):
    """Compatibility path for callers outside the atomic VPS bootstrap loop."""

    async with gate._handshake_slots:
        return await _connect_with_happy_eyeballs(
            url,
            open_timeout=_PRIVATE_WS_OPEN_TIMEOUT_SECONDS,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
        )


def _low_latency_normal_backoff(
    session: ClientSession,
    config: private_ws._PrivateConnectionConfig,
    attempt: int,
) -> float:
    cap = _env_float(
        "VPS_PRIVATE_WS_TRANSIENT_BACKOFF_MAX_SECONDS",
        _TRANSIENT_BACKOFF_MAX_SECONDS,
        minimum=2.0,
    )
    base = max(0.5, float(session.bot.reconnect_delay_seconds))
    delay = base * (1.5 ** min(max(0, int(attempt)), 6))
    if _session_is_urgent(session):
        cap = min(cap, 4.0)
    return min(cap, delay + private_ws._jitter(config))


async def _vps_connect_and_run(self: ClientSession) -> None:
    """Own one lane from OTP request through authenticated WSS open."""

    attempt = 0
    gate = private_ws._gate_for(self)
    config = gate.config
    ready_event = private_ws._ready_event(self)
    scheduler = _scheduler_for(self)

    while self.bot.is_running and (self.pending_contracts or private_ws._still_configured(self)):
        retry_delay = 0.0
        websocket = None
        bootstrap_phase = "queue"
        urgent = _session_is_urgent(self)
        await scheduler.acquire(urgent)
        try:
            await gate.wait_for_start_slot()
            bootstrap_phase = "otp"
            try:
                url = await asyncio.wait_for(
                    self.get_otp_url(),
                    timeout=_OTP_BOOTSTRAP_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                attempt += 1
                retry_delay = _low_latency_normal_backoff(self, config, attempt)
                self.bot.logger.warning(
                    "PRIVATE_WS_OTP_TIMEOUT account=%s timeout_seconds=%.1f "
                    "attempt=%s urgent=%s",
                    mask_account_id(self.account_id),
                    _OTP_BOOTSTRAP_TIMEOUT_SECONDS,
                    attempt,
                    str(urgent).lower(),
                    extra={"token_tag": self.token_tag},
                )
                url = None

            if not url:
                rate_penalty = float(
                    getattr(self, "_vps_otp_rate_limit_seconds", 0.0) or 0.0
                )
                if rate_penalty > 0:
                    await gate.penalize(rate_penalty)
                    retry_delay = max(retry_delay, rate_penalty)
                elif retry_delay <= 0:
                    attempt += 1
                    retry_delay = min(
                        _env_float(
                            "VPS_PRIVATE_WS_TRANSIENT_BACKOFF_MAX_SECONDS",
                            _TRANSIENT_BACKOFF_MAX_SECONDS,
                            minimum=2.0,
                        ),
                        config.otp_failure_backoff_seconds
                        * (1.5 ** min(attempt - 1, 5))
                        + private_ws._jitter(config),
                    )
                    self.bot.logger.warning(
                        "PRIVATE_WS_OTP_RETRY account=%s attempt=%s backoff_seconds=%.1f "
                        "urgent=%s",
                        mask_account_id(self.account_id),
                        attempt,
                        retry_delay,
                        str(urgent).lower(),
                        extra={"token_tag": self.token_tag},
                    )
            else:
                bootstrap_phase = "wss"
                self.bot.logger.info(
                    "Connecting to private WebSocket for account %s... urgent=%s atomic_otp_wss=true",
                    mask_account_id(self.account_id),
                    str(urgent).lower(),
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
                websocket = await _connect_with_happy_eyeballs(
                    url,
                    open_timeout=_PRIVATE_WS_OPEN_TIMEOUT_SECONDS,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            attempt += 1
            rate_limited = private_ws._is_rate_limit(exc)
            if rate_limited:
                retry_delay = private_ws._rate_backoff(config, attempt)
                await gate.penalize(retry_delay)
                self.bot.logger.warning(
                    "PRIVATE_WS_RATE_LIMITED account=%s status=%s attempt=%s "
                    "global_backoff_seconds=%.1f phase=%s",
                    mask_account_id(self.account_id),
                    private_ws._http_status(exc) or "unknown",
                    attempt,
                    retry_delay,
                    bootstrap_phase,
                    extra={"token_tag": self.token_tag},
                )
            else:
                retry_delay = _low_latency_normal_backoff(self, config, attempt)
                self.bot.logger.warning(
                    "PRIVATE_WS_BOOTSTRAP_FAILED account=%s phase=%s error=%s "
                    "attempt=%s reconnect_seconds=%.1f urgent=%s",
                    mask_account_id(self.account_id),
                    bootstrap_phase,
                    sanitize_account_ids(str(exc)),
                    attempt,
                    retry_delay,
                    str(urgent).lower(),
                    extra={"token_tag": self.token_tag},
                )
        finally:
            await scheduler.release()

        if websocket is None:
            self.is_connected = False
            self.ws = None
            ready_event.clear()
            if private_ws._still_configured(self):
                self.bot._set_account_execution_status(
                    self.managed_account_id,
                    "reconnecting",
                    "Authenticated Deriv connection is retrying",
                )
            if retry_delay > 0 and self.bot.is_running:
                await private_ws._sleep_or_wake(self, retry_delay)
            continue

        try:
            self.ws = websocket
            self.is_connected = True
            ready_event.set()
            if private_ws._still_configured(self):
                self.bot._set_account_execution_status(
                    self.managed_account_id,
                    "active",
                    "Private trading connection is active",
                )
            self.pending_requests.clear()
            attempt = 0
            self._vps_private_priority_until = 0.0
            self.bot.logger.info(
                "Private WebSocket connected for account %s atomic_otp_wss=true",
                mask_account_id(self.account_id),
                extra={
                    "token_tag": self.token_tag,
                    "masked_account_id": mask_account_id(self.account_id),
                },
            )
            await websocket.send('{"balance":1,"subscribe":1,"req_id":900001}')

            for contract_id in list(self.pending_contracts):
                await self.subscribe_contract(contract_id)

            self.bot._on_private_session_ready(self)
            ping_task = asyncio.create_task(self._ping_loop())
            self.reconcile_task = asyncio.create_task(self._reconcile_contracts_loop())
            try:
                async for message in websocket:
                    await self._on_message(message)
            finally:
                if self.reconcile_task:
                    self.reconcile_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self.reconcile_task
                    self.reconcile_task = None
                ping_task.cancel()
                with suppress(asyncio.CancelledError):
                    await ping_task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            attempt += 1
            retry_delay = _low_latency_normal_backoff(self, config, attempt)
            self.bot.logger.warning(
                "Private connection lost for account %s: %s. Reconnecting in %.1fs...",
                mask_account_id(self.account_id),
                sanitize_account_ids(str(exc)),
                retry_delay,
                extra={"token_tag": self.token_tag},
            )
        finally:
            self.is_connected = False
            self.ws = None
            ready_event.clear()
            with suppress(Exception):
                await websocket.close()
            if private_ws._still_configured(self):
                self.bot._set_account_execution_status(
                    self.managed_account_id,
                    "reconnecting",
                    "Private trading connection closed",
                )

        if retry_delay > 0 and self.bot.is_running:
            await private_ws._sleep_or_wake(self, retry_delay)


async def _low_latency_contract_snapshot_once(
    self: ClientSession,
    contract_id: int,
) -> dict[str, Any]:
    """Bound one-off settlement reconciliation so an open row cannot linger minutes."""

    self._vps_private_priority_until = time.monotonic() + _PRIORITY_SECONDS
    try:
        url = await asyncio.wait_for(
            self.get_otp_url(),
            timeout=_CONTRACT_SNAPSHOT_OTP_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return {
            "error": {
                "message": "Authenticated contract reconciliation OTP timed out",
                "code": "OTP_TIMEOUT",
            }
        }
    if not url:
        return {
            "error": {
                "message": "Authenticated contract status connection unavailable",
                "code": "OTP_UNAVAILABLE",
            }
        }

    req_id = 920000 + int(contract_id) % 100000
    websocket = None
    try:
        websocket = await _connect_with_happy_eyeballs(
            url,
            open_timeout=_CONTRACT_SNAPSHOT_OPEN_TIMEOUT_SECONDS,
            close_timeout=3,
            ping_interval=None,
        )
        await websocket.send(
            json.dumps(
                {
                    "proposal_open_contract": 1,
                    "contract_id": int(contract_id),
                    "req_id": req_id,
                }
            )
        )
        deadline = time.monotonic() + _CONTRACT_SNAPSHOT_RESPONSE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            response = json.loads(raw)
            if response.get("req_id") == req_id:
                return response
        return {
            "error": {
                "message": "Authenticated contract status response was not received",
                "code": "TIMEOUT",
            }
        }
    except asyncio.TimeoutError:
        return {
            "error": {
                "message": "Authenticated contract status request timed out",
                "code": "TIMEOUT",
            }
        }
    except Exception as exc:
        return {
            "error": {
                "message": sanitize_account_ids(str(exc)),
                "code": "CONNECTION_ERROR",
            }
        }
    finally:
        if websocket is not None:
            with suppress(Exception):
                await websocket.close()


def install_vps_low_latency_runtime() -> None:
    """Install the final full-VPS low-latency connection authority."""

    global _INSTALLED, _ORIGINAL_ADMIT, _ORIGINAL_WAKE
    if _INSTALLED:
        return
    if str(os.getenv("FRONTEND_HOSTING_MODE", "")).strip().lower() != "vps":
        return

    _ORIGINAL_ADMIT = stampede._admit_one_runtime_account
    _ORIGINAL_WAKE = private_ws.wake_private_connection

    instant._fast_runtime_accounts = _low_latency_fast_runtime_accounts
    instant._select_saved_strategy_markets = _low_latency_select_saved_strategy_markets
    stampede._admit_one_runtime_account = _low_latency_admit_one_runtime_account
    RFDir5TradingBot._admit_custom_runtime_account = _low_latency_admit_one_runtime_account

    # Current Deriv error payloads use an `errors` array. Normalize that shape for
    # OTP safe retries without changing financial POST replay policy.
    request_broker._DerivRequestBroker._retryable_response = _vps_broker_retryable_response
    ClientSession.get_otp_url = _vps_get_otp_url

    # The old code generated OTPs before acquiring a WSS semaphore. Under a large
    # account set that created an OTP queue, even though Deriv says OTP URLs should
    # be opened promptly. The final loop owns one atomic lane from OTP through WSS.
    private_ws.wake_private_connection = _priority_wake_private_connection
    ClientSession.connect_and_run = _vps_connect_and_run
    private_ws._PrivateConnectionGate.open_websocket = _low_latency_open_websocket
    private_ws._normal_backoff = _low_latency_normal_backoff

    ClientSession.request_contract_snapshot_once = _low_latency_contract_snapshot_once

    RFDir5TradingBot._vps_low_latency_runtime_installed = True
    RFDir5TradingBot._vps_low_latency_atomic_otp_wss = True
    RFDir5TradingBot._vps_low_latency_current_deriv_errors = True
    RFDir5TradingBot._vps_low_latency_rate_limit_backoff_preserved = True
    RFDir5TradingBot._vps_low_latency_happy_eyeballs = True
    ClientSession._vps_low_latency_contract_reconcile = True
    _INSTALLED = True

    LOGGER.warning(
        "VPS_LOW_LATENCY_RUNTIME_ACTIVE eager_credentials=true market_selection_deduplicated=true "
        "atomic_otp_wss=true current_deriv_errors=true urgent_priority=true happy_eyeballs=true "
        "transient_backoff_cap_seconds=%.1f provider_rate_limit_backoff=preserved "
        "contract_reconcile=bounded",
        _env_float(
            "VPS_PRIVATE_WS_TRANSIENT_BACKOFF_MAX_SECONDS",
            _TRANSIENT_BACKOFF_MAX_SECONDS,
            minimum=2.0,
        ),
    )
