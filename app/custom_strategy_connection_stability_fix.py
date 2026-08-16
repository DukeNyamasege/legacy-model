from __future__ import annotations

import asyncio
import socket
import time
from contextlib import suppress
from typing import Any

import aiohttp
import websockets

from app import custom_execution_consistency_authority as consistency
from app import custom_strategy_connection_stampede_guard as connection_guard
from app import final_execution_continuity as continuity
from app import manual_martingale_execution_authority as martingale_authority
from app import private_websocket_rate_limit as private_ws
from app import vps_execution_start_recovery as vps_recovery
from app.deriv.http import deriv_headers
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import (
    ClientSession,
    is_permanent_credential_error,
    mask_account_id,
    sanitize_account_ids,
)


_INSTALLED = False

_WATCHDOG_INTERVAL_SECONDS = 2.0
_DEAD_SESSION_REPAIR_GRACE_SECONDS = 8.0
_RECONNECT_LOG_INTERVAL_SECONDS = 60.0
_SOFT_RECONNECT_NOTICE_INTERVAL_SECONDS = 60.0
_PUBLIC_RECONNECT_SKIP_LOG_INTERVAL_SECONDS = 60.0
_OTP_BOOTSTRAP_TIMEOUT_SECONDS = 20.0
_OTP_HTTP_TOTAL_TIMEOUT_SECONDS = 18.0
_PRIVATE_WS_OPEN_TIMEOUT_SECONDS = 35.0

_CONSISTENCY_STAKE_POLICY_REASON = consistency._stake_policy_reason
_CONTINUITY_STAKE_POLICY_REASON = continuity._is_stake_policy_reason
_MARTINGALE_STAKE_POLICY_REASON = martingale_authority._is_stake_policy_rejection
_EXTRA_STAKE_POLICY_MARKERS = (
    "multiplier stake",
    "exceeds spendable balance",
)


class _OtpHttpError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = int(status)


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _provider_backoff_active(row: Any) -> bool:
    reason = str(_row_value(row, "execution_status_reason", "") or "").lower()
    return "rate-limit" in reason or "rate limited" in reason


def _financial_stake_policy_reason(reason: str) -> bool:
    """Classify every deterministic unaffordable-stake outcome as a financial skip."""

    text = str(reason or "").strip().lower()
    if any(marker in text for marker in _EXTRA_STAKE_POLICY_MARKERS):
        return True
    return bool(
        _CONSISTENCY_STAKE_POLICY_REASON(reason)
        or _CONTINUITY_STAKE_POLICY_REASON(reason)
        or _MARTINGALE_STAKE_POLICY_REASON(reason)
    )


def _stability_state(bot: RFDir5TradingBot) -> dict[int, dict[str, float]]:
    state = getattr(bot, "_custom_connection_stability_state", None)
    if not isinstance(state, dict):
        state = {}
        bot._custom_connection_stability_state = state
    return state


def _notice_state(bot: RFDir5TradingBot) -> dict[int, float]:
    state = getattr(bot, "_custom_soft_reconnect_notice_state", None)
    if not isinstance(state, dict):
        state = {}
        bot._custom_soft_reconnect_notice_state = state
    return state


def _notice_due(bot: RFDir5TradingBot, managed_id: int) -> bool:
    now = time.monotonic()
    state = _notice_state(bot)
    last = float(state.get(int(managed_id), 0.0) or 0.0)
    if now - last < _SOFT_RECONNECT_NOTICE_INTERVAL_SECONDS:
        return False
    state[int(managed_id)] = now
    return True


def _otp_http_session(bot: RFDir5TradingBot) -> aiohttp.ClientSession:
    """Return one keep-alive IPv4 REST client shared by every account OTP request."""

    session = getattr(bot, "_private_otp_http_session", None)
    if isinstance(session, aiohttp.ClientSession) and not session.closed:
        return session

    connector = aiohttp.TCPConnector(
        family=socket.AF_INET,
        limit=8,
        limit_per_host=4,
        use_dns_cache=True,
        ttl_dns_cache=300,
        keepalive_timeout=30,
        enable_cleanup_closed=True,
    )
    timeout = aiohttp.ClientTimeout(
        total=_OTP_HTTP_TOTAL_TIMEOUT_SECONDS,
        connect=6.0,
        sock_connect=6.0,
        sock_read=12.0,
    )
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        trust_env=False,
    )
    bot._private_otp_http_session = session
    bot.logger.info(
        "PRIVATE_WS_OTP_HTTP_POOL_ACTIVE family=ipv4 keepalive=true "
        "pool_limit=8 per_host=4 total_timeout_seconds=%.1f",
        _OTP_HTTP_TOTAL_TIMEOUT_SECONDS,
    )
    return session


def _otp_error_from_payload(payload: Any, status: int) -> dict[str, Any]:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return dict(error)
        errors = payload.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return dict(errors[0])
    return {
        "code": f"HTTP_{int(status)}",
        "message": f"Deriv OTP request failed with HTTP {int(status)}",
    }


def _cloudflare_rate_limited(status: int, text: str) -> bool:
    lower = str(text or "").lower()
    return int(status) == 429 or (
        "cloudflare" in lower
        and ("1015" in lower or "rate limit" in lower or "rate-limited" in lower)
    )


async def _fresh_otp_url(self: ClientSession) -> str:
    """Fetch one account OTP on a reused IPv4 HTTP transport."""

    session = _otp_http_session(self.bot)
    endpoint = (
        f"{self.bot.rest_base_url.rstrip('/')}"
        f"/trading/v1/options/accounts/{self.account_id}/otp"
    )
    headers = deriv_headers(self.bot.app_id, bearer_token=self.credential)

    try:
        async with session.post(endpoint, headers=headers) as response:
            try:
                payload: Any = await response.json(content_type=None)
            except Exception:
                payload = {}
            body_text = ""
            if response.status not in {200, 201}:
                with suppress(Exception):
                    body_text = await response.text()

            if response.status in {200, 201}:
                data = payload.get("data") if isinstance(payload, dict) else None
                url = str(data.get("url") or "") if isinstance(data, dict) else ""
                if url:
                    return url
                self.bot.logger.warning(
                    "PRIVATE_WS_OTP_INVALID_RESPONSE account=%s status=%s "
                    "data_url_present=false",
                    mask_account_id(self.account_id),
                    response.status,
                    extra={"token_tag": self.token_tag},
                )
                return ""

            error = _otp_error_from_payload(payload, response.status)
            message = sanitize_account_ids(
                str(error.get("message") or f"OTP HTTP {response.status}")
            )

            if _cloudflare_rate_limited(response.status, body_text):
                raise _OtpHttpError(429, message or "Deriv OTP rate limited")

            permanent = response.status in {401, 403} or is_permanent_credential_error(error)
            self.bot._set_account_execution_status(
                self.managed_account_id,
                "credential_error" if permanent else "reconnecting",
                message,
            )
            if permanent:
                self.bot.valid_clients = [
                    item for item in self.bot.valid_clients if item[0] != self.token
                ]
                self.bot.logger.error(
                    "ACCOUNT_CREDENTIAL_ISOLATED account=%s status=%s reason=%s; "
                    "other accounts continue",
                    mask_account_id(self.account_id),
                    response.status,
                    message,
                    extra={"token_tag": self.token_tag},
                )
                return ""

            self.bot.logger.warning(
                "PRIVATE_WS_OTP_HTTP_ERROR account=%s status=%s reason=%s",
                mask_account_id(self.account_id),
                response.status,
                message,
                extra={"token_tag": self.token_tag},
            )
            return ""
    except asyncio.CancelledError:
        raise
    except _OtpHttpError:
        raise
    except asyncio.TimeoutError:
        raise
    except aiohttp.ClientError as exc:
        self.bot.logger.warning(
            "PRIVATE_WS_OTP_TRANSPORT_FAILED account=%s error_type=%s",
            mask_account_id(self.account_id),
            type(exc).__name__,
            extra={"token_tag": self.token_tag},
        )
        return ""


async def _stable_execution_watchdog(bot: RFDir5TradingBot) -> None:
    """Repair missing/dead sessions without disturbing a live reconnect loop.

    A live ClientSession owns its queued connection slot, OTP request, provider
    backoff and WebSocket handshake. The watchdog must never wake, cancel or recycle
    that task merely because it has not connected yet.
    """

    while bot.is_running:
        try:
            now = time.monotonic()
            state = _stability_state(bot)
            enabled_ids: set[int] = set()

            for row in bot.repository.list_managed_accounts():
                managed_id = int(_row_value(row, "id"))
                if not bool(_row_value(row, "enabled", False)):
                    state.pop(managed_id, None)
                    continue
                enabled_ids.add(managed_id)

                session = connection_guard._private_session_for_account(bot, managed_id)
                connected = bool(
                    session is not None and getattr(session, "is_connected", False)
                )
                if connected:
                    state.pop(managed_id, None)
                    if connection_guard._direct_runtime_for_account(bot, managed_id) is None:
                        connection_guard._schedule_targeted_runtime_repair(bot, managed_id)
                    continue

                entry = state.setdefault(
                    managed_id,
                    {
                        "disconnected_since": now,
                        "last_repair": 0.0,
                        "last_log": 0.0,
                    },
                )
                disconnected_for = now - float(entry.get("disconnected_since") or now)

                if _provider_backoff_active(row):
                    continue

                task_alive = connection_guard._session_task_alive(session)

                if session is not None and task_alive:
                    last_log = float(entry.get("last_log") or 0.0)
                    if (
                        disconnected_for >= _DEAD_SESSION_REPAIR_GRACE_SECONDS
                        and now - last_log >= _RECONNECT_LOG_INTERVAL_SECONDS
                    ):
                        entry["last_log"] = now
                        bot.logger.info(
                            "VPS_EXECUTION_RECONNECT_OWNED managed_id=%s "
                            "disconnected_seconds=%.1f session_task_alive=true "
                            "watchdog_wake=false session_recycle=false",
                            managed_id,
                            disconnected_for,
                        )
                    continue

                if disconnected_for < _DEAD_SESSION_REPAIR_GRACE_SECONDS:
                    continue

                last_repair = float(entry.get("last_repair") or 0.0)
                if now - last_repair < _DEAD_SESSION_REPAIR_GRACE_SECONDS:
                    continue
                entry["last_repair"] = now
                connection_guard._schedule_targeted_runtime_repair(bot, managed_id)
                bot.logger.warning(
                    "VPS_EXECUTION_DEAD_SESSION_REPAIR managed_id=%s "
                    "session_object=%s session_task_alive=false "
                    "sibling_sessions_rebuilt=false",
                    managed_id,
                    session is not None,
                )

            for managed_id in list(state):
                if managed_id not in enabled_ids:
                    state.pop(managed_id, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            bot.logger.exception("VPS_EXECUTION_STABILITY_WATCHDOG_FAILED")
        await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)


def _soft_private_reconnect(
    bot: RFDir5TradingBot,
    managed_id: int,
    reason: str,
) -> None:
    """Recover one private execution stream without disturbing live backoff."""

    account = bot.repository.managed_account(int(managed_id)) or {}
    if not bool(account.get("enabled")):
        return

    session = connection_guard._private_session_for_account(bot, int(managed_id))
    connected = bool(session is not None and getattr(session, "is_connected", False))
    task_alive = connection_guard._session_task_alive(session)
    repair_required = session is None or not task_alive

    # A live disconnected ClientSession already owns its queued startup, OTP,
    # handshake and retry/backoff loop. Qualified strategy attempts must not set
    # its wake event because repeated signals can collapse the intended backoff.
    # Only a genuinely missing/dead session is reconstructed.
    if repair_required:
        connection_guard._schedule_targeted_runtime_repair(bot, int(managed_id))
    elif connection_guard._direct_runtime_for_account(bot, int(managed_id)) is None:
        connection_guard._schedule_targeted_runtime_repair(bot, int(managed_id))
        repair_required = True

    # A disconnected account can be visited on every qualified strategy tick.
    # Rewriting the same status, event-bus notification and warning thousands of
    # times adds avoidable PostgreSQL/logging pressure to the same event loop that
    # must complete OTP and TLS handshakes. Keep the first notice and then at most
    # one notice per account per minute while its live reconnect loop owns recovery.
    if not repair_required and not _notice_due(bot, int(managed_id)):
        return

    bot._set_account_execution_status(
        int(managed_id),
        "reconnecting",
        "Private trading connection is recovering automatically; Auto Trading remains active.",
    )
    bot.logger.warning(
        "CUSTOM_EXECUTION_SOFT_RECONNECT managed_id=%s lifecycle_stop=false "
        "forced_disconnect=false public_reconnect=false execution_wake=false "
        "session_task_alive=%s session_connected=%s reason=%s",
        int(managed_id),
        task_alive,
        connected,
        str(reason or "execution transport fault")[:140],
    )
    try:
        consistency._dashboard_wakeup(bot)
    except Exception:
        pass


def _skip_execution_driven_public_reconnect(
    bot: RFDir5TradingBot,
    reason: str,
) -> None:
    """Keep account-private faults from restarting the shared market stream."""

    now = time.monotonic()
    last = float(getattr(bot, "_private_public_skip_log_at", 0.0) or 0.0)
    if now - last < _PUBLIC_RECONNECT_SKIP_LOG_INTERVAL_SECONDS:
        return
    bot._private_public_skip_log_at = now
    bot.logger.info(
        "CUSTOM_PUBLIC_RECONNECT_SKIPPED source=account_private_execution "
        "public_stream_owner=public_websocket_resilience reason=%s",
        str(reason or "account private execution fault")[:140],
    )


async def _fresh_otp_connect_and_run(self: ClientSession) -> None:
    """Reserve handshake capacity, then obtain and consume a fresh bounded OTP."""

    attempt = 0
    gate = private_ws._gate_for(self)
    config = gate.config
    ready_event = private_ws._ready_event(self)

    while self.bot.is_running and (
        self.pending_contracts or private_ws._still_configured(self)
    ):
        retry_delay = 0.0
        websocket = None
        try:
            url = ""
            async with gate._handshake_slots:
                await gate.wait_for_start_slot()
                try:
                    url = await asyncio.wait_for(
                        _fresh_otp_url(self),
                        timeout=_OTP_BOOTSTRAP_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    self.bot.logger.warning(
                        "PRIVATE_WS_OTP_TIMEOUT account=%s timeout_seconds=%.1f "
                        "handshake_slot_released=true",
                        mask_account_id(self.account_id),
                        _OTP_BOOTSTRAP_TIMEOUT_SECONDS,
                        extra={
                            "token_tag": self.token_tag,
                            "masked_account_id": mask_account_id(self.account_id),
                        },
                    )
                    url = ""

                if url:
                    self.bot.logger.info(
                        "Connecting to private WebSocket for account %s...",
                        mask_account_id(self.account_id),
                        extra={
                            "token_tag": self.token_tag,
                            "masked_account_id": mask_account_id(self.account_id),
                        },
                    )
                    websocket = await websockets.connect(
                        url,
                        open_timeout=_PRIVATE_WS_OPEN_TIMEOUT_SECONDS,
                        close_timeout=5,
                        ping_interval=20,
                        ping_timeout=20,
                        family=socket.AF_INET,
                    )

            if not url:
                if not self.pending_contracts and not private_ws._still_configured(self):
                    return
                attempt += 1
                retry_delay = min(
                    config.maximum_backoff_seconds,
                    config.otp_failure_backoff_seconds
                    * (1.5 ** min(attempt - 1, 5)),
                ) + private_ws._jitter(config)
                self.bot.logger.warning(
                    "PRIVATE_WS_OTP_RETRY account=%s attempt=%s backoff_seconds=%.1f",
                    mask_account_id(self.account_id),
                    attempt,
                    retry_delay,
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
            else:
                if websocket is None:
                    raise RuntimeError(
                        "Private WebSocket handshake completed without a connection"
                    )
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
                self.bot.logger.info(
                    "Private WebSocket connected for account %s",
                    mask_account_id(self.account_id),
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
                await websocket.send(
                    '{"balance":1,"subscribe":1,"req_id":900001}'
                )

                for contract_id in list(self.pending_contracts):
                    await self.subscribe_contract(contract_id)

                self.bot._on_private_session_ready(self)
                ping_task = asyncio.create_task(self._ping_loop())
                self.reconcile_task = asyncio.create_task(
                    self._reconcile_contracts_loop()
                )
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

                    self.is_connected = False
                    self.ws = None
                    ready_event.clear()
                    if private_ws._still_configured(self):
                        self.bot._set_account_execution_status(
                            self.managed_account_id,
                            "reconnecting",
                            "Private trading connection closed",
                        )
                    retry_delay = private_ws._normal_backoff(self, config, attempt)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.is_connected = False
            self.ws = None
            ready_event.clear()
            attempt += 1
            rate_limited = private_ws._is_rate_limit(exc)
            if rate_limited:
                retry_delay = private_ws._rate_backoff(config, attempt)
                await gate.penalize(retry_delay)
                self.bot.logger.warning(
                    "PRIVATE_WS_RATE_LIMITED account=%s status=%s attempt=%s "
                    "global_backoff_seconds=%.1f",
                    mask_account_id(self.account_id),
                    private_ws._http_status(exc) or "unknown",
                    attempt,
                    retry_delay,
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
            else:
                retry_delay = private_ws._normal_backoff(self, config, attempt)
                self.bot.logger.warning(
                    "Private connection lost for account %s: %s. "
                    "Reconnecting in %.1fs...",
                    mask_account_id(self.account_id),
                    sanitize_account_ids(str(exc)),
                    retry_delay,
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
            if private_ws._still_configured(self):
                self.bot._set_account_execution_status(
                    self.managed_account_id,
                    "reconnecting",
                    (
                        f"Deriv connection rate-limited; retrying in "
                        f"{retry_delay:.0f} seconds"
                        if rate_limited
                        else "Private trading connection interrupted"
                    ),
                )
        finally:
            if websocket is not None:
                with suppress(Exception):
                    await websocket.close()
            self.is_connected = False
            self.ws = None
            ready_event.clear()

        if retry_delay > 0 and self.bot.is_running:
            await private_ws._sleep_or_wake(self, retry_delay)


def install_custom_strategy_connection_stability_fix() -> None:
    """Final connection invariant for the full-VPS Custom Strategy worker."""

    global _INSTALLED
    if _INSTALLED:
        return

    vps_recovery._stalled_execution_watchdog = _stable_execution_watchdog
    consistency._request_private_reconnect = _soft_private_reconnect
    consistency._request_public_reconnect = _skip_execution_driven_public_reconnect

    consistency._stake_policy_reason = _financial_stake_policy_reason
    continuity._is_stake_policy_reason = _financial_stake_policy_reason
    martingale_authority._is_stake_policy_rejection = _financial_stake_policy_reason

    ClientSession.connect_and_run = _fresh_otp_connect_and_run

    RFDir5TradingBot._custom_strategy_connection_stability_fix_installed = True
    RFDir5TradingBot._vps_stalled_execution_recycle_seconds = None
    RFDir5TradingBot._private_ws_fresh_otp_before_handshake = True
    RFDir5TradingBot._private_ws_execution_wake_enabled = False
    RFDir5TradingBot._private_ws_otp_bootstrap_timeout_seconds = (
        _OTP_BOOTSTRAP_TIMEOUT_SECONDS
    )
    RFDir5TradingBot._private_ws_otp_http_keepalive = True
    RFDir5TradingBot._private_ws_ipv4_transport = True
    RFDir5TradingBot._private_ws_open_timeout_seconds = _PRIVATE_WS_OPEN_TIMEOUT_SECONDS
    RFDir5TradingBot._private_ws_soft_reconnect_notice_interval_seconds = (
        _SOFT_RECONNECT_NOTICE_INTERVAL_SECONDS
    )
    RFDir5TradingBot._stake_policy_transport_isolation = True
    _INSTALLED = True
