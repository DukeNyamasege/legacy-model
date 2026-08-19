"""Extract settlement spots without discarding Deriv's display precision."""

from __future__ import annotations

from typing import Any, Mapping


def _present(value: Any) -> bool:
    return value is not None and value != ""


def provider_contract_spot(contract: Mapping[str, Any], side: str) -> str | None:
    """Return Deriv's exact entry/exit display value when it is available."""

    if side not in {"entry", "exit"}:
        raise ValueError("side must be 'entry' or 'exit'")

    direct = contract.get(f"{side}_spot")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    tick_stream = contract.get("tick_stream")
    if isinstance(tick_stream, list) and tick_stream:
        tick = tick_stream[0] if side == "entry" else tick_stream[-1]
        if isinstance(tick, Mapping):
            display = tick.get("tick_display_value")
            if _present(display):
                return str(display).strip()

    legacy_display = contract.get(f"{side}_tick_display_value")
    if _present(legacy_display):
        return str(legacy_display).strip()
    if _present(direct):
        return str(direct).strip()

    legacy_numeric = contract.get(f"{side}_tick")
    return str(legacy_numeric).strip() if _present(legacy_numeric) else None


def provider_contract_digit(display_value: str | None, pip_size: int) -> int | None:
    """Read a display digit exactly, formatting only legacy numeric fallbacks."""

    if display_value is None:
        return None
    text = str(display_value).strip()
    if not text:
        return None

    if "." in text:
        fractional_digits = "".join(
            character
            for character in text.rsplit(".", 1)[1]
            if character.isdigit()
        )
        if len(fractional_digits) >= max(0, int(pip_size)):
            return int(fractional_digits[-1])

    try:
        rendered = f"{float(text):.{max(0, int(pip_size))}f}"
    except (TypeError, ValueError, OverflowError):
        return None
    digits = [character for character in rendered if character.isdigit()]
    return int(digits[-1]) if digits else None


def provider_contract_number(display_value: str | None) -> float | None:
    if display_value is None:
        return None
    try:
        return float(display_value)
    except (TypeError, ValueError, OverflowError):
        return None
