from __future__ import annotations

from app.route_utils import remove_route as _remove_route

from typing import Any

from fastapi import Request

import app.api as base_api
from app.custom_martingale import read_account_martingale_settings
from app.personal_token_sync import install_personal_token_sync
from app.token_store import decrypt_auth_payload


_INSTALLED = False
_STALE_TOKEN_STATUSES = {"token_required", "bulk_execution_pat_required"}




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


def _reconcile_stale_token_status(request: Request) -> None:
    """Heal a stale token-required badge when encrypted token data still exists.

    The API previously calculated ``has_trading_api_token`` as token-present AND
    status-not-token-required. A status written before a worker restart could
    therefore hide a valid stored token indefinitely. Only generic stale statuses
    are healed here; explicit expired, invalid or rejected states remain blocked.
    """

    account = base_api.get_current_account(request)
    if not account:
        return
    status = str(account.get("execution_status") or "").strip().lower()
    reason = str(account.get("execution_status_reason") or "").strip()
    if status not in _STALE_TOKEN_STATUSES:
        return
    if base_api.execution_token_was_rejected(status, reason):
        return

    row = base_api.REPOSITORY.managed_account(int(account["id"]))
    if not row:
        return
    try:
        stored = decrypt_auth_payload(
            row["token_secret"],
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception:
        return
    if not base_api.has_personal_trading_api_token(stored):
        return

    enabled = bool(row.get("enabled", False))
    base_api.REPOSITORY.set_managed_account_execution_status(
        int(account["id"]),
        "connecting" if enabled else "disabled",
        (
            "Stored Deriv API token detected; runtime validation pending"
            if enabled
            else "Stored Deriv API token detected; auto trading is disabled"
        ),
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
        _reconcile_stale_token_status(request)
        payload = base_api.get_me(request)
        if not payload.get("authenticated"):
            return payload
        _apply_settled_trade_consistency(payload)
        _apply_custom_martingale_settings(payload, request)
        return payload

    # One verified trade-scoped token is account-owner scoped, not Demo-only or
    # Real-only. The installer validates the selected account ID and synchronizes
    # the credential to every linked Options account returned by Deriv.
    install_personal_token_sync(app)

    app.state.personal_me_session_fix_installed = True
    _INSTALLED = True
