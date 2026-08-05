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
SCALABLE_ROLE_HARDENING_VERSION = "fresh-websocket-role-subcycles-v4"


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
    """Run a bounded rotating System cohort through fresh role subcycles.

    One parent opportunity chooses one market. A maximum round-robin cohort is
    selected only after at least one current candidate survives the local freshness
    check. This avoids waking account WebSockets for stale or empty cycles.
    """

    from app import rotating_execution_cohorts as cohorts

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

        # Reject stale candidates before rotating or waking any private account.
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
            bot.logger.info(
                "EXECUTION_COHORT_NOT_ACTIVATED strategy=digits/over/system "
                "reason=no_fresh_candidate private_sessions_started=0"
            )
            return

        normal, recovery, post_virtual, virtual = aidr._account_recovery_groups(bot)
        selection = await cohorts.select_aidr_cycle(
            bot,
            normal=set(normal),
            recovery=set(recovery),
            post_virtual=set(post_virtual),
            virtual=set(virtual),
        )
        scopes = selection.scopes
        if not any(scopes.values()):
            return

        # Only the selected financial cohort receives active private sessions.
        # Virtual-only members keep their simulated state without a financial WS.
        await cohorts.activate_cycle_accounts(
            bot,
            selection.financial_ids,
            strategy="digits/over/system",
        )

        # The old path requested provider proposals for every fresh market at the
        # same instant. Rank locally, then test only a few candidates sequentially.
        trigger_result = await cohorts.select_aidr_trigger(bot, fresh)
        if trigger_result is None:
            return
        trigger_signal, _economics = trigger_result
        symbol = str(trigger_signal.symbol)
        trigger_role = continuation._candidate_role(trigger_signal)
        parent_cycle_id = str(uuid.uuid4())
        result_by_role: dict[str, str] = {}

        # Role order stays deterministic. Each selected role receives a fresh
        # proposal immediately before its own bounded private-WebSocket dispatch.
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
            "rotating_cohort=true cohort_limit=%s all_accounts_same_signal=false "
            "private_websocket_only=true bulk_purchase=false copy_trading=false "
            "global_stop_on_role_error=false",
            parent_cycle_id,
            symbol,
            trigger_role,
            result_by_role,
            len(scopes[continuation.NORMAL_ROLE]),
            len(scopes[continuation.FIRST_RECOVERY_ROLE]),
            len(scopes[continuation.POST_VIRTUAL_ROLE]),
            cohorts.COHORT_SIZE,
        )


async def _drain_fresh_aidr(bot: RFDir5TradingBot) -> None:
    while getattr(bot, "hybrid_digit_candidates", {}):
        await _fresh_grouped_aidr_arbitrate(bot)
        await asyncio.sleep(0)


def install_scalable_group_execution_hardening() -> None:
    """Make rotating private-WebSocket role subcycles the final authority."""

    global _INSTALLED
    if _INSTALLED:
        return

    standardized._standardized_aidr_arbitrate = _fresh_grouped_aidr_arbitrate
    hybrid._arbitrate_digits = _drain_fresh_aidr
    continuation._recovery_aware_arbitrate = _drain_fresh_aidr

    # Install after the final grouped role functions exist. These late imports
    # avoid circular initialization and make the scale protections authoritative.
    from app.websocket_hot_path_hardening import (
        install_websocket_hot_path_hardening,
    )
    from app.websocket_hot_path_scalability import (
        install_websocket_hot_path_scalability,
    )
    from app.rotating_execution_cohorts import (
        install_rotating_execution_cohorts,
    )
    from app.proposal_relay_runtime import (
        install_proposal_relay_runtime,
    )

    install_websocket_hot_path_hardening()
    install_websocket_hot_path_scalability()
    install_rotating_execution_cohorts()
    install_proposal_relay_runtime()

    RFDir5TradingBot._scalable_group_execution_hardening_installed = True
    _INSTALLED = True
    logging.getLogger(__name__).warning(
        "SCALABLE_ROLE_HARDENING_INSTALLED version=%s "
        "fresh_role_subcycles=true signal_holding=false "
        "rotating_cohort=true stale_cycle_activation=false "
        "proposal_relay_sockets=2 all_accounts_same_signal=false "
        "private_websocket_only=true bulk_purchase=false copy_trading=false "
        "global_stop_on_role_error=false",
        SCALABLE_ROLE_HARDENING_VERSION,
    )
