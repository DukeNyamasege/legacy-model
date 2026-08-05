from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import replace
from typing import Any

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.hybrid_digit_put as hybrid
import app.multi_strategy_runtime as multi
import app.standardized_execution_runtime as standardized
from app.repositories.rf_dir5_repository import NORMAL_MODE, RECOVERY_PENDING, VIRTUAL_MODE
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
SHARED_CLOCK_VERSION = "aidr-shared-signal-clock-v1"
_ORIGINAL_BUY_FOR_SCOPE = aidr._buy_for_scope


def _all_strategy_accounts(bot: RFDir5TradingBot) -> list[tuple[str, str, int]]:
    """Return every enabled account after its persisted strategy is resolved."""

    return [
        (route.token, route.account_id, int(route.managed_id))
        for route in multi._strategy_snapshot(bot, force=True)
    ]


def _disable_parallel_manual_tick_generator(
    _bot: RFDir5TradingBot,
    _tick_data: dict[str, Any],
) -> None:
    """Manual contracts are routed only after the System AIDR clock qualifies."""

    return None


def _contract_spec(selection: Any) -> tuple[str, str, str]:
    family = str(selection.family)
    side = str(selection.side)
    if family == "digits":
        prediction = int(selection.prediction)
        if side == "over":
            return "DIGITOVER", f"OVER_{prediction}", str(prediction)
        return "DIGITUNDER", f"UNDER_{prediction}", str(prediction)
    if family == "parity":
        return (
            "DIGITEVEN" if side == "even" else "DIGITODD",
            side.upper(),
            "",
        )
    if family == "direction":
        return (
            "CALL" if side == "rise" else "PUT",
            "RISE" if side == "rise" else "FALL",
            "",
        )
    raise ValueError(f"Unsupported manual strategy {family}/{side}")


def _manual_metrics(
    bot: RFDir5TradingBot,
    source: Any,
    selection: Any,
) -> dict[str, float]:
    """Describe the selected contract without creating a second entry gate.

    These values are proposal/audit metadata only. Qualification has already been
    completed by the System AIDR clock and is deliberately not repeated here.
    """

    family = str(selection.family)
    side = str(selection.side)
    market = bot.market_states.get(str(source.symbol))
    digits = [
        int(value)
        for value in list(getattr(market, "raw_tick_digits", []) or [])
        if 0 <= int(value) <= 9
    ]
    if family == "digits" and digits:
        prediction = int(selection.prediction)
        predicate = (
            (lambda digit, barrier=prediction: digit > barrier)
            if side == "over"
            else (lambda digit, barrier=prediction: digit < barrier)
        )
        return multi._digit_statistics(digits, predicate)
    if family == "parity" and digits:
        predicate = (
            (lambda digit: digit % 2 == 0)
            if side == "even"
            else (lambda digit: digit % 2 == 1)
        )
        return multi._digit_statistics(digits, predicate)

    # Rise/Fall receives the same AIDR entry time by explicit user choice. A
    # neutral probability is retained as metadata; it is not an extra signal gate.
    return {
        "p20": 0.50,
        "p50": 0.50,
        "p100": 0.50,
        "p500": 0.50,
        "weighted": 0.50,
        "alignment": 0.50,
    }


def _trigger_name(selection: Any, role: str) -> str:
    family = str(selection.family).upper()[:6]
    side = str(selection.side).upper()[:6]
    role_code = {
        continuation.NORMAL_ROLE: "N",
        continuation.FIRST_RECOVERY_ROLE: "R3",
        continuation.POST_VIRTUAL_ROLE: "R4",
    }.get(str(role), "S")
    return f"AIDRCLK-{family}-{side}-{role_code}"[:30]


def _manual_clone(
    bot: RFDir5TradingBot,
    source: Any,
    selection: Any,
    *,
    role: str,
) -> tuple[Any, float]:
    contract_type, direction, barrier = _contract_spec(selection)
    metrics = _manual_metrics(bot, source, selection)
    clone = replace(
        source,
        signal_id=str(uuid.uuid4()),
        strategy_version=SHARED_CLOCK_VERSION,
        direction=direction,
        contract_type=contract_type,
        barrier=barrier,
        trigger_name=_trigger_name(selection, role),
        p100=float(metrics["p20"]),
        p500=float(metrics["p100"]),
        p1000=float(metrics["p500"]),
        lower95=float(metrics["alignment"]),
        weighted_probability=float(metrics["weighted"]),
        consumed=False,
        proposal_ask_price=None,
        proposal_payout=None,
        break_even_probability=None,
        validated_edge=None,
    )
    clone._standardized_cycle_id = (
        str(getattr(source, "_standardized_cycle_id", "") or "")
        or str(uuid.uuid4())
    )
    return clone, float(metrics["weighted"])


def _delivery_role(account_route: Any, source_role: str) -> str:
    """Use role names understood by guaranteed-delivery scope refresh."""

    if source_role == continuation.NORMAL_ROLE:
        return "NORMAL"
    if source_role == continuation.FIRST_RECOVERY_ROLE:
        return "RECOVERY"
    if str(account_route.mode) == VIRTUAL_MODE:
        return "VIRTUAL"
    return "POST_VIRTUAL"


def _manual_groups(
    bot: RFDir5TradingBot,
    managed_ids: set[int],
    *,
    source_role: str,
) -> list[tuple[Any, set[int], str]]:
    grouped: dict[
        tuple[str, str, int | None, str],
        tuple[Any, set[int], str],
    ] = {}
    for account_route in multi._strategy_snapshot(bot, force=True):
        managed_id = int(account_route.managed_id)
        if managed_id not in managed_ids:
            continue
        selection = account_route.selection
        if str(selection.family) == "system":
            continue
        delivery_role = _delivery_role(account_route, source_role)
        key = (
            str(selection.family),
            str(selection.side),
            int(selection.prediction) if selection.prediction is not None else None,
            delivery_role,
        )
        if key not in grouped:
            grouped[key] = (selection, set(), delivery_role)
        grouped[key][1].add(managed_id)
    return list(grouped.values())


def _system_ids(bot: RFDir5TradingBot, managed_ids: set[int]) -> set[int]:
    return {
        int(route.managed_id)
        for route in multi._strategy_snapshot(bot, force=True)
        if int(route.managed_id) in managed_ids
        and str(route.selection.family) == "system"
    }


def _route_role(signal: Any) -> str:
    return continuation._candidate_role(signal)


def _notify_group_skip(
    bot: RFDir5TradingBot,
    scope_ids: set[int],
    *,
    selection: Any,
    role: str,
    contract: str,
    reason_code: str,
    reason: str,
) -> None:
    standardized.notify_scope_waiting(
        bot,
        scope_ids,
        strategy=f"{selection.family}/{selection.side}",
        role=role,
        contract=contract,
        reason_code=reason_code,
        reason=reason,
    )


async def _shared_clock_buy_for_scope(
    bot: RFDir5TradingBot,
    source: Any,
    source_economics: Any,
    managed_ids: set[int],
    *,
    recovery_enabled: bool,
) -> None:
    """Use one AIDR opportunity, then route each account's chosen contract."""

    scope = {int(value) for value in managed_ids}
    if not scope:
        bot.repository.mark_signal(source.signal_id, status="SKIP_NO_SCOPE_ACCOUNTS")
        return

    source_role = _route_role(source)
    system_scope = _system_ids(bot, scope)
    manual_groups = _manual_groups(bot, scope, source_role=source_role)
    accounted = set(system_scope)
    for _selection, ids, _delivery_role_value in manual_groups:
        accounted.update(ids)

    unknown = scope - accounted
    if unknown:
        standardized.notify_scope_waiting(
            bot,
            unknown,
            strategy="unknown",
            role=source_role,
            contract="none",
            reason_code="strategy_route_missing",
            reason="The account had no resolvable persisted strategy route.",
        )

    prepared: list[tuple[Any, set[int], str, Any, float, str]] = []
    for selection, ids, delivery_role in manual_groups:
        clone, predicted = _manual_clone(
            bot,
            source,
            selection,
            role=source_role,
        )
        contract = standardized._contract_label(clone)
        bot.repository.record_candidate(clone)
        bot._multi_strategy_signal_routes[clone.signal_id] = multi.CandidateRoute(
            family=str(selection.family),
            side=str(selection.side),
            role=delivery_role,
            scope_ids=set(ids),
            predicted_probability=predicted,
            # The System AIDR gate is the qualification authority. This value is
            # intentionally informational rather than a second manual entry gate.
            minimum_edge=0.0,
            created_monotonic=time.monotonic(),
        )
        prepared.append(
            (selection, set(ids), delivery_role, clone, predicted, contract)
        )

    proposal_results = await asyncio.gather(
        *(
            multi._proposal_for(bot, clone, predicted)
            for _selection, _ids, _delivery_role_value, clone, predicted, _contract in prepared
        ),
        return_exceptions=True,
    )

    # Preserve the original System Strategy contract sequence exactly.
    if system_scope:
        await _ORIGINAL_BUY_FOR_SCOPE(
            bot,
            source,
            source_economics,
            system_scope,
            recovery_enabled=recovery_enabled,
        )

    for prepared_item, result in zip(prepared, proposal_results, strict=True):
        selection, ids, delivery_role, clone, predicted, contract = prepared_item
        if isinstance(result, Exception):
            bot.repository.mark_signal(
                clone.signal_id,
                status="SKIP_SHARED_CLOCK_PROPOSAL_EXCEPTION",
            )
            _notify_group_skip(
                bot,
                ids,
                selection=selection,
                role=delivery_role,
                contract=contract,
                reason_code="shared_clock_proposal_exception",
                reason=f"Deriv proposal failed with {type(result).__name__}.",
            )
            bot.logger.error(
                "SHARED_CLOCK_PROPOSAL_FAILED family=%s side=%s source_role=%s "
                "account_role=%s signal_id=%s error=%s",
                selection.family,
                selection.side,
                source_role,
                delivery_role,
                clone.signal_id,
                type(result).__name__,
            )
            continue
        if result is None:
            bot.repository.mark_signal(
                clone.signal_id,
                status="SKIP_SHARED_CLOCK_INVALID_PROPOSAL",
            )
            _notify_group_skip(
                bot,
                ids,
                selection=selection,
                role=delivery_role,
                contract=contract,
                reason_code="shared_clock_invalid_proposal",
                reason="Deriv did not return a usable proposal for the selected contract.",
            )
            continue

        economics = result
        break_even = float(economics.break_even_probability)
        clone.proposal_ask_price = float(economics.stake)
        clone.proposal_payout = float(economics.payout)
        clone.break_even_probability = break_even
        clone.validated_edge = float(predicted) - break_even
        bot.repository.record_proposal(clone, economics)
        bot.logger.warning(
            "SHARED_SYSTEM_CLOCK_ROUTE source_signal=%s routed_signal=%s "
            "source_role=%s account_role=%s family=%s side=%s symbol=%s "
            "contract_type=%s barrier=%s accounts=%s "
            "entry_gate=system_aidr manual_tick_generator=false",
            source.signal_id,
            clone.signal_id,
            source_role,
            delivery_role,
            selection.family,
            selection.side,
            clone.symbol,
            clone.contract_type,
            clone.barrier,
            len(ids),
        )
        await _ORIGINAL_BUY_FOR_SCOPE(
            bot,
            clone,
            economics,
            ids,
            recovery_enabled=recovery_enabled,
        )

    bot.logger.warning(
        "SHARED_SYSTEM_CLOCK_CYCLE_COMPLETE source_signal=%s role=%s "
        "system_accounts=%s manual_groups=%s manual_accounts=%s unknown_accounts=%s",
        source.signal_id,
        source_role,
        len(system_scope),
        len(manual_groups),
        sum(len(ids) for _selection, ids, _delivery_role_value in manual_groups),
        len(unknown),
    )


def install_shared_system_strategy_clock() -> None:
    """Make AIDR the only entry clock for every selectable contract family."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Remove the independent per-tick manual signal engine that generated
    # thousands of superseded candidates. Manual contracts now appear only after
    # the original System Strategy has passed its role, cadence and live-edge gate.
    multi._queue_non_aidr_signals = _disable_parallel_manual_tick_generator

    # AIDR must see all enabled accounts so a manual-only deployment still has an
    # entry clock. Contract choice is partitioned later by _shared_clock_buy_for_scope.
    aidr._enabled_accounts = _all_strategy_accounts
    multi._filter_aidr_over_accounts = _all_strategy_accounts

    # Every final AIDR arbitrator calls the module attribute at execution time.
    # Patch both names for compatibility with any imported legacy path.
    aidr._buy_for_scope = _shared_clock_buy_for_scope
    continuation._buy_for_scope = _shared_clock_buy_for_scope

    RFDir5TradingBot._shared_system_strategy_clock_installed = True
    RFDir5TradingBot._shared_system_strategy_clock_version = SHARED_CLOCK_VERSION
    _INSTALLED = True
