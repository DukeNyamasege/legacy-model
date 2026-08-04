from __future__ import annotations

import asyncio
from typing import Any

from app.repositories.rf_dir5_repository import RFDir5Repository, VIRTUAL_MODE
from app.repositories.test2_repository import Test2Repository
from enhanced_bot import mask_account_id


VIRTUAL_TRIGGER_ACTUAL_LOSSES = 2
VIRTUAL_EXIT_AFTER_WINS = 1

# These are account lifecycle outcomes, never platform control states. A caller
# that accidentally sends one through Test2Repository.set_status must not pause or
# stop the worker, the public tick stream, or another trader's account.
ACCOUNT_ONLY_GLOBAL_STATUSES = frozenset(
    {
        "ACCOUNT_STOPPED",
        "ARCHIVED_REJOIN_REQUIRED",
        "CONTRACT_UNAVAILABLE",
        "CREDENTIAL_ERROR",
        "DISABLED",
        "DUPLICATE",
        "EMERGENCY_STOP",
        "INSUFFICIENT_BALANCE",
        "INVALID_ACCOUNT",
        "MANUAL_EXIT",
        "MANUAL_PAUSE",
        "PAUSED",
        "PURCHASE_INSUFFICIENT_BALANCE",
        "PURCHASE_REGISTRATION_ERROR",
        "REAL_DISABLED",
        "STOPPED",
        "STOP_LOSS",
        "TAKE_PROFIT",
        "TOKEN_REQUIRED",
    }
)

_ACCOUNT_ISOLATION_INSTALLED = False
_VIRTUAL_RUNTIME_INSTALLED = False


def platform_status_for(status: Any, pause_reason: Any = "") -> tuple[str, str]:
    """Return a platform status that cannot be changed by one account event."""

    normalized = str(status or "RUNNING").strip().upper() or "RUNNING"
    if normalized in ACCOUNT_ONLY_GLOBAL_STATUSES:
        return "RUNNING", ""
    return normalized, str(pause_reason or "")


def uniform_digit_alignment(family: str, side: str, barrier: Any = "") -> float:
    """Return the ordinary contract probability with no virtual tightening."""

    normalized_family = str(family or "").strip().lower()
    normalized_side = str(side or "").strip().lower()
    if normalized_family == "parity":
        return 0.50
    if normalized_family != "digits":
        return 0.0
    try:
        prediction = max(0, min(9, int(str(barrier).strip())))
    except (TypeError, ValueError):
        return 0.0
    if normalized_side == "over":
        return (9 - prediction) / 10.0
    if normalized_side == "under":
        return prediction / 10.0
    return 0.0


def install_account_isolation_invariants() -> None:
    """Prevent personal Pause/Stop/TP/SL failures from becoming global state."""

    global _ACCOUNT_ISOLATION_INSTALLED
    if _ACCOUNT_ISOLATION_INSTALLED:
        return

    original_set_status = Test2Repository.set_status

    def account_isolated_set_status(
        self: Test2Repository,
        status: str,
        pause_reason: str = "",
    ) -> None:
        safe_status, safe_reason = platform_status_for(status, pause_reason)
        original_set_status(self, safe_status, safe_reason)

    account_isolated_set_status._per_account_isolation = True  # type: ignore[attr-defined]
    Test2Repository.set_status = account_isolated_set_status
    Test2Repository._per_account_execution_isolation_installed = True
    _ACCOUNT_ISOLATION_INSTALLED = True


def _virtual_routes(bot: Any, scope_ids: set[int]) -> list[Any]:
    import app.multi_strategy_runtime as ms

    if not scope_ids:
        return []
    try:
        snapshot = ms._strategy_snapshot(bot, force=True)
    except Exception:
        return []
    return [
        route
        for route in snapshot
        if int(route.managed_id) in scope_ids and str(route.mode) == VIRTUAL_MODE
    ]


def _virtual_scope_ids(bot: Any, scope_ids: set[int]) -> set[int]:
    return {int(route.managed_id) for route in _virtual_routes(bot, scope_ids)}


def _ensure_parent(bot: Any, signal: Any, *, family: str) -> None:
    if str(family) == "system":
        import app.aidr_loss_continuation_fix as continuation

        continuation._ensure_directional_signal(bot, signal, role="VIRTUAL")
        return

    import app.strategy_v2_runtime as v2

    route = getattr(bot, "_multi_strategy_signal_routes", {}).get(
        str(getattr(signal, "signal_id", "") or "")
    )
    if route is not None:
        v2._ensure_parent_signal(bot, signal, route)


async def _open_virtual_accounts(
    bot: Any,
    signal: Any,
    scope_ids: set[int],
    *,
    family: str,
    side: str,
) -> None:
    """Open zero-cost observations without proposal, cadence, or purchase locks."""

    import app.multi_strategy_runtime as ms

    routes = _virtual_routes(bot, set(scope_ids))
    if not routes:
        return

    _ensure_parent(bot, signal, family=family)
    opened: list[dict[str, Any]] = []
    waiting: list[str] = []
    failed: list[str] = []

    for route in routes:
        masked = mask_account_id(route.account_id)
        try:
            configured_stake = ms._configured_stake(
                bot,
                route.token,
                route.account_id,
                int(route.managed_id),
            )
            virtual = bot.rf_repository.start_virtual_trade(
                managed_account_id=int(route.managed_id),
                account_id_masked=masked,
                signal=signal,
                configured_stake=configured_stake,
                simulated_stake=round(configured_stake, 2),
                # A virtual observation does not need provider economics. The
                # real recovery obtains a fresh proposal before risking money.
                expected_payout=None,
            )
            if virtual is None:
                waiting.append(masked)
                continue
            opened.append(virtual)
            bot._set_account_execution_status(
                int(route.managed_id),
                "virtual_protection",
                (
                    f"{family}/{side} virtual observation active; no Deriv "
                    "contract was purchased."
                ),
            )
            bot.logger.warning(
                "PER_ACCOUNT_VIRTUAL_OPENED account=%s family=%s side=%s "
                "market=%s contract_type=%s barrier=%s actual_financial_impact=0",
                masked,
                family,
                side,
                getattr(signal, "symbol", ""),
                getattr(signal, "contract_type", ""),
                getattr(signal, "barrier", ""),
            )
        except Exception as exc:
            failed.append(masked)
            # The account remains enabled and retries on a later qualifying
            # signal. One row failure never aborts another account or the worker.
            try:
                bot._set_account_execution_status(
                    int(route.managed_id),
                    "virtual_retry",
                    f"Virtual observation retry scheduled after {type(exc).__name__}",
                )
            except Exception:
                pass
            bot.logger.error(
                "PER_ACCOUNT_VIRTUAL_OPEN_FAILED account=%s family=%s side=%s "
                "error=%s global_execution_continues=true",
                masked,
                family,
                side,
                type(exc).__name__,
                exc_info=True,
            )

    if opened:
        try:
            bot.repository.mark_signal(
                signal.signal_id,
                status="VIRTUAL_TRADE_FAST",
                purchase_requested=False,
                expected_account_masks=[
                    str(item.get("account") or "") for item in opened
                ],
                registered_account_masks=[],
            )
        except Exception:
            pass
    bot.logger.info(
        "PER_ACCOUNT_VIRTUAL_RESULT signal_id=%s family=%s side=%s opened=%s "
        "waiting=%s failed=%s required_wins=%s trigger_actual_losses=%s",
        getattr(signal, "signal_id", ""),
        family,
        side,
        len(opened),
        len(waiting),
        len(failed),
        VIRTUAL_EXIT_AFTER_WINS,
        VIRTUAL_TRIGGER_ACTUAL_LOSSES,
    )


def _schedule_virtual(
    bot: Any,
    signal: Any,
    scope_ids: set[int],
    *,
    family: str,
    side: str,
) -> None:
    virtual_ids = _virtual_scope_ids(bot, set(scope_ids))
    if not virtual_ids:
        return

    signal_id = str(getattr(signal, "signal_id", "") or "")
    key = (signal_id, tuple(sorted(virtual_ids)))
    active = getattr(bot, "_per_account_virtual_tasks", None)
    if not isinstance(active, dict):
        active = {}
        bot._per_account_virtual_tasks = active
    existing = active.get(key)
    if existing is not None and not existing.done():
        return

    task = asyncio.create_task(
        _open_virtual_accounts(
            bot,
            signal,
            virtual_ids,
            family=family,
            side=side,
        ),
        name=f"virtual:{family}:{side}:{signal_id[:8]}",
    )
    active[key] = task

    def finished(done: asyncio.Task[Any]) -> None:
        active.pop(key, None)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            bot.logger.exception(
                "PER_ACCOUNT_VIRTUAL_TASK_FAILED signal_id=%s family=%s side=%s "
                "global_execution_continues=true",
                signal_id,
                family,
                side,
            )

    task.add_done_callback(finished)


def install_uniform_virtual_runtime() -> None:
    """Install one fast, per-account virtual lifecycle for every strategy."""

    global _VIRTUAL_RUNTIME_INSTALLED
    if _VIRTUAL_RUNTIME_INSTALLED:
        return

    import app.ai_digit_recovery_v1 as aidr
    import app.aidr_loss_continuation_fix as continuation
    import app.aidr_virtual_soft_gate as soft_gate
    import app.hybrid_digit_put as hybrid
    import app.multi_strategy_runtime as ms

    # Hard lifecycle invariants shared by System, Over/Under, Even/Odd and
    # Rise/Fall. Virtual observations never add debt and one virtual win arms the
    # next real recovery for only that account.
    original_record_outcome = RFDir5Repository.record_account_outcome
    original_settle_virtual = RFDir5Repository.settle_due_virtual_trades

    def record_outcome_uniform(self: RFDir5Repository, *args: Any, **kwargs: Any):
        kwargs["virtual_trigger_actual_losses"] = VIRTUAL_TRIGGER_ACTUAL_LOSSES
        return original_record_outcome(self, *args, **kwargs)

    def settle_virtual_uniform(self: RFDir5Repository, *args: Any, **kwargs: Any):
        kwargs["exit_after_wins"] = VIRTUAL_EXIT_AFTER_WINS
        kwargs["max_observations"] = 0
        return original_settle_virtual(self, *args, **kwargs)

    record_outcome_uniform._uniform_virtual_runtime = True  # type: ignore[attr-defined]
    settle_virtual_uniform._uniform_virtual_runtime = True  # type: ignore[attr-defined]
    RFDir5Repository.record_account_outcome = record_outcome_uniform
    RFDir5Repository.settle_due_virtual_trades = settle_virtual_uniform

    # No five-percent virtual tightening: OVER-4 uses its ordinary 50% baseline.
    ordinary_over4 = soft_gate._ordinary_over_hit_rate(aidr.POST_VIRTUAL_BARRIER)
    soft_gate.POST_VIRTUAL_TIGHTENING_FACTOR = 1.0
    soft_gate.POST_VIRTUAL_ALIGNMENT = ordinary_over4
    continuation.AIDR_POST_VIRTUAL_ALIGNMENT = ordinary_over4

    # Manual digit/parity candidates retain their normal analysis, but a virtual
    # account receives a zero-cost observation at the ordinary contract baseline
    # even when the stricter real-entry candidate is not available.
    original_make_digit = ms._make_digit_signal

    def make_digit_with_uniform_virtual(*args: Any, **kwargs: Any):
        bot = args[0] if args else kwargs.get("bot")
        scope_ids = {int(value) for value in set(kwargs.get("scope_ids") or set())}
        family = str(kwargs.get("family") or "")
        side = str(kwargs.get("side") or "")
        signal = original_make_digit(*args, **kwargs)
        virtual_ids = _virtual_scope_ids(bot, scope_ids) if bot is not None else set()

        if signal is None and virtual_ids:
            relaxed = uniform_digit_alignment(
                family,
                side,
                kwargs.get("barrier", ""),
            )
            requested = float(kwargs.get("minimum_alignment") or 0.0)
            if relaxed > 0.0 and relaxed + 1e-12 < requested:
                virtual_kwargs = dict(kwargs)
                virtual_kwargs["role"] = "VIRTUAL"
                virtual_kwargs["minimum_alignment"] = relaxed
                virtual_kwargs["minimum_edge"] = 0.0
                virtual_kwargs["scope_ids"] = set(virtual_ids)
                signal = original_make_digit(*args, **virtual_kwargs)

        if signal is not None and virtual_ids:
            _schedule_virtual(
                bot,
                signal,
                virtual_ids,
                family=family,
                side=side,
            )
        return signal

    original_make_direction = ms._make_direction_signal

    def make_direction_with_uniform_virtual(*args: Any, **kwargs: Any):
        bot = args[0] if args else kwargs.get("bot")
        scope_ids = {int(value) for value in set(kwargs.get("scope_ids") or set())}
        side = str(kwargs.get("side") or "")
        signal = original_make_direction(*args, **kwargs)
        if signal is not None and bot is not None:
            _schedule_virtual(
                bot,
                signal,
                scope_ids,
                family="direction",
                side=side,
            )
        return signal

    ms._make_digit_signal = make_digit_with_uniform_virtual
    ms._make_direction_signal = make_direction_with_uniform_virtual

    # System Strategy virtual OVER-4 is scheduled directly from the qualifying
    # market signal. It does not wait for the 15-second real-purchase cadence or a
    # provider proposal because no money is being risked.
    original_recovery_candidate = continuation._recovery_aware_candidate

    def recovery_candidate_with_fast_virtual(
        bot: Any,
        symbol: str,
        tick: dict[str, Any],
    ) -> Any | None:
        candidate = original_recovery_candidate(bot, symbol, tick)
        try:
            _normal, _first, _post, virtual_ids = aidr._account_recovery_groups(bot)
        except Exception:
            virtual_ids = set()
        if virtual_ids:
            virtual_candidate = candidate
            if str(getattr(virtual_candidate, "barrier", "") or "") != str(
                aidr.POST_VIRTUAL_BARRIER
            ):
                virtual_candidate = continuation._make_aidr_candidate(
                    bot,
                    symbol,
                    tick,
                    barrier=aidr.POST_VIRTUAL_BARRIER,
                    recovery=True,
                )
            if virtual_candidate is not None:
                _schedule_virtual(
                    bot,
                    virtual_candidate,
                    set(virtual_ids),
                    family="system",
                    side="system",
                )
                if candidate is None:
                    candidate = virtual_candidate
        return candidate

    continuation._recovery_aware_candidate = recovery_candidate_with_fast_virtual
    hybrid._make_digit_candidate = recovery_candidate_with_fast_virtual

    RFDir5Repository._uniform_virtual_runtime_installed = True
    hybrid._uniform_virtual_runtime_installed = True
    _VIRTUAL_RUNTIME_INSTALLED = True
