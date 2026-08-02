from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy import or_, select

import app.api as base_api
from app.final_public_controls import _reporting_timezone, _today_bounds_utc
from app.models import AccountRiskState, VirtualTrade
from app.repositories.rf_dir5_repository import REAL_RECOVERY_PENDING, VIRTUAL_WAITING_FOR_WIN

_INSTALLED = False


def _remove_route(app: Any, path: str, method: str) -> None:
    expected = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        )
    ]


def _split_remaining(managed_account_id: int) -> int:
    try:
        value = base_api.REPOSITORY.runtime_preference(
            f"aidr_split_remaining:{int(managed_account_id)}"
        )
        return max(0, min(2, int(str(value or "0"))))
    except Exception:
        return 0


def install_personal_virtual_status_api(app: Any) -> None:
    """Expose the logged-in account's AIDR protection state and $0 trades."""

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me/aidr-status", "GET")

    @app.get("/me/aidr-status")
    def personal_aidr_status(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            return {
                "authenticated": False,
                "mode": "logged_out",
                "virtual_trades": [],
            }

        managed_id = int(account["id"])
        start, end = _today_bounds_utc()
        with base_api.DATABASE.session() as session:
            state = session.get(AccountRiskState, managed_id)
            rows = session.scalars(
                select(VirtualTrade)
                .where(VirtualTrade.managed_account_id == managed_id)
                .where(
                    or_(
                        VirtualTrade.created_at.between(start, end),
                        VirtualTrade.settled_at.between(start, end),
                    )
                )
                .order_by(VirtualTrade.created_at.desc())
                .limit(250)
            ).all()

        debt = float(state.recovery_loss_debt or 0.0) if state is not None else 0.0
        raw_mode = str(state.protection_mode or "NORMAL_MODE") if state is not None else "NORMAL_MODE"
        virtual_wins = int(state.virtual_win_count or 0) if state is not None else 0
        split_remaining = _split_remaining(managed_id)

        if raw_mode == VIRTUAL_WAITING_FOR_WIN:
            mode = "virtual"
            next_action = f"Waiting for {max(0, 2 - virtual_wins)} more consecutive virtual OVER-3 win(s)."
        elif raw_mode == REAL_RECOVERY_PENDING and split_remaining > 0:
            mode = "split_recovery"
            next_action = f"Real OVER-3 split recovery: {split_remaining} profit target(s) remaining."
        elif raw_mode == REAL_RECOVERY_PENDING:
            mode = "exact_recovery"
            next_action = "Next qualifying trade is one real OVER-3 exact recovery."
        else:
            mode = "normal"
            next_action = "Normal OVER-1 execution."

        virtual_trades = [
            {
                "id": int(row.id),
                "virtual_trade_id": row.virtual_trade_id,
                "market": row.market,
                "contract_type": row.contract_type,
                "barrier": row.barrier,
                "simulated_stake": float(row.simulated_stake or 0.0),
                "expected_payout": (
                    float(row.expected_payout) if row.expected_payout is not None else None
                ),
                "result": row.result,
                "actual_last_digit": row.actual_last_digit,
                "amount_charged": float(row.amount_charged or 0.0),
                "actual_profit_loss": float(row.actual_profit_loss or 0.0),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "settled_at": row.settled_at.isoformat() if row.settled_at else None,
            }
            for row in rows
        ]

        return {
            "authenticated": True,
            "account": str(
                account.get("account_id_full")
                or account.get("login_id")
                or account.get("account_id_masked")
                or ""
            ),
            "account_type": str(account.get("account_type") or "demo"),
            "timezone": str(_reporting_timezone()),
            "mode": mode,
            "raw_mode": raw_mode,
            "recovery_debt": round(debt, 2),
            "consecutive_losses": int(state.consecutive_losses or 0) if state is not None else 0,
            "virtual_wins": virtual_wins,
            "virtual_wins_required": 2,
            "virtual_losses": int(state.virtual_loss_count or 0) if state is not None else 0,
            "virtual_observations": int(state.virtual_observation_count or 0) if state is not None else 0,
            "split_recovery_remaining": split_remaining,
            "next_action": next_action,
            "virtual_trades": virtual_trades,
        }

    app.state.personal_virtual_status_api_installed = True
    _INSTALLED = True
