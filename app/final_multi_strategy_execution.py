from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.multi_strategy_runtime as multi
import app.shared_system_strategy_clock as shared
import app.standardized_execution_runtime as standardized
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_VERSION = "multi-strategy-fast-fanout-v1"


def _groups_from_snapshot(
    routes: list[Any],
    scope: set[int],
    *,
    source_role: str,
) -> tuple[set[int], list[tuple[Any, set[int], str]], set[int]]:
    """Resolve every account from one database snapshot.

    The former router performed separate forced snapshots for System and manual
    accounts. Apart from extra database work, a preference change between those
    reads could leave an account in neither group. This helper makes one snapshot
    authoritative for the whole qualified cycle.
    """

    system_scope: set[int] = set()
    grouped: dict[
        tuple[str, str, int | None, str],
        tuple[Any, set[int], str],
    ] = {}
    accounted: set[int] = set()

    for account_route in routes:
        managed_id = int(account_route.managed_id)
        if managed_id not in scope:
            continue
        selection = account_route.selection
        family = str(getattr(selection, "family", "") or "")
        if family == "system":
            system_scope.add(managed_id)
            accounted.add(managed_id)
            continue
        if family not in {"digits", "parity", "direction"}:
            continue

        delivery_role = shared._delivery_role(account_route, source_role)
        prediction_raw = getattr(selection, "prediction", None)
        prediction = int(prediction_raw) if prediction_raw is not None else None
        key = (
            family,
            str(getattr(selection, "side", "") or ""),
            prediction,
            delivery_role,
        )
        if key not in grouped:
            grouped[key] = (selection, set(), delivery_role)
        grouped[key][1].add(managed_id)
        accounted.add(managed_id)

    return system_scope, list(grouped.values()), scope - accounted


def _manual_failure(
    bot: RFDir5TradingBot,
    *,
    source_role: str,
    selection: Any,
    ids: set[int],
    delivery_role: str,
    contract: str,
    signal: Any | None,
    reason_code: str,
    error: BaseException | None = None,
) -> None:
    signal_id = str(getattr(signal, "signal_id", "") or "")
    if signal_id:
        try:
            bot.repository.mark_signal(
                signal_id,
                status=(
                    "SKIP_SHARED_CLOCK_PROPOSAL_EXCEPTION"
                    if error is not None
                    else "SKIP_SHARED_CLOCK_INVALID_PROPOSAL"
                ),
            )
        except Exception:
            pass
    shared._notify_group_skip(
        bot,
        ids,
        selection=selection,
        role=delivery_role,
        contract=contract,
        reason_code=reason_code,
        reason=(
            f"Deriv proposal failed with {type(error).__name__}."
            if error is not None
            else "Deriv did not return a usable proposal for the selected contract."
        ),
    )
    bot.logger.error(
        "MANUAL_STRATEGY_GROUP_FAILED family=%s side=%s source_role=%s "
        "account_role=%s accounts=%s signal_id=%s reason_code=%s error_type=%s "
        "other_strategy_groups_continue=true system_execution_affected=false",
        getattr(selection, "family", "unknown"),
        getattr(selection, "side", "unknown"),
        source_role,
        delivery_role,
        len(ids),
        signal_id or "-",
        reason_code,
        type(error).__name__ if error is not None else "-",
    )


async def _fast_shared_clock_buy_for_scope(
    bot: RFDir5TradingBot,
    source: Any,
    source_economics: Any,
    managed_ids: set[int],
    *,
    recovery_enabled: bool,
) -> None:
    """Fan one AIDR opportunity out without manual strategies blocking System."""

    scope = {int(value) for value in managed_ids}
    if not scope:
        bot.repository.mark_signal(source.signal_id, status="SKIP_NO_SCOPE_ACCOUNTS")
        return

    source_role = shared._route_role(source)
    snapshot_started = time.monotonic()
    routes = multi._strategy_snapshot(bot, force=True)
    system_scope, manual_groups, unknown = _groups_from_snapshot(
        routes,
        scope,
        source_role=source_role,
    )
    snapshot_ms = (time.monotonic() - snapshot_started) * 1000.0

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

    prepared: list[
        tuple[Any, set[int], str, Any, float, str, asyncio.Task[Any]]
    ] = []
    for selection, ids, delivery_role in manual_groups:
        clone: Any | None = None
        contract = f"{getattr(selection, 'family', 'unknown')}/{getattr(selection, 'side', 'unknown')}"
        try:
            clone, predicted = shared._manual_clone(
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
                predicted_probability=float(predicted),
                minimum_edge=0.0,
                created_monotonic=time.monotonic(),
            )
            proposal_task = asyncio.create_task(
                multi._proposal_for(bot, clone, predicted),
                name=f"manual_proposal_{clone.signal_id}",
            )
            prepared.append(
                (
                    selection,
                    set(ids),
                    delivery_role,
                    clone,
                    float(predicted),
                    contract,
                    proposal_task,
                )
            )
        except Exception as exc:
            _manual_failure(
                bot,
                source_role=source_role,
                selection=selection,
                ids=set(ids),
                delivery_role=delivery_role,
                contract=contract,
                signal=clone,
                reason_code="manual_group_prepare_exception",
                error=exc,
            )

    # The source proposal is already valid. Do not make System accounts wait for
    # unrelated manual proposal round trips. Manual proposal tasks continue on the
    # shared public socket while the REST System purchase is dispatched.
    if system_scope:
        shared._register_provider_verified_contract(bot, source, system_scope)
        bot.logger.warning(
            "SYSTEM_PURCHASE_DISPATCH_IMMEDIATE source_signal=%s role=%s accounts=%s "
            "manual_proposals_in_flight=%s strategy_snapshot_ms=%.2f "
            "system_waits_for_manual=false",
            source.signal_id,
            source_role,
            len(system_scope),
            len(prepared),
            snapshot_ms,
        )
        try:
            await shared._exact_scope_buy(
                bot,
                source,
                source_economics,
                system_scope,
                recovery_enabled=recovery_enabled,
            )
        except Exception as exc:
            standardized.notify_scope_waiting(
                bot,
                system_scope,
                strategy="system/system",
                role=source_role,
                contract=standardized._contract_label(source),
                reason_code="system_purchase_exception",
                reason=f"System purchase failed with {type(exc).__name__}.",
            )
            bot.logger.exception(
                "SYSTEM_STRATEGY_GROUP_FAILED source_signal=%s role=%s accounts=%s "
                "error_type=%s manual_strategy_groups_continue=true",
                source.signal_id,
                source_role,
                len(system_scope),
                type(exc).__name__,
            )

    # Every manual proposal was already started above. One invalid contract,
    # provider error or account group cannot cancel the remaining groups.
    for (
        selection,
        ids,
        delivery_role,
        clone,
        predicted,
        contract,
        proposal_task,
    ) in prepared:
        try:
            economics = await proposal_task
            if economics is None:
                _manual_failure(
                    bot,
                    source_role=source_role,
                    selection=selection,
                    ids=ids,
                    delivery_role=delivery_role,
                    contract=contract,
                    signal=clone,
                    reason_code="shared_clock_invalid_proposal",
                )
                continue

            break_even = float(economics.break_even_probability)
            clone.proposal_ask_price = float(economics.stake)
            clone.proposal_payout = float(economics.payout)
            clone.break_even_probability = break_even
            clone.validated_edge = float(predicted) - break_even
            bot.repository.record_proposal(clone, economics)
            shared._register_provider_verified_contract(bot, clone, ids)
            bot.logger.warning(
                "SHARED_SYSTEM_CLOCK_ROUTE source_signal=%s routed_signal=%s "
                "source_role=%s account_role=%s family=%s side=%s symbol=%s "
                "contract_type=%s barrier=%s accounts=%s entry_gate=system_aidr "
                "manual_tick_generator=false independent_group=true",
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
            await shared._exact_scope_buy(
                bot,
                clone,
                economics,
                ids,
                recovery_enabled=recovery_enabled,
            )
        except Exception as exc:
            _manual_failure(
                bot,
                source_role=source_role,
                selection=selection,
                ids=ids,
                delivery_role=delivery_role,
                contract=contract,
                signal=clone,
                reason_code="manual_group_execution_exception",
                error=exc,
            )

    bot.logger.warning(
        "SHARED_SYSTEM_CLOCK_CYCLE_COMPLETE source_signal=%s role=%s "
        "system_accounts=%s manual_groups=%s manual_accounts=%s unknown_accounts=%s "
        "single_strategy_snapshot=true isolated_manual_groups=true",
        source.signal_id,
        source_role,
        len(system_scope),
        len(manual_groups),
        sum(len(ids) for _selection, ids, _role in manual_groups),
        len(unknown),
    )


def install_final_multi_strategy_execution() -> None:
    """Install after all AIDR, shared-clock and REST bulk wrappers."""

    global _INSTALLED
    aidr._buy_for_scope = _fast_shared_clock_buy_for_scope
    continuation._buy_for_scope = _fast_shared_clock_buy_for_scope
    RFDir5TradingBot._final_multi_strategy_execution_installed = True
    RFDir5TradingBot._final_multi_strategy_execution_version = _VERSION
    if not _INSTALLED:
        logging.getLogger(__name__).warning(
            "MULTI_STRATEGY_FAST_PATH_INSTALLED version=%s "
            "system_not_blocked_by_manual_proposals=true single_snapshot=true "
            "isolated_manual_groups=true exact_saved_contract=true",
            _VERSION,
        )
    _INSTALLED = True
