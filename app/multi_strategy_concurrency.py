from __future__ import annotations

import asyncio
import time
from typing import Any

from app.rf_dir5_bot import RFDir5TradingBot

_INSTALLED = False


def _prune_routes(bot: RFDir5TradingBot, *, keep_signal_id: str = "") -> None:
    routes = getattr(bot, "_multi_strategy_signal_routes", None)
    if not isinstance(routes, dict):
        return
    now = time.monotonic()
    stale = [
        signal_id
        for signal_id, route in routes.items()
        if signal_id != keep_signal_id
        and now - float(getattr(route, "created_monotonic", now) or now) > 120.0
    ]
    for signal_id in stale:
        routes.pop(signal_id, None)


def install_multi_strategy_concurrency_guard() -> None:
    """Make strategy execution atomic across every account strategy family.

    Digits/AIDR and the new parity/direction arbiters run independently so their
    proposal requests can overlap. The final purchase boundary must still be one
    atomic gate: after acquiring it, the candidate is checked again for an open
    cycle and stale tick. This prevents two groups from racing through the old
    boolean trading lock and opening overlapping cycles.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original = RFDir5TradingBot._buy_selected_accounts
    if getattr(original, "_multi_strategy_concurrency_guard", False):
        _INSTALLED = True
        return

    async def serialized_buy(
        self: RFDir5TradingBot,
        signal: Any,
        economics: Any,
    ) -> None:
        gate = getattr(self, "_multi_strategy_execution_gate", None)
        if gate is None:
            gate = asyncio.Lock()
            self._multi_strategy_execution_gate = gate

        signal_id = str(getattr(signal, "signal_id", "") or "")
        async with gate:
            _prune_routes(self, keep_signal_id=signal_id)
            market = getattr(self, "market_states", {}).get(
                str(getattr(signal, "symbol", "") or "")
            )
            if market is not None and int(getattr(market, "tick_sequence", 0)) != int(
                getattr(signal, "tick_sequence", -1)
            ):
                try:
                    self.repository.mark_signal(
                        signal_id,
                        status="SKIP_STALE_SIGNAL_AT_EXECUTION_GATE",
                        stale=True,
                    )
                finally:
                    getattr(self, "_multi_strategy_signal_routes", {}).pop(
                        signal_id, None
                    )
                return

            if bool(getattr(self, "pending_contracts_for_current_cycle", set())):
                try:
                    self.repository.mark_signal(
                        signal_id,
                        status="SKIP_STRATEGY_EXECUTION_GATE_BUSY",
                    )
                finally:
                    getattr(self, "_multi_strategy_signal_routes", {}).pop(
                        signal_id, None
                    )
                return

            try:
                await original(self, signal, economics)
            finally:
                getattr(self, "_multi_strategy_signal_routes", {}).pop(
                    signal_id, None
                )
                _prune_routes(self)

    serialized_buy._multi_strategy_concurrency_guard = True  # type: ignore[attr-defined]
    RFDir5TradingBot._buy_selected_accounts = serialized_buy
    RFDir5TradingBot._multi_strategy_concurrency_guard_installed = True
    _INSTALLED = True
