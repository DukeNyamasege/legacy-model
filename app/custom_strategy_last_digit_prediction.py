from __future__ import annotations

from typing import Any

from app import custom_strategy_v1 as custom


_INSTALLED = False
_DYNAMIC_TRADE_TYPES = {"matches", "differs"}
_SENTINELS = {"last_digit", "last digit", "last"}


def _trade_type(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    value = str(raw.get("trade_type") or "").strip().lower()
    return value


def _uses_last_digit(raw: Any) -> bool:
    if _trade_type(raw) not in _DYNAMIC_TRADE_TYPES:
        return False
    if not isinstance(raw, dict):
        return False
    prediction = raw.get("prediction")
    if prediction is None:
        return True
    return str(prediction).strip().lower() in _SENTINELS


def install_custom_strategy_last_digit_prediction() -> None:
    """Allow Matches/Differs to use the qualifying tick's last digit as barrier.

    The persisted sentinel is `prediction=None`, which is already accepted by the
    public request schema. At signal time it is resolved to the exact last digit of
    the qualifying market tick, so real and virtual contracts receive the same
    concrete numeric barrier.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_normalize = custom.normalize_custom_strategy
    original_contract = custom.contract_for_config
    original_build = custom.build_custom_signal
    original_describe = custom.describe_custom_strategy

    def normalize_custom_strategy(raw: Any) -> dict[str, Any]:
        if _uses_last_digit(raw):
            proxy = dict(raw)
            # Let the canonical validator validate every other field using a legal
            # temporary digit, then restore the dynamic sentinel.
            proxy["prediction"] = 0
            normalized = original_normalize(proxy)
            normalized["prediction"] = None
            return normalized
        return original_normalize(raw)

    def contract_for_config(
        config: dict[str, Any],
        *,
        last_digit: int | None = None,
    ) -> tuple[str, str, str]:
        normalized = normalize_custom_strategy(config)
        trade_type = str(normalized.get("trade_type") or "")
        if trade_type in _DYNAMIC_TRADE_TYPES and normalized.get("prediction") is None:
            contract_type = str(custom.TRADE_TYPES[trade_type]["contract_type"])
            prefix = "MATCHES" if trade_type == "matches" else "DIFFERS"
            if last_digit is None:
                return contract_type, f"{prefix}_LAST_DIGIT", "last_digit"
            digit = int(last_digit)
            if not 0 <= digit <= 9:
                raise ValueError("Last-digit prediction must resolve to a digit from 0 to 9")
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
        if trade_type not in _DYNAMIC_TRADE_TYPES or normalized.get("prediction") is not None:
            return original_build(bot, symbol=symbol, tick=tick, config=normalized)

        market = bot.market_states[str(symbol)]
        digits = [
            int(value)
            for value in list(getattr(market, "raw_tick_digits", []) or [])
            if 0 <= int(value) <= 9
        ]
        if not digits:
            raise ValueError("Last-digit prediction is unavailable until the market has a tick")
        last_digit = int(digits[-1])

        resolved = dict(normalized)
        resolved["prediction"] = last_digit
        signal = original_build(bot, symbol=symbol, tick=tick, config=resolved)
        contract_type, direction, barrier = contract_for_config(
            normalized,
            last_digit=last_digit,
        )
        signal.contract_type = contract_type
        signal.direction = direction
        signal.barrier = barrier
        signal.signal_last_digit = last_digit
        fingerprint = custom.custom_strategy_fingerprint(normalized)
        signal.trigger_name = f"CUSTOM-V2-{fingerprint[:8].upper()}"
        return signal

    def describe_custom_strategy(config: dict[str, Any]) -> str:
        normalized = normalize_custom_strategy(config)
        description = original_describe(normalized)
        trade_type = str(normalized.get("trade_type") or "")
        if trade_type in _DYNAMIC_TRADE_TYPES and normalized.get("prediction") is None:
            label = str(custom.TRADE_TYPES[trade_type]["label"])
            description = description.replace(
                f"THEN BUY {label} on ",
                f"THEN BUY {label} last digit on ",
                1,
            )
        return description

    custom.normalize_custom_strategy = normalize_custom_strategy
    custom.contract_for_config = contract_for_config
    custom.build_custom_signal = build_custom_signal
    custom.describe_custom_strategy = describe_custom_strategy
    custom.LAST_DIGIT_PREDICTION = None

    _INSTALLED = True
