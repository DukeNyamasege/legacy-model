from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Callable

import websockets

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.guaranteed_signal_delivery as immediate
import app.private_websocket_rate_limit as private_ws
import app.public_websocket_resilience as public_resilience
import app.scalable_group_execution as grouped
import app.tick_persistence_buffer as tick_buffer
from app.repositories.rf_dir5_repository import RFDir5Repository
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import (
    ClientSession,
    PublicMarketDataClient,
    TradingBot,
    mask_account_id,
    sanitize_account_ids,
)


LOGGER = logging.getLogger(__name__)
_INSTALLED = False
HOT_PATH_VERSION = "private-ws-hot-path-v1"


def _positive_float(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value)


def _positive_int(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), value)


PUBLIC_TICK_QUEUE_SIZE = _positive_int("DERIV_PUBLIC_TICK_QUEUE_SIZE", 4096, 512)
PUBLIC_PROTOCOL_QUEUE_SIZE = _positive_int("DERIV_PUBLIC_PROTOCOL_QUEUE_SIZE", 2048, 256)
APP_HEARTBEAT_MIN_SECONDS = _positive_float("DERIV_APP_HEARTBEAT_MIN_SECONDS", 35.0, 25.0)
APP_HEARTBEAT_MAX_SECONDS = max(
    APP_HEARTBEAT_MIN_SECONDS + 1.0,
    _positive_float("DERIV_APP_HEARTBEAT_MAX_SECONDS", 55.0, 30.0),
)
APP_HEARTBEAT_MISSES = _positive_int("DERIV_APP_HEARTBEAT_MISSES", 3, 2)
PROPOSAL_PRIMARY_TIMEOUT_SECONDS = _positive_float(
    "DERIV_PROPOSAL_PRIMARY_TIMEOUT_SECONDS", 4.0, 1.0
)
PROPOSAL_FALLBACK_TIMEOUT_SECONDS = _positive_float(
    "DERIV_PROPOSAL_FALLBACK_TIMEOUT_SECONDS", 6.0, 2.0
)
PROPOSAL_FALLBACK_SESSION_COUNT = _positive_int(
    "DERIV_PROPOSAL_FALLBACK_SESSION_COUNT", 2, 1
)
PRIVATE_WS_BUY_CONCURRENCY = _positive_int("PRIVATE_WS_BUY_CONCURRENCY", 12, 1)
PRIVATE_WS_BUY_START_INTERVAL_SECONDS = _positive_float(
    "PRIVATE_WS_BUY_START_INTERVAL_SECONDS", 0.015, 0.0
)
PRIVATE_RECONNECT_JITTER_SECONDS = _positive_float(
    "PRIVATE_WS_RECONNECT_JITTER_SECONDS", 20.0, 2.0
)
GROUP_CACHE_TTL_SECONDS = _positive_float("AIDR_GROUP_CACHE_TTL_SECONDS", 1.0, 0.25)
GROUP_CACHE_MAX_STALE_SECONDS = max(
    GROUP_CACHE_TTL_SECONDS,
    _positive_float("AIDR_GROUP_CACHE_MAX_STALE_SECONDS", 5.0, 1.0),
)
EVENT_LOOP_LAG_WARNING_SECONDS = _positive_float(
    "EVENT_LOOP_LAG_WARNING_SECONDS", 1.0, 0.25
)
DB_HOT_PATH_WORKERS = _positive_int("DB_HOT_PATH_WORKERS", 6, 2)

_HOT_EXECUTOR = ThreadPoolExecutor(
    max_workers=DB_HOT_PATH_WORKERS,
    thread_name_prefix="deriv-hot-path",
)


@dataclass
class _CoalescedState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    pending: dict[str, Any] | None = None
    completed: list[Any] = field(default_factory=list)
    running: bool = False


def _coalesced_states(owner: Any) -> dict[tuple[str, str], _CoalescedState]:
    states = getattr(owner, "_websocket_hot_coalesced_states", None)
    if not isinstance(states, dict):
        states = {}
        setattr(owner, "_websocket_hot_coalesced_states", states)
    return states


def _drain_coalesced(
    owner: Any,
    state: _CoalescedState,
    original: Callable[..., Any],
    method_name: str,
) -> None:
    while True:
        with state.lock:
            kwargs = state.pending
            state.pending = None
            if kwargs is None:
                state.running = False
                return
        try:
            value = original(owner, **kwargs)
        except Exception as exc:  # pragma: no cover - production defensive path
            LOGGER.error(
                "DB_HOT_PATH_CALL_FAILED method=%s error_type=%s error=%s",
                method_name,
                type(exc).__name__,
                sanitize_account_ids(str(exc)),
            )
            value = []
        if value:
            with state.lock:
                if isinstance(value, list):
                    state.completed.extend(value)
                else:
                    state.completed.append(value)


def _coalesced_repository_method(
    original: Callable[..., Any],
    method_name: str,
) -> Callable[..., list[Any]]:
    """Run repetitive settlement SQL off-loop and keep only the latest tick call."""

    def wrapper(owner: Any, **kwargs: Any) -> list[Any]:
        key = (method_name, str(kwargs.get("symbol") or "global"))
        state = _coalesced_states(owner).setdefault(key, _CoalescedState())
        with state.lock:
            completed = list(state.completed)
            state.completed.clear()
            state.pending = dict(kwargs)
            if not state.running:
                state.running = True
                _HOT_EXECUTOR.submit(
                    _drain_coalesced,
                    owner,
                    state,
                    original,
                    method_name,
                )
        return completed

    wrapper.__name__ = getattr(original, "__name__", method_name)
    wrapper.__doc__ = getattr(original, "__doc__", None)
    return wrapper


def _install_background_tick_flush() -> None:
    original_flush = tick_buffer.flush_tick_buffer

    def nonblocking_flush(
        repository: Test2Repository,
        *,
        force: bool = False,
    ) -> int:
        if force:
            pending: Future[Any] | None = getattr(
                repository, "_websocket_hot_tick_flush_future", None
            )
            if isinstance(pending, Future):
                with suppress(Exception):
                    pending.result(timeout=30)
            return int(original_flush(repository, force=True) or 0)

        future: Future[Any] | None = getattr(
            repository, "_websocket_hot_tick_flush_future", None
        )
        if isinstance(future, Future) and not future.done():
            return 0
        if isinstance(future, Future) and future.done():
            with suppress(Exception):
                future.result()

        now = time.monotonic()
        last_submit = float(
            getattr(repository, "_websocket_hot_tick_flush_submitted_at", 0.0)
            or 0.0
        )
        if now - last_submit < 0.25:
            return 0
        repository._websocket_hot_tick_flush_submitted_at = now
        repository._websocket_hot_tick_flush_future = _HOT_EXECUTOR.submit(
            original_flush,
            repository,
            force=False,
        )
        return 0

    tick_buffer.flush_tick_buffer = nonblocking_flush


def _install_coalesced_settlements() -> None:
    Test2Repository.settle_due_system_model_trades = _coalesced_repository_method(
        Test2Repository.settle_due_system_model_trades,
        "settle_due_system_model_trades",
    )
    RFDir5Repository.settle_due_virtual_trades = _coalesced_repository_method(
        RFDir5Repository.settle_due_virtual_trades,
        "settle_due_virtual_trades",
    )
    RFDir5Repository.settle_due_shadows = _coalesced_repository_method(
        RFDir5Repository.settle_due_shadows,
        "settle_due_shadows",
    )


async def _public_tick_reader(
    client: PublicMarketDataClient,
    websocket: Any,
    queue: asyncio.Queue[str],
    original_on_message: Callable[[PublicMarketDataClient, str], Any],
) -> None:
    async for message in websocket:
        try:
            payload = json.loads(message)
        except Exception:
            continue
        req_id = payload.get("req_id")
        if req_id and req_id in client.pending_requests:
            future = client.pending_requests[req_id]
            if not future.done():
                future.set_result(payload)
            continue
        if payload.get("msg_type") == "tick":
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull as exc:
                client.bot.logger.error(
                    "PUBLIC_TICK_QUEUE_OVERFLOW queue_size=%s action=reconnect_and_resync "
                    "financial_execution_paused_until_history_ready=true",
                    queue.maxsize,
                )
                with suppress(Exception):
                    await websocket.close(code=1013, reason="tick queue overflow")
                raise ConnectionError("Public tick queue overflow") from exc
            continue
        await original_on_message(client, message)


async def _public_tick_consumer(
    client: PublicMarketDataClient,
    queue: asyncio.Queue[str],
    original_on_message: Callable[[PublicMarketDataClient, str], Any],
) -> None:
    while client.bot.is_running and client.is_connected:
        message = await queue.get()
        try:
            await original_on_message(client, message)
        finally:
            queue.task_done()


async def _public_heartbeat(client: PublicMarketDataClient, websocket: Any) -> None:
    missed = 0
    while client.bot.is_running and client.is_connected:
        await asyncio.sleep(
            random.uniform(APP_HEARTBEAT_MIN_SECONDS, APP_HEARTBEAT_MAX_SECONDS)
        )
        if not client.is_connected or client.ws is not websocket:
            return
        response = await client.send_request({"ping": 1})
        if "error" not in response:
            missed = 0
            continue
        missed += 1
        client.bot.logger.warning(
            "PUBLIC_APP_HEARTBEAT_MISSED misses=%s threshold=%s error=%s",
            missed,
            APP_HEARTBEAT_MISSES,
            sanitize_account_ids(
                str((response.get("error") or {}).get("message") or "unknown")
            ),
        )
        if missed >= APP_HEARTBEAT_MISSES:
            with suppress(Exception):
                await websocket.close(code=1012, reason="application heartbeat failed")
            return


def _install_public_reader_isolation() -> None:
    original_on_message = PublicMarketDataClient._on_message

    async def isolated_connect_and_run(self: PublicMarketDataClient) -> None:
        if public_resilience._preflight_stream_disabled():
            self.bot.logger.warning(
                "PUBLIC_STREAM_PREFLIGHT_DISABLED deployment_id=%s "
                "duplicate_external_connection=false provider_checked_by_smoke=true",
                os.getenv("DEPLOYMENT_ID", "preflight-worker"),
            )
            while self.bot.is_running:
                await asyncio.sleep(1.0)
            return

        attempt = 0
        url = self.bot.public_ws_url
        while self.bot.is_running:
            retry_delay = public_resilience.PUBLIC_WS_BACKOFF_BASE_SECONDS
            try:
                self.bot.logger.info(
                    "Connecting to public market WebSocket: %s attempt=%s",
                    url,
                    attempt + 1,
                )
                async with websockets.connect(
                    url,
                    open_timeout=public_resilience.PUBLIC_WS_OPEN_TIMEOUT_SECONDS,
                    close_timeout=5,
                    ping_interval=None,
                    ping_timeout=None,
                    max_queue=PUBLIC_PROTOCOL_QUEUE_SIZE,
                ) as websocket:
                    self.ws = websocket
                    self.is_connected = True
                    attempt = 0
                    self.bot._on_public_connection_established()
                    self.bot.logger.info(
                        "PUBLIC_CONNECTION_ESTABLISHED reader=isolated tick_queue=%s "
                        "protocol_ping=false app_heartbeat=true",
                        PUBLIC_TICK_QUEUE_SIZE,
                    )

                    await self._fetch_precision()
                    await self._fetch_tick_history()
                    await self._subscribe_ticks()
                    self.bot._on_market_subscriptions_ready()
                    self.bot._mark_tick_received()

                    queue: asyncio.Queue[str] = asyncio.Queue(
                        maxsize=PUBLIC_TICK_QUEUE_SIZE
                    )
                    self._hot_tick_queue = queue
                    tasks = {
                        asyncio.create_task(
                            _public_tick_reader(
                                self,
                                websocket,
                                queue,
                                original_on_message,
                            ),
                            name="public_websocket_reader",
                        ),
                        asyncio.create_task(
                            _public_tick_consumer(self, queue, original_on_message),
                            name="public_tick_consumer",
                        ),
                        asyncio.create_task(
                            _public_heartbeat(self, websocket),
                            name="public_app_heartbeat",
                        ),
                    }
                    done, pending = await asyncio.wait(
                        tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    failure: BaseException | None = None
                    for task in done:
                        if task.cancelled():
                            continue
                        try:
                            result = task.exception()
                        except asyncio.CancelledError:
                            continue
                        if result is not None:
                            failure = result
                            break
                    for task in pending:
                        task.cancel()
                    for task in tasks:
                        with suppress(asyncio.CancelledError, Exception):
                            await task
                    if failure is not None:
                        raise failure
                    if self.bot.is_running:
                        raise ConnectionError("Public WebSocket task ended unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                attempt += 1
                self._handle_disconnect(error)
                limited = public_resilience._rate_limited(error)
                retry_delay = public_resilience._retry_delay(
                    attempt,
                    rate_limited=limited,
                )
                self.bot.logger.warning(
                    "PUBLIC_STREAM_BACKOFF error_type=%s error=%r attempt=%s "
                    "rate_limited=%s retry_seconds=%.1f max_retry_seconds=%.1f "
                    "global_execution_continues=true",
                    type(error).__name__,
                    error,
                    attempt,
                    str(limited).lower(),
                    retry_delay,
                    public_resilience.PUBLIC_WS_BACKOFF_MAX_SECONDS,
                )
            if self.bot.is_running:
                await public_resilience._sleep_while_running(self, retry_delay)

    PublicMarketDataClient.connect_and_run = isolated_connect_and_run


def _connected_private_sessions(bot: Any) -> list[ClientSession]:
    sessions = [
        session
        for session in list(getattr(bot, "sessions", {}).values())
        if bool(getattr(session, "is_connected", False))
        and getattr(session, "ws", None) is not None
    ]
    sessions.sort(key=lambda session: (str(session.account_id), str(session.token_tag)))
    if not sessions:
        return []
    offset = int(getattr(bot, "_proposal_fallback_offset", 0) or 0) % len(sessions)
    bot._proposal_fallback_offset = offset + PROPOSAL_FALLBACK_SESSION_COUNT
    rotated = sessions[offset:] + sessions[:offset]
    return rotated[:PROPOSAL_FALLBACK_SESSION_COUNT]


def _install_proposal_route_fallback() -> None:
    original_send_request = PublicMarketDataClient.send_request

    async def proposal_resilient_send_request(
        self: PublicMarketDataClient,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(request)
        if not payload.get("proposal"):
            return await original_send_request(self, payload)

        try:
            primary = await asyncio.wait_for(
                original_send_request(self, dict(payload)),
                timeout=PROPOSAL_PRIMARY_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            primary = {
                "error": {
                    "code": "PUBLIC_PROPOSAL_TIMEOUT",
                    "message": "Public proposal route timed out",
                }
            }
        if "error" not in primary:
            return primary

        sessions = _connected_private_sessions(self.bot)
        if not sessions:
            return primary
        self.bot.logger.warning(
            "PROPOSAL_ROUTE_FALLBACK symbol=%s contract_type=%s barrier=%s "
            "public_error=%s private_routes=%s financial_requests=0",
            payload.get("underlying_symbol") or payload.get("symbol") or "unknown",
            payload.get("contract_type") or "unknown",
            payload.get("barrier") or "-",
            str((primary.get("error") or {}).get("code") or "unknown"),
            len(sessions),
        )
        tasks = [
            asyncio.create_task(
                session.send_request(dict(payload)),
                name=f"proposal_fallback_{session.token_tag}",
            )
            for session in sessions
        ]
        try:
            for future in asyncio.as_completed(
                tasks,
                timeout=PROPOSAL_FALLBACK_TIMEOUT_SECONDS,
            ):
                try:
                    response = await future
                except Exception:
                    continue
                if "error" not in response:
                    self.bot.logger.info(
                        "PROPOSAL_ROUTE_RECOVERED symbol=%s route=private_websocket "
                        "buy_sent=false",
                        payload.get("underlying_symbol")
                        or payload.get("symbol")
                        or "unknown",
                    )
                    return response
        except asyncio.TimeoutError:
            pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await task
        return {
            "error": {
                "code": "PROPOSAL_ROUTES_UNAVAILABLE",
                "message": (
                    "Public and private WebSocket proposal routes were unavailable. "
                    "No financial buy request was sent."
                ),
            }
        }

    PublicMarketDataClient.send_request = proposal_resilient_send_request


def _install_private_application_heartbeats() -> None:
    async def open_without_protocol_ping(
        gate: Any,
        url: str,
    ) -> Any:
        async with gate._handshake_slots:
            return await websockets.connect(
                url,
                open_timeout=20,
                close_timeout=5,
                ping_interval=None,
                ping_timeout=None,
                max_queue=256,
            )

    async def private_app_heartbeat(self: ClientSession) -> None:
        missed = 0
        while self.ws is not None and self.is_connected:
            await asyncio.sleep(
                random.uniform(APP_HEARTBEAT_MIN_SECONDS, APP_HEARTBEAT_MAX_SECONDS)
            )
            websocket = self.ws
            if websocket is None or not self.is_connected:
                return
            response = await self.send_request({"ping": 1})
            if "error" not in response:
                missed = 0
                continue
            missed += 1
            self.bot.logger.warning(
                "PRIVATE_APP_HEARTBEAT_MISSED account=%s misses=%s threshold=%s "
                "error=%s",
                mask_account_id(self.account_id),
                missed,
                APP_HEARTBEAT_MISSES,
                sanitize_account_ids(
                    str((response.get("error") or {}).get("message") or "unknown")
                ),
                extra={
                    "token_tag": self.token_tag,
                    "masked_account_id": mask_account_id(self.account_id),
                },
            )
            if missed >= APP_HEARTBEAT_MISSES:
                with suppress(Exception):
                    await websocket.close(
                        code=1012,
                        reason="application heartbeat failed",
                    )
                return

    def reconnect_jitter(config: Any) -> float:
        maximum = max(
            float(getattr(config, "reconnect_jitter_seconds", 0.0) or 0.0),
            PRIVATE_RECONNECT_JITTER_SECONDS,
        )
        return random.uniform(0.0, maximum)

    private_ws._PrivateConnectionGate.open_websocket = open_without_protocol_ping
    private_ws._jitter = reconnect_jitter
    ClientSession._ping_loop = private_app_heartbeat


def _install_contract_capability_cache() -> None:
    required = {"PUT", "DIGITOVER", "DIGITUNDER"}
    original_established = RFDir5TradingBot._on_public_connection_established
    original_lost = RFDir5TradingBot._on_public_connection_lost

    def preserve_cache_on_connect(self: RFDir5TradingBot) -> None:
        cached = {
            symbol: set(types)
            for symbol, types in dict(self.rf_supported_contracts).items()
        }
        original_established(self)
        for symbol, types in cached.items():
            self.rf_supported_contracts[symbol] = set(types)

    def preserve_cache_on_loss(self: RFDir5TradingBot, error: Exception) -> None:
        cached = {
            symbol: set(types)
            for symbol, types in dict(self.rf_supported_contracts).items()
        }
        original_lost(self, error)
        for symbol, types in cached.items():
            self.rf_supported_contracts[symbol] = set(types)

    async def proposal_authoritative_contract_cache(
        self: RFDir5TradingBot,
    ) -> None:
        for symbol in self.symbols:
            self.rf_supported_contracts.setdefault(symbol, set()).update(required)
        self.logger.info(
            "RF_CONTRACT_CAPABILITY_CACHE markets=%s metadata_requests=0 "
            "provider_proposal_and_buy_authoritative=true",
            len(self.symbols),
        )

    RFDir5TradingBot._on_public_connection_established = preserve_cache_on_connect
    RFDir5TradingBot._on_public_connection_lost = preserve_cache_on_loss
    RFDir5TradingBot._validate_rf_contracts = proposal_authoritative_contract_cache


def _schedule_group_cache_refresh(bot: RFDir5TradingBot) -> None:
    task = getattr(bot, "_aidr_group_cache_refresh_task", None)
    if isinstance(task, asyncio.Task) and not task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def refresh() -> None:
        try:
            groups = await asyncio.to_thread(_ORIGINAL_ACCOUNT_RECOVERY_GROUPS, bot)
            bot._aidr_group_cache = tuple(set(group) for group in groups)
            bot._aidr_group_cache_updated_at = time.monotonic()
        except Exception as exc:
            bot.logger.warning(
                "AIDR_ACCOUNT_GROUP_CACHE_REFRESH_FAILED error_type=%s error=%s",
                type(exc).__name__,
                sanitize_account_ids(str(exc)),
            )

    bot._aidr_group_cache_refresh_task = loop.create_task(
        refresh(),
        name="aidr_account_group_cache_refresh",
    )


def _cached_account_recovery_groups(
    bot: RFDir5TradingBot,
) -> tuple[set[int], set[int], set[int], set[int]]:
    now = time.monotonic()
    cached = getattr(bot, "_aidr_group_cache", None)
    updated = float(getattr(bot, "_aidr_group_cache_updated_at", 0.0) or 0.0)
    age = now - updated if updated else float("inf")
    if cached is not None and age <= GROUP_CACHE_TTL_SECONDS:
        return tuple(set(group) for group in cached)  # type: ignore[return-value]

    _schedule_group_cache_refresh(bot)
    if cached is not None and age <= GROUP_CACHE_MAX_STALE_SECONDS:
        return tuple(set(group) for group in cached)  # type: ignore[return-value]

    last_log = float(getattr(bot, "_aidr_group_cache_wait_log_at", 0.0) or 0.0)
    if now - last_log >= 5.0:
        bot._aidr_group_cache_wait_log_at = now
        bot.logger.warning(
            "AIDR_ACCOUNT_GROUP_CACHE_WARMING financial_execution_deferred=true "
            "event_loop_blocking=false"
        )
    return set(), set(), set(), set()


def _install_account_group_cache() -> None:
    aidr._account_recovery_groups = _cached_account_recovery_groups
    continuation._account_recovery_groups = _cached_account_recovery_groups

    original_private_ready = RFDir5TradingBot._on_private_session_ready

    def ready_and_refresh(self: RFDir5TradingBot, session: Any) -> None:
        original_private_ready(self, session)
        _schedule_group_cache_refresh(self)

    RFDir5TradingBot._on_private_session_ready = ready_and_refresh


def _install_paced_private_buys() -> None:
    original_buy_one = grouped._buy_one_serialized

    async def wait_for_account_start(bot: RFDir5TradingBot) -> None:
        lock = getattr(bot, "_private_ws_buy_start_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            bot._private_ws_buy_start_lock = lock
            bot._private_ws_next_buy_start = 0.0
        async with lock:
            now = time.monotonic()
            target = float(getattr(bot, "_private_ws_next_buy_start", 0.0) or 0.0)
            if target > now:
                await asyncio.sleep(target - now)
                now = time.monotonic()
            bot._private_ws_next_buy_start = (
                now + PRIVATE_WS_BUY_START_INTERVAL_SECONDS
            )

    async def paced_buy_one(
        bot: RFDir5TradingBot,
        **kwargs: Any,
    ) -> dict[str, Any]:
        limiter = getattr(bot, "_private_ws_buy_limiter", None)
        if not isinstance(limiter, asyncio.Semaphore):
            limiter = asyncio.Semaphore(PRIVATE_WS_BUY_CONCURRENCY)
            bot._private_ws_buy_limiter = limiter
        await wait_for_account_start(bot)
        async with limiter:
            return await original_buy_one(bot, **kwargs)

    grouped._buy_one_serialized = paced_buy_one


def _install_nonblocking_role_audits() -> None:
    async def role_proposal_with_retry(
        bot: RFDir5TradingBot,
        *,
        role: str,
        symbol: str,
    ) -> tuple[Any, Any] | None:
        for attempt in (1, 2):
            signal = immediate._role_signal(bot, symbol=symbol, role=role)
            if signal is None:
                return None
            await asyncio.to_thread(bot.repository.record_candidate, signal)
            result = await immediate._provider_proposal(bot, signal)
            if result is not None:
                if attempt > 1:
                    bot.logger.info(
                        "AIDR_ROLE_PROPOSAL_RECOVERED role=%s symbol=%s attempt=%s",
                        role,
                        symbol,
                        attempt,
                    )
                return result
            if attempt == 1:
                await asyncio.sleep(0.15)
        return None

    async def provider_proposal(
        bot: RFDir5TradingBot,
        signal: Any,
    ) -> tuple[Any, Any] | None:
        try:
            returned_signal, economics = await grouped.hybrid._digit_proposal(
                bot, signal
            )
        except Exception as exc:
            await asyncio.to_thread(
                bot.repository.mark_signal,
                signal.signal_id,
                status="SKIP_PROVIDER_PROPOSAL_EXCEPTION",
            )
            bot.logger.warning(
                "AIDR_SHARED_TRIGGER_PROPOSAL_FAILED signal_id=%s barrier=%s "
                "error=%s",
                signal.signal_id,
                signal.barrier,
                type(exc).__name__,
            )
            return None
        if economics is None:
            await asyncio.to_thread(
                bot.repository.mark_signal,
                signal.signal_id,
                status="SKIP_INVALID_PROVIDER_PROPOSAL",
            )
            return None
        edge = float(returned_signal.weighted_probability) - float(
            economics.break_even_probability
        )
        grouped.standardized._mark_proposal_fields(
            returned_signal,
            economics,
            edge,
        )
        await asyncio.to_thread(
            bot.repository.record_proposal,
            returned_signal,
            economics,
        )
        return returned_signal, economics

    async def dispatch_role(
        bot: RFDir5TradingBot,
        *,
        parent_cycle_id: str,
        role: str,
        signal: Any,
        economics: Any,
        scope: set[int],
    ) -> tuple[str, str]:
        barrier, recovery_enabled = grouped.standardized._role_spec(role)
        signal._standardized_cycle_id = f"{parent_cycle_id}:{role}"
        if not immediate.refresh_signal_for_delivery(bot, signal):
            return role, "immediate_deadline_missed"
        await asyncio.to_thread(
            continuation._ensure_directional_signal,
            bot,
            signal,
            role=role,
        )
        bot.logger.warning(
            "AIDR_ROLE_DISPATCH_STARTED parent_cycle_id=%s role=%s symbol=%s "
            "barrier=%s accounts=%s transport=PRIVATE_WEBSOCKET_ONLY",
            parent_cycle_id,
            role,
            signal.symbol,
            barrier,
            len(scope),
        )
        try:
            await grouped.aidr._buy_for_scope(
                bot,
                signal,
                economics,
                scope,
                recovery_enabled=recovery_enabled,
            )
        except Exception as exc:
            bot.logger.exception(
                "AIDR_ROLE_DISPATCH_FAILED parent_cycle_id=%s role=%s "
                "symbol=%s barrier=%s accounts=%s error=%s "
                "global_execution_continues=true",
                parent_cycle_id,
                role,
                signal.symbol,
                barrier,
                len(scope),
                type(exc).__name__,
            )
            return role, f"exception_{type(exc).__name__}"
        return role, "submitted"

    grouped._role_proposal_with_retry = role_proposal_with_retry
    immediate._provider_proposal = provider_proposal
    grouped._dispatch_aidr_role = dispatch_role


async def _event_loop_watchdog(bot: RFDir5TradingBot) -> None:
    interval = 1.0
    expected = asyncio.get_running_loop().time() + interval
    while bot.is_running:
        await asyncio.sleep(interval)
        now = asyncio.get_running_loop().time()
        lag = max(0.0, now - expected)
        expected = now + interval
        if lag >= EVENT_LOOP_LAG_WARNING_SECONDS:
            queue = getattr(getattr(bot, "public_client", None), "_hot_tick_queue", None)
            bot.logger.warning(
                "EVENT_LOOP_LAG lag_ms=%.1f threshold_ms=%.1f tick_queue_depth=%s "
                "global_execution_continues=true",
                lag * 1000.0,
                EVENT_LOOP_LAG_WARNING_SECONDS * 1000.0,
                queue.qsize() if isinstance(queue, asyncio.Queue) else "unknown",
            )


def _install_event_loop_watchdog() -> None:
    original_run = RFDir5TradingBot.run

    async def run_with_watchdog(self: RFDir5TradingBot) -> None:
        watchdog = asyncio.create_task(
            _event_loop_watchdog(self),
            name="event_loop_lag_watchdog",
        )
        try:
            await original_run(self)
        finally:
            watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog

    RFDir5TradingBot.run = run_with_watchdog


_ORIGINAL_ACCOUNT_RECOVERY_GROUPS = aidr._account_recovery_groups


def install_websocket_hot_path_hardening() -> None:
    """Keep proposal and private-buy traffic responsive under multi-account load."""

    global _INSTALLED
    if _INSTALLED:
        return

    _install_background_tick_flush()
    _install_coalesced_settlements()
    _install_public_reader_isolation()
    _install_proposal_route_fallback()
    _install_private_application_heartbeats()
    _install_contract_capability_cache()
    _install_account_group_cache()
    _install_paced_private_buys()
    _install_nonblocking_role_audits()
    _install_event_loop_watchdog()

    RFDir5TradingBot._websocket_hot_path_hardening_installed = True
    _INSTALLED = True
    LOGGER.warning(
        "WEBSOCKET_HOT_PATH_HARDENING_INSTALLED version=%s "
        "public_reader_isolated=true protocol_ping=false app_heartbeat=true "
        "proposal_private_fallbacks=%s buy_concurrency=%s buy_interval_ms=%.1f "
        "settlement_sql_off_loop=true tick_flush_off_loop=true "
        "contracts_for_runtime_requests=0 private_websocket_only=true "
        "bulk_purchase=false copy_trading=false",
        HOT_PATH_VERSION,
        PROPOSAL_FALLBACK_SESSION_COUNT,
        PRIVATE_WS_BUY_CONCURRENCY,
        PRIVATE_WS_BUY_START_INTERVAL_SECONDS * 1000.0,
    )
