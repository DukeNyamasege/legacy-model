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
    """Serialize provider setup without making account groups compete.

    The lock protects mutable purchase-registration state while each account group
    reaches the private WebSocket boundary. A standardized signal may coexist with
    contracts that belong to a different account group: the base eligibility
    filter removes only accounts whose own previous contract is still settling.
    Legacy/non-standardized calls retain the conservative global pending-cycle
    guard so unknown execution paths cannot create overlapping positions.
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
        standardized = bool(getattr(signal, "_standardized_cycle_id", ""))
        async with gate:
            _prune_routes(self, keep_signal_id=signal_id)
            market = getattr(self, "market_states", {}).get(
                str(getattr(signal, "symbol", "") or "")
            )

            if standardized:
                # Proposal preparation and the previous account group can consume
                # one or more ticks. Refresh the already-qualified rolling-window
                # signal to the provider's current tick before sending money.
                from app.standardized_execution_runtime import (
                    refresh_signal_for_execution,
                )

                if not refresh_signal_for_execution(self, signal):
                    try:
                        self.repository.mark_signal(
                            signal_id,
                            status="SKIP_STANDARDIZED_SIGNAL_EXPIRED_AT_GATE",
                            stale=True,
                        )
                    finally:
                        getattr(self, "_multi_strategy_signal_routes", {}).pop(
                            signal_id, None
                        )
                    return
            elif market is not None and int(getattr(market, "tick_sequence", 0)) != int(
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

            pending_count = len(
                set(getattr(self, "pending_contracts_for_current_cycle", set()) or set())
            )
            if pending_count and not standardized:
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
            if pending_count and standardized:
                self.logger.info(
                    "STANDARDIZED_GROUP_COEXISTS_WITH_OPEN_CONTRACTS signal_id=%s "
                    "pending_contracts=%s account_scoped_eligibility=true",
                    signal_id,
                    pending_count,
                )

            try:
                await original(self, signal, economics)
            finally:
                getattr(self, "_multi_strategy_signal_routes", {}).pop(
                    signal_id, None
                )
                _prune_routes(self)
                if standardized:
                    from app.standardized_signal_metadata import (
                        clear_standardized_cycle_id,
                    )

                    clear_standardized_cycle_id(signal)

    serialized_buy._multi_strategy_concurrency_guard = True  # type: ignore[attr-defined]
    RFDir5TradingBot._buy_selected_accounts = serialized_buy
    RFDir5TradingBot._multi_strategy_concurrency_guard_installed = True
    _INSTALLED = True
