from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import app.repositories.rf_dir5_repository as rf_repository_module
from app.custom_strategy_v1 import (
    TRADE_TYPES,
    contract_for_config,
    market_selected,
    read_custom_strategy,
)
from app.models import DirectionalSignal
from app.repositories.rf_dir5_repository import RFDir5Repository, VIRTUAL_MODE


LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_ORIGINAL_START_VIRTUAL = None
_ORIGINAL_PROTECTION_PAYLOAD = None


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


def virtual_signal_matches_config(config: dict[str, Any], signal: Any) -> bool:
    """Require a virtual observation to be the exact contract the account configured.

    This is intentionally fail-closed. A saved Over 2 strategy may only create an
    Over 2 virtual observation; changing the saved strategy to Over 7 immediately
    makes Over 7 the only valid virtual observation. Market and duration are also
    checked against the same saved account configuration.
    """

    if not bool(config.get("configured")) or signal is None:
        return False
    try:
        contract_type, direction, barrier = contract_for_config(config)
        symbol = str(getattr(signal, "symbol", "") or "").strip().upper()
        if not market_selected(config, symbol):
            return False
        if str(getattr(signal, "contract_type", "") or "").strip().upper() != contract_type:
            return False
        if str(getattr(signal, "direction", "") or "").strip().upper() != direction.upper():
            return False
        if str(getattr(signal, "barrier", "") or "").strip() != str(barrier or "").strip():
            return False
        if max(1, int(getattr(signal, "duration_ticks", 1) or 1)) != int(
            config.get("duration_ticks") or 1
        ):
            return False
    except (TypeError, ValueError):
        return False
    return True


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
    managed_account_id = kwargs.get("managed_account_id")
    try:
        managed_id = int(managed_account_id)
    except (TypeError, ValueError):
        return None
    config = read_custom_strategy(self.database, managed_id)
    if not virtual_signal_matches_config(config, signal):
        LOGGER.error(
            "CUSTOM_VIRTUAL_STRATEGY_MISMATCH managed_id=%s symbol=%s contract=%s barrier=%s duration=%s",
            managed_id,
            getattr(signal, "symbol", None),
            getattr(signal, "contract_type", None),
            getattr(signal, "barrier", None),
            getattr(signal, "duration_ticks", None),
        )
        return None
    if signal is None or not _ensure_parent(self, signal):
        return None
    original = _ORIGINAL_START_VIRTUAL
    if original is None:
        return None
    return original(self, *args, **kwargs)


def _configured_virtual_description(config: dict[str, Any]) -> str:
    if not bool(config.get("configured")):
        return "configured strategy"
    trade_type = str(config.get("trade_type") or "").strip().lower()
    label = str(TRADE_TYPES.get(trade_type, {}).get("label") or trade_type or "strategy")
    prediction = config.get("prediction")
    if prediction is not None:
        label = f"{label} {prediction}"
    markets = [str(value) for value in config.get("markets") or []]
    if str(config.get("market_mode") or "all") == "all":
        market_label = "the next configured qualifying market"
    elif len(markets) == 1:
        market_label = markets[0]
    else:
        market_label = "the next qualifying configured market"
    return f"{label} on {market_label}"


def _protection_payload_for_config(
    self: RFDir5Repository,
    state: Any,
) -> dict[str, Any]:
    original = _ORIGINAL_PROTECTION_PAYLOAD
    if original is None:
        return self._default_virtual_state()
    payload = original(self, state)
    if str(payload.get("mode") or "") != VIRTUAL_MODE or state is None:
        return payload
    try:
        config = read_custom_strategy(self.database, int(state.managed_account_id))
        description = _configured_virtual_description(config)
        required = max(1, int(payload.get("virtual_wins_required") or 1))
        payload["next_action"] = (
            f"Waiting for {required} virtual {description} win"
            f"{'' if required == 1 else 's'} using the exact saved strategy"
        )
    except Exception:
        payload["next_action"] = "Waiting for the next exact configured-strategy virtual result"
    return payload


def install_custom_strategy_settlement() -> None:
    global _INSTALLED, _ORIGINAL_START_VIRTUAL, _ORIGINAL_PROTECTION_PAYLOAD
    if _INSTALLED:
        return
    _ORIGINAL_START_VIRTUAL = RFDir5Repository.start_virtual_trade
    _ORIGINAL_PROTECTION_PAYLOAD = RFDir5Repository._protection_payload
    RFDir5Repository.start_virtual_trade = _start_virtual_with_parent
    RFDir5Repository._protection_payload = _protection_payload_for_config
    rf_repository_module._virtual_trade_outcome = custom_virtual_outcome
    RFDir5Repository._custom_strategy_settlement_installed = True
    RFDir5Repository._custom_strategy_virtual_parity_installed = True
    _INSTALLED = True