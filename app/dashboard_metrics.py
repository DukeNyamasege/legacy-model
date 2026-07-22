from __future__ import annotations

from typing import Any


def build_execution_summary(
    summary: dict[str, Any],
    *,
    active_accounts: list[dict[str, Any]],
    linked_accounts: list[dict[str, Any]],
    master: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shape global cards around the master while retaining an all-account P/L."""
    result = dict(summary)
    result["accounts"] = active_accounts
    result["total_traders"] = len(active_accounts)
    result["account_balance_total"] = sum(
        float(account.get("balance") or 0.0) for account in active_accounts
    )
    if not active_accounts and str(result.get("status", "")) == "RUNNING":
        result.update(
            {
                "ai_activity_mode": "watching",
                "ai_activity_label": "Market watcher online",
                "ai_activity_message": "Waiting for an active trading account",
                "ai_activity_detail": (
                    "Ticks remain online and execution becomes available when an "
                    "enabled account has a healthy private connection."
                ),
            }
        )

    if master:
        result["primary_account"] = master.get("account", "")
        result["primary_account_balance"] = float(master.get("balance") or 0.0)
        result["primary_account_currency"] = master.get("currency", "USD")
        result["purchased_trades"] = int(master.get("trades") or 0)
        result["wins"] = int(master.get("wins") or 0)
        result["losses"] = int(master.get("losses") or 0)
        result["win_rate"] = float(master.get("win_rate") or 0.0)
        result["net_profit"] = float(master.get("profit") or 0.0)
        result["longest_win_streak"] = int(master.get("longest_win_streak") or 0)
        result["longest_loss_streak"] = int(master.get("longest_loss_streak") or 0)
        result["open_trades"] = int(master.get("open_trades") or 0)
        result["oldest_open_trade_seconds"] = int(
            master.get("oldest_open_trade_seconds") or 0
        )
        result["primary_virtual_protection"] = master.get(
            "virtual_protection",
            {
                "mode": "NORMAL_MODE",
                "state": "NORMAL_MODE",
                "consecutive_actual_losses": 0,
                "actual_recovery_debt": 0.0,
                "virtual_observations": 0,
                "virtual_wins": 0,
                "virtual_losses": 0,
                "current_virtual_loss_streak": 0,
                "entered_virtual_mode_at": None,
            },
        )
        result["stale_open_trades"] = int(
            result["open_trades"] > 0
            and result["oldest_open_trade_seconds"]
            > int(result.get("max_open_trade_seconds") or 6)
        )
    else:
        result.update(
            {
                "primary_account": "",
                "primary_account_balance": 0.0,
                "primary_account_currency": "USD",
                "purchased_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "net_profit": 0.0,
                "longest_win_streak": 0,
                "longest_loss_streak": 0,
                "open_trades": 0,
                "oldest_open_trade_seconds": 0,
                "stale_open_trades": 0,
                "primary_virtual_protection": {
                    "mode": "NORMAL_MODE",
                    "state": "NORMAL_MODE",
                    "consecutive_actual_losses": 0,
                    "actual_recovery_debt": 0.0,
                    "virtual_observations": 0,
                    "virtual_wins": 0,
                    "virtual_losses": 0,
                    "current_virtual_loss_streak": 0,
                    "entered_virtual_mode_at": None,
                },
            }
        )

    master_trade_count = int(master.get("trades") or 0) if master else 0
    result["copy_trade_gap"] = max(
        (
            abs(int(account.get("trades") or 0) - master_trade_count)
            for account in active_accounts
        ),
        default=0,
    )
    result["copy_consistency_ok"] = result["copy_trade_gap"] == 0
    result["all_accounts_profit"] = sum(
        float(account.get("profit") or 0.0) for account in linked_accounts
    )
    result["all_accounts_trades"] = sum(
        int(account.get("trades") or 0) for account in linked_accounts
    )
    return result
