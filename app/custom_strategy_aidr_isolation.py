from __future__ import annotations

from typing import Any

import app.ai_digit_recovery_v1 as aidr
import app.multi_strategy_runtime as multi
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False


def install_custom_strategy_aidr_isolation() -> None:
    """Prevent Custom-only accounts from keeping the System scanner active."""

    global _INSTALLED
    if _INSTALLED:
        return

    current_enabled = aidr._enabled_accounts

    def enabled_without_custom(bot: Any) -> list[tuple[str, str, int]]:
        custom_ids = {
            int(route.managed_id)
            for route in multi._strategy_snapshot(bot)
            if str(getattr(route.selection, "family", "") or "") == "custom"
        }
        return [
            (token, account_id, int(managed_id))
            for token, account_id, managed_id in current_enabled(bot)
            if int(managed_id) not in custom_ids
        ]

    enabled_without_custom._custom_strategy_aidr_isolation = True  # type: ignore[attr-defined]
    aidr._enabled_accounts = enabled_without_custom
    multi._filter_aidr_over_accounts = enabled_without_custom
    RFDir5TradingBot._custom_strategy_aidr_isolation_installed = True
    _INSTALLED = True
