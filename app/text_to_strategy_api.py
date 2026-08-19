from __future__ import annotations

from app.route_utils import remove_route as _remove_route

import re
from typing import Any

from fastapi import HTTPException, Request

import app.api as base_api


_INSTALLED = False
_MAX_WORDS = 250
_SUPPORTED_MARKET_NUMBERS = (10, 25, 50, 75, 100)




def _words(text: str) -> list[str]:
    return re.findall(r"\b[\w%.$+-]+\b", text, flags=re.UNICODE)


def _number_word(value: str) -> int | None:
    raw = str(value or "").strip().lower()
    names = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    if raw in names:
        return names[raw]
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _money(text: str, labels: tuple[str, ...]) -> float | None:
    label = "|".join(re.escape(item) for item in labels)
    match = re.search(
        rf"(?:{label})\s*(?:of|at|is|with|=|:)?\s*\$?\s*(\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _operator(text: str, fallback: str = "<=") -> str:
    lowered = text.lower()
    if re.search(r"less than or equal|lower than or equal|at most|<=|not more than", lowered):
        return "<="
    if re.search(r"greater than or equal|more than or equal|at least|>=|not less than", lowered):
        return ">="
    if re.search(r"less than|lower than|below|under\s+the\s+value|<", lowered):
        return "<"
    if re.search(r"greater than|more than|above|over\s+the\s+value|>", lowered):
        return ">"
    if re.search(r"equal to|equals|==", lowered):
        return "=="
    return fallback


def _market_selection(text: str, adjustments: list[str]) -> tuple[str, list[str], str]:
    lowered = text.lower()
    if "all markets" in lowered or "every market" in lowered or "all volatility" in lowered:
        return "all", [], "All supported markets"

    matches: list[tuple[int, bool]] = []
    for number in _SUPPORTED_MARKET_NUMBERS:
        one_second = re.search(
            rf"(?:volatility|vol|v)\s*{number}\s*(?:\(?\s*1\s*s\s*\)?|1s|one\s*second)",
            lowered,
        )
        normal = re.search(rf"(?:volatility|vol|v)\s*{number}\b", lowered)
        symbol_one = re.search(rf"\b1hz{number}v\b", lowered)
        symbol_normal = re.search(rf"\br[_-]?{number}\b", lowered)
        if one_second or symbol_one:
            matches.append((number, True))
        elif normal or symbol_normal:
            matches.append((number, False))

    unique: list[str] = []
    labels: list[str] = []
    for number, one_second in matches:
        symbol = f"1HZ{number}V" if one_second else f"R_{number}"
        if symbol in unique:
            continue
        unique.append(symbol)
        labels.append(f"Volatility {number}{' (1s)' if one_second else ''}")

    if not unique:
        adjustments.append("No supported market was stated, so Volatility 100 (1s) was selected.")
        return "single", ["1HZ100V"], "Volatility 100 (1s)"
    if len(unique) == 1:
        return "single", unique, labels[0]
    return "selected", unique, ", ".join(labels)


def _trade_selection(text: str, adjustments: list[str]) -> tuple[str, str, int | None, str]:
    lowered = text.lower()
    candidates = [
        (r"\b(?:digit\s+)?over\s*([0-9])\b", "over_under", "over", True, "Digit Over"),
        (r"\b(?:digit\s+)?under\s*([0-9])\b", "over_under", "under", True, "Digit Under"),
        (r"\bmatches?\s*([0-9])\b", "matches_differs", "matches", True, "Matches"),
        (r"\bdiffers?\s*(?:from)?\s*([0-9])?\b", "matches_differs", "differs", True, "Differs"),
        (r"\beven\b", "odd_even", "even", False, "Even"),
        (r"\bodd\b", "odd_even", "odd", False, "Odd"),
        (r"\b(?:rise|rising|up\s*ticks?)\b", "rise_fall", "rise", False, "Rise"),
        (r"\b(?:fall|falling|down\s*ticks?)\b", "rise_fall", "fall", False, "Fall"),
    ]
    for pattern, group, side, prediction_needed, label in candidates:
        match = re.search(pattern, lowered)
        if not match:
            continue
        prediction: int | None = None
        if prediction_needed:
            raw = match.group(1) if match.lastindex else None
            prediction = _number_word(raw or "")
            if prediction is None:
                prediction = 3 if side == "over" else 6 if side == "under" else 5
                adjustments.append(
                    f"{label} was recognized without a clear digit, so digit {prediction} was selected."
                )
        return group, side, prediction, f"{label}{'' if prediction is None else f' {prediction}'}"

    adjustments.append("No supported contract type was clear, so Digit Over 3 was selected as the nearest supported default.")
    return "over_under", "over", 3, "Digit Over 3"


def _strategy_name(text: str) -> str:
    match = re.search(
        r"(?:strategy|bot)\s+(?:is\s+)?(?:called|named)\s+([a-z0-9][a-z0-9 _-]{1,58}?)(?=[.,;]|\s+(?:trade|when|that|which|using|on)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"(?:called|named)\s+([a-z0-9][a-z0-9 _-]{1,58}?)(?=[.,;]|\s+(?:trade|when|that|which|using|on)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
    if not match:
        return "AI Strategy"
    value = re.sub(r"\s+", " ", match.group(1)).strip(" -_")
    return value[:60] or "AI Strategy"


def _last_digit_condition(
    text: str,
    trade_side: str,
    prediction: int | None,
) -> dict[str, Any] | None:
    lowered = text.lower()
    match = re.search(
        r"last\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:digits?|numbers?)\s+(?:are|is|must\s+be|should\s+be|be)?\s*([^.,;]+)",
        lowered,
    )
    if not match:
        return None
    window = _number_word(match.group(1)) or 3
    clause = match.group(2)
    value_match = re.search(r"([0-9])", clause)
    value = _number_word(value_match.group(1) if value_match else "")
    if value is None:
        value = prediction if prediction is not None else 5
    fallback = ">=" if trade_side == "under" else "<="
    return {
        "kind": "digit_compare",
        "window": max(1, min(1000, int(window))),
        "operator": _operator(clause, fallback),
        "value": max(0, min(9, int(value))),
    }


def _percentage_condition(
    text: str,
    trade_side: str,
    prediction: int | None,
) -> dict[str, Any] | None:
    lowered = text.lower()
    percent_match = re.search(
        r"(?:percentage|percent|%)\s*(?:of|for)?\s*([^.,;]{0,90}?)\s*(?:is|must\s+be|should\s+be|be)?\s*(?:above|over|greater\s+than|at\s+least|>=|>)\s*(\d+(?:\.\d+)?)\s*%?",
        lowered,
    )
    reverse_match = re.search(
        r"(?:over|under|even|odd|digit|matches?)\s*([0-9])?\s*(?:percentage|percent|%)?\s*(?:over|across|in|using)?\s*(?:the\s+)?(?:last|past)?\s*(\d{1,4})\s*(?:ticks?|digits?|numbers?)?\s*(?:is|must\s+be|should\s+be|be)?\s*(?:above|over|greater\s+than|at\s+least|>=|>)\s*(\d+(?:\.\d+)?)\s*%?",
        lowered,
    )
    if not percent_match and not reverse_match:
        simple = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:over|above|greater\s+than)?", lowered)
        if not simple:
            return None
        threshold = float(simple.group(1))
        window_match = re.search(r"(?:last|past|over)\s+(\d{2,4})\s+(?:ticks?|digits?|numbers?)", lowered)
        window = int(window_match.group(1)) if window_match else 1000
        target = trade_side if trade_side in {"over", "under", "even", "odd"} else "digit"
        condition: dict[str, Any] = {
            "kind": "percentage",
            "window": max(1, min(1000, window)),
            "target": target,
            "operator": ">=",
            "threshold": max(0.0, min(100.0, threshold)),
        }
        if target in {"over", "under", "digit"}:
            condition["value"] = int(prediction if prediction is not None else 5)
        return condition

    if reverse_match:
        target_text = reverse_match.group(0)
        threshold = float(reverse_match.group(3))
        window = int(reverse_match.group(2) or 1000)
    else:
        assert percent_match is not None
        target_text = percent_match.group(1)
        threshold = float(percent_match.group(2))
        window_match = re.search(r"(?:last|past|over)\s+(\d{1,4})\s+(?:ticks?|digits?|numbers?)", lowered)
        window = int(window_match.group(1)) if window_match else 1000

    target = "digit"
    for candidate in ("over", "under", "even", "odd"):
        if re.search(rf"\b{candidate}\b", target_text):
            target = candidate
            break
    if target == "digit" and trade_side in {"over", "under", "even", "odd"}:
        target = trade_side

    digit_match = re.search(r"\b([0-9])\b", target_text)
    value = _number_word(digit_match.group(1) if digit_match else "")
    if value is None:
        value = prediction if prediction is not None else 5

    condition = {
        "kind": "percentage",
        "window": max(1, min(1000, int(window))),
        "target": target,
        "operator": ">=",
        "threshold": max(0.0, min(100.0, threshold)),
    }
    if target in {"over", "under", "digit"}:
        condition["value"] = max(0, min(9, int(value)))
    return condition


def _direction_condition(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    direction: str | None = None
    if re.search(r"(?:last|previous|past).*?(?:rise|rising|up\s*ticks?)", lowered):
        direction = "rising"
    elif re.search(r"(?:last|previous|past).*?(?:fall|falling|down\s*ticks?)", lowered):
        direction = "falling"
    elif re.search(r"(?:last|previous|past).*?(?:no\s*move|same\s*price|flat)", lowered):
        direction = "no_move"
    if not direction:
        return None
    window_match = re.search(
        r"(?:last|previous|past)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:ticks?|moves?)",
        lowered,
    )
    window = _number_word(window_match.group(1) if window_match else "") or 3
    return {"kind": "direction", "window": max(1, min(1000, int(window))), "direction": direction}


def _default_condition(
    side: str,
    prediction: int | None,
    adjustments: list[str],
) -> dict[str, Any]:
    if side == "under":
        value = prediction if prediction is not None else 6
        adjustments.append("No explicit entry rule was clear, so the nearest Last-3-digits >= prediction rule was created for review.")
        return {"kind": "digit_compare", "window": 3, "operator": ">=", "value": int(value)}
    if side in {"even", "odd"}:
        adjustments.append(f"No explicit entry rule was clear, so a 500-digit {side.title()} percentage >= 52% rule was created for review.")
        return {"kind": "percentage", "window": 500, "target": side, "operator": ">=", "threshold": 52.0}
    if side in {"rise", "fall"}:
        direction = "rising" if side == "rise" else "falling"
        adjustments.append(f"No explicit entry rule was clear, so the last 3 ticks must be {direction} before entry.")
        return {"kind": "direction", "window": 3, "direction": direction}
    if side in {"matches", "differs"}:
        value = prediction if prediction is not None else 5
        adjustments.append("No explicit entry rule was clear, so the predicted digit must appear at least 10% over the last 100 digits before entry.")
        return {"kind": "percentage", "window": 100, "target": "digit", "value": int(value), "operator": ">=", "threshold": 10.0}
    value = prediction if prediction is not None else 3
    adjustments.append("No explicit entry rule was clear, so the nearest Last-3-digits <= prediction rule was created for review.")
    return {"kind": "digit_compare", "window": 3, "operator": "<=", "value": int(value)}


def _compile(text: str) -> dict[str, Any]:
    adjustments: list[str] = []
    name = _strategy_name(text)
    market_mode, markets, market_label = _market_selection(text, adjustments)
    trade_group, trade_side, prediction, contract_label = _trade_selection(text, adjustments)

    conditions: list[dict[str, Any]] = []
    for condition in (
        _last_digit_condition(text, trade_side, prediction),
        _percentage_condition(text, trade_side, prediction),
        _direction_condition(text),
    ):
        if condition and condition not in conditions:
            conditions.append(condition)
    if not conditions:
        conditions.append(_default_condition(trade_side, prediction, adjustments))

    stake = _money(text, ("stake", "base stake"))
    take_profit = _money(text, ("take profit", "tp", "profit target", "target profit"))
    stop_loss = _money(text, ("stop loss", "sl", "loss limit"))
    if stake is None:
        stake = 0.5
        adjustments.append("No stake was stated, so the draft uses the current platform default of $0.50 for review.")
    if take_profit is None:
        take_profit = 0.0
    if stop_loss is None:
        stop_loss = 0.0

    duration_match = re.search(r"(\d+)\s*(?:tick|ticks)\b", text, flags=re.IGNORECASE)
    duration = max(1, min(100, int(duration_match.group(1)))) if duration_match else 1

    loss_count_match = re.search(
        r"(?:after|following)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:consecutive\s+)?loss(?:es)?",
        text,
        flags=re.IGNORECASE,
    )
    loss_count = _number_word(loss_count_match.group(1) if loss_count_match else "") or 2
    virtual_requested = bool(re.search(r"virtual", text, flags=re.IGNORECASE))
    if loss_count_match and re.search(r"stop\s+(?:trading\s+)?after", text, flags=re.IGNORECASE) and not virtual_requested:
        adjustments.append(
            f"'Stop after {loss_count} losses' is not a monetary Stop Loss, so it was normalized to re-analyze after {loss_count} losses for review."
        )

    last = next((item for item in conditions if item["kind"] == "digit_compare"), None)
    percentage = next((item for item in conditions if item["kind"] == "percentage"), None)
    direction = next((item for item in conditions if item["kind"] == "direction"), None)
    strategy_mode = "combined" if last and percentage else "percentage" if percentage else "last_digit"

    one_market = markets[0] if markets else "1HZ100V"
    builder = {
        "version": 3,
        "name": name,
        "strategyMode": strategy_mode,
        "marketMode": market_mode,
        "markets": markets or ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V", "R_10", "R_25", "R_50", "R_75", "R_100"],
        "oneMarket": one_market,
        "lastRule": {
            "window": int((last or {}).get("window", 3)),
            "target": "last_digits",
            "operator": str((last or {}).get("operator", "<=")),
            "value": int((last or {}).get("value", prediction if prediction is not None else 3)),
        },
        "percentageRule": {
            "target": str((percentage or {}).get("target", trade_side if trade_side in {"over", "under", "even", "odd"} else "digit")),
            "value": int((percentage or {}).get("value", prediction if prediction is not None else 5)),
            "window": int((percentage or {}).get("window", 1000)),
            "operator": str((percentage or {}).get("operator", ">=")),
            "threshold": float((percentage or {}).get("threshold", 70.0)),
        },
        "tickDirectionRule": {
            "enabled": bool(direction),
            "window": int((direction or {}).get("window", 3)),
            "direction": str((direction or {}).get("direction", "rising")),
        },
        "trade": {"group": trade_group, "side": trade_side, "prediction": prediction if prediction is not None else 0},
        "reanalyze": {
            "mode": "after_loss" if loss_count_match else "after_every_trade",
            "losses": int(loss_count),
            "wins": 1,
        },
        "money": {
            "stake": max(0.35, float(stake)),
            "takeProfit": max(0.0, float(take_profit)),
            "stopLoss": max(0.0, float(stop_loss)),
            "martingale": 1.2,
            "ticks": duration,
        },
        "virtualHook": {
            "enabled": virtual_requested,
            "enterAfterLosses": int(loss_count),
            "exitAfterConsecutiveWins": 2,
        },
    }

    custom_strategy = {
        "name": name,
        "market_mode": market_mode,
        "markets": markets,
        "trade_type": trade_side,
        "prediction": prediction,
        "duration_ticks": duration,
        "conditions": conditions,
        "match": "all",
        "reanalyze": {"mode": builder["reanalyze"]["mode"], "losses": int(loss_count), "wins": 1},
        "virtual_hook_enabled": virtual_requested,
        "virtual_hook": {
            "enabled": virtual_requested,
            "enter_after_losses": int(loss_count),
            "exit_after_consecutive_wins": 2,
        },
        "martingale": {"mode": "multiplier", "multiplier": 1.2, "split_count": 1},
    }
    settings = {
        "stake_amount": max(0.35, float(stake)),
        "take_profit": max(0.0, float(take_profit)),
        "stop_loss": max(0.0, float(stop_loss)),
    }

    rule_lines: list[str] = []
    for condition in conditions:
        if condition["kind"] == "digit_compare":
            rule_lines.append(
                f"Last {condition['window']} digits {condition['operator']} {condition['value']}"
            )
        elif condition["kind"] == "percentage":
            target = str(condition.get("target") or "digit").title()
            digit = f" {condition['value']}" if "value" in condition else ""
            rule_lines.append(
                f"{target}{digit} percentage over {condition['window']} ticks/digits {condition['operator']} {condition['threshold']}%"
            )
        elif condition["kind"] == "direction":
            rule_lines.append(
                f"Last {condition['window']} ticks are {condition['direction']}"
            )

    return {
        "success": True,
        "status": "ready_for_review",
        "compiler": "nearest-supported-v1",
        "name": name,
        "market_label": market_label,
        "contract_label": contract_label,
        "rules": rule_lines,
        "settings": settings,
        "adjustments": adjustments,
        "builder": builder,
        "custom_strategy": custom_strategy,
        "direct_execution_allowed": False,
        "review_required": True,
    }


def install_text_to_strategy_api(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    path = "/me/text-to-strategy/compile"
    _remove_route(app, path, "POST")

    @app.post(path)
    def compile_text_to_strategy(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        if not base_api.get_current_account(request):
            raise HTTPException(status_code=401, detail="Log in with Deriv before creating a strategy.")

        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="Describe the strategy you want to create.")
        word_count = len(_words(text))
        if word_count > _MAX_WORDS:
            raise HTTPException(
                status_code=422,
                detail=f"Strategy description is {word_count} words. The maximum is {_MAX_WORDS} words.",
            )

        result = _compile(text)
        result.update(
            {
                "source_text": text,
                "word_count": word_count,
                "max_words": _MAX_WORDS,
                "interpretation_policy": "nearest_supported_doable",
            }
        )
        return result

    app.state.text_to_strategy_action2_installed = True
    _INSTALLED = True
