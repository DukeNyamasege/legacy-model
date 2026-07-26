from __future__ import annotations

import asyncio
from typing import Any

from enhanced_bot import TradingBot, sanitize_account_ids


def _masked_app_id(value: Any) -> str:
    app_id = str(value or "").strip()
    if len(app_id) <= 7:
        return app_id or "unset"
    return f"{app_id[:4]}...{app_id[-3:]}"


async def _websocket_only_purchase_accounts_by_stake(
    self: TradingBot,
    *,
    signal: Any,
    eligible_accounts: list[tuple[str, str]],
    stake_by_token: dict[str, float],
    pre_trade_profit_ratio: float = 0.0,
) -> list[dict[str, Any]]:
    """Execute every production account purchase through a private Deriv WebSocket.

    REST Bulk Purchase is intentionally unreachable from the production worker.
    The existing private-session buy path remains responsible for attaching
    app_markup_percentage, registering the provider contract and subscribing to
    settlement updates.
    """

    del pre_trade_profit_ratio

    private_groups: dict[float, list[tuple[str, str]]] = {}
    rejected: list[dict[str, Any]] = []

    for token, account_id in eligible_accounts:
        environment = self._account_environment_for_token(token)
        if environment == "real" and not self._real_trading_allowed():
            message = "Real trading is disabled on this VPS"
            self._set_account_execution_status(
                self._managed_account_id_for_token(token),
                "real_disabled",
                message,
            )
            rejected.append(
                {
                    "account_id": account_id,
                    "error": {
                        "code": "REAL_DISABLED",
                        "message": message,
                    },
                }
            )
            continue

        stake = round(float(stake_by_token[token]), 2)
        private_groups.setdefault(stake, []).append((token, account_id))

    group_items = sorted(private_groups.items(), key=lambda item: item[0])

    self.logger.warning(
        "WEBSOCKET_ONLY_EXECUTION signal_id=%s transport=PRIVATE_WEBSOCKET "
        "app_id=%s markup_percentage=%.2f accounts=%s stake_groups=%s",
        getattr(signal, "signal_id", "unknown"),
        _masked_app_id(getattr(self, "app_id", "")),
        float(getattr(self, "app_markup_percentage", 0.0) or 0.0),
        sum(len(accounts) for _stake, accounts in group_items),
        len(group_items),
    )

    tasks = [
        self._purchase_via_private_sessions(
            signal=signal,
            eligible_accounts=accounts,
            stake_amount=stake,
        )
        for stake, accounts in group_items
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    transactions: list[dict[str, Any]] = list(rejected)

    for (stake, accounts), result in zip(group_items, results):
        if isinstance(result, Exception):
            message = sanitize_account_ids(str(result))
            self.logger.error(
                "PRIVATE_WEBSOCKET_GROUP_FAILED signal_id=%s stake=%.2f "
                "accounts=%s error=%s",
                getattr(signal, "signal_id", "unknown"),
                stake,
                len(accounts),
                message,
            )
            result = [
                {
                    "account_id": account_id,
                    "error": {
                        "code": "PRIVATE_WEBSOCKET_GROUP_FAILED",
                        "message": message,
                    },
                }
                for _token, account_id in accounts
            ]

        for transaction in result:
            item = dict(transaction)
            item["stake_amount"] = stake
            item["execution_transport"] = (
                "PRIVATE_WS_MARKUP"
                if float(getattr(self, "app_markup_percentage", 0.0) or 0.0) > 0
                else "PRIVATE_WS"
            )
            # Historical rows may have a bulk_batch_id, but a new private
            # WebSocket execution must never be classified as a Bulk purchase.
            item.pop("bulk_batch_id", None)
            transactions.append(item)

    return transactions


def install_websocket_only_execution() -> None:
    """Install the production transport guard before the worker creates the bot."""

    TradingBot._purchase_accounts_by_stake = _websocket_only_purchase_accounts_by_stake
