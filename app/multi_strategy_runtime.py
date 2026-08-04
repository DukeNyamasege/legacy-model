from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select

import app.ai_digit_recovery_v1 as aidr
import app.hybrid_digit_put as hybrid
import app.repositories.rf_dir5_repository as rf_repository_module
from app.models import AccountRiskState, RuntimePreference
from app.repositories.rf_dir5_repository import (
    NORMAL_MODE,
    RECOVERY_PENDING,
    REAL_RECOVERY_PENDING,
    VIRTUAL_MODE,
    VIRTUAL_WAITING_FOR_WIN,
)
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy.decision_engine import parse_proposal_economics
from app.strategy.rise_fall_strategy import (
    build_five_move_features,
    calculate_directional_score,
    check_exhaustion_filter,
    check_volatility_filter,
    detect_fall_candidate,
    detect_rise_candidate,
    make_signal_event,
)
from app.strategy_preferences import (
    STRATEGY_KEY_PREFIX,
    StrategySelection,
    default_strategy,
    normalize_strategy,
)
from enhanced_bot import mask_account_id

_INSTALLED = False
MULTI_STRATEGY_VERSION = "multi-strategy-v1"


@dataclass(slots=True)
class AccountRoute:
    token: str
    account_id: str
    managed_id: int
    selection: StrategySelection
    mode: str
    recovery_debt: float
    split_remaining: int


@dataclass(slots=True)
class CandidateRoute:
    family: str
    side: str
    role: str
    scope_ids: set[int]
    predicted_probability: float
    minimum_edge: float
    created_monotonic: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _final_digit(value: Decimal) -> int:
    rendered = format(Decimal(str(value)), "f")
    for character in reversed(rendered):
        if character.isdigit():
            return int(character)
    raise ValueError(f"Could not derive final digit from {value!r}")


def _install_parity_virtual_outcome() -> None:
    original = rf_repository_module._virtual_trade_outcome
    if getattr(original, "_multi_strategy_parity", False):
        return

    def parity_aware_outcome(
        *,
        direction: str,
        contract_type: str,
        barrier: str | int | None,
        prediction_digit: int | None,
        entry_quote: Decimal,
        exit_quote: Decimal,
        exit_digit: int | None = None,
    ) -> tuple[str, int | None]:
        normalized_contract = str(contract_type or "").upper()
        normalized_direction = str(direction or "").upper()
        if normalized_contract in {"DIGITEVEN", "DIGITODD"} or normalized_direction in {
            "EVEN",
            "ODD",
        }:
            digit = (
                int(exit_digit)
                if exit_digit is not None and 0 <= int(exit_digit) <= 9
                else _final_digit(exit_quote)
            )
            wants_even = normalized_contract == "DIGITEVEN" or normalized_direction == "EVEN"
            won = (digit % 2 == 0) if wants_even else (digit % 2 == 1)
            return ("WIN" if won else "LOSS", digit)
        return original(
            direction=direction,
            contract_type=contract_type,
            barrier=barrier,
            prediction_digit=prediction_digit,
            entry_quote=entry_quote,
            exit_quote=exit_quote,
            exit_digit=exit_digit,
        )

    parity_aware_outcome._multi_strategy_parity = True  # type: ignore[attr-defined]
    rf_repository_module._virtual_trade_outcome = parity_aware_outcome


def _all_eligible(bot: RFDir5TradingBot) -> list[tuple[str, str]]:
    source = getattr(bot, "_aidr_original_eligible_accounts", None)
    if callable(source):
        try:
            return list(source())
        except Exception:
            pass
    source = getattr(bot, "_multi_strategy_original_eligible", None)
    if callable(source):
        return list(source())
    return list(bot._eligible_purchase_accounts())


def _decode_preference(raw: str) -> StrategySelection:
    if not raw:
        return default_strategy()
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return normalize_strategy(value.get("family"), value.get("side"))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return default_strategy()


def _strategy_snapshot(bot: RFDir5TradingBot, *, force: bool = False) -> list[AccountRoute]:
    now = time.monotonic()
    cached_at = float(getattr(bot, "_multi_strategy_snapshot_at", 0.0) or 0.0)
    cached = getattr(bot, "_multi_strategy_snapshot", None)
    if not force and isinstance(cached, list) and now - cached_at < 0.75:
        return cached

    eligible = _all_eligible(bot)
    token_rows: list[tuple[str, str, int]] = []
    for token, account_id in eligible:
        managed_id = bot._managed_account_id_for_token(token)
        if managed_id is not None:
            token_rows.append((token, account_id, int(managed_id)))
    managed_ids = {managed_id for _token, _account, managed_id in token_rows}
    if not managed_ids:
        bot._multi_strategy_snapshot = []
        bot._multi_strategy_snapshot_at = now
        return []

    preference_values: dict[int, StrategySelection] = {}
    split_values: dict[int, int] = {}
    states: dict[int, AccountRiskState] = {}
    with bot.repository.database.session() as session:
        for row in session.scalars(
            select(RuntimePreference).where(
                RuntimePreference.preference_key.like(f"{STRATEGY_KEY_PREFIX}%")
            )
        ).all():
            suffix = str(row.preference_key).removeprefix(STRATEGY_KEY_PREFIX)
            if suffix.isdigit() and int(suffix) in managed_ids:
                preference_values[int(suffix)] = _decode_preference(
                    str(row.preference_value or "")
                )
        for row in session.scalars(
            select(RuntimePreference).where(
                RuntimePreference.preference_key.like("aidr_split_remaining:%")
            )
        ).all():
            suffix = str(row.preference_key).removeprefix("aidr_split_remaining:")
            if suffix.isdigit() and int(suffix) in managed_ids:
                try:
                    split_values[int(suffix)] = 1 if int(row.preference_value or "0") > 0 else 0
                except (TypeError, ValueError):
                    split_values[int(suffix)] = 0
        for state in session.scalars(
            select(AccountRiskState).where(
                AccountRiskState.managed_account_id.in_(sorted(managed_ids))
            )
        ).all():
            states[int(state.managed_account_id)] = state

    routes: list[AccountRoute] = []
    for token, account_id, managed_id in token_rows:
        state = states.get(managed_id)
        raw_mode = str(state.protection_mode or NORMAL_MODE) if state else NORMAL_MODE
        if raw_mode == VIRTUAL_WAITING_FOR_WIN:
            mode = VIRTUAL_MODE
        elif raw_mode == REAL_RECOVERY_PENDING or (
            state is not None
            and (
                float(state.recovery_loss_debt or 0.0) > 0.009
                or bool(state.recovery_pending)
                or bool(state.recovery_attempt_active)
            )
        ):
            mode = RECOVERY_PENDING
        else:
            mode = NORMAL_MODE
        routes.append(
            AccountRoute(
                token=token,
                account_id=account_id,
                managed_id=managed_id,
                selection=preference_values.get(managed_id, default_strategy()),
                mode=mode,
                recovery_debt=float(state.recovery_loss_debt or 0.0) if state else 0.0,
                split_remaining=split_values.get(managed_id, 0),
            )
        )

    bot._multi_strategy_snapshot = routes
    bot._multi_strategy_snapshot_at = now
    return routes


def _routes_for(
    bot: RFDir5TradingBot,
    family: str,
    side: str,
) -> list[AccountRoute]:
    return [
        route
        for route in _strategy_snapshot(bot)
        if route.selection.family == family and route.selection.side == side
    ]


def _filter_aidr_over_accounts(bot: RFDir5TradingBot) -> list[tuple[str, str, int]]:
    return [
        (route.token, route.account_id, route.managed_id)
        for route in _routes_for(bot, "digits", "over")
    ]


def _probability(digits: list[int], predicate: Callable[[int], bool], window: int) -> float:
    sample = digits[-window:]
    if not sample:
        return 0.0
    return sum(1 for digit in sample if predicate(digit)) / len(sample)


def _digit_statistics(
    digits: list[int],
    predicate: Callable[[int], bool],
) -> dict[str, float]:
    p20 = _probability(digits, predicate, 20)
    p50 = _probability(digits, predicate, 50)
    p100 = _probability(digits, predicate, 100)
    p500 = _probability(digits, predicate, 500)
    weighted = 0.42 * p20 + 0.25 * p50 + 0.20 * p100 + 0.13 * p500
    alignment = min(p20, p50, p100)
    return {
        "p20": p20,
        "p50": p50,
        "p100": p100,
        "p500": p500,
        "weighted": weighted,
        "alignment": alignment,
    }


def _make_digit_signal(
    bot: RFDir5TradingBot,
    *,
    symbol: str,
    tick: dict[str, Any],
    family: str,
    side: str,
    role: str,
    contract_type: str,
    direction: str,
    barrier: str,
    predicate: Callable[[int], bool],
    minimum_alignment: float,
    minimum_edge: float,
    scope_ids: set[int],
) -> hybrid.DigitSignal | None:
    market = bot.market_states[symbol]
    digits = [int(value) for value in market.raw_tick_digits if 0 <= int(value) <= 9]
    if len(digits) < 500:
        return None
    metrics = _digit_statistics(digits, predicate)
    if metrics["alignment"] + 1e-12 < minimum_alignment:
        return None
    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    tick_id = bot._tick_identity(symbol, epoch, quote)
    trigger_digits = tuple(digits[-100:])
    signal = hybrid.DigitSignal(
        signal_id=str(uuid.uuid4()),
        run_id=bot.test2_config.model.run_id,
        strategy_version=MULTI_STRATEGY_VERSION,
        symbol=symbol,
        direction=direction,
        contract_type=contract_type,
        duration_ticks=1,
        reference_entry_quote=quote,
        quality_score=max(1, min(10, int(round(metrics["weighted"] * 10)))),
        signal_tick_epoch=epoch,
        signal_tick_id=tick_id,
        generated_at=_now_iso(),
        generated_monotonic=time.monotonic(),
        connection_session_id=bot.connection_session_id,
        tick_sequence=int(market.tick_sequence),
        barrier=barrier,
        trigger_name=f"MSV1-{family.upper()}-{side.upper()}-{role}",
        trigger_digits=trigger_digits,
        signal_last_digit=trigger_digits[-1],
        p100=metrics["p20"],
        p500=metrics["p100"],
        p1000=metrics["p500"],
        lower95=metrics["alignment"],
        weighted_probability=metrics["weighted"],
    )
    bot.repository.record_candidate(signal)
    bot._multi_strategy_signal_routes[signal.signal_id] = CandidateRoute(
        family=family,
        side=side,
        role=role,
        scope_ids=set(scope_ids),
        predicted_probability=float(metrics["weighted"]),
        minimum_edge=float(minimum_edge),
        created_monotonic=time.monotonic(),
    )
    return signal


def _direction_probability(features: Any, quality_score: int) -> float:
    efficiency_component = max(0.0, float(features.efficiency) - 0.35) * 0.14
    score_component = max(0, int(quality_score) - 5) * 0.008
    return min(0.64, 0.50 + efficiency_component + score_component)


def _make_direction_signal(
    bot: RFDir5TradingBot,
    *,
    symbol: str,
    tick: dict[str, Any],
    side: str,
    scope_ids: set[int],
) -> Any | None:
    market = bot.market_states[symbol]
    quotes = [Decimal(str(item["quote"])) for item in market.ticks_history]
    normalization_size = int(bot.rf_config.normalization_movements)
    if len(quotes) < normalization_size + 6:
        return None
    historical = quotes[:-5]
    normalization = [
        later - earlier for earlier, later in zip(historical[:-1], historical[1:])
    ][-normalization_size:]
    try:
        features = build_five_move_features(
            quotes[-6:],
            normalization_movements=normalization,
        )
    except ValueError:
        return None

    direction = "RISE" if side == "rise" else "FALL"
    detector = detect_rise_candidate if side == "rise" else detect_fall_candidate
    if not detector(
        features,
        minimum_directional_moves=bot.rf_config.minimum_directional_moves,
        minimum_recent_directional_moves=getattr(
            bot.rf_config, "minimum_recent_directional_moves", 2
        ),
        minimum_efficiency=bot.rf_config.minimum_efficiency,
    ):
        return None
    volatility_ok = check_volatility_filter(
        features,
        minimum_impulse=bot.rf_config.minimum_impulse,
        maximum_impulse=bot.rf_config.maximum_impulse,
    )
    exhaustion_ok = check_exhaustion_filter(
        features,
        maximum_move_ratio=bot.rf_config.maximum_move_ratio,
    )
    if not volatility_ok or not exhaustion_ok:
        return None
    quality = calculate_directional_score(
        features,
        direction=direction,
        volatility_ok=volatility_ok,
        exhaustion_ok=exhaustion_ok,
    )
    if quality < bot.rf_config.minimum_directional_score:
        return None
    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    signal = make_signal_event(
        run_id=bot.test2_config.model.run_id,
        symbol=symbol,
        direction=direction,
        duration_ticks=1,
        features=features,
        quality_score=quality,
        signal_tick_epoch=epoch,
        signal_tick_id=bot._tick_identity(symbol, epoch, quote),
        connection_session_id=bot.connection_session_id,
        tick_sequence=int(market.tick_sequence),
    )
    bot.rf_repository.record_signal(signal)
    bot._multi_strategy_signal_routes[signal.signal_id] = CandidateRoute(
        family="direction",
        side=side,
        role="SHARED",
        scope_ids=set(scope_ids),
        predicted_probability=_direction_probability(features, quality),
        minimum_edge=0.005,
        created_monotonic=time.monotonic(),
    )
    return signal


def _queue_candidate(bot: RFDir5TradingBot, signal: Any) -> None:
    route = bot._multi_strategy_signal_routes.get(signal.signal_id)
    if route is None or not route.scope_ids:
        return
    key = (route.family, route.side, route.role, signal.symbol)
    previous = bot._multi_strategy_candidates.get(key)
    if previous is not None:
        try:
            bot.repository.mark_signal(previous.signal_id, status="SKIP_NEWER_STRATEGY_SIGNAL")
        except Exception:
            pass
    bot._multi_strategy_candidates[key] = signal
    if bot._multi_strategy_task is None or bot._multi_strategy_task.done():
        task = asyncio.create_task(
            _arbitrate_multi_strategy(bot),
            name="multi_strategy_arbitration",
        )
        bot._multi_strategy_task = task

        def finished(done: asyncio.Task) -> None:
            if bot._multi_strategy_task is done:
                bot._multi_strategy_task = None
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception:
                bot.logger.exception("MULTI_STRATEGY_ARBITRATION_FAILED")

        task.add_done_callback(finished)


def _queue_non_aidr_signals(bot: RFDir5TradingBot, tick_data: dict[str, Any]) -> None:
    tick = tick_data.get("tick") or {}
    symbol = str(tick.get("symbol") or bot.symbol)
    market = bot.market_states.get(symbol)
    if market is None or not tick.get("quote"):
        return
    routes = _strategy_snapshot(bot)
    if not routes:
        return

    under_routes = [
        route
        for route in routes
        if route.selection.family == "digits" and route.selection.side == "under"
    ]
    if under_routes:
        role_groups = {
            "NORMAL": {
                route.managed_id for route in under_routes if route.mode == NORMAL_MODE
            },
            "RECOVERY": {
                route.managed_id
                for route in under_routes
                if route.mode == RECOVERY_PENDING and route.split_remaining <= 0
            },
            "POST_VIRTUAL": {
                route.managed_id
                for route in under_routes
                if route.mode == RECOVERY_PENDING and route.split_remaining > 0
            },
            "VIRTUAL": {
                route.managed_id for route in under_routes if route.mode == VIRTUAL_MODE
            },
        }
        under_rules = {
            "NORMAL": (8, 0.78, 0.005),
            "RECOVERY": (6, 0.60, 0.005),
            "POST_VIRTUAL": (5, 0.525, 0.005),
            "VIRTUAL": (5, 0.525, 0.005),
        }
        for role, scope_ids in role_groups.items():
            if not scope_ids:
                continue
            barrier, alignment, edge = under_rules[role]
            signal = _make_digit_signal(
                bot,
                symbol=symbol,
                tick=tick,
                family="digits",
                side="under",
                role=role,
                contract_type="DIGITUNDER",
                direction=f"UNDER_{barrier}",
                barrier=str(barrier),
                predicate=lambda digit, limit=barrier: digit < limit,
                minimum_alignment=alignment,
                minimum_edge=edge,
                scope_ids=scope_ids,
            )
            if signal is not None:
                _queue_candidate(bot, signal)

    for side, contract_type, predicate in (
        ("even", "DIGITEVEN", lambda digit: digit % 2 == 0),
        ("odd", "DIGITODD", lambda digit: digit % 2 == 1),
    ):
        scope_ids = {
            route.managed_id
            for route in routes
            if route.selection.family == "parity" and route.selection.side == side
        }
        if not scope_ids:
            continue
        signal = _make_digit_signal(
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
            _queue_candidate(bot, signal)

    for side in ("rise", "fall"):
        scope_ids = {
            route.managed_id
            for route in routes
            if route.selection.family == "direction" and route.selection.side == side
        }
        if not scope_ids:
            continue
        signal = _make_direction_signal(
            bot,
            symbol=symbol,
            tick=tick,
            side=side,
            scope_ids=scope_ids,
        )
        if signal is not None:
            _queue_candidate(bot, signal)


async def _proposal_for(bot: RFDir5TradingBot, signal: Any, predicted: float) -> Any | None:
    requested = time.monotonic()
    response = await bot.public_client.send_request(
        bot._proposal_request_for(
            signal,
            0.50,
            int(getattr(signal, "duration_ticks", 1) or 1),
        )
    )
    received = time.monotonic()
    if "error" in response:
        return None
    try:
        return parse_proposal_economics(
            response,
            stake=0.50,
            predicted_probability=float(predicted),
            requested_monotonic=requested,
            received_monotonic=received,
            app_markup_percentage=bot.app_markup_percentage,
            commission_in_ask=True,
        )
    except (TypeError, ValueError):
        return None


def _role_priority(role: str) -> int:
    return {
        "VIRTUAL": 0,
        "POST_VIRTUAL": 1,
        "RECOVERY": 2,
        "SHARED": 3,
        "NORMAL": 4,
    }.get(str(role), 5)


async def _arbitrate_multi_strategy(bot: RFDir5TradingBot) -> None:
    await asyncio.sleep(0.08)
    candidates = list(bot._multi_strategy_candidates.values())
    bot._multi_strategy_candidates.clear()
    if not candidates:
        return

    bot._prune_stale_pending_contracts("multi_strategy_pre_proposal")
    if bot.is_trading_locked or bool(bot.pending_contracts_for_current_cycle):
        for signal in candidates:
            bot.repository.mark_signal(signal.signal_id, status="SKIP_TRADING_LOCK")
        return

    fresh: list[Any] = []
    for signal in candidates:
        market = bot.market_states.get(signal.symbol)
        route = bot._multi_strategy_signal_routes.get(signal.signal_id)
        if market is None or route is None or not route.scope_ids:
            continue
        if market.tick_sequence != int(signal.tick_sequence):
            bot.repository.mark_signal(signal.signal_id, status="SKIP_STALE_SIGNAL", stale=True)
            continue
        fresh.append(signal)
    if not fresh:
        return

    fresh.sort(
        key=lambda signal: (
            _role_priority(bot._multi_strategy_signal_routes[signal.signal_id].role),
            -float(bot._multi_strategy_signal_routes[signal.signal_id].predicted_probability),
            -int(getattr(signal, "quality_score", 0) or 0),
            bot._multi_strategy_signal_routes[signal.signal_id].created_monotonic,
            signal.symbol,
        )
    )
    selected = fresh[0]
    for signal in fresh[1:]:
        bot.repository.mark_signal(signal.signal_id, status="SKIP_MULTI_STRATEGY_ARBITRATION")

    route = bot._multi_strategy_signal_routes[selected.signal_id]
    economics = await _proposal_for(bot, selected, route.predicted_probability)
    if economics is None:
        bot.repository.mark_signal(selected.signal_id, status="SKIP_INVALID_PROPOSAL")
        return
    edge = float(route.predicted_probability) - float(economics.break_even_probability)
    try:
        selected.proposal_ask_price = float(economics.stake)
        selected.proposal_payout = float(economics.payout)
        selected.break_even_probability = float(economics.break_even_probability)
        selected.validated_edge = edge
    except Exception:
        pass
    bot.repository.record_proposal(selected, economics)
    if edge + 1e-12 < route.minimum_edge:
        bot.repository.mark_signal(selected.signal_id, status="SKIP_MULTI_STRATEGY_EDGE")
        bot.logger.info(
            "MULTI_STRATEGY_SKIP family=%s side=%s role=%s symbol=%s predicted=%.5f "
            "break_even=%.5f edge=%.5f required=%.5f",
            route.family,
            route.side,
            route.role,
            selected.symbol,
            route.predicted_probability,
            float(economics.break_even_probability),
            edge,
            route.minimum_edge,
        )
        return
    if bot.market_states[selected.symbol].tick_sequence != int(selected.tick_sequence):
        bot.repository.mark_signal(selected.signal_id, status="SKIP_STALE_SIGNAL", stale=True)
        return

    bot.logger.warning(
        "MULTI_STRATEGY_SIGNAL_SELECTED family=%s side=%s role=%s symbol=%s "
        "contract_type=%s accounts=%s edge=%.5f",
        route.family,
        route.side,
        route.role,
        selected.symbol,
        selected.contract_type,
        len(route.scope_ids),
        edge,
    )
    await bot._buy_selected_accounts(selected, economics)


def _configured_stake(bot: RFDir5TradingBot, token: str, account_id: str, managed_id: int) -> float:
    try:
        profile = bot._managed_account_profile(managed_id)
    except Exception:
        profile = {}
    try:
        state = bot._client_state_for_token(token, account_id=account_id)
    except Exception:
        state = {}
    return max(
        0.35,
        float(
            profile.get("stake_amount")
            or state.get("base_stake")
            or getattr(bot, "base_stake", 0.50)
            or 0.50
        ),
    )


def _mark_virtual_only(bot: RFDir5TradingBot, signal: Any, opened: list[dict[str, Any]], waiting: set[str]) -> None:
    try:
        bot.repository.consume_signal(signal.signal_id)
    except Exception:
        pass
    signal.consumed = True
    status = "VIRTUAL_TRADE" if opened else "VIRTUAL_WAITING_SETTLEMENT"
    bot.repository.mark_signal(
        signal.signal_id,
        status=status,
        purchase_requested=False,
        expected_account_masks=(
            [str(item.get("account") or "") for item in opened]
            if opened
            else sorted(waiting)
        ),
        registered_account_masks=[],
    )
    try:
        bot.rf_repository.set_signal_decision(
            signal.signal_id,
            status,
            "MULTI_STRATEGY_VIRTUAL_NO_PURCHASE",
            selected=True,
            validated_edge=getattr(signal, "validated_edge", None),
        )
    except Exception:
        pass


def _install_purchase_router() -> None:
    original_buy = RFDir5TradingBot._buy_selected_accounts
    if getattr(original_buy, "_multi_strategy_router", False):
        return

    async def routed_buy(self: RFDir5TradingBot, signal: Any, economics: Any) -> None:
        route = getattr(self, "_multi_strategy_signal_routes", {}).get(
            getattr(signal, "signal_id", "")
        )
        if route is None:
            return await original_buy(self, signal, economics)

        eligible = [
            (item.token, item.account_id, item.managed_id, item.mode)
            for item in _strategy_snapshot(self, force=True)
            if item.managed_id in route.scope_ids
        ]
        if not eligible:
            self.repository.mark_signal(signal.signal_id, status="SKIP_NO_STRATEGY_ACCOUNTS")
            return

        repository = self.rf_repository
        real_ids: set[int] = set()
        virtual_opened: list[dict[str, Any]] = []
        virtual_waiting: set[str] = set()

        for token, account_id, managed_id, mode in eligible:
            masked = mask_account_id(account_id)
            if mode != VIRTUAL_MODE:
                real_ids.add(managed_id)
                continue
            configured_stake = _configured_stake(self, token, account_id, managed_id)
            expected_payout = None
            if float(getattr(economics, "stake", 0.0) or 0.0) > 0:
                expected_payout = round(
                    (float(economics.payout) / float(economics.stake))
                    * configured_stake,
                    2,
                )
            virtual = repository.start_virtual_trade(
                managed_account_id=managed_id,
                account_id_masked=masked,
                signal=signal,
                configured_stake=configured_stake,
                simulated_stake=round(configured_stake, 2),
                expected_payout=expected_payout,
            )
            if virtual is None:
                virtual_waiting.add(masked)
            else:
                virtual_opened.append(virtual)
                self._set_account_execution_status(
                    managed_id,
                    "virtual_protection",
                    f"{route.family}/{route.side} virtual confirmation active; no real contract purchased.",
                )
                self.logger.warning(
                    "MULTI_STRATEGY_VIRTUAL_OPENED account=%s family=%s side=%s market=%s "
                    "contract_type=%s actual_financial_impact=0",
                    masked,
                    route.family,
                    route.side,
                    signal.symbol,
                    signal.contract_type,
                )

        if not real_ids:
            _mark_virtual_only(self, signal, virtual_opened, virtual_waiting)
            return

        previous_scope = getattr(self, "_aidr_purchase_scope_ids", None)
        original_protection = repository.virtual_protection_for_account

        def compatible_protection(*args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = dict(original_protection(*args, **kwargs) or {})
            managed_id = kwargs.get("managed_account_id")
            if managed_id is not None and int(managed_id) not in real_ids:
                return payload
            mode = str(payload.get("mode") or NORMAL_MODE)
            contract_type = str(getattr(signal, "contract_type", "") or "").upper()
            if contract_type == "PUT":
                # The old hybrid envelope treated every PUT as recovery-only.
                # A user-selected FALL account is allowed to buy PUT normally;
                # actual DB recovery state remains untouched for stake planning.
                payload["multi_strategy_mode"] = mode
                payload["mode"] = RECOVERY_PENDING
            elif mode == RECOVERY_PENDING:
                # DIGITUNDER and the other primary families must enter the shared
                # real-purchase path while their DB debt still sizes recovery.
                payload["multi_strategy_mode"] = mode
                payload["mode"] = NORMAL_MODE
            return payload

        repository.virtual_protection_for_account = compatible_protection
        self._aidr_purchase_scope_ids = set(real_ids)
        try:
            await original_buy(self, signal, economics)
        finally:
            repository.virtual_protection_for_account = original_protection
            self._aidr_purchase_scope_ids = previous_scope

    routed_buy._multi_strategy_router = True  # type: ignore[attr-defined]
    RFDir5TradingBot._buy_selected_accounts = routed_buy


def install_multi_strategy_runtime() -> None:
    """Install per-account Digits, Even/Odd and Rise/Fall execution routing."""

    global _INSTALLED
    if _INSTALLED:
        return

    _install_parity_virtual_outcome()

    # AIDR remains the exact current production model, but it receives only
    # accounts explicitly configured for Digits -> Over.
    aidr._enabled_accounts = _filter_aidr_over_accounts

    original_init = RFDir5TradingBot.__init__
    original_on_tick = RFDir5TradingBot._on_tick
    original_eligible = RFDir5TradingBot._eligible_purchase_accounts

    def multi_strategy_init(self: RFDir5TradingBot, config_path: str | None = None) -> None:
        original_init(self, config_path)
        self._multi_strategy_original_eligible = lambda: original_eligible(self)
        self._multi_strategy_snapshot: list[AccountRoute] = []
        self._multi_strategy_snapshot_at = 0.0
        self._multi_strategy_candidates: dict[tuple[str, str, str, str], Any] = {}
        self._multi_strategy_signal_routes: dict[str, CandidateRoute] = {}
        self._multi_strategy_task: asyncio.Task | None = None
        self.logger.warning(
            "MULTI_STRATEGY_RUNTIME_ACTIVE version=%s families=digits,parity,direction "
            "sides=over,under,even,odd,rise,fall account_isolation=true",
            MULTI_STRATEGY_VERSION,
        )

    async def multi_strategy_on_tick(
        self: RFDir5TradingBot,
        tick_data: dict[str, Any],
    ) -> None:
        await original_on_tick(self, tick_data)
        try:
            _queue_non_aidr_signals(self, tick_data)
        except Exception:
            self.logger.exception("MULTI_STRATEGY_SIGNAL_GENERATION_FAILED")

    RFDir5TradingBot.__init__ = multi_strategy_init
    RFDir5TradingBot._on_tick = multi_strategy_on_tick
    _install_purchase_router()

    RFDir5TradingBot._multi_strategy_runtime_installed = True
    _INSTALLED = True
