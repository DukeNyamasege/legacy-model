from __future__ import annotations

import hashlib
import json
import time
import uuid
from decimal import Decimal
from typing import Any, Iterable

from app.hybrid_digit_put import DigitSignal
from app.models import RuntimePreference, utc_now


VERSION = "custom-strategy-v1"
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
    "rise": {"label": "Rise", "contract_type": "CALL"},
    "fall": {"label": "Fall", "contract_type": "PUT"},
}
COMPARATORS = ("<", "<=", "==", "!=", ">=", ">")
CONDITION_KINDS = ("digit_parity", "digit_compare", "direction")
MAX_CONDITIONS = 12
MAX_WINDOW = 100


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
        "conditions": [],
        "match": "all",
    }


def _window(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Condition window must be a whole number") from exc
    if not 1 <= result <= MAX_WINDOW:
        raise ValueError(f"Condition window must be between 1 and {MAX_WINDOW}")
    return result


def _digit(value: Any, *, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole digit") from exc
    if not 0 <= result <= 9:
        raise ValueError(f"{label} must be between 0 and 9")
    return result


def normalize_condition(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Every custom strategy condition must be an object")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in CONDITION_KINDS:
        raise ValueError(
            "Condition type must be digit_parity, digit_compare, or direction"
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
                "Digit comparator must be one of <, <=, ==, !=, >=, >"
            )
        value = _digit(raw.get("value"), label="Comparator value")
        return {
            "kind": kind,
            "window": window,
            "operator": operator,
            "value": value,
        }
    direction = str(raw.get("direction") or "").strip().lower()
    if direction not in {"rise", "fall"}:
        raise ValueError("Tick direction must be rise or fall")
    return {"kind": kind, "window": window, "direction": direction}


def normalize_custom_strategy(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    market_mode = str(source.get("market_mode") or "all").strip().lower()
    if market_mode not in {"all", "selected"}:
        raise ValueError("Market mode must be all or selected")

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
    if market_mode == "selected" and not markets:
        raise ValueError("Select at least one market or choose All Markets")
    if market_mode == "all":
        markets = []

    trade_type = str(source.get("trade_type") or "").strip().lower()
    if trade_type not in TRADE_TYPES:
        raise ValueError("Trade type must be rise, fall, even, odd, over, or under")

    prediction: int | None = None
    if trade_type in {"over", "under"}:
        prediction = _digit(source.get("prediction"), label="Prediction")
        if trade_type == "over" and prediction > 8:
            raise ValueError("Over prediction must be between 0 and 8")
        if trade_type == "under" and prediction < 1:
            raise ValueError("Under prediction must be between 1 and 9")

    conditions_raw = source.get("conditions") or []
    if not isinstance(conditions_raw, list):
        raise ValueError("Conditions must be a list")
    if not 1 <= len(conditions_raw) <= MAX_CONDITIONS:
        raise ValueError(
            f"Custom Strategy requires between 1 and {MAX_CONDITIONS} conditions"
        )
    conditions = [normalize_condition(item) for item in conditions_raw]

    # V1 intentionally uses AND only. It mirrors the requested examples where a
    # second condition further narrows the first pattern and avoids hidden OR
    # precedence in a financial execution rule.
    match = str(source.get("match") or "all").strip().lower()
    if match not in {"all", "and"}:
        raise ValueError("Custom Strategy V1 combines conditions with AND")

    return {
        "version": VERSION,
        "configured": True,
        "market_mode": market_mode,
        "markets": markets,
        "trade_type": trade_type,
        "prediction": prediction,
        "conditions": conditions,
        "match": "all",
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
        target = int(condition.get("value"))
        return all(_compare(int(digit), operator, target) for digit in digits[-window:])

    if kind == "direction":
        # Last N tick directions require N movements and therefore N+1 quotes.
        if len(quotes) < window + 1:
            return False
        sample = quotes[-(window + 1) :]
        moves = [later - earlier for earlier, later in zip(sample[:-1], sample[1:])]
        if str(condition.get("direction") or "") == "rise":
            return all(move > 0 for move in moves)
        return all(move < 0 for move in moves)

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
    else:
        direction = trade_type.upper()
    return contract_type, direction, barrier


def nominal_probability(config: dict[str, Any]) -> float:
    normalized = normalize_custom_strategy(config)
    trade_type = str(normalized["trade_type"])
    prediction = normalized.get("prediction")
    if trade_type == "over":
        return max(0.01, min(0.99, (9 - int(prediction)) / 10.0))
    if trade_type == "under":
        return max(0.01, min(0.99, int(prediction) / 10.0))
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
        duration_ticks=1,
        reference_entry_quote=quote,
        quality_score=10,
        signal_tick_epoch=epoch,
        signal_tick_id=bot._tick_identity(str(symbol), epoch, quote),
        generated_at=utc_now().isoformat(),
        generated_monotonic=time.monotonic(),
        connection_session_id=bot.connection_session_id,
        tick_sequence=int(market.tick_sequence),
        barrier=barrier,
        trigger_name=f"CUSTOM-V1-{fingerprint[:8].upper()}",
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
        return (
            f"last {window} digit(s) are {normalized['operator']} "
            f"{normalized['value']}"
        )
    return (
        f"last {window} tick direction(s) are "
        f"{str(normalized['direction']).title()}"
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
    return f"IF {conditions} THEN BUY {trade} on {markets}"
