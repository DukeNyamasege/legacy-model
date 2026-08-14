from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from app.custom_strategy_v1 import MAX_WINDOW
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False


def _cancel_removed_account_tasks(bot: RFDir5TradingBot, active_ids: set[int]) -> None:
    for task in list(getattr(bot, "_custom_direct_tasks", set()) or set()):
        name = str(task.get_name() or "")
        if not name.startswith("custom_direct_"):
            continue
        parts = name.split("_", 3)
        try:
            managed_id = int(parts[2])
        except (IndexError, TypeError, ValueError):
            continue
        if managed_id not in active_ids and not task.done():
            task.cancel()
            getattr(bot, "_custom_direct_inflight", set()).discard(managed_id)


def _custom_history(
    self: RFDir5TradingBot,
    *,
    symbol: str,
    prices: list[Any],
    times: list[Any],
    pip_size: Any,
) -> None:
    market = self.market_states.get(symbol)
    if market is None:
        return
    try:
        market.pip_size = int(pip_size if pip_size is not None else market.pip_size)
    except (TypeError, ValueError):
        pass
    history_prices = list(prices[-MAX_WINDOW:])
    history_times = list(times[-len(history_prices):]) if history_prices else []
    market.ticks_history.clear()
    market.live_ticks_history.clear()
    market.raw_tick_digits.clear()
    for index, raw_quote in enumerate(history_prices):
        quote = Decimal(str(raw_quote))
        display = f"{quote:.{market.pip_size}f}"
        digit = next(
            (int(character) for character in reversed(display) if character.isdigit()),
            None,
        )
        try:
            epoch = int(history_times[index]) if index < len(history_times) else 0
        except (TypeError, ValueError):
            epoch = 0
        snapshot = {
            "quote": quote,
            "display": display,
            "epoch": epoch,
            "tick_id": f"history:{symbol}:{epoch}:{index}",
            "last_digit": digit if digit is not None else "-",
        }
        market.ticks_history.append(snapshot)
        market.live_ticks_history.append(snapshot)
        if digit is not None:
            market.raw_tick_digits.append(digit)
    if market.ticks_history:
        latest = market.ticks_history[-1]
        self.rf_last_epoch[symbol] = int(latest["epoch"] or 0)
        self.rf_last_tick_id[symbol] = str(latest["tick_id"])


def install_custom_strategy_runtime_lifecycle() -> None:
    """Remove remaining RF subscription work and tear down stopped account tasks."""

    global _INSTALLED
    if _INSTALLED:
        return

    current_refresh = RFDir5TradingBot._refresh_runtime_accounts_if_needed

    async def refresh_with_teardown(self: RFDir5TradingBot) -> None:
        before = set(getattr(self, "_custom_direct_accounts", {}).keys())
        await current_refresh(self)
        after = set(getattr(self, "_custom_direct_accounts", {}).keys())
        removed = before - after
        if removed:
            _cancel_removed_account_tasks(self, after)
            for managed_id in removed:
                self._custom_direct_virtual_due.pop(managed_id, None)
                self._custom_direct_seen = {
                    key for key in self._custom_direct_seen if int(key[0]) != managed_id
                }
                self.logger.info(
                    "CUSTOM_ACCOUNT_RUNTIME_DESTROYED managed_id=%s subscriptions_released=true",
                    managed_id,
                )

    def custom_subscriptions_ready(self: RFDir5TradingBot) -> None:
        # PublicMarketClient already owns exactly the symbols in self.symbols.
        # Do not run RF contract validation/proposal work after subscription.
        self.logger.info(
            "CUSTOM_MARKET_SUBSCRIPTIONS_READY markets=%s rf_validation=false",
            ",".join(self.symbols),
        )

        # Public market watching and private financial execution readiness are two
        # different states. Once live subscriptions exist, do not leave a trader on
        # a generic six-minute-looking STARTING banner merely because their account
        # OTP/private stream is still completing in the background. BUY remains
        # impossible until AccountExecutionSession.prepare() verifies that stream.
        for token, _account_id in list(getattr(self, "valid_clients", []) or []):
            managed_id = self._managed_account_id_for_token(token)
            if managed_id is None:
                continue
            private = getattr(self, "sessions", {}).get(token)
            if private is not None and bool(getattr(private, "is_connected", False)):
                continue
            self._set_account_execution_status(
                int(managed_id),
                "watching",
                "Watching market now; authenticated execution stream is connecting in background",
            )

    RFDir5TradingBot._refresh_runtime_accounts_if_needed = refresh_with_teardown
    RFDir5TradingBot._on_market_subscriptions_ready = custom_subscriptions_ready
    RFDir5TradingBot._on_public_history = _custom_history
    RFDir5TradingBot._custom_strategy_runtime_lifecycle_installed = True
    _INSTALLED = True
