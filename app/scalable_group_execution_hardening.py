from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.hybrid_digit_put as hybrid
import app.scalable_group_execution as grouped
import app.standardized_execution_runtime as standardized
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
SCALABLE_ROLE_HARDENING_VERSION = "fresh-websocket-role-subcycles-v2"


async def _fresh_role_subcycle(
    bot: RFDir5TradingBot,
    *,
    parent_cycle_id: str,
    trigger_role: str,
    role: str,
    symbol: str,
    scope: set[int],
) -> tuple[str, str]:
    """Create the role proposal only when this role is ready to execute."""

    barrier, _recovery_enabled = standardized._role_spec(role)
    started = time.monotonic()
    bot.logger.warning(
        "AIDR_ROLE_SUBCYCLE_STARTED parent_cycle_id=%s trigger_role=%s "
        "role=%s symbol=%s barrier=%s accounts=%s fresh_proposal=true "
        "transport=PRIVATE_WEBSOCKET_ONLY",
        parent_cycle_id,
        trigger_role,
        role,
        symbol,
        barrier,
        len(scope),
    )
    try:
        proposal = await grouped._role_proposal_with_retry(
            bot,
            role=role,
            symbol=symbol,
        )
    except Exception as exc:
        bot.logger.exception(
            "AIDR_ROLE_SUBCYCLE_PROPOSAL_FAILED parent_cycle_id=%s role=%s "
            "symbol=%s barrier=%s error=%s global_execution_continues=true",
            parent_cycle_id,
            role,
            symbol,
            barrier,
            type(exc).__name__,
        )
        return role, f"proposal_exception_{type(exc).__name__}"

    if proposal is None:
        standardized.notify_scope_waiting(
            bot,
            scope,
            strategy="system",
            role=role,
            contract=f"DIGITOVER {barrier}",
            reason_code="provider_proposal_unavailable",
            reason=(
                "The role's fresh Deriv proposal was unavailable after one "
                "bounded immediate retry."
            ),
        )
        return role, "provider_proposal_unavailable"

    signal, economics = proposal
    returned_role, status = await grouped._dispatch_aidr_role(
        bot,
        parent_cycle_id=parent_cycle_id,
        role=role,
        signal=signal,
        economics=economics,
        scope=scope,
    )
    bot.logger.warning(
        "AIDR_ROLE_SUBCYCLE_COMPLETE parent_cycle_id=%s role=%s barrier=%s "
        "accounts=%s result=%s elapsed_ms=%.1f "
        "signal_created_for_this_subcycle=true transport=PRIVATE_WEBSOCKET_ONLY",
        parent_cycle_id,
        role,
        barrier,
        len(scope),
        status,
        (time.monotonic() - started) * 1000.0,
    )
    return returned_role, status


async def _fresh_grouped_aidr_arbitrate(bot: RFDir5TradingBot) -> None:
    """Run every active System role as its own fresh WebSocket subcycle.

    The parent System opportunity chooses one market. Each active role creates a
    new current proposal immediately before its own private-WebSocket dispatch, so
    OVER-1, OVER-3 and OVER-4 cannot expire while waiting for another role. Role
    failures remain account-group scoped and never stop the worker.
    """

    cfg = bot.test2_config.hybrid_strategy
    await asyncio.sleep(float(getattr(cfg, "candidate_window_ms", 75)) / 1000.0)
    queued = list(getattr(bot, "hybrid_digit_candidates", {}).values())
    bot.hybrid_digit_candidates.clear()
    if not queued:
        return

    async with standardized._cycle_gate(bot):
        bot._prune_stale_pending_contracts("fresh_websocket_aidr_pre_proposal")
        if continuation._cadence_blocked(bot, queued):
            return

        normal, recovery, post_virtual, virtual = aidr._account_recovery_groups(bot)
        scopes = {
            continuation.NORMAL_ROLE: set(normal),
            continuation.FIRST_RECOVERY_ROLE: set(recovery),
            continuation.POST_VIRTUAL_ROLE: set(post_virtual) | set(virtual),
        }
        if not any(scopes.values()):
            return

        # Every financial account uses its own private WebSocket. Warm missing
        # sessions while the shared public trigger proposal is evaluated.
        all_scope_ids = set().union(*scopes.values())
        for token, account_id in list(getattr(bot, "valid_clients", []) or []):
            managed_id = bot._managed_account_id_for_token(token)
            if managed_id is not None and int(managed_id) in all_scope_ids:
                grouped.immediate._ensure_session(bot, token, account_id)

        fresh = [
            candidate
            for candidate in queued
            if (
                getattr(bot, "market_states", {}).get(str(candidate.symbol))
                is not None
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
        _score, trigger_signal, _economics = qualified[0]
        symbol = str(trigger_signal.symbol)
        trigger_role = continuation._candidate_role(trigger_signal)
        parent_cycle_id = str(uuid.uuid4())
        result_by_role: dict[str, str] = {}

        # Role order stays deterministic because registration state is shared, but
        # the accounts inside each role are dispatched in bounded WebSocket groups.
        # Each role receives a newly created proposal immediately before transport.
        for role in standardized.AIDR_EXECUTION_ORDER:
            scope = scopes[role]
            if not scope:
                continue
            try:
                _returned_role, status = await _fresh_role_subcycle(
                    bot,
                    parent_cycle_id=parent_cycle_id,
                    trigger_role=trigger_role,
                    role=role,
                    symbol=symbol,
                    scope=scope,
                )
            except Exception as exc:
                status = f"subcycle_exception_{type(exc).__name__}"
                bot.logger.exception(
                    "AIDR_ROLE_SUBCYCLE_FAILED parent_cycle_id=%s role=%s "
                    "symbol=%s accounts=%s error=%s global_execution_continues=true",
                    parent_cycle_id,
                    role,
                    symbol,
                    len(scope),
                    type(exc).__name__,
                )
            result_by_role[role] = status
            barrier, _recovery = standardized._role_spec(role)
            bot.logger.warning(
                "AIDR_ROLE_DISPATCH_RESULT parent_cycle_id=%s trigger_role=%s "
                "role=%s barrier=%s accounts=%s result=%s "
                "transport=PRIVATE_WEBSOCKET_ONLY global_execution_continues=true",
                parent_cycle_id,
                trigger_role,
                role,
                barrier,
                len(scope),
                status,
            )

        if any(status == "submitted" for status in result_by_role.values()):
            bot.rf_last_purchase_monotonic = time.monotonic()

        bot.logger.warning(
            "AIDR_GROUPED_CYCLE_COMPLETE parent_cycle_id=%s symbol=%s "
            "trigger_role=%s role_results=%s normal_accounts=%s "
            "first_recovery_accounts=%s post_virtual_accounts=%s "
            "fresh_role_subcycles=true role_scope_context=task_local "
            "private_websocket_only=true bulk_purchase=false copy_trading=false "
            "global_stop_on_role_error=false",
            parent_cycle_id,
            symbol,
            trigger_role,
            result_by_role,
            len(scopes[continuation.NORMAL_ROLE]),
            len(scopes[continuation.FIRST_RECOVERY_ROLE]),
            len(scopes[continuation.POST_VIRTUAL_ROLE]),
        )


async def _drain_fresh_aidr(bot: RFDir5TradingBot) -> None:
    while getattr(bot, "hybrid_digit_candidates", {}):
        await _fresh_grouped_aidr_arbitrate(bot)
        await asyncio.sleep(0)


def install_scalable_group_execution_hardening() -> None:
    """Make fresh private-WebSocket role subcycles the final authority."""

    global _INSTALLED
    if _INSTALLED:
        return

    standardized._standardized_aidr_arbitrate = _fresh_grouped_aidr_arbitrate
    hybrid._arbitrate_digits = _drain_fresh_aidr
    continuation._recovery_aware_arbitrate = _drain_fresh_aidr

    # Install after the final grouped role functions exist. These late imports
    # avoid circular initialization and make both transport and scale protections
    # authoritative over every earlier wrapper.
    from app.websocket_hot_path_hardening import (
        install_websocket_hot_path_hardening,
    )
    from app.websocket_hot_path_scalability import (
        install_websocket_hot_path_scalability,
    )

    install_websocket_hot_path_hardening()
    install_websocket_hot_path_scalability()

    RFDir5TradingBot._scalable_group_execution_hardening_installed = True
    _INSTALLED = True
    logging.getLogger(__name__).warning(
        "SCALABLE_ROLE_HARDENING_INSTALLED version=%s "
        "fresh_role_subcycles=true signal_holding=false "
        "private_websocket_only=true bulk_purchase=false copy_trading=false "
        "global_stop_on_role_error=false",
        SCALABLE_ROLE_HARDENING_VERSION,
    )
