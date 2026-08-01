from __future__ import annotations

from typing import Any

from fastapi import Request

import app.api as base_api
from app.custom_martingale import read_account_martingale_settings


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


def _apply_settled_trade_consistency(payload: dict[str, Any]) -> None:
    stats = dict(payload.get("stats") or {})
    reported_trades = int(stats.get("trades") or 0)
    wins = int(stats.get("wins") or 0)
    losses = int(stats.get("losses") or 0)
    settled_trades = wins + losses
    stats.update(
        {
            "trades": settled_trades,
            "settled_trades": settled_trades,
            "open_trades": max(0, reported_trades - settled_trades),
        }
    )
    payload["stats"] = stats
    payload["data_consistency"] = {
        "invariant_ok": settled_trades == wins + losses,
        "rule": "completed_trades_equal_wins_plus_losses",
    }


def _apply_custom_martingale_settings(
    payload: dict[str, Any],
    request: Request,
) -> None:
    account = base_api.get_current_account(request)
    if not account:
        return
    settings = read_account_martingale_settings(
        base_api.REPOSITORY,
        int(account["id"]),
    )
    payload.setdefault("settings", {}).update(
        {
            "martingale_enabled": bool(settings["martingale_enabled"]),
            "martingale_mode": settings["mode"],
            "martingale_trigger_losses": settings["trigger_losses"],
            "martingale_multiplier": settings["multiplier"],
            "martingale_max_levels": settings["max_levels"],
            "martingale_max_stake": settings["max_stake"],
            "martingale_policy": settings["policy"],
        }
    )


def install_personal_me_session_fix(app: Any) -> None:
    """Install the final `/me` route with a real FastAPI Request parameter.

    The custom Martingale layer replaced `/me` using a locally imported Request
    annotation while postponed annotations are enabled. FastAPI can then treat the
    unresolved annotation like a missing query parameter and return 422 for every
    personal dashboard request. This final route is installed after all wrappers,
    preserves the existing base `/me` payload, then adds settled-trade consistency
    and account-scoped Martingale settings.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me", "GET")

    @app.get("/me")
    def personal_me_session_fixed(request: Request) -> dict[str, Any]:
        payload = base_api.get_me(request)
        if not payload.get("authenticated"):
            return payload
        _apply_settled_trade_consistency(payload)
        _apply_custom_martingale_settings(payload, request)
        return payload

    app.state.personal_me_session_fix_installed = True
    _INSTALLED = True
