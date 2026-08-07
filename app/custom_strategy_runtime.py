from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from sqlalchemy import select

import app.final_multi_strategy_execution as final_multi
import app.multi_strategy_runtime as multi
import app.shared_system_strategy_clock as shared
from app.custom_strategy_v1 import (
    PREFERENCE_PREFIX,
    VERSION,
    build_custom_signal,
    custom_strategy_fingerprint,
    evaluate_custom_strategy,
    market_selected,
    nominal_probability,
    normalize_custom_strategy,
)
from app.models import RuntimePreference
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy_v2_runtime import _ensure_parent_signal


_INSTALLED = False
LOGGER = logging.getLogger(__name__)
CACHE_TTL_SECONDS = 0.75


def _config_key(managed_id: int) -> str:
    return f"{PREFERENCE_PREFIX}{int(managed_id)}"


def _custom_routes(bot: RFDir5TradingBot) -> list[Any]:
    return [
        route
        for route in multi._strategy_snapshot(bot)
        if str(getattr(route.selection, "family", "") or "") == "custom"
    ]


def _load_configs(bot: RFDir5TradingBot, routes: list[Any]) -> dict[int, dict[str, Any]]:
    now = time.monotonic()
    route_ids = tuple(sorted({int(route.managed_id) for route in routes}))
    cached_ids = tuple(getattr(bot, "_custom_strategy_config_ids", ()))
    cached_at = float(getattr(bot, "_custom_strategy_config_at", 0.0) or 0.0)
    if route_ids == cached_ids and now - cached_at <= CACHE_TTL_SECONDS:
        return dict(getattr(bot, "_custom_strategy_configs", {}) or {})

    keys = {_config_key(managed_id): managed_id for managed_id in route_ids}
    configs: dict[int, dict[str, Any]] = {}
    if keys:
        with bot.repository.database.session() as session:
            rows = session.scalars(
                select(RuntimePreference).where(
                    RuntimePreference.preference_key.in_(list(keys))
                )
            ).all()
        for row in rows:
            managed_id = keys.get(str(row.preference_key or ""))
            if managed_id is None:
                continue
            try:
                payload = json.loads(str(row.preference_value or ""))
                config = normalize_custom_strategy(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            configs[int(managed_id)] = config

    bot._custom_strategy_configs = configs
    bot._custom_strategy_config_ids = route_ids
    bot._custom_strategy_config_at = now
    return dict(configs)


def _quotes(market: Any) -> list[Any]:
    values: list[Any] = []
    for item in list(getattr(market, "ticks_history", []) or []):
        if not isinstance(item, dict) or item.get("quote") is None:
            continue
        values.append(item["quote"])
    return values


def _digits(market: Any) -> list[int]:
    values: list[int] = []
    for raw in list(getattr(market, "raw_tick_digits", []) or []):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 9:
            values.append(value)
    return values


def _group_matches(
    bot: RFDir5TradingBot,
    *,
    symbol: str,
    routes: list[Any],
    configs: dict[int, dict[str, Any]],
) -> list[tuple[dict[str, Any], set[int]]]:
    market = bot.market_states.get(symbol)
    if market is None:
        return []
    digits = _digits(market)
    quotes = _quotes(market)

    grouped: dict[str, tuple[dict[str, Any], set[int]]] = {}
    for route in routes:
        managed_id = int(route.managed_id)
        config = configs.get(managed_id)
        if not config or not market_selected(config, symbol):
            continue
        fingerprint = custom_strategy_fingerprint(config)
        if fingerprint not in grouped:
            grouped[fingerprint] = (config, set())
        grouped[fingerprint][1].add(managed_id)

    matched: list[tuple[dict[str, Any], set[int]]] = []
    for config, ids in grouped.values():
        try:
            qualifies = evaluate_custom_strategy(config, digits=digits, quotes=quotes)
        except (TypeError, ValueError):
            qualifies = False
        if qualifies:
            matched.append((config, ids))
    return matched


async def _execute_custom_group(
    bot: RFDir5TradingBot,
    *,
    signal: Any,
    config: dict[str, Any],
    managed_ids: set[int],
) -> None:
    ids = {int(value) for value in managed_ids}
    if not ids:
        return
    try:
        predicted = nominal_probability(config)
        economics = await multi._proposal_for(bot, signal, predicted)
        if economics is None:
            bot.repository.mark_signal(
                signal.signal_id,
                status="SKIP_CUSTOM_PROPOSAL_FAILED",
            )
            bot.logger.warning(
                "CUSTOM_STRATEGY_PROPOSAL_FAILED signal_id=%s symbol=%s trade_type=%s "
                "accounts=%s purchase_sent=false",
                signal.signal_id,
                signal.symbol,
                config["trade_type"],
                len(ids),
            )
            return

        break_even = float(economics.break_even_probability)
        signal.proposal_ask_price = float(economics.stake)
        signal.proposal_payout = float(economics.payout)
        signal.break_even_probability = break_even
        # This is user-authored rule execution. Provider economics remain visible
        # for reporting, but there is deliberately no second statistical edge gate.
        signal.validated_edge = float(predicted) - break_even
        bot.repository.record_proposal(signal, economics)
        shared._register_provider_verified_contract(bot, signal, ids)
        route = bot._multi_strategy_signal_routes[signal.signal_id]
        _ensure_parent_signal(bot, signal, route)

        bot.logger.warning(
            "CUSTOM_STRATEGY_SIGNAL_QUALIFIED signal_id=%s symbol=%s trade_type=%s "
            "contract_type=%s barrier=%s conditions=%s accounts=%s "
            "entry_gate=user_custom_pattern condition_join=AND edge_gate=false "
            "system_strategy_affected=false",
            signal.signal_id,
            signal.symbol,
            config["trade_type"],
            signal.contract_type,
            signal.barrier or "-",
            len(config["conditions"]),
            len(ids),
        )
        await shared._exact_scope_buy(
            bot,
            signal,
            economics,
            ids,
            recovery_enabled=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        try:
            bot.repository.mark_signal(
                signal.signal_id,
                status="SKIP_CUSTOM_EXECUTION_EXCEPTION",
            )
        except Exception:
            pass
        bot.logger.exception(
            "CUSTOM_STRATEGY_EXECUTION_FAILED signal_id=%s symbol=%s trade_type=%s "
            "accounts=%s error_type=%s system_strategy_affected=false",
            signal.signal_id,
            signal.symbol,
            config.get("trade_type", "unknown"),
            len(ids),
            type(exc).__name__,
        )
    finally:
        inflight = getattr(bot, "_custom_strategy_inflight_ids", set())
        for managed_id in ids:
            inflight.discard(managed_id)


def _schedule_matches(bot: RFDir5TradingBot, tick_data: dict[str, Any]) -> None:
    tick = tick_data.get("tick") or {}
    symbol = str(tick.get("symbol") or "").strip()
    if not symbol or tick.get("quote") is None:
        return
    market = bot.market_states.get(symbol)
    if market is None:
        return

    routes = _custom_routes(bot)
    if not routes:
        return
    configs = _load_configs(bot, routes)
    if not configs:
        return

    matches = _group_matches(
        bot,
        symbol=symbol,
        routes=routes,
        configs=configs,
    )
    if not matches:
        # Custom scanning is intentionally silent. A failed pattern check is not a
        # candidate, skip, killed signal or dashboard notification.
        return

    inflight: set[int] = getattr(bot, "_custom_strategy_inflight_ids", set())
    tasks: set[asyncio.Task[Any]] = getattr(bot, "_custom_strategy_tasks", set())
    bot._custom_strategy_inflight_ids = inflight
    bot._custom_strategy_tasks = tasks

    for config, raw_ids in matches:
        ids = {int(value) for value in raw_ids if int(value) not in inflight}
        if not ids:
            continue
        fingerprint = custom_strategy_fingerprint(config)
        tick_key = (symbol, int(market.tick_sequence), fingerprint)
        seen: set[tuple[str, int, str]] = getattr(bot, "_custom_strategy_seen_ticks", set())
        bot._custom_strategy_seen_ticks = seen
        if tick_key in seen:
            continue
        seen.add(tick_key)
        # Keep the dedupe set bounded without any database churn.
        if len(seen) > 4000:
            bot._custom_strategy_seen_ticks = {
                item for item in seen if item[1] >= int(market.tick_sequence) - 5
            }
            seen = bot._custom_strategy_seen_ticks

        signal = build_custom_signal(
            bot,
            symbol=symbol,
            tick=tick,
            config=config,
        )
        predicted = nominal_probability(config)
        bot.repository.record_candidate(signal)
        bot._multi_strategy_signal_routes[signal.signal_id] = multi.CandidateRoute(
            family="custom",
            side=str(config["trade_type"]),
            role="CUSTOM",
            scope_ids=set(ids),
            predicted_probability=float(predicted),
            minimum_edge=0.0,
            created_monotonic=time.monotonic(),
        )
        inflight.update(ids)
        task = asyncio.create_task(
            _execute_custom_group(
                bot,
                signal=signal,
                config=config,
                managed_ids=ids,
            ),
            name=f"custom_strategy_{signal.signal_id}",
        )
        tasks.add(task)

        def done(completed: asyncio.Task[Any], *, task_set: set[asyncio.Task[Any]] = tasks) -> None:
            task_set.discard(completed)
            try:
                completed.result()
            except asyncio.CancelledError:
                return
            except Exception:
                LOGGER.exception("CUSTOM_STRATEGY_TASK_FAILED")

        task.add_done_callback(done)


def _exclude_custom_from_shared_aidr() -> None:
    current = final_multi._groups_from_snapshot
    if getattr(current, "_custom_strategy_exclusion", False):
        return

    def groups_with_custom_excluded(
        routes: list[Any],
        scope: set[int],
        *,
        source_role: str,
    ) -> tuple[set[int], list[tuple[Any, set[int], str]], set[int]]:
        system_scope, manual_groups, unknown = current(
            routes,
            scope,
            source_role=source_role,
        )
        custom_ids = {
            int(route.managed_id)
            for route in routes
            if int(route.managed_id) in scope
            and str(getattr(route.selection, "family", "") or "") == "custom"
        }
        return system_scope, manual_groups, set(unknown) - custom_ids

    groups_with_custom_excluded._custom_strategy_exclusion = True  # type: ignore[attr-defined]
    final_multi._groups_from_snapshot = groups_with_custom_excluded


def install_custom_strategy_runtime() -> None:
    """Install Custom Strategy as an independent user-pattern entry authority."""

    global _INSTALLED
    if _INSTALLED:
        return

    _exclude_custom_from_shared_aidr()
    original_init = RFDir5TradingBot.__init__
    original_on_tick = RFDir5TradingBot._on_tick

    def custom_init(self: RFDir5TradingBot, config_path: str | None = None) -> None:
        original_init(self, config_path)
        self._custom_strategy_configs: dict[int, dict[str, Any]] = {}
        self._custom_strategy_config_ids: tuple[int, ...] = ()
        self._custom_strategy_config_at = 0.0
        self._custom_strategy_inflight_ids: set[int] = set()
        self._custom_strategy_tasks: set[asyncio.Task[Any]] = set()
        self._custom_strategy_seen_ticks: set[tuple[str, int, str]] = set()
        self.logger.warning(
            "CUSTOM_STRATEGY_RUNTIME_ACTIVE version=%s markets=%s "
            "trade_types=rise,fall,even,odd,over,under condition_join=AND "
            "silent_scanning=true independent_from_system_aidr=true "
            "manual_martingale_compatible=true",
            VERSION,
            10,
        )

    async def custom_on_tick(
        self: RFDir5TradingBot,
        tick_data: dict[str, Any],
    ) -> None:
        await original_on_tick(self, tick_data)
        try:
            _schedule_matches(self, tick_data)
        except Exception:
            self.logger.exception("CUSTOM_STRATEGY_SCAN_FAILED")

    RFDir5TradingBot.__init__ = custom_init
    RFDir5TradingBot._on_tick = custom_on_tick
    RFDir5TradingBot._custom_strategy_runtime_installed = True
    RFDir5TradingBot._custom_strategy_runtime_version = VERSION
    _INSTALLED = True
