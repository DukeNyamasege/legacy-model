from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import app.repositories.rf_dir5_repository as rf_repository_module
from app.models import DirectionalSignal
from app.repositories.rf_dir5_repository import RFDir5Repository


LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_ORIGINAL_START_VIRTUAL = None


def _last_digit(value: Decimal) -> int:
    rendered = format(Decimal(str(value)), "f")
    for character in reversed(rendered):
        if character.isdigit():
            return int(character)
    raise ValueError(f"Could not derive final digit from {value!r}")


def _barrier_digit(barrier: str | int | None, prediction_digit: int | None) -> int:
    for value in (prediction_digit, barrier):
        text = str(value if value is not None else "").strip()
        if text.isdigit() and 0 <= int(text) <= 9:
            return int(text)
    raise ValueError("Digit contract is missing a valid prediction barrier")


def custom_virtual_outcome(
    *,
    direction: str,
    contract_type: str,
    barrier: str | int | None,
    prediction_digit: int | None,
    entry_quote: Decimal,
    exit_quote: Decimal,
    exit_digit: int | None = None,
) -> tuple[str, int | None]:
    """Settle every contract supported by Custom Strategy Builder."""

    contract = str(contract_type or "").strip().upper()
    normalized_direction = str(direction or "").strip().upper()
    digit = (
        int(exit_digit)
        if exit_digit is not None and 0 <= int(exit_digit) <= 9
        else _last_digit(exit_quote)
    )

    if contract == "DIGITEVEN" or normalized_direction == "EVEN":
        return ("WIN" if digit % 2 == 0 else "LOSS", digit)
    if contract == "DIGITODD" or normalized_direction == "ODD":
        return ("WIN" if digit % 2 == 1 else "LOSS", digit)
    if contract in {"DIGITOVER", "DIGITUNDER", "DIGITMATCH", "DIGITDIFF"}:
        prediction = _barrier_digit(barrier, prediction_digit)
        if contract == "DIGITOVER":
            won = digit > prediction
        elif contract == "DIGITUNDER":
            won = digit < prediction
        elif contract == "DIGITMATCH":
            won = digit == prediction
        else:
            won = digit != prediction
        return ("WIN" if won else "LOSS", digit)
    if contract == "CALL" or normalized_direction in {"RISE", "CALL"}:
        return ("WIN" if exit_quote > entry_quote else "LOSS", digit)
    if contract == "PUT" or normalized_direction in {"FALL", "PUT"}:
        return ("WIN" if exit_quote < entry_quote else "LOSS", digit)
    raise ValueError(f"Unsupported custom virtual contract {contract or normalized_direction}")


def _ensure_parent(self: RFDir5Repository, signal: Any) -> bool:
    signal_id = str(getattr(signal, "signal_id", "") or "").strip()
    if not signal_id:
        return False
    with self.database.session() as session:
        if session.get(DirectionalSignal, signal_id) is not None:
            return True
        trigger_digits = [
            str(value)
            for value in list(getattr(signal, "trigger_digits", ()) or ())[-20:]
        ]
        session.add(
            DirectionalSignal(
                signal_id=signal_id,
                run_id=int(self.run_id),
                strategy_version=str(
                    getattr(signal, "strategy_version", "CUSTOM-STRATEGY")
                    or "CUSTOM-STRATEGY"
                ),
                symbol=str(getattr(signal, "symbol", "") or ""),
                direction=str(getattr(signal, "direction", "") or ""),
                contract_type=str(getattr(signal, "contract_type", "") or "").upper(),
                duration_ticks=max(1, int(getattr(signal, "duration_ticks", 1) or 1)),
                signal_epoch=int(getattr(signal, "signal_tick_epoch", 0) or 0),
                signal_tick_id=str(getattr(signal, "signal_tick_id", "") or ""),
                tick_sequence=int(getattr(signal, "tick_sequence", 0) or 0),
                reference_entry_quote=float(
                    getattr(signal, "reference_entry_quote", 0.0) or 0.0
                ),
                analysis_quotes=trigger_digits,
                movements=[],
                feature_values={
                    "trigger_name": str(getattr(signal, "trigger_name", "") or ""),
                    "barrier": str(getattr(signal, "barrier", "") or ""),
                    "runtime": "custom_direct",
                },
                quality_score=max(1, int(getattr(signal, "quality_score", 1) or 1)),
                validated_edge=(
                    float(getattr(signal, "validated_edge"))
                    if getattr(signal, "validated_edge", None) is not None
                    else None
                ),
                selected_for_execution=True,
                execution_decision="VIRTUAL_SELECTED",
                execution_reason="Custom direct runtime virtual parent",
            )
        )
    return True


def _start_virtual_with_parent(
    self: RFDir5Repository,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    signal = kwargs.get("signal")
    if signal is None or not _ensure_parent(self, signal):
        return None
    original = _ORIGINAL_START_VIRTUAL
    if original is None:
        return None
    return original(self, *args, **kwargs)


def install_custom_strategy_settlement() -> None:
    global _INSTALLED, _ORIGINAL_START_VIRTUAL
    if _INSTALLED:
        return
    _ORIGINAL_START_VIRTUAL = RFDir5Repository.start_virtual_trade
    RFDir5Repository.start_virtual_trade = _start_virtual_with_parent
    rf_repository_module._virtual_trade_outcome = custom_virtual_outcome
    RFDir5Repository._custom_strategy_settlement_installed = True
    _INSTALLED = True
