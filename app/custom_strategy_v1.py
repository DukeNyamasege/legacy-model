from __future__ import annotations

import hashlib
import json
import time
import uuid
from decimal import Decimal
from typing import Any, Iterable

from app.hybrid_digit_put import DigitSignal
from app.custom_strategy_virtual_hook import (
    DEFAULT_VIRTUAL_ENTER_AFTER_LOSSES,
    DEFAULT_VIRTUAL_EXIT_AFTER_CONSECUTIVE_WINS,
    normalize_virtual_hook_settings,
)
from app.models import RuntimePreference, utc_now


VERSION = "custom-strategy-v2"
PREFERENCE_PREFIX = "custom_strategy:v1:"
SUPPORTED_MARKETS = (
    "1HZ100V",
    "1HZ10V",
    "1HZ25V",
    "1HZ50V",
    "1HZ75V",
    "R_10",
    "R_25",
    "R_50",
    "R_75",
    "R_100",
)
TRADE_TYPES: dict[str, dict[str, Any]] = {
    "even": {"label": "Even", "contract_type": "DIGITEVEN"},
    "odd": {"label": "Odd", "contract_type": "DIGITODD"},
    "over": {"label": "Over", "contract_type": "DIGITOVER"},
    "under": {"label": "Under", "contract_type": "DIGITUNDER"},
    "matches": {"label": "Matches", "contract_type": "DIGITMATCH"},
    "differs": {"label": "Differs", "contract_type": "DIGITDIFF"},
    "rise": {"label": "Rise", "contract_type": "CALL"},
    "fall": {"label": "Fall", "contract_type": "PUT"},
}
COMPARATORS = ("<", "<=", "==", "!=", ">=", ">", "all_same")
NUMERIC_COMPARATORS = ("<", "<=", "==", "!=", ">=", ">")
CONDITION_KINDS = ("digit_parity", "digit_compare", "direction", "percentage")
MAX_CONDITIONS = 12
MAX_WINDOW = 1000
MIN_DURATION_TICKS = 1
MAX_DURATION_TICKS = 100
DEFAULT_DURATION_TICKS = 1


def _key(managed_account_id: int) -> str:
    return f"{PREFERENCE_PREFIX}{int(managed_account_id)}"


def default_custom_strategy() -> dict[str, Any]:
    return {
        "version": VERSION,
        "configured": False,
        "market_mode": "all",
        "markets": [],
        "trade_type": "even",
        "prediction": None,
        "duration_ticks": DEFAULT_DURATION_TICKS,
        "conditions": [],
        "match": "all",
        "reanalyze": {
            "mode": "after_every_trade",
            "losses": 1,
            "wins": 1,
        },
        "virtual_hook_enabled": True,
        "virtual_hook": {
            "enabled": True,
            "enter_after_losses": DEFAULT_VIRTUAL_ENTER_AFTER_LOSSES,
            "exit_after_consecutive_wins": DEFAULT_VIRTUAL_EXIT_AFTER_CONSECUTIVE_WINS,
        },
    }


def _window(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Condition window must be a whole number") from exc
    if not 1 <= result <= MAX_WINDOW:
        raise ValueError(f"Condition window must be between 1 and {MAX_WINDOW}")
    return result


def _duration_ticks(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Contract duration must be a whole number of ticks") from exc
    if not MIN_DURATION_TICKS <= result <= MAX_DURATION_TICKS:
        raise ValueError(
            f"Contract duration must be between {MIN_DURATION_TICKS} and "
            f"{MAX_DURATION_TICKS} ticks"
        )
    return result


def _digit(value: Any, *, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole digit") from exc
    if not 0 <= result <= 9:
        raise ValueError(f"{label} must be between 0 and 9")
    return result


def _threshold(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Percentage threshold must be a number") from exc
    if not 0 <= result <= 100:
        raise ValueError("Percentage threshold must be between 0 and 100")
    return round(result, 4)


def _normalize_reanalyze(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    mode = str(source.get("mode") or "after_every_trade").strip().lower()
    if mode not in {"after_every_trade", "after_loss", "after_win", "custom"}:
        mode = "after_every_trade"
    try:
        losses = int(source.get("losses", 1))
    except (TypeError, ValueError):
        losses = 1
    try:
        wins = int(source.get("wins", 1))
    except (TypeError, ValueError):
        wins = 1
    return {
        "mode": mode,
        "losses": max(1, min(50, losses)),
        "wins": max(1, min(50, wins)),
    }


def _normalize_direction(value: Any) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_")
    if raw in {"rise", "rising", "up"}:
        return "rising"
    if raw in {"fall", "falling", "down"}:
        return "falling"
    if raw in {"no_move", "nomove", "flat", "same", "equal"}:
        return "no_move"
    raise ValueError("Tick direction must be Rising, Falling, or No Move")


def normalize_condition(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Every custom strategy condition must be an object")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in CONDITION_KINDS:
        raise ValueError(
            "Condition type must be digit_parity, digit_compare, direction, or percentage"
        )
    window = _window(raw.get("window"))
    if kind == "digit_parity":
        parity = str(raw.get("parity") or "").strip().lower()
        if parity not in {"even", "odd"}:
            raise ValueError("Digit parity must be even or odd")
        return {"kind": kind, "window": window, "parity": parity}
    if kind == "digit_compare":
        operator = str(raw.get("operator") or "").strip()
        if operator not in COMPARATORS:
            raise ValueError(
                "Digit comparator must be one of <, <=, ==, !=, >=, >, all_same"
            )
        value = 0 if operator == "all_same" else _digit(raw.get("value"), label="Comparator value")
        return {
            "kind": kind,
            "window": window,
            "operator": operator,
            "value": value,
        }
    if kind == "percentage":
        target = str(raw.get("target") or "").strip().lower()
        if target not in {"even", "odd", "over", "under", "digit", "rise", "fall", "no_move"}:
            raise ValueError(
                "Percentage target must be even, odd, over, under, digit, rise, fall, or no_move"
            )
        operator = str(raw.get("operator") or "").strip()
        if operator not in NUMERIC_COMPARATORS:
            raise ValueError(
                "Percentage comparator must be one of <, <=, ==, !=, >=, >"
            )
        value = None
        if target in {"over", "under", "digit"}:
            value = _digit(raw.get("value"), label="Percentage target digit")
        return {
            "kind": kind,
            "window": window,
            "target": target,
            "operator": operator,
            "threshold": _threshold(raw.get("threshold")),
            "value": value,
        }
    direction = _normalize_direction(raw.get("direction"))
    return {"kind": kind, "window": window, "direction": direction}


def normalize_custom_strategy(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    market_mode = str(source.get("market_mode") or "all").strip().lower()
    if market_mode == "one":
        market_mode = "single"
    if market_mode not in {"all", "selected", "single"}:
        raise ValueError("Market mode must be all, selected, or single")

    requested_markets = source.get("markets") or []
    if isinstance(requested_markets, str):
        requested_markets = [requested_markets]
    if not isinstance(requested_markets, list):
        raise ValueError("Selected markets must be a list")
    markets: list[str] = []
    for value in requested_markets:
        symbol = str(value or "").strip().upper()
        if symbol not in SUPPORTED_MARKETS:
            raise ValueError(f"Unsupported custom strategy market: {symbol or '-'}")
        if symbol not in markets:
            markets.append(symbol)
    if market_mode in {"selected", "single"} and not markets:
        raise ValueError("Select at least one market or choose All Markets")
    if market_mode == "single":
        markets = markets[:1]
    if market_mode == "all":
        markets = []

    trade_type = str(source.get("trade_type") or "").strip().lower()
    if trade_type == "higher":
        trade_type = "rise"
    elif trade_type == "lower":
        trade_type = "fall"
    if trade_type not in TRADE_TYPES:
        raise ValueError(
            "Trade type must be rise, fall, even, odd, over, under, matches, or differs"
        )

    prediction: int | None = None
    if trade_type in {"over", "under", "matches", "differs"}:
        prediction = _digit(source.get("prediction"), label="Prediction")
        if trade_type == "over" and prediction > 8:
            raise ValueError("Over prediction must be between 0 and 8")
        if trade_type == "under" and prediction < 1:
            raise ValueError("Under prediction must be between 1 and 9")

    # Contract duration is separate from every condition's lookback window.
    # Existing V1 configurations remain valid and default to one tick.
    duration_ticks = _duration_ticks(
        source.get("duration_ticks", DEFAULT_DURATION_TICKS)
    )

    conditions_raw = source.get("conditions") or []
    if not isinstance(conditions_raw, list):
        raise ValueError("Conditions must be a list")
    if not 1 <= len(conditions_raw) <= MAX_CONDITIONS:
        raise ValueError(
            f"Custom Strategy requires between 1 and {MAX_CONDITIONS} conditions"
        )
    conditions = [normalize_condition(item) for item in conditions_raw]
    virtual_hook = normalize_virtual_hook_settings(source)

    # Custom Strategy intentionally uses AND only. It mirrors the requested
    # examples where each additional condition further narrows the entry pattern.
    match = str(source.get("match") or "all").strip().lower()
    if match not in {"all", "and"}:
        raise ValueError("Custom Strategy combines conditions with AND")

    return {
        "version": VERSION,
        "configured": True,
        "market_mode": market_mode,
        "markets": markets,
        "trade_type": trade_type,
        "prediction": prediction,
        "duration_ticks": duration_ticks,
        "conditions": conditions,
        "match": "all",
        "reanalyze": _normalize_reanalyze(source.get("reanalyze")),
        "virtual_hook_enabled": bool(virtual_hook.enabled),
        "virtual_hook": {
            "enabled": bool(virtual_hook.enabled),
            "enter_after_losses": int(virtual_hook.enter_after_losses),
            "exit_after_consecutive_wins": int(
                virtual_hook.exit_after_consecutive_wins
            ),
        },
    }


def custom_strategy_fingerprint(config: dict[str, Any]) -> str:
    canonical = json.dumps(
        normalize_custom_strategy(config),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def read_custom_strategy(database: Any, managed_account_id: int) -> dict[str, Any]:
    with database.session() as session:
        row = session.get(RuntimePreference, _key(managed_account_id))
        raw = str(row.preference_value or "") if row else ""
    if not raw:
        return default_custom_strategy()
    try:
        payload = json.loads(raw)
        return normalize_custom_strategy(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default_custom_strategy()


def write_custom_strategy(
    session: Any,
    managed_account_id: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_custom_strategy(config)
    preference_key = _key(managed_account_id)
    row = session.get(RuntimePreference, preference_key)
    value = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    if row is None:
        session.add(
            RuntimePreference(
                preference_key=preference_key,
                preference_value=value,
            )
        )
    else:
        row.preference_value = value
        row.updated_at = utc_now()
    return normalized


def market_selected(config: dict[str, Any], symbol: str) -> bool:
    normalized_symbol = str(symbol or "").strip().upper()
    if normalized_symbol not in SUPPORTED_MARKETS:
        return False
    if str(config.get("market_mode") or "all") == "all":
        return True
    return normalized_symbol in set(config.get("markets") or [])


def _compare(value: int, operator: str, target: int) -> bool:
    if operator == "<":
        return value < target
    if operator == "<=":
        return value <= target
    if operator == "==":
        return value == target
    if operator == "!=":
        return value != target
    if operator == ">=":
        return value >= target
    if operator == ">":
        return value > target
    return False


def _compare_float(value: float, operator: str, target: float) -> bool:
    if operator == "<":
        return value < target
    if operator == "<=":
        return value <= target
    if operator == "==":
        return abs(value - target) < 0.000001
    if operator == "!=":
        return abs(value - target) >= 0.000001
    if operator == ">=":
        return value >= target
    if operator == ">":
        return value > target
    return False


def condition_matches(
    condition: dict[str, Any],
    *,
    digits: list[int],
    quotes: list[Decimal],
) -> bool:
    kind = str(condition.get("kind") or "")
    window = int(condition.get("window") or 0)
    if window <= 0:
        return False

    if kind == "digit_parity":
        if len(digits) < window:
            return False
        sample = digits[-window:]
        wants_even = str(condition.get("parity") or "") == "even"
        return all((digit % 2 == 0) == wants_even for digit in sample)

    if kind == "digit_compare":
        if len(digits) < window:
            return False
        operator = str(condition.get("operator") or "")
        if operator == "all_same":
            sample = digits[-window:]
            return bool(sample) and all(int(digit) == int(sample[0]) for digit in sample)
        target = int(condition.get("value"))
        return all(_compare(int(digit), operator, target) for digit in digits[-window:])

    if kind == "direction":
        # Last N tick directions require N movements and therefore N+1 quotes.
        if len(quotes) < window + 1:
            return False
        sample = quotes[-(window + 1) :]
        moves = [later - earlier for earlier, later in zip(sample[:-1], sample[1:])]
        direction = str(condition.get("direction") or "")
        if direction in {"rise", "rising"}:
            return all(move > 0 for move in moves)
        if direction in {"fall", "falling"}:
            return all(move < 0 for move in moves)
        return all(move == 0 for move in moves)

    if kind == "percentage":
        target = str(condition.get("target") or "")
        operator = str(condition.get("operator") or "")
        threshold = float(condition.get("threshold") or 0.0)
        matches = 0
        total = 0
        if target in {"rise", "fall", "no_move"}:
            if len(quotes) < window + 1:
                return False
            sample = quotes[-(window + 1) :]
            moves = [later - earlier for earlier, later in zip(sample[:-1], sample[1:])]
            total = len(moves)
            if target == "rise":
                matches = sum(1 for move in moves if move > 0)
            elif target == "fall":
                matches = sum(1 for move in moves if move < 0)
            else:
                matches = sum(1 for move in moves if move == 0)
        else:
            if len(digits) < window:
                return False
            sample_digits = digits[-window:]
            total = len(sample_digits)
            if target == "even":
                matches = sum(1 for digit in sample_digits if digit % 2 == 0)
            elif target == "odd":
                matches = sum(1 for digit in sample_digits if digit % 2 == 1)
            elif target == "over":
                value = int(condition.get("value"))
                matches = sum(1 for digit in sample_digits if digit > value)
            elif target == "under":
                value = int(condition.get("value"))
                matches = sum(1 for digit in sample_digits if digit < value)
            elif target == "digit":
                value = int(condition.get("value"))
                matches = sum(1 for digit in sample_digits if digit == value)
        if total <= 0:
            return False
        percentage = matches * 100.0 / total
        return _compare_float(percentage, operator, threshold)

    return False


def evaluate_custom_strategy(
    config: dict[str, Any],
    *,
    digits: Iterable[int],
    quotes: Iterable[Decimal | str | float | int],
) -> bool:
    normalized = normalize_custom_strategy(config)
    digit_values = [int(value) for value in digits if 0 <= int(value) <= 9]
    quote_values = [
        value if isinstance(value, Decimal) else Decimal(str(value)) for value in quotes
    ]
    return all(
        condition_matches(condition, digits=digit_values, quotes=quote_values)
        for condition in normalized["conditions"]
    )


def contract_for_config(config: dict[str, Any]) -> tuple[str, str, str]:
    normalized = normalize_custom_strategy(config)
    trade_type = str(normalized["trade_type"])
    contract_type = str(TRADE_TYPES[trade_type]["contract_type"])
    prediction = normalized.get("prediction")
    barrier = str(prediction) if trade_type in {"over", "under"} else ""
    if trade_type == "over":
        direction = f"OVER_{prediction}"
    elif trade_type == "under":
        direction = f"UNDER_{prediction}"
    elif trade_type == "matches":
        direction = f"MATCHES_{prediction}"
    elif trade_type == "differs":
        direction = f"DIFFERS_{prediction}"
    else:
        direction = trade_type.upper()
    if trade_type in {"matches", "differs"}:
        barrier = str(prediction)
    return contract_type, direction, barrier


def nominal_probability(config: dict[str, Any]) -> float:
    normalized = normalize_custom_strategy(config)
    trade_type = str(normalized["trade_type"])
    prediction = normalized.get("prediction")
    if trade_type == "over":
        return max(0.01, min(0.99, (9 - int(prediction)) / 10.0))
    if trade_type == "under":
        return max(0.01, min(0.99, int(prediction) / 10.0))
    if trade_type == "matches":
        return 0.10
    if trade_type == "differs":
        return 0.90
    return 0.50


def build_custom_signal(
    bot: Any,
    *,
    symbol: str,
    tick: dict[str, Any],
    config: dict[str, Any],
) -> DigitSignal:
    normalized = normalize_custom_strategy(config)
    market = bot.market_states[str(symbol)]
    digits = [int(value) for value in market.raw_tick_digits if 0 <= int(value) <= 9]
    contract_type, direction, barrier = contract_for_config(normalized)
    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    probability = nominal_probability(normalized)
    trigger_digits = tuple(digits[-100:])
    if not trigger_digits:
        trigger_digits = (0,)
    fingerprint = custom_strategy_fingerprint(normalized)
    return DigitSignal(
        signal_id=str(uuid.uuid4()),
        run_id=bot.test2_config.model.run_id,
        strategy_version=VERSION,
        symbol=str(symbol),
        direction=direction,
        contract_type=contract_type,
        duration_ticks=int(normalized["duration_ticks"]),
        reference_entry_quote=quote,
        quality_score=10,
        signal_tick_epoch=epoch,
        signal_tick_id=bot._tick_identity(str(symbol), epoch, quote),
        generated_at=utc_now().isoformat(),
        generated_monotonic=time.monotonic(),
        connection_session_id=bot.connection_session_id,
        tick_sequence=int(market.tick_sequence),
        barrier=barrier,
        trigger_name=f"CUSTOM-V2-{fingerprint[:8].upper()}",
        trigger_digits=trigger_digits,
        signal_last_digit=int(trigger_digits[-1]),
        p100=probability,
        p500=probability,
        p1000=probability,
        lower95=probability,
        weighted_probability=probability,
    )


def describe_condition(condition: dict[str, Any]) -> str:
    normalized = normalize_condition(condition)
    window = int(normalized["window"])
    if normalized["kind"] == "digit_parity":
        return f"last {window} digit(s) are {str(normalized['parity']).title()}"
    if normalized["kind"] == "digit_compare":
        if normalized["operator"] == "all_same":
            return f"last {window} digit(s) are all same"
        return (
            f"last {window} digit(s) are {normalized['operator']} "
            f"{normalized['value']}"
        )
    if normalized["kind"] == "percentage":
        target = str(normalized["target"])
        value = normalized.get("value")
        label = target
        if target in {"over", "under"}:
            label = f"{target} {value}"
        elif target == "digit":
            label = f"digit {value}"
        return (
            f"{label} percentage over last {window} tick(s) is "
            f"{normalized['operator']} {normalized['threshold']}%"
        )
    return (
        f"last {window} tick direction(s) are "
        f"{str(normalized['direction']).replace('_', ' ').title()}"
    )


def describe_custom_strategy(config: dict[str, Any]) -> str:
    normalized = normalize_custom_strategy(config)
    trade = str(TRADE_TYPES[str(normalized["trade_type"])]["label"])
    if normalized.get("prediction") is not None:
        trade += f" {normalized['prediction']}"
    conditions = " AND ".join(describe_condition(item) for item in normalized["conditions"])
    markets = (
        "all markets"
        if normalized["market_mode"] == "all"
        else ", ".join(normalized["markets"])
    )
    duration = int(normalized["duration_ticks"])
    unit = "tick" if duration == 1 else "ticks"
    return f"IF {conditions} THEN BUY {trade} on {markets} for {duration} {unit}"
