from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.hybrid_digit_put as hybrid
import app.multi_strategy_runtime as multi
from app.models import Trade, VirtualTrade
from app.repositories.rf_dir5_repository import NORMAL_MODE
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import mask_account_id


_INSTALLED = False
STANDARDIZED_EXECUTION_VERSION = "account-standardized-v1"
MAX_STANDARDIZED_SIGNAL_AGE_SECONDS = 5.0
NOTICE_REPEAT_SECONDS = 45.0

# The order affects only milliseconds of transport setup. Every qualified group is
# executed in the same standardized cycle. Normal comes first so a newly enabled
# System account immediately receives OVER-1 even when older accounts need OVER-3
# or OVER-4 recovery.
AIDR_EXECUTION_ORDER = (
    continuation.NORMAL_ROLE,
    continuation.FIRST_RECOVERY_ROLE,
    continuation.POST_VIRTUAL_ROLE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cycle_gate(bot: RFDir5TradingBot) -> asyncio.Lock:
    gate = getattr(bot, "_standardized_account_cycle_gate", None)
    if gate is None:
        gate = asyncio.Lock()
        bot._standardized_account_cycle_gate = gate
    return gate


def _signal_scope_ids(bot: RFDir5TradingBot, signal: Any) -> set[int]:
    explicit = getattr(bot, "_aidr_purchase_scope_ids", None)
    if explicit:
        return {int(value) for value in explicit}
    route = getattr(bot, "_multi_strategy_signal_routes", {}).get(
        str(getattr(signal, "signal_id", "") or "")
    )
    if route is None:
        return set()
    return {int(value) for value in set(getattr(route, "scope_ids", set()) or set())}


def _current_tick(bot: RFDir5TradingBot, symbol: str) -> dict[str, Any] | None:
    market = getattr(bot, "market_states", {}).get(str(symbol or ""))
    if market is None:
        return None
    history = list(getattr(market, "ticks_history", []) or [])
    if not history:
        return None
    latest = dict(history[-1])
    quote = latest.get("quote")
    if quote is None:
        return None
    latest["quote"] = Decimal(str(quote))
    latest["epoch"] = int(latest.get("epoch") or 0)
    latest["tick_sequence"] = int(getattr(market, "tick_sequence", 0) or 0)
    return latest


def refresh_signal_for_execution(bot: RFDir5TradingBot, signal: Any) -> bool:
    """Refresh a qualified standardized signal at its financial boundary.

    Proposal and account-group preparation can take longer than one market tick.
    The rolling-window strategy remains qualified, while the contract itself must
    be bought against the provider's current tick. This refresh is allowed only
    for signals explicitly owned by a standardized cycle and only for five seconds.
    """

    if not bool(getattr(signal, "_standardized_cycle_id", "")):
        return False
    generated = float(getattr(signal, "generated_monotonic", 0.0) or 0.0)
    age = time.monotonic() - generated if generated else 0.0
    if age > MAX_STANDARDIZED_SIGNAL_AGE_SECONDS:
        return False
    tick = _current_tick(bot, str(getattr(signal, "symbol", "") or ""))
    if tick is None:
        return False
    symbol = str(getattr(signal, "symbol", "") or "")
    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    signal.reference_entry_quote = quote
    signal.signal_tick_epoch = epoch
    signal.signal_tick_id = bot._tick_identity(symbol, epoch, quote)
    signal.tick_sequence = int(tick["tick_sequence"])
    signal.generated_monotonic = time.monotonic()
    signal.generated_at = _now_iso()
    return True


def _notice_cache(bot: RFDir5TradingBot) -> dict[tuple[Any, ...], float]:
    cache = getattr(bot, "_standardized_execution_notice_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        bot._standardized_execution_notice_cache = cache
    return cache


def _set_account_notice(
    bot: RFDir5TradingBot,
    managed_id: int,
    status: str,
    reason: str,
    *,
    key: tuple[Any, ...],
) -> None:
    now = time.monotonic()
    cache = _notice_cache(bot)
    previous = float(cache.get((int(managed_id),) + key, 0.0) or 0.0)
    if previous and now - previous < NOTICE_REPEAT_SECONDS:
        return
    cache[(int(managed_id),) + key] = now
    try:
        account = bot.repository.managed_account(int(managed_id)) or {}
        current = str(account.get("execution_status") or "").lower()
        # Never overwrite a terminal personal decision or a concrete safety block
        # with a transient signal-waiting notification.
        if current in {
            "disabled",
            "manual_pause",
            "stopped",
            "take_profit",
            "stop_loss",
            "insufficient_balance",
            "purchase_insufficient_balance",
            "credential_error",
            "invalid_account",
            "real_disabled",
        }:
            return
        bot._set_account_execution_status(int(managed_id), status, reason[:160])
    except Exception:
        bot.logger.exception(
            "ACCOUNT_EXECUTION_NOTICE_FAILED managed_id=%s status=%s",
            int(managed_id),
            status,
        )


def _contract_label(signal: Any) -> str:
    contract_type = str(getattr(signal, "contract_type", "") or "contract")
    barrier = str(getattr(signal, "barrier", "") or "").strip()
    return f"{contract_type} {barrier}".strip()


def notify_scope_waiting(
    bot: RFDir5TradingBot,
    scope_ids: set[int],
    *,
    strategy: str,
    role: str,
    contract: str,
    reason_code: str,
    reason: str,
) -> None:
    for managed_id in sorted({int(value) for value in scope_ids}):
        message = (
            f"No contract on this cycle: {contract} for {strategy}/{role}. "
            f"{reason} The account remains enabled and will retry automatically."
        )
        _set_account_notice(
            bot,
            managed_id,
            "signal_waiting",
            message,
            key=("signal_waiting", strategy, role, contract, reason_code),
        )
        account = bot.repository.managed_account(managed_id) or {}
        bot.logger.info(
            "ACCOUNT_CYCLE_WAITING account=%s managed_id=%s strategy=%s role=%s "
            "contract=%s reason_code=%s reason=%s global_execution_continues=true",
            str(account.get("label") or f"managed-{managed_id}"),
            managed_id,
            strategy,
            role,
            contract,
            reason_code,
            reason,
        )


def _route_key(route: Any, signal: Any) -> tuple[Any, ...]:
    return (
        str(getattr(route, "family", "") or ""),
        str(getattr(route, "side", "") or ""),
        str(getattr(route, "role", "") or ""),
        str(getattr(signal, "contract_type", "") or ""),
        str(getattr(signal, "barrier", "") or ""),
        tuple(sorted(int(value) for value in set(getattr(route, "scope_ids", set()) or set()))),
    )


def _queue_key(route: Any, signal: Any) -> tuple[Any, ...]:
    return _route_key(route, signal) + (str(getattr(signal, "symbol", "") or ""),)


def _queue_standardized_candidate(bot: RFDir5TradingBot, signal: Any) -> None:
    route = getattr(bot, "_multi_strategy_signal_routes", {}).get(
        str(getattr(signal, "signal_id", "") or "")
    )
    if route is None or not set(getattr(route, "scope_ids", set()) or set()):
        return
    key = _queue_key(route, signal)
    previous = bot._multi_strategy_candidates.get(key)
    if previous is not None:
        try:
            bot.repository.mark_signal(
                previous.signal_id,
                status="SKIP_NEWER_SAME_ACCOUNT_GROUP_SIGNAL",
            )
        except Exception:
            pass
    bot._multi_strategy_candidates[key] = signal
    if bot._multi_strategy_task is None or bot._multi_strategy_task.done():
        task = asyncio.create_task(
            _standardized_multi_strategy_arbitrate(bot),
            name="standardized_multi_strategy_arbitration",
        )
        bot._multi_strategy_task = task

        def finished(done: asyncio.Task[Any]) -> None:
            if bot._multi_strategy_task is done:
                bot._multi_strategy_task = None
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception:
                bot.logger.exception("STANDARDIZED_MULTI_STRATEGY_ARBITRATION_FAILED")

        task.add_done_callback(finished)


def _mark_proposal_fields(signal: Any, economics: Any, edge: float) -> None:
    signal.proposal_ask_price = float(economics.stake)
    signal.proposal_payout = float(economics.payout)
    signal.break_even_probability = float(economics.break_even_probability)
    signal.validated_edge = float(edge)


async def _standardized_multi_strategy_arbitrate(bot: RFDir5TradingBot) -> None:
    await asyncio.sleep(0.08)
    candidates = list(bot._multi_strategy_candidates.values())
    bot._multi_strategy_candidates.clear()
    if not candidates:
        return

    async with _cycle_gate(bot):
        bot._prune_stale_pending_contracts("standardized_multi_strategy_pre_proposal")
        valid: list[tuple[Any, Any]] = []
        rejected_scope: dict[tuple[Any, ...], tuple[Any, str, str]] = {}
        for signal in candidates:
            route = bot._multi_strategy_signal_routes.get(signal.signal_id)
            if route is None or not route.scope_ids:
                continue
            age = time.monotonic() - float(
                getattr(signal, "generated_monotonic", time.monotonic()) or time.monotonic()
            )
            if age > MAX_STANDARDIZED_SIGNAL_AGE_SECONDS:
                bot.repository.mark_signal(signal.signal_id, status="SKIP_STANDARDIZED_SIGNAL_EXPIRED", stale=True)
                rejected_scope[_route_key(route, signal)] = (
                    route,
                    _contract_label(signal),
                    "The qualified signal expired before the standardized purchase boundary.",
                )
                continue
            valid.append((signal, route))
        if not valid:
            return

        results = await asyncio.gather(
            *(
                multi._proposal_for(bot, signal, route.predicted_probability)
                for signal, route in valid
            ),
            return_exceptions=True,
        )
        qualified: dict[tuple[Any, ...], list[tuple[float, Any, Any, Any]]] = defaultdict(list)
        for (signal, route), result in zip(valid, results, strict=True):
            key = _route_key(route, signal)
            if isinstance(result, Exception):
                bot.repository.mark_signal(signal.signal_id, status="SKIP_PROPOSAL_EXCEPTION")
                rejected_scope[key] = (
                    route,
                    _contract_label(signal),
                    f"Proposal request failed with {type(result).__name__}.",
                )
                continue
            if result is None:
                bot.repository.mark_signal(signal.signal_id, status="SKIP_INVALID_PROPOSAL")
                rejected_scope[key] = (
                    route,
                    _contract_label(signal),
                    "Deriv did not return a usable proposal.",
                )
                continue
            economics = result
            edge = float(route.predicted_probability) - float(economics.break_even_probability)
            _mark_proposal_fields(signal, economics, edge)
            bot.repository.record_proposal(signal, economics)
            if edge + 1e-12 < float(route.minimum_edge):
                bot.repository.mark_signal(signal.signal_id, status="SKIP_MULTI_STRATEGY_EDGE")
                rejected_scope[key] = (
                    route,
                    _contract_label(signal),
                    (
                        f"Live edge {edge:.4f} was below the required "
                        f"{float(route.minimum_edge):.4f}."
                    ),
                )
                continue
            score = edge + 0.05 * float(getattr(signal, "lower95", 0.0) or 0.0)
            qualified[key].append((score, signal, economics, route))

        selected_groups: list[tuple[Any, Any, Any]] = []
        for key, group in qualified.items():
            group.sort(
                key=lambda item: (
                    -float(item[0]),
                    -float(getattr(item[1], "weighted_probability", 0.0) or 0.0),
                    -int(getattr(item[1], "quality_score", 0) or 0),
                    str(getattr(item[1], "symbol", "") or ""),
                )
            )
            _score, selected, economics, route = group[0]
            selected_groups.append((selected, economics, route))
            for _other_score, other, _other_economics, _other_route in group[1:]:
                bot.repository.mark_signal(
                    other.signal_id,
                    status="SKIP_MARKET_ARBITRATION_WITHIN_ACCOUNT_GROUP",
                )

        if not selected_groups:
            return

        selected_groups.sort(
            key=lambda item: (
                str(item[2].family),
                str(item[2].side),
                str(item[2].role),
                str(getattr(item[0], "barrier", "") or ""),
            )
        )
        cycle_id = str(uuid.uuid4())
        executed_ids: set[int] = set()
        for selected, economics, route in selected_groups:
            selected._standardized_cycle_id = cycle_id
            if not refresh_signal_for_execution(bot, selected):
                bot.repository.mark_signal(
                    selected.signal_id,
                    status="SKIP_STANDARDIZED_SIGNAL_EXPIRED",
                    stale=True,
                )
                notify_scope_waiting(
                    bot,
                    set(route.scope_ids),
                    strategy=f"{route.family}/{route.side}",
                    role=str(route.role),
                    contract=_contract_label(selected),
                    reason_code="signal_expired",
                    reason="The account-group signal expired before transport.",
                )
                continue
            bot.logger.warning(
                "STANDARDIZED_GROUP_SELECTED cycle_id=%s family=%s side=%s role=%s "
                "symbol=%s contract_type=%s barrier=%s accounts=%s edge=%.5f",
                cycle_id,
                route.family,
                route.side,
                route.role,
                selected.symbol,
                selected.contract_type,
                getattr(selected, "barrier", ""),
                len(route.scope_ids),
                float(getattr(selected, "validated_edge", 0.0) or 0.0),
            )
            await bot._buy_selected_accounts(selected, economics)
            executed_ids.update(int(value) for value in route.scope_ids)

        # A strategy group that had no selected candidate is told why it did not
        # join this cycle. It was not defeated by another group; its own contract
        # condition or proposal was not ready.
        snapshot = multi._strategy_snapshot(bot, force=True)
        for route in snapshot:
            if route.selection.family == "system" or route.managed_id in executed_ids:
                continue
            family = str(route.selection.family)
            side = str(route.selection.side)
            prediction = getattr(route.selection, "prediction", None)
            contract = (
                f"DIGIT{side.upper()} {prediction}"
                if family == "digits"
                else side.upper()
            )
            notify_scope_waiting(
                bot,
                {int(route.managed_id)},
                strategy=f"{family}/{side}",
                role=str(route.mode),
                contract=contract,
                reason_code="own_strategy_not_qualified",
                reason=(
                    "Other account groups executed, but this account's selected "
                    "contract did not produce a qualifying signal/proposal in the cycle."
                ),
            )

        bot.logger.warning(
            "STANDARDIZED_MULTI_STRATEGY_CYCLE_COMPLETE cycle_id=%s groups=%s "
            "account_scope=%s competition_removed=true",
            cycle_id,
            len(selected_groups),
            len(executed_ids),
        )


def _role_spec(role: str) -> tuple[int, bool]:
    if role == continuation.NORMAL_ROLE:
        return aidr.NORMAL_BARRIER, False
    if role == continuation.FIRST_RECOVERY_ROLE:
        return aidr.RECOVERY_BARRIER, True
    return aidr.POST_VIRTUAL_BARRIER, True


async def _standardized_aidr_arbitrate(bot: RFDir5TradingBot) -> None:
    cfg = bot.test2_config.hybrid_strategy
    await asyncio.sleep(float(getattr(cfg, "candidate_window_ms", 75)) / 1000.0)
    queued = list(bot.hybrid_digit_candidates.values())
    bot.hybrid_digit_candidates.clear()
    if not queued:
        return

    async with _cycle_gate(bot):
        bot._prune_stale_pending_contracts("standardized_aidr_pre_proposal")
        if continuation._cadence_blocked(bot, queued):
            return

        normal_ids, recovery_ids, post_ids, virtual_ids = aidr._account_recovery_groups(bot)
        scopes = {
            continuation.NORMAL_ROLE: set(normal_ids),
            continuation.FIRST_RECOVERY_ROLE: set(recovery_ids),
            continuation.POST_VIRTUAL_ROLE: set(post_ids) | set(virtual_ids),
        }
        if not any(scopes.values()):
            for candidate in queued:
                bot.repository.mark_signal(candidate.signal_id, status="SKIP_NO_ENABLED_ACCOUNTS")
            return

        symbols = sorted(
            {
                str(getattr(candidate, "symbol", "") or "")
                for candidate in queued
                if str(getattr(candidate, "symbol", "") or "")
            }
        )
        role_candidates: dict[str, list[Any]] = defaultdict(list)
        role_missing_reason: dict[str, str] = {}
        for role in AIDR_EXECUTION_ORDER:
            if not scopes[role]:
                continue
            barrier, recovery = _role_spec(role)
            for symbol in symbols:
                tick = _current_tick(bot, symbol)
                if tick is None:
                    continue
                candidate = continuation._make_aidr_candidate(
                    bot,
                    symbol,
                    tick,
                    barrier=barrier,
                    recovery=recovery,
                )
                if candidate is None:
                    continue
                bot.repository.record_candidate(candidate)
                role_candidates[role].append(candidate)
            if not role_candidates[role]:
                role_missing_reason[role] = (
                    f"No OVER-{barrier} market met its rolling-window alignment requirement."
                )

        entries: list[tuple[str, Any, Any]] = []
        for role in AIDR_EXECUTION_ORDER:
            for candidate in role_candidates.get(role, []):
                entries.append(
                    (
                        role,
                        candidate,
                        continuation._proposal_ok(
                            bot,
                            candidate,
                            continuation.AIDR_MINIMUM_LIVE_EDGE,
                        ),
                    )
                )
        if not entries:
            return

        results = await asyncio.gather(
            *(entry[2] for entry in entries),
            return_exceptions=True,
        )
        qualified: dict[str, list[tuple[float, Any, Any]]] = defaultdict(list)
        for (role, candidate, _task), result in zip(entries, results, strict=True):
            if isinstance(result, Exception):
                role_missing_reason[role] = (
                    f"Proposal evaluation failed with {type(result).__name__}."
                )
                bot.logger.exception(
                    "STANDARDIZED_AIDR_PROPOSAL_FAILED role=%s signal_id=%s",
                    role,
                    candidate.signal_id,
                    exc_info=result,
                )
                continue
            if result is None:
                role_missing_reason.setdefault(
                    role,
                    "The provider proposal did not meet the required live edge.",
                )
                continue
            signal, economics = result
            score = float(signal.validated_edge or 0.0) + 0.05 * float(signal.lower95 or 0.0)
            qualified[role].append((score, signal, economics))

        selected: dict[str, tuple[Any, Any]] = {}
        for role, group in qualified.items():
            group.sort(
                key=lambda item: (
                    -float(item[0]),
                    -float(item[1].weighted_probability),
                    str(item[1].symbol),
                )
            )
            _score, signal, economics = group[0]
            selected[role] = (signal, economics)
            for _other_score, other, _other_economics in group[1:]:
                bot.repository.mark_signal(
                    other.signal_id,
                    status="SKIP_MARKET_ARBITRATION_WITHIN_AIDR_ROLE",
                )

        if not selected:
            return

        cycle_id = str(uuid.uuid4())
        executed_roles: list[str] = []
        for role in AIDR_EXECUTION_ORDER:
            item = selected.get(role)
            if item is None:
                continue
            signal, economics = item
            signal._standardized_cycle_id = cycle_id
            if not refresh_signal_for_execution(bot, signal):
                role_missing_reason[role] = "The qualified role signal expired before transport."
                bot.repository.mark_signal(
                    signal.signal_id,
                    status="SKIP_STANDARDIZED_SIGNAL_EXPIRED",
                    stale=True,
                )
                continue
            continuation._ensure_directional_signal(bot, signal, role=role)
            bot.logger.warning(
                "AIDR_STANDARDIZED_ROLE_SELECTED cycle_id=%s role=%s symbol=%s "
                "contract_type=%s barrier=%s accounts=%s normal_accounts=%s "
                "first_recovery_accounts=%s post_virtual_accounts=%s virtual_accounts=%s",
                cycle_id,
                role,
                signal.symbol,
                signal.contract_type,
                signal.barrier,
                len(scopes[role]),
                len(normal_ids),
                len(recovery_ids),
                len(post_ids),
                len(virtual_ids),
            )
            await aidr._buy_for_scope(
                bot,
                signal,
                economics,
                scopes[role],
                recovery_enabled=(role != continuation.NORMAL_ROLE),
            )
            executed_roles.append(role)

        if executed_roles:
            bot.rf_last_purchase_monotonic = time.monotonic()
        for role in AIDR_EXECUTION_ORDER:
            if not scopes[role] or role in executed_roles:
                continue
            barrier, _recovery = _role_spec(role)
            notify_scope_waiting(
                bot,
                scopes[role],
                strategy="system",
                role=role,
                contract=f"DIGITOVER {barrier}",
                reason_code="aidr_role_not_qualified",
                reason=role_missing_reason.get(
                    role,
                    f"The OVER-{barrier} role did not produce a qualifying proposal.",
                ),
            )

        bot.logger.warning(
            "AIDR_STANDARDIZED_CYCLE_COMPLETE cycle_id=%s executed_roles=%s "
            "normal_accounts=%s recovery_accounts=%s post_virtual_accounts=%s "
            "virtual_accounts=%s role_competition_removed=true",
            cycle_id,
            executed_roles,
            len(normal_ids),
            len(recovery_ids),
            len(post_ids),
            len(virtual_ids),
        )


def _scope_identity(bot: RFDir5TradingBot, managed_id: int) -> tuple[str, str | None, Any | None]:
    for token, account_id in list(getattr(bot, "valid_clients", []) or []):
        try:
            candidate = bot._managed_account_id_for_token(token)
        except Exception:
            candidate = None
        if candidate is not None and int(candidate) == int(managed_id):
            return str(account_id), str(token), getattr(bot, "sessions", {}).get(token)
    account = bot.repository.managed_account(int(managed_id)) or {}
    return str(account.get("label") or f"managed-{managed_id}"), None, None


def _existing_cycle_results(
    bot: RFDir5TradingBot,
    signal_id: str,
    scope_ids: set[int],
) -> tuple[set[int], set[int]]:
    if not scope_ids:
        return set(), set()
    with bot.repository.database.session() as session:
        purchased = {
            int(value)
            for value in session.scalars(
                select(Trade.managed_account_id).where(
                    Trade.signal_id == signal_id,
                    Trade.managed_account_id.in_(sorted(scope_ids)),
                )
            ).all()
            if value is not None
        }
        virtual = {
            int(value)
            for value in session.scalars(
                select(VirtualTrade.managed_account_id).where(
                    VirtualTrade.signal_id == signal_id,
                    VirtualTrade.managed_account_id.in_(sorted(scope_ids)),
                )
            ).all()
            if value is not None
        }
    return purchased, virtual


def _missing_reason(
    bot: RFDir5TradingBot,
    managed_id: int,
    *,
    pending_before: bool,
) -> tuple[str, str, bool]:
    account = bot.repository.managed_account(int(managed_id)) or {}
    status = str(account.get("execution_status") or "").strip().lower()
    reason = str(account.get("execution_status_reason") or "").strip()
    if not bool(account.get("enabled", False)):
        return "account_disabled", reason or "Auto trading is disabled for this account.", False
    if pending_before:
        return (
            "previous_contract_settling",
            "A previous contract was still settling, so this account alone skipped the cycle.",
            True,
        )
    _account_id, _token, session = _scope_identity(bot, managed_id)
    if session is None or not bool(getattr(session, "is_connected", False)):
        return (
            "private_connection_not_ready",
            "The account's private trading connection was not ready for this cycle.",
            True,
        )
    if status not in {"", "active", "connecting", "execution_waiting", "signal_waiting", "cycle_skipped"}:
        return status or "account_blocked", reason or "The account had an account-specific safety block.", False
    return (
        "provider_confirmation_missing",
        "Other eligible accounts completed the cycle, but no provider contract confirmation was registered for this account.",
        True,
    )


def _install_cycle_receipt_notifications() -> None:
    original = RFDir5TradingBot._buy_selected_accounts
    if getattr(original, "_standardized_cycle_receipts", False):
        return

    async def buy_with_receipts(
        self: RFDir5TradingBot,
        signal: Any,
        economics: Any,
    ) -> None:
        scope_ids = _signal_scope_ids(self, signal)
        route = getattr(self, "_multi_strategy_signal_routes", {}).get(
            str(getattr(signal, "signal_id", "") or "")
        )
        strategy = (
            f"{route.family}/{route.side}" if route is not None else "system"
        )
        role = str(getattr(route, "role", "SYSTEM") or "SYSTEM")
        pending_before: dict[int, bool] = {}
        for managed_id in scope_ids:
            _account_id, _token, session = _scope_identity(self, managed_id)
            pending_before[managed_id] = bool(
                session is not None and getattr(session, "pending_contracts", set())
            )

        await original(self, signal, economics)
        if not scope_ids:
            return
        signal_id = str(getattr(signal, "signal_id", "") or "")
        purchased, virtual = _existing_cycle_results(self, signal_id, scope_ids)
        contract = _contract_label(signal)
        for managed_id in sorted(scope_ids):
            account_id, _token, _session = _scope_identity(self, managed_id)
            masked = mask_account_id(account_id) if account_id else f"managed-{managed_id}"
            if managed_id in purchased:
                self.logger.warning(
                    "ACCOUNT_CYCLE_RECEIVED account=%s managed_id=%s signal_id=%s "
                    "strategy=%s role=%s contract=%s market=%s result=provider_confirmed",
                    masked,
                    managed_id,
                    signal_id,
                    strategy,
                    role,
                    contract,
                    getattr(signal, "symbol", ""),
                )
                continue
            if managed_id in virtual:
                self.logger.warning(
                    "ACCOUNT_CYCLE_RECEIVED account=%s managed_id=%s signal_id=%s "
                    "strategy=%s role=%s contract=%s market=%s result=virtual_zero_cost",
                    masked,
                    managed_id,
                    signal_id,
                    strategy,
                    role,
                    contract,
                    getattr(signal, "symbol", ""),
                )
                continue

            reason_code, reason, transient = _missing_reason(
                self,
                managed_id,
                pending_before=bool(pending_before.get(managed_id)),
            )
            if transient:
                _set_account_notice(
                    self,
                    managed_id,
                    "cycle_skipped",
                    f"No {contract} contract on cycle {signal_id[:8]}: {reason}",
                    key=("cycle_skipped", strategy, role, contract, reason_code),
                )
            self.logger.warning(
                "ACCOUNT_CYCLE_NOT_PURCHASED account=%s managed_id=%s signal_id=%s "
                "strategy=%s role=%s contract=%s market=%s reason_code=%s "
                "reason=%s global_execution_continues=true",
                masked,
                managed_id,
                signal_id,
                strategy,
                role,
                contract,
                getattr(signal, "symbol", ""),
                reason_code,
                reason,
            )

    buy_with_receipts._standardized_cycle_receipts = True  # type: ignore[attr-defined]
    RFDir5TradingBot._buy_selected_accounts = buy_with_receipts


def install_standardized_execution_runtime() -> None:
    """Remove cross-account role/strategy competition and expose every miss."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Exact contract groups are independent. The queue key includes contract type,
    # barrier and immutable account scope so OVER-2 cannot supersede OVER-3 and ODD
    # cannot compete with CALL, PUT, EVEN or another user's chosen contract.
    multi._queue_candidate = _queue_standardized_candidate
    multi._arbitrate_multi_strategy = _standardized_multi_strategy_arbitrate

    # System Strategy executes every qualified role in one cadence batch instead
    # of round-robin role fairness. New NORMAL accounts therefore receive OVER-1
    # while recovery accounts receive OVER-3 and post-virtual accounts OVER-4.
    continuation._recovery_aware_arbitrate = _standardized_aidr_arbitrate
    hybrid._arbitrate_digits = _standardized_aidr_arbitrate

    # Install after the transport serialization guard so receipt auditing sees the
    # final outcome of the complete wrapper chain.
    _install_cycle_receipt_notifications()

    RFDir5TradingBot._standardized_execution_runtime_installed = True
    _INSTALLED = True
