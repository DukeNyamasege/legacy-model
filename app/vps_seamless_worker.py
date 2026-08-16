from __future__ import annotations

import asyncio
import contextvars
import os
import time
from typing import Any

import aiohttp

from app import custom_strategy_direct_runtime as direct_runtime
from app import custom_strategy_result_router as result_router
from app.custom_strategy_v1 import custom_strategy_fingerprint, describe_condition
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_ORIGINAL_SCHEDULE: Any = None
_ORIGINAL_EVALUATE: Any = None
_ORIGINAL_EXECUTE: Any = None
_EVENT_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "vps_runtime_event_context",
    default=None,
)
_PENDING_LATEST: dict[int, dict[str, Any]] = {}
_PENDING_PRIORITY: list[dict[str, Any]] = []
_FLUSH_TASK: asyncio.Task[Any] | None = None


def _event_url() -> str:
    return os.getenv(
        "INTERNAL_VPS_RUNTIME_EVENTS_URL",
        "http://api:8080/control/internal/vps-runtime-events",
    ).strip()


def _tick_sequence(tick: dict[str, Any], bot: RFDir5TradingBot, symbol: str) -> int:
    for raw in (tick.get("tick_sequence"), tick.get("sequence")):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    market = getattr(bot, "market_states", {}).get(symbol)
    try:
        return max(0, int(getattr(market, "tick_sequence", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _event(
    managed_id: int,
    event_type: str,
    message: str,
    *,
    symbol: str = "",
    tick_sequence: int = 0,
    digit: int | None = None,
) -> dict[str, Any]:
    return {
        "managed_account_id": int(managed_id),
        "event": str(event_type),
        "message": str(message)[:220],
        "symbol": str(symbol or "")[:32],
        "tick_sequence": max(0, int(tick_sequence or 0)),
        "digit": digit if digit is None else int(digit),
        "emitted_at": time.time(),
    }


def _scan_description(config: dict[str, Any]) -> tuple[str, str]:
    """Return exact saved market scope and condition copy for the tiny UI strip."""

    conditions: list[str] = []
    for condition in list(config.get("conditions") or []):
        try:
            conditions.append(describe_condition(condition))
        except Exception:
            continue
    criteria = " AND ".join(conditions) or "configured Custom Strategy conditions"
    mode = str(config.get("market_mode") or "all").strip().lower()
    if mode == "all":
        market_scope = "all markets"
    else:
        markets = [str(value) for value in list(config.get("markets") or []) if str(value)]
        market_scope = ", ".join(markets) or "configured markets"
    return market_scope, criteria


async def _flush_events(bot: RFDir5TradingBot, delay: float) -> None:
    global _FLUSH_TASK
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        priority = list(_PENDING_PRIORITY[:200])
        del _PENDING_PRIORITY[: len(priority)]
        latest = list(_PENDING_LATEST.values())
        _PENDING_LATEST.clear()
        events = priority + latest
        if not events:
            return
        url = _event_url()
        api_key = os.getenv("CONTROL_API_KEY", "").strip()
        if not url or not api_key:
            return
        timeout = aiohttp.ClientTimeout(total=0.65, connect=0.25)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(
                url,
                headers={"X-API-Key": api_key},
                json={"events": events},
            ) as response:
                if response.status >= 400:
                    bot.logger.debug(
                        "VPS_RUNTIME_EVENT_DEFERRED status=%s execution_unaffected=true",
                        response.status,
                    )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        bot.logger.debug(
            "VPS_RUNTIME_EVENT_DEFERRED error_type=%s execution_unaffected=true",
            type(exc).__name__,
        )
    finally:
        _FLUSH_TASK = None
        if _PENDING_PRIORITY or _PENDING_LATEST:
            _schedule_flush(bot, delay=0.06)


def _schedule_flush(bot: RFDir5TradingBot, *, delay: float) -> None:
    global _FLUSH_TASK
    if _FLUSH_TASK is not None and not _FLUSH_TASK.done():
        return
    try:
        _FLUSH_TASK = asyncio.create_task(
            _flush_events(bot, delay),
            name="vps_runtime_event_flush",
        )
    except RuntimeError:
        _FLUSH_TASK = None


def _emit(
    bot: RFDir5TradingBot,
    managed_id: int,
    event_type: str,
    message: str,
    *,
    symbol: str = "",
    tick_sequence: int = 0,
    digit: int | None = None,
) -> None:
    payload = _event(
        managed_id,
        event_type,
        message,
        symbol=symbol,
        tick_sequence=tick_sequence,
        digit=digit,
    )
    if event_type == "condition_not_met":
        _PENDING_LATEST[int(managed_id)] = payload
        _schedule_flush(bot, delay=0.08)
        return
    _PENDING_PRIORITY.append(payload)
    if len(_PENDING_PRIORITY) > 400:
        del _PENDING_PRIORITY[:-200]
    _schedule_flush(bot, delay=0.015)


def _active_fingerprint_map(bot: RFDir5TradingBot) -> dict[str, list[int]]:
    mapping: dict[str, list[int]] = {}
    runtime = getattr(bot, "_custom_direct_accounts", {}) or {}
    for managed_id, item in list(runtime.items()):
        try:
            config = result_router._active_config(bot, item)
        except Exception:
            config = dict(getattr(item, "config", {}) or {})
        try:
            fingerprint = custom_strategy_fingerprint(config)
        except Exception:
            continue
        mapping.setdefault(fingerprint, []).append(int(managed_id))
    return mapping


def install_vps_seamless_worker() -> None:
    """Publish account-scoped tick decisions without touching the financial path.

    The monitor is deliberately ephemeral. Strategy evaluation remains in-memory,
    no tick is persisted just for UI purposes, and event delivery is a best-effort
    background task. The existing manual-stop guard remains the final BUY barrier.
    """

    global _INSTALLED, _ORIGINAL_SCHEDULE, _ORIGINAL_EVALUATE, _ORIGINAL_EXECUTE
    if _INSTALLED:
        return

    _ORIGINAL_SCHEDULE = direct_runtime._schedule_account_matches
    _ORIGINAL_EVALUATE = direct_runtime.evaluate_custom_strategy
    _ORIGINAL_EXECUTE = direct_runtime._execute_for_account

    def schedule_with_event_context(
        bot: RFDir5TradingBot,
        *,
        symbol: str,
        tick: dict[str, Any],
    ) -> None:
        original = _ORIGINAL_SCHEDULE
        if original is None:
            return
        context = {
            "bot": bot,
            "symbol": str(symbol),
            "tick": dict(tick or {}),
            "fingerprints": _active_fingerprint_map(bot),
        }
        token = _EVENT_CONTEXT.set(context)
        try:
            original(bot, symbol=symbol, tick=tick)
        finally:
            _EVENT_CONTEXT.reset(token)

    def evaluate_with_live_decision(
        config: dict[str, Any],
        *,
        digits: list[int],
        quotes: list[Any],
    ) -> bool:
        original = _ORIGINAL_EVALUATE
        if original is None:
            return False
        qualifies = bool(original(config, digits=digits, quotes=quotes))
        context = _EVENT_CONTEXT.get()
        if not context:
            return qualifies
        bot = context.get("bot")
        if not isinstance(bot, RFDir5TradingBot):
            return qualifies
        try:
            fingerprint = custom_strategy_fingerprint(config)
        except Exception:
            return qualifies
        managed_ids = list((context.get("fingerprints") or {}).get(fingerprint, []))
        if not managed_ids:
            return qualifies
        symbol = str(context.get("symbol") or "")
        tick = dict(context.get("tick") or {})
        sequence = _tick_sequence(tick, bot, symbol)
        digit = digits[-1] if digits else None
        market_scope, criteria = _scan_description(config)
        event_type = "condition_met" if qualifies else "condition_not_met"
        message = (
            f"Matched on {symbol}: {criteria}."
            if qualifies
            else f"Scanning {market_scope} for {criteria}."
        )
        for managed_id in managed_ids:
            _emit(
                bot,
                int(managed_id),
                event_type,
                message,
                symbol=symbol,
                tick_sequence=sequence,
                digit=digit,
            )
        return qualifies

    async def execute_with_live_progress(
        bot: RFDir5TradingBot,
        item: Any,
        *,
        signal: Any,
    ) -> None:
        original = _ORIGINAL_EXECUTE
        if original is None:
            return
        managed_id = int(item.managed_id)
        symbol = str(getattr(signal, "symbol", "") or "")
        try:
            sequence = int(getattr(signal, "tick_sequence", 0) or 0)
        except (TypeError, ValueError):
            sequence = 0
        _emit(
            bot,
            managed_id,
            "trade_preparing",
            "Condition matched; preparing proposal and exact account BUY.",
            symbol=symbol,
            tick_sequence=sequence,
        )
        await original(bot, item, signal=signal)

        if direct_runtime._account_has_open_actual(item):
            _emit(
                bot,
                managed_id,
                "trade_open",
                "Purchase confirmed; contract is open.",
                symbol=symbol,
                tick_sequence=sequence,
            )
        elif direct_runtime._account_has_open_virtual(bot, managed_id):
            _emit(
                bot,
                managed_id,
                "virtual_observation",
                "Virtual Hook observation opened with zero monetary stake.",
                symbol=symbol,
                tick_sequence=sequence,
            )

    direct_runtime._schedule_account_matches = schedule_with_event_context
    direct_runtime.evaluate_custom_strategy = evaluate_with_live_decision
    direct_runtime._execute_for_account = execute_with_live_progress
    RFDir5TradingBot._vps_seamless_worker_installed = True
    _INSTALLED = True
