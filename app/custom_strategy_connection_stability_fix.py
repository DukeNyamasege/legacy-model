from __future__ import annotations

import asyncio
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
_PRIORITY_WAKE_INTERVAL_SECONDS = 8.0

# Keep bootstrap bounded, but do not let one account hold the whole fleet for
# 45 seconds. Deriv documents the OTP endpoint as a short REST step that returns
# a ready-to-use WebSocket URL; if it does not answer promptly, retry the account
# without blocking the private WS handshake pool.
_OTP_BOOTSTRAP_CONCURRENCY = 3
_OTP_BOOTSTRAP_TIMEOUT_SECONDS = 12.0
_OTP_HTTP_TOTAL_TIMEOUT_SECONDS = 10.0
_OTP_RETRY_MAX_SECONDS = 20.0
_PRIVATE_WS_OPEN_TIMEOUT_SECONDS = 25.0

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


def _set_execution_transport_status(
    session: ClientSession,
    reason: str,
    *,
    status: str = "reconnecting",
) -> None:
    """Persist the exact private-transport reason so the UI does not stay vague."""

    if not private_ws._still_configured(session):
        return
    try:
        session.bot._set_account_execution_status(
            session.managed_account_id,
            status,
            str(reason or "Private Deriv execution transport is reconnecting")[:240],
        )
    except Exception:
        session.bot.logger.exception(
            "PRIVATE_WS_STATUS_UPDATE_FAILED account=%s",
            mask_account_id(session.account_id),
            extra={"token_tag": session.token_tag},
        )


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


def _priority_wake_due(bot: RFDir5TradingBot, managed_id: int) -> bool:
    state = getattr(bot, "_custom_private_priority_wake_state", None)
    if not isinstance(state, dict):
        state = {}
        bot._custom_private_priority_wake_state = state
    now = time.monotonic()
    last = float(state.get(int(managed_id), 0.0) or 0.0)
    if now - last < _PRIORITY_WAKE_INTERVAL_SECONDS:
        return False
    state[int(managed_id)] = now
    return True


def _otp_bootstrap_slots(bot: RFDir5TradingBot) -> asyncio.Semaphore:
    """Bound REST OTP bootstrap separately from WebSocket handshake capacity."""

    slots = getattr(bot, "_private_otp_bootstrap_slots", None)
    if isinstance(slots, asyncio.Semaphore):
        return slots
    slots = asyncio.Semaphore(_OTP_BOOTSTRAP_CONCURRENCY)
    bot._private_otp_bootstrap_slots = slots
    bot.logger.info(
        "PRIVATE_WS_OTP_BOOTSTRAP_POOL_ACTIVE concurrency=%s "
        "handshake_slot_held_during_otp=false",
        _OTP_BOOTSTRAP_CONCURRENCY,
    )
    return slots


def _otp_http_session(bot: RFDir5TradingBot) -> aiohttp.ClientSession:
    """Return one keep-alive REST client using the system-selected network path."""

    session = getattr(bot, "_private_otp_http_session", None)
    if isinstance(session, aiohttp.ClientSession) and not session.closed:
        return session

    # Do not force IPv4. The VPS has IPv6 too, and Deriv's documented endpoint is
    # api.derivws.com; letting aiohttp use system DNS / happy-eyeballs restores
    # the pre-regression network path instead of pinning every OTP request to one
    # possibly slow IPv4 route.
    connector = aiohttp.TCPConnector(
        limit=6,
        limit_per_host=3,
        use_dns_cache=True,
        ttl_dns_cache=120,
        keepalive_timeout=20,
        enable_cleanup_closed=True,
    )
    timeout = aiohttp.ClientTimeout(
        total=_OTP_HTTP_TOTAL_TIMEOUT_SECONDS,
        connect=4.0,
        sock_connect=4.0,
        sock_read=8.0,
    )
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        trust_env=False,
    )
    bot._private_otp_http_session = session
    bot.logger.info(
        "PRIVATE_WS_OTP_HTTP_POOL_ACTIVE family=auto keepalive=true "
        "pool_limit=6 per_host=3 total_timeout_seconds=%.1f",
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
    """Fetch one account OTP on a reused keep-alive REST transport."""

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
                reason = (
                    "Deriv OTP REST response was successful but did not include "
                    "data.url; private execution cannot open the account WebSocket yet."
                )
                _set_execution_transport_status(self, reason)
                self.bot.logger.warning(
                    "PRIVATE_WS_OTP_INVALID_RESPONSE account=%s status=%s "
                    "data_url_present=false ui_reason=%s",
                    mask_account_id(self.account_id),
                    response.status,
                    reason,
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
            reason = (
                f"Deriv OTP REST failed with HTTP {response.status}: {message}. "
                "Private execution cannot buy until this account receives a valid OTP URL."
            )
            self.bot._set_account_execution_status(
                self.managed_account_id,
                "credential_error" if permanent else "reconnecting",
                reason,
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
                "PRIVATE_WS_OTP_HTTP_ERROR account=%s status=%s reason=%s "
                "ui_reason=%s",
                mask_account_id(self.account_id),
                response.status,
                message,
                reason,
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
        reason = (
            f"Deriv OTP REST transport failed: {type(exc).__name__}. "
            "Private execution is retrying this account."
        )
        _set_execution_transport_status(self, reason)
        self.bot.logger.warning(
            "PRIVATE_WS_OTP_TRANSPORT_FAILED account=%s error_type=%s ui_reason=%s",
            mask_account_id(self.account_id),
            type(exc).__name__,
            reason,
            extra={"token_tag": self.token_tag},
        )
        return ""


async def _stable_execution_watchdog(bot: RFDir5TradingBot) -> None:
    """Repair missing/dead sessions without disturbing a live reconnect loop."""

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
    """Recover one private execution stream without disturbing live in-flight work."""

    account = bot.repository.managed_account(int(managed_id)) or {}
    if not bool(account.get("enabled")):
        return

    session = connection_guard._private_session_for_account(bot, int(managed_id))
    connected = bool(session is not None and getattr(session, "is_connected", False))
    task_alive = connection_guard._session_task_alive(session)
    repair_required = session is None or not task_alive

    if repair_required:
        connection_guard._schedule_targeted_runtime_repair(bot, int(managed_id))
    elif connection_guard._direct_runtime_for_account(bot, int(managed_id)) is None:
        connection_guard._schedule_targeted_runtime_repair(bot, int(managed_id))
        repair_required = True
    elif (
        not connected
        and session is not None
        and _priority_wake_due(bot, int(managed_id))
        and not bool(getattr(session, "_private_otp_inflight", False))
        and not bool(getattr(session, "_private_ws_handshake_inflight", False))
    ):
        private_ws.wake_private_connection(session)
        bot.logger.info(
            "PRIVATE_WS_PRIORITY_WAKE managed_id=%s reason=qualified_signal "
            "wake_scope=single_account in_flight=false",
            int(managed_id),
        )

    if not repair_required and not _notice_due(bot, int(managed_id)):
        return

    ui_reason = (
        "Private Deriv execution WebSocket is not connected yet. "
        "Scanner can detect signals, but BUY is blocked until this account's "
        "OTP URL and private WebSocket connect successfully."
    )
    bot._set_account_execution_status(
        int(managed_id),
        "reconnecting",
        ui_reason,
    )
    bot.logger.warning(
        "CUSTOM_EXECUTION_SOFT_RECONNECT managed_id=%s lifecycle_stop=false "
        "forced_disconnect=false public_reconnect=false execution_wake=priority_single_account "
        "session_task_alive=%s session_connected=%s reason=%s ui_reason=%s",
        int(managed_id),
        task_alive,
        connected,
        str(reason or "execution transport fault")[:140],
        ui_reason,
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
    """Fetch Deriv OTP over REST, then consume it promptly in the private WS."""

    attempt = 0
    gate = private_ws._gate_for(self)
    config = gate.config
    ready_event = private_ws._ready_event(self)
    bootstrap_slots = _otp_bootstrap_slots(self.bot)

    while self.bot.is_running and (
        self.pending_contracts or private_ws._still_configured(self)
    ):
        retry_delay = 0.0
        websocket = None
        try:
            url = ""
            async with bootstrap_slots:
                await gate.wait_for_start_slot()
                try:
                    self._private_otp_inflight = True
                    url = await asyncio.wait_for(
                        _fresh_otp_url(self),
                        timeout=_OTP_BOOTSTRAP_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    reason = (
                        "Deriv OTP REST request timed out after "
                        f"{_OTP_BOOTSTRAP_TIMEOUT_SECONDS:.0f}s. Scanner may be ready, "
                        "but BUY is blocked until the private execution WebSocket receives "
                        "a fresh OTP URL. Retrying this account only."
                    )
                    _set_execution_transport_status(self, reason)
                    self.bot.logger.warning(
                        "PRIVATE_WS_OTP_TIMEOUT account=%s timeout_seconds=%.1f "
                        "handshake_slot_held=false ui_reason=%s",
                        mask_account_id(self.account_id),
                        _OTP_BOOTSTRAP_TIMEOUT_SECONDS,
                        reason,
                        extra={
                            "token_tag": self.token_tag,
                            "masked_account_id": mask_account_id(self.account_id),
                        },
                    )
                    url = ""
                finally:
                    self._private_otp_inflight = False

            if url:
                self.bot.logger.info(
                    "PRIVATE_WS_OTP_READY account=%s otp_validity_seconds=120 "
                    "websocket_connect_next=true network_family=auto",
                    mask_account_id(self.account_id),
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
                async with gate._handshake_slots:
                    self._private_ws_handshake_inflight = True
                    try:
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
                        )
                    finally:
                        self._private_ws_handshake_inflight = False

            if not url:
                if not self.pending_contracts and not private_ws._still_configured(self):
                    return
                attempt += 1
                retry_delay = min(
                    _OTP_RETRY_MAX_SECONDS,
                    config.otp_failure_backoff_seconds
                    * (1.4 ** min(attempt - 1, 4)),
                ) + private_ws._jitter(config)
                if private_ws._still_configured(self):
                    _set_execution_transport_status(
                        self,
                        "Deriv OTP URL is unavailable; private execution is retrying "
                        f"this account in {retry_delay:.1f}s.",
                    )
                self.bot.logger.warning(
                    "PRIVATE_WS_OTP_RETRY account=%s attempt=%s backoff_seconds=%.1f "
                    "handshake_slot_held=false retry_cap_seconds=%.1f",
                    mask_account_id(self.account_id),
                    attempt,
                    retry_delay,
                    _OTP_RETRY_MAX_SECONDS,
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
                        "Private Deriv execution WebSocket is active; BUY is allowed.",
                    )
                self.pending_requests.clear()
                attempt = 0
                self.bot.logger.info(
                    "Private WebSocket connected for account %s network_family=auto",
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
                            "Private Deriv execution WebSocket closed; reconnecting this account.",
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
                reason = (
                    "Deriv private execution transport is rate-limited; "
                    f"retrying in {retry_delay:.0f}s."
                )
                _set_execution_transport_status(self, reason)
                self.bot.logger.warning(
                    "PRIVATE_WS_RATE_LIMITED account=%s status=%s attempt=%s "
                    "global_backoff_seconds=%.1f ui_reason=%s",
                    mask_account_id(self.account_id),
                    private_ws._http_status(exc) or "unknown",
                    attempt,
                    retry_delay,
                    reason,
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
            else:
                retry_delay = private_ws._normal_backoff(self, config, attempt)
                error_text = sanitize_account_ids(str(exc))
                reason = (
                    "Private Deriv execution WebSocket failed: "
                    f"{error_text}. Retrying in {retry_delay:.1f}s."
                )
                _set_execution_transport_status(self, reason)
                self.bot.logger.warning(
                    "Private connection lost for account %s: %s. "
                    "Reconnecting in %.1fs... ui_reason=%s",
                    mask_account_id(self.account_id),
                    error_text,
                    retry_delay,
                    reason,
                    extra={
                        "token_tag": self.token_tag,
                        "masked_account_id": mask_account_id(self.account_id),
                    },
                )
        finally:
            if websocket is not None:
                with suppress(Exception):
                    await websocket.close()
            self.is_connected = False
            self.ws = None
            ready_event.clear()
            self._private_otp_inflight = False
            self._private_ws_handshake_inflight = False

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
    RFDir5TradingBot._private_ws_execution_wake_enabled = True
    RFDir5TradingBot._private_ws_priority_wake_interval_seconds = _PRIORITY_WAKE_INTERVAL_SECONDS
    RFDir5TradingBot._private_ws_otp_bootstrap_concurrency = _OTP_BOOTSTRAP_CONCURRENCY
    RFDir5TradingBot._private_ws_otp_bootstrap_timeout_seconds = (
        _OTP_BOOTSTRAP_TIMEOUT_SECONDS
    )
    RFDir5TradingBot._private_ws_otp_retry_max_seconds = _OTP_RETRY_MAX_SECONDS
    RFDir5TradingBot._private_ws_otp_http_keepalive = True
    RFDir5TradingBot._private_ws_network_family = "auto"
    RFDir5TradingBot._private_ws_ipv4_transport = False
    RFDir5TradingBot._private_ws_open_timeout_seconds = _PRIVATE_WS_OPEN_TIMEOUT_SECONDS
    RFDir5TradingBot._private_ws_soft_reconnect_notice_interval_seconds = (
        _SOFT_RECONNECT_NOTICE_INTERVAL_SECONDS
    )
    RFDir5TradingBot._private_ws_exact_error_ui = True
    RFDir5TradingBot._stake_policy_transport_isolation = True
    _INSTALLED = True
