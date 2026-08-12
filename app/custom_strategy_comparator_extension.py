from __future__ import annotations

from typing import Any

from app import custom_strategy_v1 as custom


_INSTALLED = False
_SPECIAL_COMPARATORS = {"all_even", "all_odd"}


def install_custom_strategy_comparator_extension() -> None:
    """Add value-free all-even/all-odd operators without changing stored schema.

    The operators remain `digit_compare` conditions so existing Custom Strategy
    persistence, hashing and cross-device synchronization continue to work. They
    intentionally ignore `value`; only the selected lookback window matters.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_normalize_condition = custom.normalize_condition
    original_condition_matches = custom.condition_matches
    original_describe_condition = custom.describe_condition

    def normalize_condition(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            kind = str(raw.get("kind") or "").strip().lower()
            operator = str(raw.get("operator") or "").strip().lower()
            if kind == "digit_compare" and operator in _SPECIAL_COMPARATORS:
                return {
                    "kind": "digit_compare",
                    "window": custom._window(raw.get("window")),
                    "operator": operator,
                    "value": None,
                }
        return original_normalize_condition(raw)

    def condition_matches(
        condition: dict[str, Any],
        *,
        digits: list[int],
        quotes: list[Any],
    ) -> bool:
        if str(condition.get("kind") or "") == "digit_compare":
            operator = str(condition.get("operator") or "").strip().lower()
            if operator in _SPECIAL_COMPARATORS:
                window = int(condition.get("window") or 0)
                if window <= 0 or len(digits) < window:
                    return False
                sample = [int(value) for value in digits[-window:]]
                if operator == "all_even":
                    return bool(sample) and all(value % 2 == 0 for value in sample)
                return bool(sample) and all(value % 2 == 1 for value in sample)
        return original_condition_matches(condition, digits=digits, quotes=quotes)

    def describe_condition(condition: dict[str, Any]) -> str:
        normalized = normalize_condition(condition)
        if normalized.get("kind") == "digit_compare":
            operator = str(normalized.get("operator") or "")
            window = int(normalized.get("window") or 0)
            if operator == "all_even":
                return f"last {window} digit(s) are all even"
            if operator == "all_odd":
                return f"last {window} digit(s) are all odd"
        return original_describe_condition(condition)

    custom.COMPARATORS = tuple(
        value for value in custom.COMPARATORS if value not in _SPECIAL_COMPARATORS
    ) + ("all_even", "all_odd")
    custom.normalize_condition = normalize_condition
    custom.condition_matches = condition_matches
    custom.describe_condition = describe_condition

    _INSTALLED = True
