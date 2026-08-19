from __future__ import annotations

from app.route_utils import remove_route as _remove_route

import math
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

import app.api as base_api
from app.custom_martingale import (
    normalize_martingale_mode,
    normalize_martingale_settings,
    save_account_martingale_settings,
)

_INSTALLED = False


class FinalTradingSettingsRequest(BaseModel):
    stake_amount: float
    take_profit: float = 0.0
    stop_loss: float = 0.0
    martingale_enabled: bool = True
    martingale_mode: str | None = Field(default=None, pattern="^(system|custom|flat)$")
    martingale_trigger_losses: int = Field(default=1, ge=1, le=10)
    martingale_multiplier: float = Field(default=2.0, ge=1.10, le=10.0)
    martingale_max_levels: int = Field(default=6, ge=1, le=10)
    martingale_max_stake: float = Field(default=1000.0, ge=0.35, le=1_000_000.0)




def _validate_money(value: float, *, name: str, minimum: float, maximum: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise HTTPException(status_code=400, detail=f"{name} must be a finite number.")
    number = round(number, 2)
    if number < minimum or number > maximum:
        raise HTTPException(
            status_code=400,
            detail=f"{name} must be between {minimum:g} and {maximum:g}.",
        )
    return number


def install_settings_persistence_fix(app: Any) -> None:
    """Install the final account-settings route.

    Settings are part of onboarding and must be saved before a trading token is
    available. Execution still requires a valid token, but stake, take-profit,
    stop-loss and Martingale preferences must persist immediately and be returned
    to the UI as the source of truth.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me/trading-settings", "POST")

    @app.post("/me/trading-settings")
    def final_update_trading_settings(
        request: Request,
        body: FinalTradingSettingsRequest,
    ) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")

        stake_amount = _validate_money(
            body.stake_amount,
            name="Stake amount",
            minimum=0.35,
            maximum=1_000_000.0,
        )
        take_profit = _validate_money(
            body.take_profit,
            name="Take profit",
            minimum=0.0,
            maximum=1_000_000.0,
        )
        stop_loss = _validate_money(
            abs(float(body.stop_loss)),
            name="Stop loss",
            minimum=0.0,
            maximum=1_000_000.0,
        )

        mode = normalize_martingale_mode(
            body.martingale_mode,
            legacy_enabled=bool(body.martingale_enabled),
        )
        advanced = normalize_martingale_settings(
            {
                "mode": mode,
                "trigger_losses": body.martingale_trigger_losses,
                "multiplier": body.martingale_multiplier,
                "max_levels": body.martingale_max_levels,
                "max_stake": body.martingale_max_stake,
            },
            legacy_enabled=bool(body.martingale_enabled),
            base_stake=stake_amount,
        )

        basic = base_api.REPOSITORY.update_account_execution_settings(
            int(account["id"]),
            stake_amount=stake_amount,
            take_profit=take_profit,
            stop_loss=stop_loss,
            martingale_enabled=bool(advanced["martingale_enabled"]),
        )
        advanced = save_account_martingale_settings(
            base_api.REPOSITORY,
            int(account["id"]),
            advanced,
        )
        settings = {
            **basic,
            "stake_amount": stake_amount,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "martingale_enabled": bool(advanced["martingale_enabled"]),
            "martingale_mode": advanced["mode"],
            "martingale_trigger_losses": advanced["trigger_losses"],
            "martingale_multiplier": advanced["multiplier"],
            "martingale_max_levels": advanced["max_levels"],
            "martingale_max_stake": advanced["max_stake"],
            "martingale_policy": advanced["policy"],
        }

        try:
            base_api.mark_dashboard_dirty(account.get("account_type"))
            base_api.REPOSITORY.audit(
                "FINAL_TRADING_SETTINGS_SAVED",
                "standalone-dashboard",
                request.client.host if request.client else "unknown",
                {
                    "managed_account_id": int(account["id"]),
                    "account_id_masked": account.get("account_id_masked"),
                    "token_ready": bool(account.get("has_trading_api_token", False)),
                    **settings,
                },
            )
        except Exception:
            base_api.LOGGER.exception(
                "FINAL_TRADING_SETTINGS_AUDIT_FAILED account=%s",
                account.get("account_id_masked", "account"),
            )

        return {
            "success": True,
            "settings": settings,
            "token_required_before_start": not bool(
                account.get("has_trading_api_token", False)
            ),
            "message": "Trading settings saved.",
        }

    app.state.settings_persistence_fix_installed = True
    _INSTALLED = True
