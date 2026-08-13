from __future__ import annotations

import json
from typing import Any

from app.custom_strategy_comparator_extension import (
    install_custom_strategy_comparator_extension,
)

install_custom_strategy_comparator_extension()

from app.custom_strategy_v1 import (  # noqa: E402
    DEFAULT_DURATION_TICKS,
    MAX_CONDITIONS,
    MAX_DURATION_TICKS,
    MIN_DURATION_TICKS,
    TRADE_TYPES,
    describe_condition,
    normalize_condition,
    normalize_custom_strategy,
)
from app.models import RuntimePreference, utc_now  # noqa: E402


VERSION = "custom-result-routing-v1"
PREFERENCE_PREFIX = "custom_result_routing:v1:"
AFTER_WIN = "after_win"
AFTER_LOSS = "after_loss"
_DYNAMIC_PREDICTIONS = {
    "last_digit": "last digit",
    "most_appearing": "most appearing",
    "second_most_appearing": "second most appearing",
}
_DYNAMIC_ALIASES = {
    "last": "last_digit",
    "last digit": "last_digit",
    "last_digit": "last_digit",
    "most": "most_appearing",
    "most appearing": "most_appearing",
    "most_appearing": "most_appearing",
    "second most": "second_most_appearing",
    "second most appearing": "second_most_appearing",
    "second_most_appearing": "second_most_appearing",
}


def preference_key(managed_account_id: int) -> str:
    return f"{PREFERENCE_PREFIX}{int(managed_account_id)}"


def _normalize_trade_type(value: Any) -> str:
    trade_type = str(value or "").strip().lower()
    if trade_type == "higher":
        trade_type = "rise"
    elif trade_type == "lower":
        trade_type = "fall"
    if trade_type not in TRADE_TYPES:
        raise ValueError(
            "Recovery trade type must be rise, fall, even, odd, over, under, matches, or differs"
        )
    return trade_type


def _normalize_prediction(trade_type: str, value: Any) -> int | str | None:
    if trade_type not in {"over", "under", "matches", "differs"}:
        return None
    if trade_type in {"matches", "differs"} and isinstance(value, str):
        dynamic = _DYNAMIC_ALIASES.get(value.strip().lower())
        if dynamic:
            return dynamic
    try:
        prediction = int(value)
    except (TypeError, ValueError) as exc:
        if trade_type in {"matches", "differs"}:
            raise ValueError(
                "Recovery prediction must be 0-9, Last digit, Most appearing, or Second most appearing"
            ) from exc
        raise ValueError("Recovery prediction must be a whole digit") from exc
    if not 0 <= prediction <= 9:
        raise ValueError("Recovery prediction must be between 0 and 9")
    if trade_type == "over" and prediction > 8:
        raise ValueError("Recovery Over prediction must be between 0 and 8")
    if trade_type == "under" and prediction < 1:
        raise ValueError("Recovery Under prediction must be between 1 and 9")
    return prediction


def _normalize_duration(value: Any) -> int:
    try:
        duration = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Recovery contract duration must be a whole number of ticks") from exc
    if not MIN_DURATION_TICKS <= duration <= MAX_DURATION_TICKS:
        raise ValueError(
            f"Recovery contract duration must be between {MIN_DURATION_TICKS} and {MAX_DURATION_TICKS} ticks"
        )
    return duration


def normalize_result_route(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("After-loss recovery strategy must be an object")
    trade_type = _normalize_trade_type(raw.get("trade_type"))
    prediction = _normalize_prediction(trade_type, raw.get("prediction"))
    duration_ticks = _normalize_duration(
        raw.get("duration_ticks", DEFAULT_DURATION_TICKS)
    )
    conditions_raw = raw.get("conditions") or []
    if not isinstance(conditions_raw, list):
        raise ValueError("Recovery conditions must be a list")
    if not 1 <= len(conditions_raw) <= MAX_CONDITIONS:
        raise ValueError(
            f"Recovery strategy requires between 1 and {MAX_CONDITIONS} conditions"
        )
    conditions = [normalize_condition(item) for item in conditions_raw]
    return {
        "trade_type": trade_type,
        "prediction": prediction,
        "duration_ticks": duration_ticks,
        "conditions": conditions,
        "match": "all",
    }


def normalize_result_routing(
    raw: Any,
    *,
    fallback_after_loss: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    enabled = bool(source.get("enabled", False))
    route_raw = source.get(AFTER_LOSS)
    if route_raw is None:
        route_raw = fallback_after_loss
    after_loss = normalize_result_route(route_raw) if isinstance(route_raw, dict) else None
    if enabled and after_loss is None:
        raise ValueError(
            "Enable Result-Based Trading only after configuring the After Loss strategy"
        )
    return {
        "version": VERSION,
        "enabled": enabled,
        AFTER_WIN: "primary",
        AFTER_LOSS: after_loss,
    }


def read_result_routing(
    database: Any,
    managed_account_id: int,
) -> dict[str, Any]:
    with database.session() as session:
        row = session.get(RuntimePreference, preference_key(managed_account_id))
        raw = str(row.preference_value or "") if row else ""
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    try:
        return normalize_result_routing(payload)
    except ValueError:
        return normalize_result_routing({"enabled": False})


def write_result_routing(
    session: Any,
    managed_account_id: int,
    routing: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_result_routing(routing)
    key = preference_key(managed_account_id)
    row = session.get(RuntimePreference, key)
    value = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=value))
    else:
        row.preference_value = value
        row.updated_at = utc_now()
    return normalized


def merge_result_route(
    base_config: dict[str, Any],
    routing: dict[str, Any] | None,
    route: str,
) -> dict[str, Any]:
    base = normalize_custom_strategy(base_config)
    normalized = normalize_result_routing(routing or {"enabled": False})
    if route != AFTER_LOSS or not bool(normalized.get("enabled")):
        return base
    override = normalized.get(AFTER_LOSS)
    if not isinstance(override, dict):
        return base

    result = dict(base)
    prediction = override.get("prediction")
    dynamic = prediction if isinstance(prediction, str) and prediction in _DYNAMIC_PREDICTIONS else ""
    reanalyze = dict(result.get("reanalyze") or {})
    if dynamic:
        reanalyze["prediction_mode"] = dynamic
        resolved_prediction: int | None = None
    else:
        reanalyze.pop("prediction_mode", None)
        resolved_prediction = int(prediction) if prediction is not None else None

    result.update(
        {
            "trade_type": override["trade_type"],
            "prediction": resolved_prediction,
            "duration_ticks": int(override["duration_ticks"]),
            "conditions": [dict(item) for item in override["conditions"]],
            "match": "all",
            "reanalyze": reanalyze,
        }
    )
    return normalize_custom_strategy(result)


def describe_result_routing(routing: dict[str, Any] | None) -> str:
    normalized = normalize_result_routing(routing or {"enabled": False})
    if not bool(normalized.get("enabled")):
        return "Result-Based Trading is off; the primary strategy is used after both wins and losses."
    route = normalized.get(AFTER_LOSS) or {}
    trade_type = str(route.get("trade_type") or "")
    label = str(TRADE_TYPES.get(trade_type, {}).get("label") or trade_type.title())
    prediction = route.get("prediction")
    if isinstance(prediction, str) and prediction in _DYNAMIC_PREDICTIONS:
        label = f"{label} {_DYNAMIC_PREDICTIONS[prediction]}"
    elif prediction is not None:
        label = f"{label} {prediction}"
    conditions = " AND ".join(
        describe_condition(item) for item in list(route.get("conditions") or [])
    )
    duration = int(route.get("duration_ticks") or 1)
    unit = "tick" if duration == 1 else "ticks"
    return (
        "After a win (and for the first trade), use the primary strategy. "
        f"After an actual loss or while recovery debt remains: IF {conditions} "
        f"THEN BUY {label} for {duration} {unit}."
    )
