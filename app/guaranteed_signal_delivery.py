from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from decimal import Decimal
from typing import Any

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.hybrid_digit_put as hybrid
import app.multi_strategy_runtime as multi
import app.private_websocket_rate_limit as private_ws
import app.standardized_execution_runtime as standardized
import app.standardized_signal_metadata as signal_metadata
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import ClientSession, mask_account_id


_INSTALLED = False
GUARANTEED_SIGNAL_DELIVERY_VERSION = "immediate-qualified-delivery-v2"
IMMEDIATE_SIGNAL_MAX_AGE_SECONDS = max(
    10.0,
    float(os.getenv("IMMEDIATE_SIGNAL_MAX_AGE_SECONDS", "30")),
)
PRIVATE_READY_TIMEOUT_SECONDS = max(
    3.0,
    float(os.getenv("PRIVATE_PURCHASE_READY_TIMEOUT_SECONDS", "2.5")),
)
PRIVATE_RETRY_TIMEOUT_SECONDS = max(
    2.0,
    float(os.getenv("PRIVATE_PURCHASE_RETRY_TIMEOUT_SECONDS", "8")),
)


class _ImmediateTickSequence:
    """Follow the current tick only during one short purchase preparation window."""

    __slots__ = ("bot", "symbol", "fallback", "deadline")

    def __init__(
        self,
        bot: RFDir5TradingBot,
        symbol: str,
        fallback: int,
        *,
        deadline: float,
    ) -> None:
        self.bot = bot
        self.symbol = str(symbol)
        self.fallback = int(fallback)
        self.deadline = float(deadline)

    def value(self) -> int:
        if time.monotonic() > self.deadline:
            return self.fallback
        market = getattr(self.bot, "market_states", {}).get(self.symbol)
        if market is None:
            return self.fallback
        try:
            return int(getattr(market, "tick_sequence", self.fallback))
        except (TypeError, ValueError):
            return self.fallback

    def __int__(self) -> int:
        return self.value()

    def __index__(self) -> int:
        return self.value()

    def __eq__(self, other: object) -> bool:
        try:
            return self.value() == int(other)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __str__(self) -> str:
        return str(self.value())


def _signal_age_seconds(signal: Any) -> float:
    generated = float(getattr(signal, "generated_monotonic", 0.0) or 0.0)
    if not generated:
        return 0.0
    return max(0.0, time.monotonic() - generated)


def _current_tick(bot: RFDir5TradingBot, signal: Any) -> dict[str, Any] | None:
    return standardized._current_tick(
        bot,
        str(getattr(signal, "symbol", "") or ""),
    )


def _apply_current_tick(
    bot: RFDir5TradingBot,
    signal: Any,
    *,
    dynamic_during_preparation: bool,
) -> bool:
    tick = _current_tick(bot, signal)
    if tick is None:
        return False
    symbol = str(getattr(signal, "symbol", "") or "")
    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    sequence = int(tick.get("tick_sequence") or 0)
    signal.reference_entry_quote = quote
    signal.signal_tick_epoch = epoch
    signal.signal_tick_id = bot._tick_identity(symbol, epoch, quote)
    if dynamic_during_preparation:
        signal.tick_sequence = _ImmediateTickSequence(
            bot,
            symbol,
            sequence,
            deadline=time.monotonic() + IMMEDIATE_SIGNAL_MAX_AGE_SECONDS,
        )
    else:
        signal.tick_sequence = sequence
    signal.generated_at = standardized._now_iso()
    return True


def refresh_signal_for_delivery(bot: RFDir5TradingBot, signal: Any) -> bool:
    """Prepare a recent qualified signal for immediate transport, never storage."""

    cycle_id = signal_metadata.standardized_cycle_id(signal)
    if not cycle_id:
        return False
    age = _signal_age_seconds(signal)
    if age > IMMEDIATE_SIGNAL_MAX_AGE_SECONDS:
        bot.logger.warning(
            "IMMEDIATE_SIGNAL_DEADLINE_MISSED signal_id=%s cycle_id=%s "
            "signal_age_ms=%.1f deadline_ms=%.1f held=false",
            str(getattr(signal, "signal_id", "") or ""),
            cycle_id,
            age * 1000.0,
            IMMEDIATE_SIGNAL_MAX_AGE_SECONDS * 1000.0,
        )
        return False
    if not _apply_current_tick(bot, signal, dynamic_during_preparation=True):
        return False
    bot.logger.info(
        "IMMEDIATE_SIGNAL_READY signal_id=%s cycle_id=%s symbol=%s "
        "contract_type=%s barrier=%s original_age_ms=%.1f "
        "held=false next_action=connect_and_buy",
        str(getattr(signal, "signal_id", "") or ""),
        cycle_id,
        str(getattr(signal, "symbol", "") or ""),
        str(getattr(signal, "contract_type", "") or ""),
        str(getattr(signal, "barrier", "") or ""),
        age * 1000.0,
    )
    return True


def _finalize_private_boundary(bot: RFDir5TradingBot, signal: Any) -> bool:
    cycle_id = signal_metadata.standardized_cycle_id(signal)
    age = _signal_age_seconds(signal)
    if not cycle_id or age > IMMEDIATE_SIGNAL_MAX_AGE_SECONDS:
        bot.logger.warning(
            "IMMEDIATE_PRIVATE_BOUNDARY_REJECTED signal_id=%s cycle_id=%s "
            "signal_age_ms=%.1f deadline_ms=%.1f held=false",
            str(getattr(signal, "signal_id", "") or ""),
            cycle_id or "missing",
            age * 1000.0,
            IMMEDIATE_SIGNAL_MAX_AGE_SECONDS * 1000.0,
        )
        return False
    if not _apply_current_tick(bot, signal, dynamic_during_preparation=False):
        return False
    bot.logger.warning(
        "IMMEDIATE_PRIVATE_BUY_BOUNDARY signal_id=%s cycle_id=%s symbol=%s "
        "contract_type=%s barrier=%s tick_sequence=%s signal_age_ms=%.1f "
        "next_action=private_buy",
        str(getattr(signal, "signal_id", "") or ""),
        cycle_id,
        str(getattr(signal, "symbol", "") or ""),
        str(getattr(signal, "contract_type", "") or ""),
        str(getattr(signal, "barrier", "") or ""),
        int(getattr(signal, "tick_sequence", 0) or 0),
        age * 1000.0,
    )
    return True


def _role_matches_account(route: Any, account_route: Any) -> bool:
    if account_route.selection.family != route.family:
        return False
    if account_route.selection.side != route.side:
        return False
    role = str(getattr(route, "role", "") or "").upper()
    mode = str(getattr(account_route, "mode", "") or "").upper()
    if role == "SHARED":
        return True
    if role == "NORMAL":
        return mode == "NORMAL_MODE"
    if role == "RECOVERY":
        return mode == "RECOVERY_PENDING" and int(account_route.split_remaining) <= 0
    if role == "POST_VIRTUAL":
        return mode == "RECOVERY_PENDING" and int(account_route.split_remaining) > 0
    if role == "VIRTUAL":
        return mode == "VIRTUAL_MODE"
    return True


def _refresh_manual_scope(bot: RFDir5TradingBot, signal: Any) -> None:
    route = getattr(bot, "_multi_strategy_signal_routes", {}).get(
        str(getattr(signal, "signal_id", "") or "")
    )
    if route is None:
        return
    before = {int(value) for value in set(getattr(route, "scope_ids", set()) or set())}
    current = {
        int(account_route.managed_id)
        for account_route in multi._strategy_snapshot(bot, force=True)
        if _role_matches_account(route, account_route)
    }
    route.scope_ids = set(current)
    joined = sorted(current - before)
    removed = sorted(before - current)
    if joined or removed:
        bot.logger.warning(
            "STANDARDIZED_SCOPE_REFRESHED signal_id=%s family=%s side=%s role=%s "
            "previous_accounts=%s current_accounts=%s joined_accounts=%s "
            "removed_accounts=%s new_accounts_join_current_cycle=true",
            str(getattr(signal, "signal_id", "") or ""),
            str(getattr(route, "family", "") or ""),
            str(getattr(route, "side", "") or ""),
            str(getattr(route, "role", "") or ""),
            len(before),
            len(current),
            joined,
            removed,
        )


def _live_aidr_scope(bot: RFDir5TradingBot, signal: Any) -> set[int]:
    normal, first_recovery, post_virtual, virtual = aidr._account_recovery_groups(bot)
    try:
        barrier = int(str(getattr(signal, "barrier", "") or "-1"))
    except (TypeError, ValueError):
        barrier = -1
    if barrier == int(aidr.NORMAL_BARRIER):
        return set(normal)
    if barrier == int(aidr.RECOVERY_BARRIER):
        return set(first_recovery)
    if barrier == int(aidr.POST_VIRTUAL_BARRIER):
        return set(post_virtual) | set(virtual)
    return set()


def _role_signal(
    bot: RFDir5TradingBot,
    *,
    symbol: str,
    role: str,
) -> Any | None:
    market = getattr(bot, "market_states", {}).get(symbol)
    tick = standardized._current_tick(bot, symbol)
    if market is None or tick is None:
        return None
    digits = [
        int(value)
        for value in list(getattr(market, "raw_tick_digits", []) or [])
        if 0 <= int(value) <= 9
    ]
    if len(digits) < int(aidr.DEEP_WINDOW):
        return None
    barrier, _recovery = standardized._role_spec(role)
    metrics = aidr._digit_metrics(digits, barrier=int(barrier))
    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    trigger_digits = tuple(digits[-int(aidr.RECENT_WINDOW) :])
    trigger_name = (
        aidr.AIDR_TRIGGER_BASE
        if role == continuation.NORMAL_ROLE
        else aidr.AIDR_TRIGGER_RECOVERY
        if role == continuation.FIRST_RECOVERY_ROLE
        else aidr.AIDR_TRIGGER_POST_VIRTUAL
    )
    return hybrid.DigitSignal(
        signal_id=str(uuid.uuid4()),
        run_id=aidr.AIDR_RUN_ID,
        strategy_version=aidr.AIDR_VERSION,
        symbol=symbol,
        direction=f"OVER_{barrier}",
        contract_type="DIGITOVER",
        duration_ticks=1,
        reference_entry_quote=quote,
        quality_score=max(
            1,
            min(10, int(round(float(metrics["regime_score"]) * 10))),
        ),
        signal_tick_epoch=epoch,
        signal_tick_id=bot._tick_identity(symbol, epoch, quote),
        generated_at=standardized._now_iso(),
        generated_monotonic=time.monotonic(),
        connection_session_id=bot.connection_session_id,
        tick_sequence=int(tick.get("tick_sequence") or 0),
        barrier=str(barrier),
        trigger_name=trigger_name,
        trigger_digits=trigger_digits,
        signal_last_digit=int(trigger_digits[-1]),
        p100=float(metrics["p20"]),
        p500=float(metrics["p100"]),
        p1000=float(metrics["p500"]),
        lower95=float(metrics["alignment"]),
        weighted_probability=float(metrics["weighted"]),
    )


async def _provider_proposal(
    bot: RFDir5TradingBot,
    signal: Any,
) -> tuple[Any, Any] | None:
    try:
        returned_signal, economics = await hybrid._digit_proposal(bot, signal)
    except Exception as exc:
        bot.repository.mark_signal(
            signal.signal_id,
            status="SKIP_PROVIDER_PROPOSAL_EXCEPTION",
        )
        bot.logger.warning(
            "AIDR_SHARED_TRIGGER_PROPOSAL_FAILED signal_id=%s barrier=%s error=%s",
            signal.signal_id,
            signal.barrier,
            type(exc).__name__,
        )
        return None
    if economics is None:
        bot.repository.mark_signal(
            signal.signal_id,
            status="SKIP_INVALID_PROVIDER_PROPOSAL",
        )
        return None
    edge = float(returned_signal.weighted_probability) - float(
        economics.break_even_probability
    )
    standardized._mark_proposal_fields(returned_signal, economics, edge)
    bot.repository.record_proposal(returned_signal, economics)
    return returned_signal, economics


async def _immediate_aidr_arbitrate(bot: RFDir5TradingBot) -> None:
    """One qualified System trigger dispatches OVER-1/3/4 to their account scopes."""

    cfg = bot.test2_config.hybrid_strategy
    await asyncio.sleep(float(getattr(cfg, "candidate_window_ms", 75)) / 1000.0)
    queued = list(getattr(bot, "hybrid_digit_candidates", {}).values())
    bot.hybrid_digit_candidates.clear()
    if not queued:
        return

    async with standardized._cycle_gate(bot):
        bot._prune_stale_pending_contracts("immediate_aidr_pre_proposal")
        if continuation._cadence_blocked(bot, queued):
            return

        scopes = {
            continuation.NORMAL_ROLE: set(),
            continuation.FIRST_RECOVERY_ROLE: set(),
            continuation.POST_VIRTUAL_ROLE: set(),
        }
        normal, recovery, post_virtual, virtual = aidr._account_recovery_groups(bot)
        scopes[continuation.NORMAL_ROLE] = set(normal)
        scopes[continuation.FIRST_RECOVERY_ROLE] = set(recovery)
        scopes[continuation.POST_VIRTUAL_ROLE] = set(post_virtual) | set(virtual)
        if not any(scopes.values()):
            return

        # Start or wake every account session before proposal work. Connection
        # setup then runs in parallel with provider proposal evaluation, and one
        # slow account cannot delay purchases for accounts that are already ready.
        all_scope_ids = set().union(*scopes.values())
        for token, account_id in list(getattr(bot, "valid_clients", []) or []):
            managed_id = bot._managed_account_id_for_token(token)
            if managed_id is not None and int(managed_id) in all_scope_ids:
                _ensure_session(bot, token, account_id)

        fresh = [
            candidate
            for candidate in queued
            if (
                getattr(bot, "market_states", {}).get(str(candidate.symbol)) is not None
                and int(bot.market_states[candidate.symbol].tick_sequence)
                == int(candidate.tick_sequence)
            )
        ]
        if not fresh:
            return

        trigger_results = await asyncio.gather(
            *(
                continuation._proposal_ok(
                    bot,
                    candidate,
                    continuation.AIDR_MINIMUM_LIVE_EDGE,
                )
                for candidate in fresh
            ),
            return_exceptions=True,
        )
        qualified: list[tuple[float, Any, Any]] = []
        for result in trigger_results:
            if isinstance(result, Exception) or result is None:
                continue
            signal, economics = result
            score = float(signal.validated_edge or 0.0) + 0.05 * float(
                signal.lower95 or 0.0
            )
            qualified.append((score, signal, economics))
        if not qualified:
            return

        qualified.sort(
            key=lambda item: (
                -float(item[0]),
                -float(getattr(item[1], "weighted_probability", 0.0) or 0.0),
                str(getattr(item[1], "symbol", "") or ""),
            )
        )
        _score, trigger_signal, _trigger_economics = qualified[0]
        trigger_symbol = str(trigger_signal.symbol)
        trigger_role = continuation._candidate_role(trigger_signal)

        role_entries: list[tuple[str, Any]] = []
        for role in standardized.AIDR_EXECUTION_ORDER:
            if not scopes[role]:
                continue
            signal = _role_signal(bot, symbol=trigger_symbol, role=role)
            if signal is None:
                continue
            bot.repository.record_candidate(signal)
            role_entries.append((role, signal))

        proposal_results = await asyncio.gather(
            *(_provider_proposal(bot, signal) for _role, signal in role_entries),
            return_exceptions=True,
        )
        cycle_id = str(uuid.uuid4())
        dispatched: list[str] = []
        for (role, original_signal), result in zip(
            role_entries,
            proposal_results,
            strict=True,
        ):
            barrier, recovery_enabled = standardized._role_spec(role)
            if isinstance(result, Exception) or result is None:
                standardized.notify_scope_waiting(
                    bot,
                    scopes[role],
                    strategy="system",
                    role=role,
                    contract=f"DIGITOVER {barrier}",
                    reason_code="invalid_provider_proposal",
                    reason="Deriv did not return a usable proposal for this role.",
                )
                continue
            signal, economics = result
            signal._standardized_cycle_id = cycle_id
            if not refresh_signal_for_delivery(bot, signal):
                standardized.notify_scope_waiting(
                    bot,
                    scopes[role],
                    strategy="system",
                    role=role,
                    contract=f"DIGITOVER {barrier}",
                    reason_code="immediate_deadline_missed",
                    reason="The short purchase deadline was missed.",
                )
                continue
            continuation._ensure_directional_signal(bot, signal, role=role)
            bot.logger.warning(
                "AIDR_SHARED_TRIGGER_DISPATCH cycle_id=%s trigger_role=%s role=%s "
                "symbol=%s contract_type=DIGITOVER barrier=%s accounts=%s "
                "independent_role_edge_gate=false next_action=connect_and_buy",
                cycle_id,
                trigger_role,
                role,
                trigger_symbol,
                barrier,
                len(scopes[role]),
            )
            await aidr._buy_for_scope(
                bot,
                signal,
                economics,
                scopes[role],
                recovery_enabled=recovery_enabled,
            )
            dispatched.append(role)

        if dispatched:
            bot.rf_last_purchase_monotonic = time.monotonic()
        bot.logger.warning(
            "AIDR_SHARED_TRIGGER_CYCLE_COMPLETE cycle_id=%s symbol=%s "
            "trigger_role=%s dispatched_roles=%s normal_accounts=%s "
            "first_recovery_accounts=%s post_virtual_accounts=%s",
            cycle_id,
            trigger_symbol,
            trigger_role,
            dispatched,
            len(scopes[continuation.NORMAL_ROLE]),
            len(scopes[continuation.FIRST_RECOVERY_ROLE]),
            len(scopes[continuation.POST_VIRTUAL_ROLE]),
        )


async def _drain_aidr_candidates(bot: RFDir5TradingBot) -> None:
    while getattr(bot, "hybrid_digit_candidates", {}):
        await _immediate_aidr_arbitrate(bot)
        await asyncio.sleep(0)


async def _drain_multi_strategy(bot: RFDir5TradingBot) -> None:
    while getattr(bot, "_multi_strategy_candidates", {}):
        await standardized._standardized_multi_strategy_arbitrate(bot)
        await asyncio.sleep(0)


def _ensure_multi_task(bot: RFDir5TradingBot) -> None:
    task = getattr(bot, "_multi_strategy_task", None)
    if task is not None and not task.done():
        return
    if not getattr(bot, "_multi_strategy_candidates", {}):
        return
    task = asyncio.create_task(
        _drain_multi_strategy(bot),
        name="immediate_multi_strategy_delivery",
    )
    bot._multi_strategy_task = task

    def finished(done: asyncio.Task[Any]) -> None:
        if getattr(bot, "_multi_strategy_task", None) is done:
            bot._multi_strategy_task = None
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            bot.logger.exception("IMMEDIATE_MULTI_STRATEGY_DELIVERY_FAILED")
        if getattr(bot, "_multi_strategy_candidates", {}):
            asyncio.get_running_loop().call_soon(_ensure_multi_task, bot)

    task.add_done_callback(finished)


def _queue_multi_strategy(bot: RFDir5TradingBot, signal: Any) -> None:
    route = getattr(bot, "_multi_strategy_signal_routes", {}).get(
        str(getattr(signal, "signal_id", "") or "")
    )
    if route is None or not set(getattr(route, "scope_ids", set()) or set()):
        return
    key = standardized._queue_key(route, signal)
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
    _ensure_multi_task(bot)


def _ensure_session(
    bot: RFDir5TradingBot,
    token: str,
    account_id: str,
) -> ClientSession:
    session = bot.sessions.get(token)
    if session is None or str(session.account_id) != str(account_id):
        if session is not None and session.task and not session.task.done():
            session.task.cancel()
        session = ClientSession(
            token,
            account_id,
            bot,
            bot._managed_account_id_for_token(token),
            credential=bot._credential_for_token(token),
        )
        bot.sessions[token] = session
    if session.task is None or session.task.done():
        session.task = asyncio.create_task(
            session.connect_and_run(),
            name=f"private_session_{mask_account_id(account_id)}",
        )
    private_ws.wake_private_connection(session)
    return session


async def _ready_accounts(
    bot: RFDir5TradingBot,
    accounts: list[tuple[str, str]],
    *,
    timeout: float,
    phase: str,
) -> tuple[list[tuple[str, str]], dict[str, dict[str, Any]]]:
    sessions = [
        _ensure_session(bot, token, account_id)
        for token, account_id in accounts
    ]
    outcomes = await asyncio.gather(
        *(
            private_ws.wait_until_connected(session, timeout=timeout)
            for session in sessions
        ),
        return_exceptions=True,
    )
    ready: list[tuple[str, str]] = []
    blocked: dict[str, dict[str, Any]] = {}
    for (token, account_id), outcome in zip(accounts, outcomes, strict=True):
        if not isinstance(outcome, Exception) and bool(outcome):
            ready.append((token, account_id))
            continue
        if phase == "grace":
            bot.logger.warning(
                "PRIVATE_CONNECTION_STILL_CONNECTING account=%s "
                "grace_seconds=%.1f ready_accounts_purchase_now=true "
                "this_account_retrying_separately=true",
                mask_account_id(account_id),
                timeout,
            )
        else:
            bot.logger.error(
                "PRIVATE_CONNECTION_PURCHASE_TIMEOUT account=%s "
                "timeout_seconds=%.1f purchase_sent=false "
                "retry_on_next_qualified_cycle=true",
                mask_account_id(account_id),
                timeout,
            )
        blocked[account_id] = {
            "account_id": account_id,
            "error": {
                "code": "PRIVATE_CONNECTION_TIMEOUT",
                "message": "Private connection was not ready before the purchase deadline",
            },
        }
    return ready, blocked


def _is_connection_error(result: dict[str, Any]) -> bool:
    error = result.get("error") or {}
    code = str(error.get("code") or "").upper()
    message = str(error.get("message") or "").lower()
    return code in {"NOT_CONNECTED", "CONNECTION_ERROR"} or any(
        marker in message
        for marker in (
            "private websocket is not connected",
            "not connected",
            "connection closed",
            "connection lost",
        )
    )


def _merge_results(
    accounts: list[tuple[str, str]],
    results: list[dict[str, Any]],
    blocked: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_account = dict(blocked)
    by_account.update(
        {
            str(item.get("account_id") or ""): item
            for item in results
        }
    )
    return [
        by_account.get(
            account_id,
            {
                "account_id": account_id,
                "error": {
                    "code": "PURCHASE_RESULT_MISSING",
                    "message": "No purchase result was returned for this account",
                },
            },
        )
        for _token, account_id in accounts
    ]


def install_guaranteed_signal_delivery() -> None:
    """Install immediate System dispatch and connect-before-buy execution."""

    global _INSTALLED
    if _INSTALLED:
        return

    signal_metadata._METADATA_TTL_SECONDS = max(
        60.0,
        IMMEDIATE_SIGNAL_MAX_AGE_SECONDS * 2.0,
    )
    standardized.MAX_STANDARDIZED_SIGNAL_AGE_SECONDS = (
        IMMEDIATE_SIGNAL_MAX_AGE_SECONDS
    )
    standardized.refresh_signal_for_execution = refresh_signal_for_delivery

    original_buy_scope = aidr._buy_for_scope

    async def buy_for_current_scope(
        bot: RFDir5TradingBot,
        signal: Any,
        economics: Any,
        managed_ids: set[int],
        *,
        recovery_enabled: bool,
    ) -> None:
        current = _live_aidr_scope(bot, signal)
        before = {int(value) for value in set(managed_ids or set())}
        joined = sorted(current - before)
        removed = sorted(before - current)
        if joined or removed:
            bot.logger.warning(
                "AIDR_SCOPE_REFRESHED signal_id=%s barrier=%s "
                "previous_accounts=%s current_accounts=%s joined_accounts=%s "
                "removed_accounts=%s new_accounts_join_current_cycle=true",
                str(getattr(signal, "signal_id", "") or ""),
                str(getattr(signal, "barrier", "") or ""),
                len(before),
                len(current),
                joined,
                removed,
            )
        await original_buy_scope(
            bot,
            signal,
            economics,
            current,
            recovery_enabled=recovery_enabled,
        )

    aidr._buy_for_scope = buy_for_current_scope

    original_buy = RFDir5TradingBot._buy_selected_accounts

    async def immediate_buy(
        self: RFDir5TradingBot,
        signal: Any,
        economics: Any,
    ) -> None:
        if signal_metadata.standardized_cycle_id(signal):
            _refresh_manual_scope(self, signal)
            if not refresh_signal_for_delivery(self, signal):
                return
        await original_buy(self, signal, economics)

    immediate_buy._immediate_signal_delivery = True  # type: ignore[attr-defined]
    RFDir5TradingBot._buy_selected_accounts = immediate_buy

    original_purchase = RFDir5TradingBot._purchase_accounts_by_stake

    async def connect_then_purchase(
        self: RFDir5TradingBot,
        *,
        signal: Any,
        eligible_accounts: list[tuple[str, str]],
        stake_by_token: dict[str, float],
        pre_trade_profit_ratio: float = 0.0,
    ) -> list[dict[str, Any]]:
        if not eligible_accounts:
            return []

        # Give sessions a short grace window, then purchase every ready account
        # immediately. Accounts still connecting are retried separately and never
        # hold back the ready majority.
        ready, initially_blocked = await _ready_accounts(
            self,
            eligible_accounts,
            timeout=PRIVATE_READY_TIMEOUT_SECONDS,
            phase="grace",
        )
        results_by_account: dict[str, dict[str, Any]] = {}

        async def purchase_batch(
            batch: list[tuple[str, str]],
        ) -> list[dict[str, Any]]:
            if not batch:
                return []
            if signal_metadata.standardized_cycle_id(signal):
                if not _finalize_private_boundary(self, signal):
                    return [
                        {
                            "account_id": account_id,
                            "error": {
                                "code": "IMMEDIATE_DEADLINE_MISSED",
                                "message": "The immediate purchase deadline was missed",
                            },
                        }
                        for _token, account_id in batch
                    ]
            return await original_purchase(
                self,
                signal=signal,
                eligible_accounts=batch,
                stake_by_token={
                    token: stake_by_token[token]
                    for token, _account_id in batch
                },
                pre_trade_profit_ratio=pre_trade_profit_ratio,
            )

        first_results = await purchase_batch(ready)
        for item in first_results:
            results_by_account[str(item.get("account_id") or "")] = item

        connection_failed = [
            (token, account_id)
            for token, account_id in ready
            if _is_connection_error(results_by_account.get(account_id, {}))
        ]
        initially_unready = [
            (token, account_id)
            for token, account_id in eligible_accounts
            if account_id in initially_blocked
        ]
        retry_candidates = list(
            {
                account_id: (token, account_id)
                for token, account_id in (
                    initially_unready + connection_failed
                )
            }.values()
        )

        final_blocked = dict(initially_blocked)
        if retry_candidates:
            retry_ready, retry_blocked = await _ready_accounts(
                self,
                retry_candidates,
                timeout=PRIVATE_RETRY_TIMEOUT_SECONDS,
                phase="retry",
            )
            final_blocked.update(retry_blocked)
            if retry_ready:
                self.logger.warning(
                    "PRIVATE_BUY_CONNECTION_RETRY signal_id=%s accounts=%s retry=1",
                    str(getattr(signal, "signal_id", "") or ""),
                    len(retry_ready),
                )
                retry_results = await purchase_batch(retry_ready)
                for item in retry_results:
                    account_id = str(item.get("account_id") or "")
                    results_by_account[account_id] = item
                    if not item.get("error"):
                        final_blocked.pop(account_id, None)

        return _merge_results(
            eligible_accounts,
            list(results_by_account.values()),
            final_blocked,
        )

    connect_then_purchase._connect_before_private_buy = True  # type: ignore[attr-defined]
    RFDir5TradingBot._purchase_accounts_by_stake = connect_then_purchase

    multi._queue_candidate = _queue_multi_strategy
    standardized._queue_standardized_candidate = _queue_multi_strategy
    standardized._standardized_aidr_arbitrate = _immediate_aidr_arbitrate
    hybrid._arbitrate_digits = _drain_aidr_candidates
    continuation._recovery_aware_arbitrate = _drain_aidr_candidates

    RFDir5TradingBot._guaranteed_signal_delivery_installed = True
    RFDir5TradingBot._immediate_purchase_runtime_installed = True
    _INSTALLED = True
    logging.getLogger(__name__).warning(
        "IMMEDIATE_PURCHASE_RUNTIME_INSTALLED version=%s signal_deadline_seconds=%.1f "
        "connect_before_buy=true reconnect_retry=1 shared_system_trigger=true "
        "infinite_signal_holding=false",
        GUARANTEED_SIGNAL_DELIVERY_VERSION,
        IMMEDIATE_SIGNAL_MAX_AGE_SECONDS,
    )
