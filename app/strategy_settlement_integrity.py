from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, select

import app.repositories.rf_dir5_repository as rf_repository_module
from app.models import (
    CandidateSignalRecord,
    DirectionalSignal,
    SystemModelTrade,
    Trade,
    utc_now,
)
from app.repositories.rf_dir5_repository import RFDir5Repository
from app.repositories.test2_repository import Test2Repository


LOGGER = logging.getLogger("legacy_model.strategy_integrity")
_INSTALLED = False
_ORIGINAL_START_VIRTUAL = None
_ORIGINAL_VIRTUAL_OUTCOME = None


def _quote_last_digit(value: Decimal) -> int:
    rendered = format(Decimal(str(value)), "f")
    for character in reversed(rendered):
        if character.isdigit():
            return int(character)
    raise ValueError(f"Could not derive a final digit from {value!r}")


def _strategy_outcome(
    *,
    contract_type: str,
    direction: str,
    barrier: str,
    entry_quote: Decimal,
    exit_quote: Decimal,
    exit_digit: int | None,
) -> tuple[str, int | None]:
    """Settle every supported strategy without crossing strategy families."""

    contract = str(contract_type or "").strip().upper()
    normalized_direction = str(direction or "").strip().upper()
    digit = (
        int(exit_digit)
        if exit_digit is not None and 0 <= int(exit_digit) <= 9
        else _quote_last_digit(exit_quote)
    )

    if contract == "DIGITEVEN" or normalized_direction == "EVEN":
        return ("WIN" if digit % 2 == 0 else "LOSS", digit)
    if contract == "DIGITODD" or normalized_direction == "ODD":
        return ("WIN" if digit % 2 == 1 else "LOSS", digit)
    if contract in {"DIGITOVER", "DIGITUNDER"}:
        text = str(barrier or "").strip()
        if not text.isdigit() or not 0 <= int(text) <= 9:
            raise ValueError(f"{contract} is missing a valid prediction barrier")
        prediction = int(text)
        if contract == "DIGITOVER":
            return ("WIN" if digit > prediction else "LOSS", digit)
        return ("WIN" if digit < prediction else "LOSS", digit)

    if contract == "CALL" or normalized_direction in {"RISE", "CALL"}:
        return ("WIN" if exit_quote > entry_quote else "LOSS", digit)
    if contract == "PUT" or normalized_direction in {"FALL", "PUT"}:
        return ("WIN" if exit_quote < entry_quote else "LOSS", digit)

    raise ValueError(
        f"Unsupported strategy settlement contract={contract!r} direction={normalized_direction!r}"
    )


def _settle_supported_system_trades(
    self: Test2Repository,
    *,
    symbol: str,
    tick_sequence: int,
    exit_spot: float,
) -> list[dict[str, Any]]:
    """Settle system-model rows per contract type and never crash the tick loop."""

    digit_map = getattr(self, "_hybrid_digit_by_symbol", {})
    exit_digit = digit_map.get(str(symbol))
    exit_quote = Decimal(str(exit_spot))
    now = utc_now()
    settled: list[dict[str, Any]] = []

    with self.database.session() as session:
        rows = session.scalars(
            select(SystemModelTrade)
            .where(
                SystemModelTrade.run_id == self.run_id,
                SystemModelTrade.symbol == str(symbol),
                SystemModelTrade.outcome.is_(None),
                SystemModelTrade.expiry_tick_sequence <= int(tick_sequence),
                exists().where(Trade.signal_id == SystemModelTrade.signal_id),
            )
            .with_for_update()
        ).all()

        for trade in rows:
            contract = str(trade.contract_type or "").strip().upper()
            candidate = session.get(CandidateSignalRecord, trade.signal_id)
            barrier = str(getattr(candidate, "barrier", "") or "")
            try:
                outcome, actual_digit = _strategy_outcome(
                    contract_type=contract,
                    direction=str(trade.direction or ""),
                    barrier=barrier,
                    entry_quote=Decimal(str(trade.entry_spot)),
                    exit_quote=exit_quote,
                    exit_digit=exit_digit,
                )
            except (TypeError, ValueError) as exc:
                # This is a reference-model row, not a provider contract. Mark it
                # invalid once so one malformed row cannot throw on every tick.
                trade.outcome = "INVALID"
                trade.is_virtual = False
                trade.exit_spot = float(exit_spot)
                trade.settlement_timestamp = now
                trade.fixed_stake_profit = 0.0
                settled.append(
                    {
                        "signal_id": trade.signal_id,
                        "outcome": "INVALID",
                        "is_virtual": False,
                        "contract_type": contract,
                        "expected_profit_ratio": float(
                            trade.expected_profit_ratio or 0.0
                        ),
                        "exit_digit": exit_digit,
                        "settlement_error": str(exc),
                    }
                )
                LOGGER.error(
                    "SYSTEM_MODEL_SETTLEMENT_ISOLATED signal_id=%s contract=%s "
                    "direction=%s error=%s global_execution_continues=true",
                    trade.signal_id,
                    contract,
                    trade.direction,
                    exc,
                )
                continue

            trade.outcome = outcome
            trade.is_virtual = False
            trade.exit_spot = float(exit_spot)
            trade.settlement_timestamp = now
            ratio = max(0.0, float(trade.expected_profit_ratio or 0.0))
            trade.fixed_stake_profit = ratio * 0.50 if outcome == "WIN" else -0.50
            settled.append(
                {
                    "signal_id": trade.signal_id,
                    "outcome": outcome,
                    "is_virtual": False,
                    "contract_type": contract,
                    "expected_profit_ratio": ratio,
                    "exit_digit": actual_digit,
                }
            )

    callback = getattr(self, "_hybrid_settlement_callback", None)
    if callable(callback):
        for payload in settled:
            try:
                callback(payload)
            except Exception:
                LOGGER.exception(
                    "SYSTEM_MODEL_SETTLEMENT_CALLBACK_ISOLATED signal_id=%s "
                    "global_execution_continues=true",
                    payload.get("signal_id", ""),
                )
    return settled


def _signal_feature_payload(signal: Any) -> tuple[list[str], list[str], dict[str, Any]]:
    features = getattr(signal, "features", None)
    analysis_quotes: list[str] = []
    movements: list[str] = []
    feature_values: dict[str, Any] = {}
    if features is not None:
        analysis_quotes = [
            str(value) for value in list(getattr(features, "analysis_quotes", ()) or ())
        ]
        movements = [
            str(value) for value in list(getattr(features, "movements", ()) or ())
        ]
        to_dict = getattr(features, "to_dict", None)
        if callable(to_dict):
            try:
                raw = to_dict()
                if isinstance(raw, dict):
                    feature_values.update(raw)
            except Exception:
                pass

    if not analysis_quotes:
        analysis_quotes = [
            str(value)
            for value in list(getattr(signal, "trigger_digits", ()) or ())[-20:]
        ]
    feature_values.update(
        {
            "trigger_name": str(getattr(signal, "trigger_name", "") or ""),
            "barrier": str(getattr(signal, "barrier", "") or ""),
            "weighted_probability": float(
                getattr(signal, "weighted_probability", 0.0) or 0.0
            ),
            "break_even_probability": float(
                getattr(signal, "break_even_probability", 0.0) or 0.0
            ),
            "validated_edge": float(
                getattr(signal, "validated_edge", 0.0) or 0.0
            ),
        }
    )
    return analysis_quotes, movements, feature_values


def _ensure_virtual_parent(self: RFDir5Repository, signal: Any) -> bool:
    signal_id = str(getattr(signal, "signal_id", "") or "").strip()
    if not signal_id:
        return False

    try:
        with self.database.session() as session:
            if session.get(DirectionalSignal, signal_id) is not None:
                return True
            analysis_quotes, movements, feature_values = _signal_feature_payload(signal)
            session.add(
                DirectionalSignal(
                    signal_id=signal_id,
                    run_id=int(self.run_id),
                    strategy_version=str(
                        getattr(signal, "strategy_version", "MULTI-STRATEGY")
                        or "MULTI-STRATEGY"
                    ),
                    symbol=str(getattr(signal, "symbol", "") or ""),
                    direction=str(getattr(signal, "direction", "") or ""),
                    contract_type=str(
                        getattr(signal, "contract_type", "") or ""
                    ).upper(),
                    duration_ticks=max(
                        1, int(getattr(signal, "duration_ticks", 1) or 1)
                    ),
                    signal_epoch=int(
                        getattr(signal, "signal_tick_epoch", 0) or 0
                    ),
                    signal_tick_id=str(
                        getattr(signal, "signal_tick_id", "") or ""
                    ),
                    tick_sequence=int(getattr(signal, "tick_sequence", 0) or 0),
                    reference_entry_quote=float(
                        getattr(signal, "reference_entry_quote", 0.0) or 0.0
                    ),
                    analysis_quotes=analysis_quotes,
                    movements=movements,
                    feature_values=feature_values,
                    quality_score=max(
                        1, int(getattr(signal, "quality_score", 1) or 1)
                    ),
                    validated_edge=(
                        float(getattr(signal, "validated_edge"))
                        if getattr(signal, "validated_edge", None) is not None
                        else None
                    ),
                    selected_for_execution=True,
                    execution_decision="VIRTUAL_SELECTED",
                    execution_reason="Parent created at virtual-trade integrity boundary",
                )
            )
            session.flush()
        return True
    except Exception as exc:
        # A simultaneous task may have inserted the same parent. Confirm in a new
        # transaction before classifying this account for retry.
        try:
            with self.database.session() as session:
                if session.get(DirectionalSignal, signal_id) is not None:
                    return True
        except Exception:
            pass
        LOGGER.error(
            "VIRTUAL_PARENT_CREATE_FAILED signal_id=%s error=%s "
            "global_execution_continues=true",
            signal_id,
            type(exc).__name__,
            exc_info=True,
        )
        return False


def _start_virtual_with_parent(
    self: RFDir5Repository,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    signal = kwargs.get("signal")
    if signal is None:
        LOGGER.error(
            "VIRTUAL_TRADE_REJECTED reason=missing_signal global_execution_continues=true"
        )
        return None
    if not _ensure_virtual_parent(self, signal):
        return None

    original = _ORIGINAL_START_VIRTUAL
    if original is None:
        return None
    try:
        return original(self, *args, **kwargs)
    except Exception:
        LOGGER.exception(
            "VIRTUAL_TRADE_INSERT_ISOLATED managed_id=%s signal_id=%s "
            "global_execution_continues=true",
            kwargs.get("managed_account_id", ""),
            getattr(signal, "signal_id", ""),
        )
        return None


def _virtual_outcome_all_strategies(
    *,
    direction: str,
    contract_type: str,
    barrier: str | int | None,
    prediction_digit: int | None,
    entry_quote: Decimal,
    exit_quote: Decimal,
    exit_digit: int | None = None,
) -> tuple[str, int | None]:
    contract = str(contract_type or "").strip().upper()
    normalized_direction = str(direction or "").strip().upper()
    if contract in {"DIGITEVEN", "DIGITODD", "CALL", "PUT"} or normalized_direction in {
        "EVEN",
        "ODD",
        "CALL",
        "PUT",
    }:
        return _strategy_outcome(
            contract_type=contract,
            direction=normalized_direction,
            barrier=str(barrier or prediction_digit or ""),
            entry_quote=entry_quote,
            exit_quote=exit_quote,
            exit_digit=exit_digit,
        )

    original = _ORIGINAL_VIRTUAL_OUTCOME
    if original is None:
        raise ValueError("Original virtual settlement function is unavailable")
    return original(
        direction=direction,
        contract_type=contract_type,
        barrier=barrier,
        prediction_digit=prediction_digit,
        entry_quote=entry_quote,
        exit_quote=exit_quote,
        exit_digit=exit_digit,
    )


def install_strategy_settlement_integrity() -> None:
    """Install per-strategy settlement and virtual FK integrity boundaries."""

    global _INSTALLED, _ORIGINAL_START_VIRTUAL, _ORIGINAL_VIRTUAL_OUTCOME
    if _INSTALLED:
        return

    _ORIGINAL_START_VIRTUAL = RFDir5Repository.start_virtual_trade
    _ORIGINAL_VIRTUAL_OUTCOME = rf_repository_module._virtual_trade_outcome

    Test2Repository.settle_due_system_model_trades = _settle_supported_system_trades
    RFDir5Repository.start_virtual_trade = _start_virtual_with_parent
    rf_repository_module._virtual_trade_outcome = _virtual_outcome_all_strategies

    Test2Repository._strategy_settlement_integrity_installed = True
    RFDir5Repository._strategy_settlement_integrity_installed = True
    _INSTALLED = True
