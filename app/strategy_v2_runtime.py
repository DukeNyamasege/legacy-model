from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select

from app.models import DirectionalSignal
from app.strategy_v2_preferences import (
    STRATEGY_KEY_PREFIX,
    STRATEGY_VERSION,
    StrategySelectionV2,
    _decode_payload,
    default_strategy,
    install_strategy_v2_preferences,
    normalize_strategy,
)

_INSTALLED = False


def _ensure_parent_signal(bot: Any, signal: Any, route: Any) -> None:
    """Create the directional_signals parent required by VirtualTrade.

    Rise/Fall already uses RFDir5Repository.record_signal. Manual digit and parity
    candidates previously existed only in candidate_signals, so PostgreSQL rejected
    every virtual-trade insert after the account entered protection mode.
    """

    signal_id = str(getattr(signal, "signal_id", "") or "").strip()
    if not signal_id:
        return
    database = bot.repository.database
    with database.session() as session:
        if session.get(DirectionalSignal, signal_id) is not None:
            return
        trigger_digits = [
            int(value)
            for value in tuple(getattr(signal, "trigger_digits", ()) or ())[-100:]
            if str(value).lstrip("-").isdigit()
        ]
        session.add(
            DirectionalSignal(
                signal_id=signal_id,
                run_id=int(bot.rf_repository.run_id),
                strategy_version=STRATEGY_VERSION,
                symbol=str(getattr(signal, "symbol", "") or ""),
                direction=str(getattr(signal, "direction", "") or "")[:10],
                contract_type=str(getattr(signal, "contract_type", "") or "")[:10],
                duration_ticks=int(getattr(signal, "duration_ticks", 1) or 1),
                signal_epoch=int(getattr(signal, "signal_tick_epoch", 0) or 0),
                signal_tick_id=str(getattr(signal, "signal_tick_id", "") or ""),
                tick_sequence=int(getattr(signal, "tick_sequence", 0) or 0),
                reference_entry_quote=float(
                    getattr(signal, "reference_entry_quote", 0.0) or 0.0
                ),
                analysis_quotes=[str(value) for value in trigger_digits],
                movements=[],
                feature_values={
                    "family": str(route.family),
                    "side": str(route.side),
                    "role": str(route.role),
                    "barrier": str(getattr(signal, "barrier", "") or ""),
                    "trigger_name": str(
                        getattr(signal, "trigger_name", "") or ""
                    ),
                    "predicted_probability": float(
                        route.predicted_probability or 0.0
                    ),
                    "minimum_edge": float(route.minimum_edge or 0.0),
                    "p20": float(getattr(signal, "p100", 0.0) or 0.0),
                    "p100": float(getattr(signal, "p500", 0.0) or 0.0),
                    "p500": float(getattr(signal, "p1000", 0.0) or 0.0),
                },
                quality_score=int(getattr(signal, "quality_score", 1) or 1),
                validated_edge=getattr(signal, "validated_edge", None),
                selected_for_execution=False,
                execution_decision="PENDING",
                execution_reason="Manual strategy candidate awaiting arbitration",
            )
        )


def _ordinary_probability(side: str, prediction: int) -> float:
    if side == "over":
        return max(0.0, min(1.0, (9 - int(prediction)) / 10.0))
    return max(0.0, min(1.0, int(prediction) / 10.0))


def _manual_alignment(side: str, prediction: int) -> float:
    # Match the system virtual philosophy: ordinary contract probability with only
    # five-percent relative tightening. The user's chosen barrier never changes.
    return min(0.95, _ordinary_probability(side, prediction) * 1.05)


def _manual_predicate(side: str, prediction: int) -> Callable[[int], bool]:
    if side == "over":
        return lambda digit, barrier=prediction: digit > barrier
    return lambda digit, barrier=prediction: digit < barrier


def install_strategy_v2_runtime() -> None:
    """Install System Strategy plus fixed-choice manual contracts.

    All families retain the shared account-scoped loss debt, first recovery,
    two-loss virtual entry, virtual confirmation and real-recovery lifecycle. Only
    System Strategy changes barriers automatically. Manual strategies keep the
    exact selected contract and prediction in every mode.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    install_strategy_v2_preferences()

    import app.ai_digit_recovery_v1 as aidr
    import app.multi_strategy_runtime as ms

    # The old module imported preference helpers by value. Replace those bindings
    # so every worker snapshot reads the v2 schema, including prediction barriers.
    ms.STRATEGY_KEY_PREFIX = STRATEGY_KEY_PREFIX
    ms.StrategySelection = StrategySelectionV2
    ms.default_strategy = default_strategy
    ms.normalize_strategy = normalize_strategy
    ms._decode_preference = _decode_payload
    ms.MULTI_STRATEGY_VERSION = STRATEGY_VERSION

    def system_accounts(bot: Any) -> list[tuple[str, str, int]]:
        return [
            (route.token, route.account_id, route.managed_id)
            for route in ms._routes_for(bot, "system", "system")
        ]

    # Only the explicit System Strategy receives AIDR OVER1/OVER3/OVER4. A manual
    # DIGITOVER account must not be silently routed back into the system sequence.
    aidr._enabled_accounts = system_accounts
    ms._filter_aidr_over_accounts = system_accounts

    original_make_digit = ms._make_digit_signal

    def make_digit_with_parent(*args: Any, **kwargs: Any) -> Any | None:
        signal = original_make_digit(*args, **kwargs)
        if signal is None:
            return None
        bot = args[0] if args else kwargs.get("bot")
        route = getattr(bot, "_multi_strategy_signal_routes", {}).get(
            str(signal.signal_id)
        )
        if route is None:
            raise RuntimeError(
                f"Manual strategy signal {signal.signal_id} has no account route"
            )
        _ensure_parent_signal(bot, signal, route)
        return signal

    ms._make_digit_signal = make_digit_with_parent

    def queue_v2_signals(bot: Any, tick_data: dict[str, Any]) -> None:
        tick = tick_data.get("tick") or {}
        symbol = str(tick.get("symbol") or bot.symbol)
        market = bot.market_states.get(symbol)
        if market is None or not tick.get("quote"):
            return
        routes = ms._strategy_snapshot(bot)
        if not routes:
            return

        # Group manual digit accounts by exact side and prediction. Normal,
        # recovery and virtual accounts share one candidate because their contract
        # must remain identical throughout the lifecycle.
        digit_groups: dict[tuple[str, int], set[int]] = {}
        for route in routes:
            selection = route.selection
            if selection.family != "digits":
                continue
            prediction = int(selection.prediction)
            digit_groups.setdefault((selection.side, prediction), set()).add(
                int(route.managed_id)
            )

        for (side, prediction), scope_ids in digit_groups.items():
            contract_type = "DIGITOVER" if side == "over" else "DIGITUNDER"
            direction = f"{side.upper()}_{prediction}"
            signal = ms._make_digit_signal(
                bot,
                symbol=symbol,
                tick=tick,
                family="digits",
                side=side,
                role="SHARED",
                contract_type=contract_type,
                direction=direction,
                barrier=str(prediction),
                predicate=_manual_predicate(side, prediction),
                minimum_alignment=_manual_alignment(side, prediction),
                minimum_edge=0.005,
                scope_ids=scope_ids,
            )
            if signal is not None:
                ms._queue_candidate(bot, signal)

        for side, contract_type, predicate in (
            ("even", "DIGITEVEN", lambda digit: digit % 2 == 0),
            ("odd", "DIGITODD", lambda digit: digit % 2 == 1),
        ):
            scope_ids = {
                int(route.managed_id)
                for route in routes
                if route.selection.family == "parity"
                and route.selection.side == side
            }
            if not scope_ids:
                continue
            signal = ms._make_digit_signal(
                bot,
                symbol=symbol,
                tick=tick,
                family="parity",
                side=side,
                role="SHARED",
                contract_type=contract_type,
                direction=side.upper(),
                barrier="",
                predicate=predicate,
                minimum_alignment=0.525,
                minimum_edge=0.005,
                scope_ids=scope_ids,
            )
            if signal is not None:
                ms._queue_candidate(bot, signal)

        for side in ("rise", "fall"):
            scope_ids = {
                int(route.managed_id)
                for route in routes
                if route.selection.family == "direction"
                and route.selection.side == side
            }
            if not scope_ids:
                continue
            signal = ms._make_direction_signal(
                bot,
                symbol=symbol,
                tick=tick,
                side=side,
                scope_ids=scope_ids,
            )
            if signal is not None:
                ms._queue_candidate(bot, signal)

    ms._queue_non_aidr_signals = queue_v2_signals

    # Invalidate any v1 snapshot created while the bot constructor was being
    # wrapped. The first live tick must classify every account using v2 choices.
    original_init = ms.RFDir5TradingBot.__init__

    def v2_init(self: Any, config_path: str | None = None) -> None:
        original_init(self, config_path)
        self._multi_strategy_snapshot = []
        self._multi_strategy_snapshot_at = 0.0
        self.logger.warning(
            "STRATEGY_V2_RUNTIME_ACTIVE default=system options=system,digits,parity,direction "
            "manual_prediction_fixed=true virtual_parent_repair=true"
        )

    ms.RFDir5TradingBot.__init__ = v2_init
    ms.RFDir5TradingBot._strategy_v2_runtime_installed = True
    _INSTALLED = True
