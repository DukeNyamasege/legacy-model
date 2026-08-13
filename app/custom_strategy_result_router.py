from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import select

from app import custom_strategy_direct_runtime as direct_runtime
from app import custom_strategy_settlement as settlement
from app.custom_strategy_result_routing import (
    AFTER_LOSS,
    AFTER_WIN,
    PREFERENCE_PREFIX,
    merge_result_route,
    normalize_result_routing,
)
from app.custom_strategy_v1 import (
    MAX_WINDOW,
    contract_for_config,
    read_custom_strategy,
)
from app.custom_virtual_contract_parity import virtual_contract_display
from app.models import AccountRiskState, RuntimePreference
from app.repositories.rf_dir5_repository import RFDir5Repository, VIRTUAL_MODE
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_ORIGINAL_REFRESH = None
_ORIGINAL_SCHEDULE = None
_ORIGINAL_HISTORY_COUNT = None
_ORIGINAL_RECORD_OUTCOME = None
_ORIGINAL_START_VIRTUAL = None
_ORIGINAL_PROTECTION_PAYLOAD = None

# Route state is updated on actual settlement and synchronized from PostgreSQL on
# every runtime-account refresh. It is never queried from the database per tick.
_ROUTE_STATE: dict[tuple[int, int], str] = {}


def _state_key(database: Any, managed_account_id: int) -> tuple[int, int]:
    return id(database), int(managed_account_id)


def _route_from_debt(recovery_loss_debt: Any) -> str:
    try:
        debt = float(recovery_loss_debt or 0.0)
    except (TypeError, ValueError):
        debt = 0.0
    return AFTER_LOSS if debt > 0.009 else AFTER_WIN


def _load_routing_rows(
    bot: RFDir5TradingBot,
    managed_ids: set[int],
) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    if not managed_ids:
        return {}, {}
    preference_to_id = {
        f"{PREFERENCE_PREFIX}{int(managed_id)}": int(managed_id)
        for managed_id in managed_ids
    }
    routing: dict[int, dict[str, Any]] = {}
    states: dict[int, str] = {int(value): AFTER_WIN for value in managed_ids}
    with bot.repository.database.session() as session:
        rows = session.scalars(
            select(RuntimePreference).where(
                RuntimePreference.preference_key.in_(list(preference_to_id))
            )
        ).all()
        risk_rows = session.scalars(
            select(AccountRiskState).where(
                AccountRiskState.managed_account_id.in_(list(managed_ids))
            )
        ).all()
    for row in rows:
        managed_id = preference_to_id.get(str(row.preference_key or ""))
        if managed_id is None:
            continue
        try:
            raw = json.loads(str(row.preference_value or "{}"))
            routing[managed_id] = normalize_result_routing(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            routing[managed_id] = normalize_result_routing({"enabled": False})
    for managed_id in managed_ids:
        routing.setdefault(
            int(managed_id),
            normalize_result_routing({"enabled": False}),
        )
    for state in risk_rows:
        managed_id = int(state.managed_account_id)
        states[managed_id] = _route_from_debt(state.recovery_loss_debt)
    return routing, states


def _sync_route_runtime(
    bot: RFDir5TradingBot,
    runtime: dict[int, Any],
) -> None:
    managed_ids = {int(value) for value in runtime}
    routing, states = _load_routing_rows(bot, managed_ids)
    bot._custom_result_routing = routing
    database = bot.repository.database
    for managed_id in managed_ids:
        _ROUTE_STATE[_state_key(database, managed_id)] = states.get(managed_id, AFTER_WIN)


def _current_route(bot: RFDir5TradingBot, managed_id: int) -> str:
    return _ROUTE_STATE.get(
        _state_key(bot.repository.database, int(managed_id)),
        AFTER_WIN,
    )


def _active_config(bot: RFDir5TradingBot, item: Any) -> dict[str, Any]:
    managed_id = int(item.managed_id)
    routing = dict(
        getattr(bot, "_custom_result_routing", {}).get(managed_id, {}) or {}
    )
    return merge_result_route(
        item.config,
        routing,
        _current_route(bot, managed_id),
    )


def _active_config_from_repository(
    repository: RFDir5Repository,
    managed_id: int,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    base_config = read_custom_strategy(repository.database, int(managed_id))
    with repository.database.session() as session:
        row = session.get(RuntimePreference, f"{PREFERENCE_PREFIX}{int(managed_id)}")
        risk = session.get(AccountRiskState, int(managed_id))
        raw = str(row.preference_value or "") if row else ""
    try:
        routing = normalize_result_routing(json.loads(raw) if raw else {})
    except (TypeError, ValueError, json.JSONDecodeError):
        routing = normalize_result_routing({"enabled": False})
    route = _route_from_debt(risk.recovery_loss_debt if risk is not None else 0.0)
    return merge_result_route(base_config, routing, route), routing, route


def install_custom_strategy_result_router() -> None:
    """Select the account's primary or after-loss Custom Strategy at runtime.

    First trade and debt-free state use the existing primary strategy exactly.
    Actual loss debt activates the independently configured recovery route. Partial
    successful spread recovery keeps that route active until the final actual debt
    is cleared. Virtual observations never alter routing because only the financial
    AccountRiskState debt ledger drives this selector.
    """

    global _INSTALLED
    global _ORIGINAL_REFRESH, _ORIGINAL_SCHEDULE, _ORIGINAL_HISTORY_COUNT
    global _ORIGINAL_RECORD_OUTCOME, _ORIGINAL_START_VIRTUAL, _ORIGINAL_PROTECTION_PAYLOAD
    if _INSTALLED:
        return

    _ORIGINAL_REFRESH = direct_runtime._refresh_direct_accounts
    _ORIGINAL_SCHEDULE = direct_runtime._schedule_account_matches
    _ORIGINAL_HISTORY_COUNT = RFDir5TradingBot._public_history_count
    _ORIGINAL_RECORD_OUTCOME = RFDir5Repository.record_account_outcome
    _ORIGINAL_START_VIRTUAL = RFDir5Repository.start_virtual_trade
    _ORIGINAL_PROTECTION_PAYLOAD = RFDir5Repository._protection_payload

    def refresh_with_result_routes(
        bot: RFDir5TradingBot,
        *,
        require_connected: bool,
        fail_invalid: bool,
    ) -> dict[int, Any]:
        original = _ORIGINAL_REFRESH
        if original is None:
            return {}
        runtime = original(
            bot,
            require_connected=require_connected,
            fail_invalid=fail_invalid,
        )
        _sync_route_runtime(bot, runtime)
        return runtime

    def schedule_by_result(
        bot: RFDir5TradingBot,
        *,
        symbol: str,
        tick: dict[str, Any],
    ) -> None:
        original = _ORIGINAL_SCHEDULE
        if original is None:
            return
        primary_runtime: dict[int, Any] = getattr(bot, "_custom_direct_accounts", {})
        if not primary_runtime:
            return original(bot, symbol=symbol, tick=tick)

        routed_runtime: dict[int, Any] = {}
        for managed_id, item in list(primary_runtime.items()):
            active = _active_config(bot, item)
            routed_runtime[int(managed_id)] = direct_runtime.DirectRuntimeAccount(
                token=item.token,
                account_id=item.account_id,
                managed_id=int(item.managed_id),
                config=active,
                execution=item.execution,
            )

        # Scheduling is synchronous. Each created asyncio task captures its routed
        # item before the authoritative primary-runtime map is restored.
        bot._custom_direct_accounts = routed_runtime
        try:
            original(bot, symbol=symbol, tick=tick)
        finally:
            bot._custom_direct_accounts = primary_runtime

    def history_count_with_routes(self: RFDir5TradingBot) -> int:
        original = _ORIGINAL_HISTORY_COUNT
        primary_required = int(original(self)) if original is not None else 0
        required = primary_required
        routing = getattr(self, "_custom_result_routing", {}) or {}
        for payload in routing.values():
            if not bool((payload or {}).get("enabled")):
                continue
            route = (payload or {}).get(AFTER_LOSS) or {}
            for condition in route.get("conditions") or []:
                try:
                    required = max(required, int(condition.get("window") or 1))
                except (TypeError, ValueError):
                    continue
        return min(MAX_WINDOW, max(1, required)) if required else 0

    def record_outcome_and_route(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        original = _ORIGINAL_RECORD_OUTCOME
        if original is None:
            raise RuntimeError("Account settlement recorder is unavailable")
        result = original(self, *args, **kwargs)
        raw_id = kwargs.get("managed_account_id")
        try:
            managed_id = int(raw_id)
        except (TypeError, ValueError):
            return result
        next_route = _route_from_debt(result.get("recovery_loss_debt"))
        key = _state_key(self.database, managed_id)
        previous = _ROUTE_STATE.get(key, AFTER_WIN)
        _ROUTE_STATE[key] = next_route
        if previous != next_route:
            try:
                LOGGER = getattr(getattr(self, "base", None), "logger", None)
                if LOGGER is not None:
                    LOGGER.info(
                        "CUSTOM_RESULT_ROUTE_CHANGED managed_id=%s from=%s to=%s debt=%.2f",
                        managed_id,
                        previous,
                        next_route,
                        float(result.get("recovery_loss_debt") or 0.0),
                    )
            except Exception:
                pass
        result["custom_result_route"] = next_route
        return result

    def start_virtual_for_active_route(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        raw_id = kwargs.get("managed_account_id")
        signal = kwargs.get("signal")
        try:
            managed_id = int(raw_id)
        except (TypeError, ValueError):
            return None
        try:
            active, routing, route = _active_config_from_repository(self, managed_id)
        except Exception:
            original = _ORIGINAL_START_VIRTUAL
            return original(self, *args, **kwargs) if original is not None else None
        if not bool(routing.get("enabled")) or route != AFTER_LOSS:
            original = _ORIGINAL_START_VIRTUAL
            return original(self, *args, **kwargs) if original is not None else None

        # The older Custom settlement wrapper validates only the primary saved
        # contract. For an enabled after-loss route, perform the same fail-closed
        # checks against the *active* routed strategy, then invoke its captured base
        # creator so an Over-4/Odd/Rise recovery observation is not rejected merely
        # because the primary strategy is Over-1.
        if not settlement.virtual_signal_matches_config(active, signal):
            return None
        if signal is None or not settlement._ensure_parent(self, signal):
            return None
        base_start = settlement._ORIGINAL_START_VIRTUAL
        if base_start is None:
            return None
        return base_start(self, *args, **kwargs)

    def protection_payload_with_route(
        self: RFDir5Repository,
        state: Any,
    ) -> dict[str, Any]:
        original = _ORIGINAL_PROTECTION_PAYLOAD
        payload = original(self, state) if original is not None else self._default_virtual_state()
        if state is None or str(payload.get("mode") or "") != VIRTUAL_MODE:
            return payload
        try:
            active, routing, route = _active_config_from_repository(
                self,
                int(state.managed_account_id),
            )
            if not bool(routing.get("enabled")) or route != AFTER_LOSS:
                return payload
            contract_type, direction, barrier = contract_for_config(active)
            label = virtual_contract_display(
                contract_type,
                barrier=barrier,
                direction=direction,
            )
            required = max(1, int(payload.get("virtual_wins_required") or 1))
            payload["next_action"] = (
                f"Waiting for {required} virtual {label} win"
                f"{'' if required == 1 else 's'} before the next routed recovery trade"
            )
        except Exception:
            return payload
        return payload

    direct_runtime._refresh_direct_accounts = refresh_with_result_routes
    direct_runtime._schedule_account_matches = schedule_by_result
    RFDir5TradingBot._public_history_count = history_count_with_routes
    RFDir5Repository.record_account_outcome = record_outcome_and_route
    RFDir5Repository.start_virtual_trade = start_virtual_for_active_route
    RFDir5Repository._protection_payload = protection_payload_with_route
    RFDir5TradingBot._custom_strategy_result_router_installed = True
    _INSTALLED = True
