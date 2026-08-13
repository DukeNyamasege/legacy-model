from __future__ import annotations

from typing import Any

from app import exact_strategy_execution_authority as exact
from app.account_execution_session import AccountExecutionError, AccountExecutionSession
from app.custom_strategy_v1 import (
    contract_for_config,
    custom_strategy_fingerprint,
    market_selected,
)
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False


def _allow_latched_trigger(
    _session: AccountExecutionSession,
    _signal: Any,
) -> None:
    """Do not expire an already-qualified signal when later ticks arrive.

    Qualification is the entry decision boundary. Once the scanner creates the
    signal, proposal/stake/BUY work may finish on a later market tick. Manual Stop,
    TP/SL, account lifecycle, credential, balance and provider checks remain owned
    by their existing authorities and are deliberately not bypassed here.
    """

    return None


def _assert_latched_signal_matches_saved_strategy(item: Any, signal: Any) -> None:
    """Validate the immutable qualified signal, not the subsequently moving market.

    The previous exact-entry authority re-ran the strategy against live ticks and
    required market.tick_sequence == signal.tick_sequence until BUY. That made a
    valid signal expire while proposal/stake processing was still in progress.

    This authority instead verifies that the signal created at qualification still
    belongs to the saved strategy: selected market, contract type, prediction,
    duration and strategy fingerprint must match. It intentionally does not
    re-evaluate current ticks or compare current and trigger tick sequences.
    """

    config = dict(getattr(item, "config", {}) or {})
    symbol = str(getattr(signal, "symbol", "") or "")

    if not symbol:
        raise AccountExecutionError("Latched Custom Strategy signal is missing its market")
    if not market_selected(config, symbol):
        raise AccountExecutionError(
            f"Latched signal market {symbol} is no longer selected by this strategy"
        )

    market_states = getattr(getattr(item, "execution", None), "bot", None)
    market_states = getattr(market_states, "market_states", {})
    if symbol not in market_states:
        raise AccountExecutionError(
            f"Latched signal market {symbol} is unavailable in this worker"
        )

    contract_type, _direction, barrier = contract_for_config(config)
    configured_duration = max(1, int(config.get("duration_ticks") or 1))
    signal_duration = max(1, int(getattr(signal, "duration_ticks", 1) or 1))

    if str(getattr(signal, "contract_type", "") or "").upper() != str(contract_type).upper():
        raise AccountExecutionError(
            "Latched signal contract type does not match the saved Custom Strategy"
        )
    if str(getattr(signal, "barrier", "") or "") != str(barrier or ""):
        raise AccountExecutionError(
            "Latched signal prediction/barrier does not match the saved Custom Strategy"
        )
    if signal_duration != configured_duration:
        raise AccountExecutionError(
            "Latched signal duration does not match the saved Custom Strategy"
        )

    expected_trigger = f"CUSTOM-V2-{custom_strategy_fingerprint(config)[:8].upper()}"
    if str(getattr(signal, "trigger_name", "") or "") != expected_trigger:
        raise AccountExecutionError(
            "Latched signal fingerprint does not match the saved Custom Strategy"
        )


def install_custom_strategy_latched_entry() -> None:
    """Make every Custom Strategy trade type purchase its qualified signal.

    Applies centrally to Over/Under, Matches/Differs, Odd/Even and Rise/Fall because
    they all pass through the same exact-strategy execution authority.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    # The existing proposal/BUY/execute wrappers resolve these functions by module
    # global name at call time. Replacing them here removes only trigger-age checks;
    # the rest of the account-scoped execution stack remains unchanged.
    exact._assert_trigger_tick_current = _allow_latched_trigger
    exact._assert_strategy_exact = _assert_latched_signal_matches_saved_strategy

    RFDir5TradingBot._custom_strategy_latched_entry_installed = True
    _INSTALLED = True
