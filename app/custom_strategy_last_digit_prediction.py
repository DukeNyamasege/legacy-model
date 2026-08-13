from __future__ import annotations

import os
from collections import Counter
from typing import Any

from app import custom_strategy_v1 as custom


_INSTALLED = False
_DYNAMIC_TRADE_TYPES = {"matches", "differs"}
_DYNAMIC_MODES = {
    "last_digit",
    "most_appearing",
    "second_most_appearing",
    "least_appearing",
}
_FREQUENCY_MODES = {
    "most_appearing",
    "second_most_appearing",
    "least_appearing",
}
_DEFAULT_PREDICTION_WINDOW = 100
_SENTINEL_ALIASES = {
    "last": "last_digit",
    "last digit": "last_digit",
    "last_digit": "last_digit",
    "most": "most_appearing",
    "most appearing": "most_appearing",
    "most_appearing": "most_appearing",
    "second most": "second_most_appearing",
    "second most appearing": "second_most_appearing",
    "second_most_appearing": "second_most_appearing",
    "least": "least_appearing",
    "least appearing": "least_appearing",
    "least_appearing": "least_appearing",
}


def _trade_type(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("trade_type") or "").strip().lower()


def _prediction_mode(raw: Any) -> str:
    if _trade_type(raw) not in _DYNAMIC_TRADE_TYPES or not isinstance(raw, dict):
        return ""
    prediction = raw.get("prediction")
    if isinstance(prediction, str):
        alias = _SENTINEL_ALIASES.get(prediction.strip().lower())
        if alias:
            return alias
    reanalyze = raw.get("reanalyze") if isinstance(raw.get("reanalyze"), dict) else {}
    nested = str(
        reanalyze.get("prediction_mode") or raw.get("prediction_mode") or ""
    ).strip().lower()
    alias = _SENTINEL_ALIASES.get(nested, nested)
    if alias in _DYNAMIC_MODES:
        return alias
    if prediction is None:
        return "last_digit"
    return ""


def _fallback_window(raw: Any) -> int:
    windows: list[int] = []
    source = raw if isinstance(raw, dict) else {}
    for condition in list(source.get("conditions") or []):
        try:
            value = int(condition.get("window") or 0)
        except (TypeError, ValueError, AttributeError):
            value = 0
        if 1 <= value <= int(custom.MAX_WINDOW):
            windows.append(value)
    return max(windows or [_DEFAULT_PREDICTION_WINDOW])


def _requested_prediction_window(raw: Any, mode: str) -> int | None:
    if mode not in _FREQUENCY_MODES:
        return None
    source = raw if isinstance(raw, dict) else {}
    reanalyze = source.get("reanalyze") if isinstance(source.get("reanalyze"), dict) else {}
    raw_value = reanalyze.get("prediction_window", source.get("prediction_window"))
    if raw_value in {None, ""}:
        return _fallback_window(source)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Prediction analysis ticks must be a whole number") from exc
    if not 1 <= value <= int(custom.MAX_WINDOW):
        raise ValueError(
            f"Prediction analysis ticks must be between 1 and {int(custom.MAX_WINDOW)}"
        )
    return value


def _with_prediction_mode(
    normalized: dict[str, Any],
    mode: str,
    prediction_window: int | None = None,
) -> dict[str, Any]:
    result = dict(normalized)
    reanalyze = dict(result.get("reanalyze") or {})
    if mode:
        result["prediction"] = None
        result["prediction_mode"] = mode
        reanalyze["prediction_mode"] = mode
        if mode in _FREQUENCY_MODES:
            window = int(prediction_window or _DEFAULT_PREDICTION_WINDOW)
            result["prediction_window"] = window
            reanalyze["prediction_window"] = window
        else:
            result.pop("prediction_window", None)
            reanalyze.pop("prediction_window", None)
    else:
        result.pop("prediction_mode", None)
        result.pop("prediction_window", None)
        reanalyze.pop("prediction_mode", None)
        reanalyze.pop("prediction_window", None)
    result["reanalyze"] = reanalyze
    return result


def _prediction_window(config: dict[str, Any]) -> int:
    source = config if isinstance(config, dict) else {}
    reanalyze = source.get("reanalyze") if isinstance(source.get("reanalyze"), dict) else {}
    raw_value = reanalyze.get("prediction_window", source.get("prediction_window"))
    try:
        requested = int(raw_value)
    except (TypeError, ValueError):
        requested = _fallback_window(source)
    return max(1, min(int(custom.MAX_WINDOW), requested))


def _rank_digits(sample: list[int]) -> list[int]:
    counts = Counter(int(value) for value in sample if 0 <= int(value) <= 9)
    last_seen = {digit: -1 for digit in range(10)}
    for index, digit in enumerate(sample):
        if 0 <= int(digit) <= 9:
            last_seen[int(digit)] = index
    return sorted(
        range(10),
        key=lambda digit: (-int(counts.get(digit, 0)), -int(last_seen[digit]), digit),
    )


def _least_rank_digits(sample: list[int]) -> list[int]:
    counts = Counter(int(value) for value in sample if 0 <= int(value) <= 9)
    last_seen = {digit: -1 for digit in range(10)}
    for index, digit in enumerate(sample):
        if 0 <= int(digit) <= 9:
            last_seen[int(digit)] = index
    return sorted(
        range(10),
        key=lambda digit: (int(counts.get(digit, 0)), int(last_seen[digit]), digit),
    )


def _resolve_prediction(mode: str, digits: list[int], config: dict[str, Any]) -> int:
    if not digits:
        raise ValueError("Dynamic prediction is unavailable until the market has a tick")
    if mode == "last_digit":
        return int(digits[-1])

    window = _prediction_window(config)
    if len(digits) < window:
        raise ValueError(
            f"Dynamic prediction needs {window} consecutive ticks but only {len(digits)} are available"
        )
    sample = digits[-window:]
    if mode == "least_appearing":
        return int(_least_rank_digits(sample)[0])
    ranked = _rank_digits(sample)
    if mode == "second_most_appearing":
        return int(ranked[1])
    return int(ranked[0])


def _mode_label(mode: str, prediction_window: int | None = None) -> str:
    if mode == "most_appearing":
        return f"most appearing digit in the last {int(prediction_window or _DEFAULT_PREDICTION_WINDOW)} ticks"
    if mode == "second_most_appearing":
        return f"second most appearing digit in the last {int(prediction_window or _DEFAULT_PREDICTION_WINDOW)} ticks"
    if mode == "least_appearing":
        return f"least appearing digit in the last {int(prediction_window or _DEFAULT_PREDICTION_WINDOW)} ticks"
    return "last qualifying trigger digit"


def install_custom_strategy_last_digit_prediction() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_normalize = custom.normalize_custom_strategy
    original_contract = custom.contract_for_config
    original_build = custom.build_custom_signal
    original_describe = custom.describe_custom_strategy

    def normalize_custom_strategy(raw: Any) -> dict[str, Any]:
        mode = _prediction_mode(raw)
        if mode:
            prediction_window = _requested_prediction_window(raw, mode)
            proxy = dict(raw) if isinstance(raw, dict) else {}
            proxy["prediction"] = 0
            normalized = original_normalize(proxy)
            return _with_prediction_mode(normalized, mode, prediction_window)
        normalized = original_normalize(raw)
        return _with_prediction_mode(normalized, "")

    def contract_for_config(
        config: dict[str, Any],
        *,
        last_digit: int | None = None,
    ) -> tuple[str, str, str]:
        normalized = normalize_custom_strategy(config)
        trade_type = str(normalized.get("trade_type") or "")
        mode = _prediction_mode(normalized)
        if trade_type in _DYNAMIC_TRADE_TYPES and mode:
            contract_type = str(custom.TRADE_TYPES[trade_type]["contract_type"])
            prefix = "MATCHES" if trade_type == "matches" else "DIFFERS"
            if last_digit is None:
                return contract_type, f"{prefix}_{mode.upper()}", mode
            digit = int(last_digit)
            if not 0 <= digit <= 9:
                raise ValueError("Dynamic prediction must resolve to a digit from 0 to 9")
            return contract_type, f"{prefix}_{digit}", str(digit)
        return original_contract(normalized)

    def build_custom_signal(
        bot: Any,
        *,
        symbol: str,
        tick: dict[str, Any],
        config: dict[str, Any],
    ) -> Any:
        normalized = normalize_custom_strategy(config)
        trade_type = str(normalized.get("trade_type") or "")
        mode = _prediction_mode(normalized)
        if trade_type not in _DYNAMIC_TRADE_TYPES or not mode:
            return original_build(bot, symbol=symbol, tick=tick, config=normalized)

        market = bot.market_states[str(symbol)]
        digits = [
            int(value)
            for value in list(getattr(market, "raw_tick_digits", []) or [])
            if 0 <= int(value) <= 9
        ]
        resolved_digit = _resolve_prediction(mode, digits, normalized)

        resolved = dict(normalized)
        resolved["prediction"] = resolved_digit
        resolved.pop("prediction_mode", None)
        resolved.pop("prediction_window", None)
        resolved_reanalyze = dict(resolved.get("reanalyze") or {})
        resolved_reanalyze.pop("prediction_mode", None)
        resolved_reanalyze.pop("prediction_window", None)
        resolved["reanalyze"] = resolved_reanalyze

        signal = original_build(bot, symbol=symbol, tick=tick, config=resolved)
        contract_type = str(custom.TRADE_TYPES[trade_type]["contract_type"])
        prefix = "MATCHES" if trade_type == "matches" else "DIFFERS"
        signal.contract_type = contract_type
        signal.direction = f"{prefix}_{resolved_digit}"
        signal.barrier = str(resolved_digit)
        # DigitSignal is a slotted dataclass. Keep the concrete dynamic barrier in
        # the canonical field that the exact-entry/virtual runtimes already read;
        # never attach ad-hoc attributes that are not declared by DigitSignal.
        signal.signal_last_digit = resolved_digit
        fingerprint = custom.custom_strategy_fingerprint(normalized)
        signal.trigger_name = f"CUSTOM-V2-{fingerprint[:8].upper()}"
        return signal

    def describe_custom_strategy(config: dict[str, Any]) -> str:
        normalized = normalize_custom_strategy(config)
        mode = _prediction_mode(normalized)
        if not mode:
            return original_describe(normalized)
        trade_type = str(normalized.get("trade_type") or "")
        label = str(custom.TRADE_TYPES[trade_type]["label"])
        proxy = dict(normalized)
        proxy["prediction"] = 0
        proxy.pop("prediction_mode", None)
        proxy.pop("prediction_window", None)
        proxy_reanalyze = dict(proxy.get("reanalyze") or {})
        proxy_reanalyze.pop("prediction_mode", None)
        proxy_reanalyze.pop("prediction_window", None)
        proxy["reanalyze"] = proxy_reanalyze
        description = original_describe(proxy)
        prediction_window = (
            _prediction_window(normalized) if mode in _FREQUENCY_MODES else None
        )
        return description.replace(
            f"THEN BUY {label} 0 on ",
            f"THEN BUY {label} {_mode_label(mode, prediction_window)} on ",
            1,
        )

    custom.normalize_custom_strategy = normalize_custom_strategy
    custom.contract_for_config = contract_for_config
    custom.build_custom_signal = build_custom_signal
    custom.describe_custom_strategy = describe_custom_strategy
    custom.LAST_DIGIT_PREDICTION = None
    custom.DYNAMIC_MATCH_PREDICTION_MODES = tuple(sorted(_DYNAMIC_MODES))

    _INSTALLED = True

    # The worker needs one final fail-visible layer after result routing and the
    # manual Stop status authority. Chain that later installation here without
    # importing worker-only runtime modules into the API container.
    if os.getenv("DEPLOYMENT_ID", "").strip() == "vps-custom-worker":
        from app.custom_strategy_fail_visible import chain_after_manual_stop_install

        chain_after_manual_stop_install()
