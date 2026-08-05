from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import app.aidr_loss_continuation_fix as continuation
import app.guaranteed_signal_delivery as immediate
import app.multi_strategy_runtime as multi
import app.private_websocket_rate_limit as private_ws
import app.standardized_execution_runtime as standardized
from app.repositories.rf_dir5_repository import NORMAL_MODE, RECOVERY_PENDING, VIRTUAL_MODE
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import ClientSession


LOGGER = logging.getLogger(__name__)
_INSTALLED = False
ROTATING_COHORT_VERSION = "strategy-round-robin-v2"

COHORT_SIZE = max(1, int(os.getenv("EXECUTION_COHORT_SIZE", "10")))
NORMAL_RESERVE = max(
    0,
    min(
        COHORT_SIZE,
        int(os.getenv("EXECUTION_COHORT_NORMAL_RESERVE", "2")),
    ),
)
HANDOVER_SECONDS = max(
    0.0,
    float(os.getenv("EXECUTION_COHORT_HANDOVER_SECONDS", "8")),
)
MAX_TRIGGER_PROPOSAL_CANDIDATES = max(
    1,
    int(os.getenv("EXECUTION_TRIGGER_PROPOSAL_CANDIDATES", "3")),
)
TRIGGER_PROPOSAL_INTERVAL_SECONDS = max(
    0.0,
    float(os.getenv("EXECUTION_TRIGGER_PROPOSAL_INTERVAL_SECONDS", "0.10")),
)
PROPOSAL_START_INTERVAL_SECONDS = max(
    0.0,
    float(os.getenv("EXECUTION_PROPOSAL_START_INTERVAL_SECONDS", "0.15")),
)

_ORIGINAL_STILL_CONFIGURED = private_ws._still_configured
_ORIGINAL_MULTI_PROPOSAL_FOR = multi._proposal_for
_ORIGINAL_BUY_SELECTED_ACCOUNTS: Callable[
    [RFDir5TradingBot, Any, Any], Awaitable[None]
] | None = None


@dataclass(slots=True)
class AidrCohortSelection:
    scopes: dict[str, set[int]]
    financial_ids: set[int]
    virtual_ids: set[int]


def _cursor_map(bot: RFDir5TradingBot) -> dict[str, int]:
    value = getattr(bot, "_execution_cohort_cursors", None)
    if not isinstance(value, dict):
        value = {}
        bot._execution_cohort_cursors = value
    return value


def _cursor_lock(bot: RFDir5TradingBot) -> asyncio.Lock:
    value = getattr(bot, "_execution_cohort_cursor_lock", None)
    if not isinstance(value, asyncio.Lock):
        value = asyncio.Lock()
        bot._execution_cohort_cursor_lock = value
    return value


def _activation_lock(bot: RFDir5TradingBot) -> asyncio.Lock:
    value = getattr(bot, "_execution_cohort_activation_lock", None)
    if not isinstance(value, asyncio.Lock):
        value = asyncio.Lock()
        bot._execution_cohort_activation_lock = value
    return value


def _proposal_lock(bot: RFDir5TradingBot) -> asyncio.Lock:
    value = getattr(bot, "_execution_proposal_lock", None)
    if not isinstance(value, asyncio.Lock):
        value = asyncio.Lock()
        bot._execution_proposal_lock = value
    return value


async def _wait_for_proposal_start_slot(bot: RFDir5TradingBot) -> None:
    now = time.monotonic()
    next_start = float(
        getattr(bot, "_execution_next_proposal_start", 0.0) or 0.0
    )
    if next_start > now:
        await asyncio.sleep(next_start - now)
        now = time.monotonic()
    bot._execution_next_proposal_start = (
        now + PROPOSAL_START_INTERVAL_SECONDS
    )


def _signal_is_stale(signal: Any) -> bool:
    generated = float(getattr(signal, "generated_monotonic", 0.0) or 0.0)
    if not generated:
        return False
    return (
        time.monotonic() - generated
        > float(standardized.MAX_STANDARDIZED_SIGNAL_AGE_SECONDS)
    )


async def _persist_cursor(bot: RFDir5TradingBot, key: str, cursor: int) -> None:
    try:
        await asyncio.to_thread(
            bot.repository.set_runtime_preference,
            f"execution_cohort_cursor:{key}",
            str(int(cursor)),
        )
    except Exception as exc:
        bot.logger.warning(
            "EXECUTION_COHORT_CURSOR_PERSIST_FAILED key=%s error_type=%s",
            key,
            type(exc).__name__,
        )


async def _round_robin(
    bot: RFDir5TradingBot,
    *,
    key: str,
    managed_ids: set[int],
    count: int,
) -> set[int]:
    ordered = sorted({int(value) for value in managed_ids})
    limit = min(max(0, int(count)), len(ordered))
    if not ordered or limit <= 0:
        return set()

    async with _cursor_lock(bot):
        cursors = _cursor_map(bot)
        if key not in cursors:
            try:
                raw = await asyncio.to_thread(
                    bot.repository.runtime_preference,
                    f"execution_cohort_cursor:{key}",
                )
                cursors[key] = int(raw or "0")
            except (TypeError, ValueError):
                cursors[key] = 0
            except Exception:
                cursors[key] = 0

        start = int(cursors.get(key, 0) or 0) % len(ordered)
        selected = {
            ordered[(start + offset) % len(ordered)]
            for offset in range(limit)
        }
        next_cursor = (start + limit) % len(ordered)
        cursors[key] = next_cursor

    try:
        asyncio.get_running_loop().create_task(
            _persist_cursor(bot, key, next_cursor),
            name=f"persist_execution_cohort_cursor_{key.replace(':', '_')}",
        )
    except RuntimeError:
        pass
    return selected


def _allocate_counts(
    *,
    capacity: int,
    normal_count: int,
    recovery_counts: dict[str, int],
) -> dict[str, int]:
    capacity = max(0, int(capacity))
    allocation = {"normal": 0, **{key: 0 for key in recovery_counts}}
    recovery_total = sum(max(0, int(value)) for value in recovery_counts.values())

    if capacity <= 0:
        return allocation
    if recovery_total <= 0:
        allocation["normal"] = min(capacity, max(0, normal_count))
        return allocation

    allocation["normal"] = min(
        max(0, normal_count),
        min(NORMAL_RESERVE, capacity),
    )
    remaining = capacity - allocation["normal"]

    recovery_order = [
        key
        for key in ("virtual", "post_virtual", "recovery")
        if key in recovery_counts
    ]
    while remaining > 0:
        progressed = False
        for key in recovery_order:
            if allocation[key] >= max(0, int(recovery_counts[key])):
                continue
            allocation[key] += 1
            remaining -= 1
            progressed = True
            if remaining <= 0:
                break
        if not progressed:
            break

    if remaining > 0 and allocation["normal"] < max(0, normal_count):
        extra = min(remaining, normal_count - allocation["normal"])
        allocation["normal"] += extra
        remaining -= extra

    if remaining > 0:
        while remaining > 0:
            progressed = False
            for key in recovery_order:
                if allocation[key] >= max(0, int(recovery_counts[key])):
                    continue
                allocation[key] += 1
                remaining -= 1
                progressed = True
                if remaining <= 0:
                    break
            if not progressed:
                break
    return allocation


async def select_aidr_cycle(
    bot: RFDir5TradingBot,
    *,
    normal: set[int],
    recovery: set[int],
    post_virtual: set[int],
    virtual: set[int],
) -> AidrCohortSelection:
    allocation = _allocate_counts(
        capacity=COHORT_SIZE,
        normal_count=len(normal),
        recovery_counts={
            "recovery": len(recovery),
            "post_virtual": len(post_virtual),
            "virtual": len(virtual),
        },
    )
    selected_normal, selected_recovery, selected_post, selected_virtual = (
        await asyncio.gather(
            _round_robin(
                bot,
                key="digits:over:NORMAL",
                managed_ids=set(normal),
                count=allocation["normal"],
            ),
            _round_robin(
                bot,
                key="digits:over:RECOVERY",
                managed_ids=set(recovery),
                count=allocation["recovery"],
            ),
            _round_robin(
                bot,
                key="digits:over:POST_VIRTUAL",
                managed_ids=set(post_virtual),
                count=allocation["post_virtual"],
            ),
            _round_robin(
                bot,
                key="digits:over:VIRTUAL",
                managed_ids=set(virtual),
                count=allocation["virtual"],
            ),
        )
    )
    scopes = {
        continuation.NORMAL_ROLE: set(selected_normal),
        continuation.FIRST_RECOVERY_ROLE: set(selected_recovery),
        continuation.POST_VIRTUAL_ROLE: set(selected_post) | set(selected_virtual),
    }
    financial_ids = set(selected_normal) | set(selected_recovery) | set(selected_post)
    selection = AidrCohortSelection(
        scopes=scopes,
        financial_ids=financial_ids,
        virtual_ids=set(selected_virtual),
    )
    bot.logger.warning(
        "EXECUTION_COHORT_SELECTED strategy=digits/over capacity=%s "
        "selected_total=%s financial_accounts=%s virtual_accounts=%s "
        "normal=%s recovery=%s post_virtual=%s "
        "rotation=round_robin recovery_state_preserved=true",
        COHORT_SIZE,
        sum(len(scope) for scope in scopes.values()),
        len(financial_ids),
        len(selected_virtual),
        len(selected_normal),
        len(selected_recovery),
        len(selected_post),
    )
    return selection


def _session_managed_id(session: ClientSession) -> int | None:
    value = getattr(session, "managed_account_id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _unique_sessions(bot: RFDir5TradingBot) -> list[ClientSession]:
    sessions: list[ClientSession] = []
    seen: set[int] = set()
    for session in list(getattr(bot, "sessions", {}).values()):
        if not isinstance(session, ClientSession):
            continue
        identity = id(session)
        if identity in seen:
            continue
        seen.add(identity)
        sessions.append(session)
    return sessions


async def _close_inactive_sessions_after_handover(
    bot: RFDir5TradingBot,
    activation_generation: int,
) -> None:
    if HANDOVER_SECONDS > 0:
        await asyncio.sleep(HANDOVER_SECONDS)
    if int(getattr(bot, "_execution_cohort_generation", 0) or 0) != int(
        activation_generation
    ):
        return

    active = {
        int(value)
        for value in set(getattr(bot, "_rotating_active_managed_ids", set()) or set())
    }
    closed = 0
    for session in _unique_sessions(bot):
        managed_id = _session_managed_id(session)
        if managed_id is not None and managed_id in active:
            continue
        if bool(getattr(session, "pending_contracts", set())):
            continue
        websocket = getattr(session, "ws", None)
        if websocket is None:
            continue
        with suppress(Exception):
            await websocket.close(code=1000, reason="rotating cohort standby")
            closed += 1
    if closed:
        bot.logger.info(
            "EXECUTION_COHORT_IDLE_SESSIONS_CLOSED count=%s "
            "pending_contract_sessions_preserved=true",
            closed,
        )


async def activate_cycle_accounts(
    bot: RFDir5TradingBot,
    managed_ids: set[int],
    *,
    strategy: str,
) -> None:
    selected = {int(value) for value in managed_ids}
    async with _activation_lock(bot):
        bot._rotating_active_managed_ids = set(selected)
        generation = int(getattr(bot, "_execution_cohort_generation", 0) or 0) + 1
        bot._execution_cohort_generation = generation

        started = 0
        already_connected = 0
        for token, account_id in list(getattr(bot, "valid_clients", []) or []):
            managed_id = bot._managed_account_id_for_token(token)
            if managed_id is None or int(managed_id) not in selected:
                continue
            session = immediate._ensure_session(bot, token, account_id)
            started += 1
            if bool(getattr(session, "is_connected", False)):
                already_connected += 1

        previous_reaper = getattr(bot, "_execution_cohort_reaper_task", None)
        if isinstance(previous_reaper, asyncio.Task) and not previous_reaper.done():
            previous_reaper.cancel()
        bot._execution_cohort_reaper_task = asyncio.create_task(
            _close_inactive_sessions_after_handover(bot, generation),
            name="execution_cohort_idle_session_reaper",
        )
        bot.logger.warning(
            "EXECUTION_COHORT_ACTIVE strategy=%s financial_accounts=%s "
            "private_sessions_started=%s already_connected=%s handover_seconds=%.1f "
            "all_other_accounts_standby=true pending_contracts_preserved=true",
            strategy,
            len(selected),
            started,
            already_connected,
            HANDOVER_SECONDS,
        )


def _cohort_still_configured(session: ClientSession) -> bool:
    if bool(getattr(session, "pending_contracts", set())):
        return True
    managed_id = _session_managed_id(session)
    active = {
        int(value)
        for value in set(
            getattr(session.bot, "_rotating_active_managed_ids", set()) or set()
        )
    }
    return (
        managed_id is not None
        and managed_id in active
        and bool(_ORIGINAL_STILL_CONFIGURED(session))
    )


async def select_aidr_trigger(
    bot: RFDir5TradingBot,
    candidates: list[Any],
) -> tuple[Any, Any] | None:
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            -float(getattr(candidate, "weighted_probability", 0.0) or 0.0),
            -float(getattr(candidate, "lower95", 0.0) or 0.0),
            -int(getattr(candidate, "quality_score", 0) or 0),
            str(getattr(candidate, "symbol", "") or ""),
        ),
    )
    attempted = 0
    for candidate in ranked[:MAX_TRIGGER_PROPOSAL_CANDIDATES]:
        attempted += 1
        bot.logger.info(
            "AIDR_TRIGGER_PROPOSAL_ATTEMPT attempt=%s max_attempts=%s "
            "symbol=%s concurrent_proposals=false",
            attempted,
            min(MAX_TRIGGER_PROPOSAL_CANDIDATES, len(ranked)),
            str(getattr(candidate, "symbol", "") or ""),
        )
        try:
            result = await continuation._proposal_ok(
                bot,
                candidate,
                continuation.AIDR_MINIMUM_LIVE_EDGE,
            )
        except Exception as exc:
            bot.logger.warning(
                "AIDR_TRIGGER_PROPOSAL_FAILED attempt=%s symbol=%s "
                "error_type=%s next_candidate=%s",
                attempted,
                str(getattr(candidate, "symbol", "") or ""),
                type(exc).__name__,
                str(
                    attempted
                    < min(MAX_TRIGGER_PROPOSAL_CANDIDATES, len(ranked))
                ).lower(),
            )
            result = None
        if result is not None:
            bot.logger.warning(
                "AIDR_TRIGGER_PROPOSAL_SELECTED attempt=%s symbol=%s "
                "proposal_burst=false",
                attempted,
                str(getattr(candidate, "symbol", "") or ""),
            )
            return result
        if TRIGGER_PROPOSAL_INTERVAL_SECONDS > 0:
            await asyncio.sleep(TRIGGER_PROPOSAL_INTERVAL_SECONDS)

    bot.logger.warning(
        "AIDR_TRIGGER_PROPOSALS_UNAVAILABLE attempted=%s "
        "concurrent_proposals=false financial_requests=0",
        attempted,
    )
    return None


async def _select_multi_route_cohort(
    bot: RFDir5TradingBot,
    route: Any,
) -> tuple[set[int], set[int]]:
    pool_ids = {int(value) for value in set(route.scope_ids)}
    try:
        snapshot = await asyncio.to_thread(multi._strategy_snapshot, bot, force=True)
    except Exception:
        snapshot = []
    route_by_id = {
        int(item.managed_id): item
        for item in snapshot
        if int(item.managed_id) in pool_ids
    }
    normal_ids = {
        managed_id
        for managed_id, item in route_by_id.items()
        if str(item.mode) == NORMAL_MODE
    }
    virtual_ids = {
        managed_id
        for managed_id, item in route_by_id.items()
        if str(item.mode) == VIRTUAL_MODE
    }
    recovery_ids = {
        managed_id
        for managed_id, item in route_by_id.items()
        if str(item.mode) == RECOVERY_PENDING
    }
    unclassified = pool_ids - normal_ids - virtual_ids - recovery_ids
    normal_ids.update(unclassified)

    allocation = _allocate_counts(
        capacity=COHORT_SIZE,
        normal_count=len(normal_ids),
        recovery_counts={
            "recovery": len(recovery_ids),
            "virtual": len(virtual_ids),
        },
    )
    base_key = f"{route.family}:{route.side}:{route.role}"
    selected_normal, selected_recovery, selected_virtual = await asyncio.gather(
        _round_robin(
            bot,
            key=f"{base_key}:NORMAL",
            managed_ids=normal_ids,
            count=allocation["normal"],
        ),
        _round_robin(
            bot,
            key=f"{base_key}:RECOVERY",
            managed_ids=recovery_ids,
            count=allocation["recovery"],
        ),
        _round_robin(
            bot,
            key=f"{base_key}:VIRTUAL",
            managed_ids=virtual_ids,
            count=allocation["virtual"],
        ),
    )
    selected = set(selected_normal) | set(selected_recovery) | set(selected_virtual)
    financial = set(selected_normal) | set(selected_recovery)
    return selected, financial


async def _rotating_multi_proposal_for(
    bot: RFDir5TradingBot,
    signal: Any,
    predicted: float,
) -> Any | None:
    """Serialize public proposals; defer cohort mutation until a signal is chosen."""

    route = getattr(bot, "_multi_strategy_signal_routes", {}).get(
        str(getattr(signal, "signal_id", "") or "")
    )
    if route is None:
        return await _ORIGINAL_MULTI_PROPOSAL_FOR(bot, signal, predicted)
    if _signal_is_stale(signal):
        bot.repository.mark_signal(
            signal.signal_id,
            status="SKIP_COHORT_PROPOSAL_EXPIRED",
            stale=True,
        )
        return None

    async with _proposal_lock(bot):
        if _signal_is_stale(signal):
            bot.repository.mark_signal(
                signal.signal_id,
                status="SKIP_COHORT_PROPOSAL_QUEUE_EXPIRED",
                stale=True,
            )
            return None
        await _wait_for_proposal_start_slot(bot)
        bot.logger.info(
            "EXECUTION_PROPOSAL_SERIALIZED strategy=%s/%s role=%s symbol=%s "
            "concurrent_proposals=false cohort_selection_deferred=true",
            route.family,
            route.side,
            route.role,
            str(getattr(signal, "symbol", "") or ""),
        )
        return await _ORIGINAL_MULTI_PROPOSAL_FOR(bot, signal, predicted)


async def _rotating_buy_selected_accounts(
    self: RFDir5TradingBot,
    signal: Any,
    economics: Any,
) -> None:
    original = _ORIGINAL_BUY_SELECTED_ACCOUNTS
    if original is None:
        raise RuntimeError("Rotating cohort purchase authority is not installed")

    route = getattr(self, "_multi_strategy_signal_routes", {}).get(
        str(getattr(signal, "signal_id", "") or "")
    )
    if route is None:
        await original(self, signal, economics)
        return

    original_scope = {
        int(value)
        for value in set(getattr(route, "scope_ids", set()) or set())
    }
    selected, financial_ids = await _select_multi_route_cohort(self, route)
    if not selected:
        self.repository.mark_signal(
            signal.signal_id,
            status="SKIP_NO_COHORT_ACCOUNTS",
        )
        return

    route.scope_ids = set(selected)
    self.logger.warning(
        "EXECUTION_COHORT_SELECTED strategy=%s/%s role=%s "
        "pool_accounts=%s selected_accounts=%s financial_accounts=%s "
        "rotation=round_robin recovery_state_preserved=true "
        "selection_at_purchase_boundary=true",
        route.family,
        route.side,
        route.role,
        len(original_scope),
        len(selected),
        len(financial_ids),
    )
    try:
        await activate_cycle_accounts(
            self,
            financial_ids,
            strategy=f"{route.family}/{route.side}/{route.role}",
        )
        await original(self, signal, economics)
    finally:
        # The inner receipt and strategy routers must see only the chosen cohort.
        # Restore the full subscribed pool afterwards so non-selected accounts are
        # treated as standby, not as accounts whose own strategy failed.
        route.scope_ids = set(original_scope)


def install_rotating_execution_cohorts() -> None:
    """Keep only one strategy-specific round-robin cohort financially active."""

    global _INSTALLED, _ORIGINAL_BUY_SELECTED_ACCOUNTS
    if _INSTALLED:
        return

    _ORIGINAL_BUY_SELECTED_ACCOUNTS = RFDir5TradingBot._buy_selected_accounts
    private_ws._still_configured = _cohort_still_configured
    multi._proposal_for = _rotating_multi_proposal_for
    RFDir5TradingBot._buy_selected_accounts = _rotating_buy_selected_accounts

    RFDir5TradingBot._rotating_execution_cohorts_installed = True
    _INSTALLED = True
    LOGGER.warning(
        "ROTATING_EXECUTION_COHORTS_INSTALLED version=%s cohort_size=%s "
        "normal_reserve=%s persistent_private_sockets_for_all_accounts=false "
        "proposal_concurrency=1 proposal_start_interval_seconds=%.2f "
        "cohort_selection_at_purchase_boundary=true "
        "one_account_one_private_websocket=true bulk_purchase=false "
        "copy_trading=false per_account_recovery_state=true",
        ROTATING_COHORT_VERSION,
        COHORT_SIZE,
        NORMAL_RESERVE,
        PROPOSAL_START_INTERVAL_SECONDS,
    )
