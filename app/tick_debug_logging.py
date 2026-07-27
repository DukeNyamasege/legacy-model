from __future__ import annotations

import os
import time
from typing import Any

from app.rf_dir5_bot import RFDir5TradingBot

_INSTALLED = False
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _enabled() -> bool:
    return os.getenv("EVERY_TICK_LOGS", "false").strip().lower() in _TRUE_VALUES


def _log_interval_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("EVERY_TICK_ERROR_SUMMARY_SECONDS", "10")))
    except ValueError:
        return 10.0


def install_every_tick_debug_logging() -> None:
    """Emit one visible log line for every public market tick when enabled.

    This is intentionally opt-in because the worker subscribes to several fast tick
    streams and Docker logs can grow very quickly. Enable with EVERY_TICK_LOGS=true.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    original_on_tick = RFDir5TradingBot._on_tick

    async def on_tick_with_debug(self: RFDir5TradingBot, tick_data: dict[str, Any]) -> None:
        if not _enabled():
            await original_on_tick(self, tick_data)
            return

        tick = tick_data.get("tick") or {}
        symbol = str(tick.get("symbol") or getattr(self, "symbol", "unknown"))
        quote_raw = tick.get("quote")
        epoch = tick.get("epoch", "unknown")
        tick_id = str(tick.get("id") or "")
        market = getattr(self, "market_states", {}).get(symbol)
        before_global_seq = int(getattr(self, "tick_sequence", 0) or 0)
        before_market_seq = int(getattr(market, "tick_sequence", 0) or 0) if market is not None else 0
        mode = "unknown"
        try:
            mode = str(getattr(self, "hybrid_state", {}).get("mode") or "unknown")
        except Exception:
            mode = "unknown"

        try:
            quote = float(quote_raw)
            pip_size = int(getattr(market, "pip_size", 2) or 2) if market is not None else 2
            display = f"{quote:.{pip_size}f}"
            digit = display[-1]
        except Exception:
            display = str(quote_raw)
            digit = "?"

        started = time.perf_counter()
        error_name = ""
        try:
            await original_on_tick(self, tick_data)
        except Exception as exc:
            error_name = type(exc).__name__
            self.logger.exception(
                "EVERY_TICK_ERROR symbol=%s epoch=%s tick_id=%s quote=%s digit=%s "
                "before_global_seq=%s before_market_seq=%s mode=%s error=%s",
                symbol,
                epoch,
                tick_id or "-",
                display,
                digit,
                before_global_seq,
                before_market_seq,
                mode,
                error_name,
            )
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            after_market = getattr(self, "market_states", {}).get(symbol)
            after_global_seq = int(getattr(self, "tick_sequence", 0) or 0)
            after_market_seq = int(getattr(after_market, "tick_sequence", 0) or 0) if after_market is not None else 0
            locked = bool(getattr(self, "is_trading_locked", False))
            pending_contracts = len(getattr(self, "pending_contracts_for_current_cycle", set()) or [])
            try:
                mode_after = str(getattr(self, "hybrid_state", {}).get("mode") or mode)
                debt_after = float(getattr(self, "hybrid_state", {}).get("canonical_debt") or 0.0)
            except Exception:
                mode_after = mode
                debt_after = 0.0
            self.logger.info(
                "EVERY_TICK symbol=%s global_seq=%s market_seq=%s quote=%s digit=%s "
                "epoch=%s tick_id=%s mode=%s debt=%.2f locked=%s open_cycle=%s "
                "elapsed_ms=%.1f error=%s",
                symbol,
                after_global_seq,
                after_market_seq,
                display,
                digit,
                epoch,
                tick_id or "-",
                mode_after,
                debt_after,
                locked,
                pending_contracts,
                elapsed_ms,
                error_name or "none",
            )

        interval = _log_interval_seconds()
        if interval > 0:
            now = time.monotonic()
            last = float(getattr(self, "_every_tick_debug_last_summary", 0.0) or 0.0)
            if now - last >= interval:
                setattr(self, "_every_tick_debug_last_summary", now)
                self.logger.info(
                    "EVERY_TICK_SUMMARY global_seq=%s mode=%s debt=%.2f locked=%s open_cycle=%s",
                    int(getattr(self, "tick_sequence", 0) or 0),
                    str(getattr(self, "hybrid_state", {}).get("mode") or "unknown"),
                    float(getattr(self, "hybrid_state", {}).get("canonical_debt") or 0.0),
                    bool(getattr(self, "is_trading_locked", False)),
                    len(getattr(self, "pending_contracts_for_current_cycle", set()) or []),
                )

    RFDir5TradingBot._on_tick = on_tick_with_debug
    _INSTALLED = True
