from __future__ import annotations

import json
import math
from typing import Any

from pydantic import BaseModel, Field

from app.models import AccountRiskState
from app.recovery import ceil_cents
from app.repositories.rf_dir5_repository import RFDir5Repository, StakePlan

SYSTEM_MODE = "system"
CUSTOM_MODE = "custom"
FLAT_MODE = "flat"
ALLOWED_MODES = {SYSTEM_MODE, CUSTOM_MODE, FLAT_MODE}
PREFERENCE_PREFIX = "account_martingale:"
DEFAULT_TRIGGER_LOSSES = 1
DEFAULT_MULTIPLIER = 2.0
DEFAULT_MAX_LEVELS = 6
DEFAULT_MAX_STAKE = 1000.0

_API_INSTALLED = False
_WORKER_INSTALLED = False


class AdvancedTradingSettingsRequest(BaseModel):
    stake_amount: float
    take_profit: float = 0.0
    stop_loss: float = 0.0
    martingale_enabled: bool = True
    martingale_mode: str | None = Field(
        default=None,
        pattern="^(system|custom|flat)$",
    )
    martingale_trigger_losses: int = Field(default=1, ge=1, le=10)
    martingale_multiplier: float = Field(default=2.0, ge=1.10, le=10.0)
    martingale_max_levels: int = Field(default=6, ge=1, le=10)
    martingale_max_stake: float = Field(
        default=1000.0,
        ge=0.35,
        le=1_000_000.0,
    )


def _preference_key(managed_account_id: int) -> str:
    return f"{PREFERENCE_PREFIX}{int(managed_account_id)}"


def normalize_martingale_mode(value: Any, *, legacy_enabled: bool = True) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in ALLOWED_MODES:
        return normalized
    return SYSTEM_MODE if legacy_enabled else FLAT_MODE


def normalize_martingale_settings(
    payload: dict[str, Any] | None,
    *,
    legacy_enabled: bool = True,
    base_stake: float = 0.50,
) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    mode = normalize_martingale_mode(
        source.get("mode") or source.get("martingale_mode"),
        legacy_enabled=legacy_enabled,
    )

    try:
        trigger_losses = int(source.get("trigger_losses", DEFAULT_TRIGGER_LOSSES))
    except (TypeError, ValueError):
        trigger_losses = DEFAULT_TRIGGER_LOSSES
    trigger_losses = max(1, min(10, trigger_losses))

    try:
        multiplier = float(source.get("multiplier", DEFAULT_MULTIPLIER))
    except (TypeError, ValueError):
        multiplier = DEFAULT_MULTIPLIER
    if not math.isfinite(multiplier):
        multiplier = DEFAULT_MULTIPLIER
    multiplier = round(max(1.10, min(10.0, multiplier)), 2)

    try:
        max_levels = int(source.get("max_levels", DEFAULT_MAX_LEVELS))
    except (TypeError, ValueError):
        max_levels = DEFAULT_MAX_LEVELS
    max_levels = max(1, min(10, max_levels))

    minimum_cap = ceil_cents(max(0.35, float(base_stake or 0.50)))
    try:
        max_stake = float(source.get("max_stake", DEFAULT_MAX_STAKE))
    except (TypeError, ValueError):
        max_stake = DEFAULT_MAX_STAKE
    if not math.isfinite(max_stake):
        max_stake = DEFAULT_MAX_STAKE
    max_stake = ceil_cents(
        max(minimum_cap, min(1_000_000.0, max_stake))
    )

    return {
        "mode": mode,
        "trigger_losses": trigger_losses,
        "multiplier": multiplier,
        "max_levels": max_levels,
        "max_stake": max_stake,
        "martingale_enabled": mode != FLAT_MODE,
        "policy": (
            "system_exact_debt_recovery"
            if mode == SYSTEM_MODE
            else "custom_multiplier"
            if mode == CUSTOM_MODE
            else "flat_primary_stake"
        ),
    }


def read_account_martingale_settings(
    repository: Any,
    managed_account_id: int,
) -> dict[str, Any]:
    account = repository.managed_account(int(managed_account_id)) or {}
    legacy_enabled = bool(account.get("martingale_enabled", True))
    base_stake = float(account.get("stake_amount", 0.50) or 0.50)
    try:
        raw = repository.runtime_preference(
            _preference_key(managed_account_id)
        )
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        payload = {}
    return normalize_martingale_settings(
        payload,
        legacy_enabled=legacy_enabled,
        base_stake=base_stake,
    )


def save_account_martingale_settings(
    repository: Any,
    managed_account_id: int,
    settings: dict[str, Any],
) -> dict[str, Any]:
    account = repository.managed_account(int(managed_account_id)) or {}
    normalized = normalize_martingale_settings(
        settings,
        legacy_enabled=bool(account.get("martingale_enabled", True)),
        base_stake=float(account.get("stake_amount", 0.50) or 0.50),
    )
    repository.set_runtime_preference(
        _preference_key(managed_account_id),
        json.dumps(normalized, sort_keys=True, separators=(",", ":")),
    )
    return normalized


def custom_martingale_stake(
    *,
    base_stake: float,
    consecutive_losses: int,
    trigger_losses: int,
    multiplier: float,
    max_levels: int,
    max_stake: float,
) -> tuple[float, int, bool]:
    """Return the account's custom stake, level and cap state.

    Level one starts when consecutive actual losses reach trigger_losses. Each
    additional actual loss advances one multiplier level. Virtual observations do
    not change the level because they have no monetary loss.
    """

    base = ceil_cents(max(0.35, float(base_stake)))
    losses = max(0, int(consecutive_losses))
    trigger = max(1, int(trigger_losses))
    if losses < trigger:
        return base, 0, False

    level = min(
        max(1, losses - trigger + 1),
        max(1, int(max_levels)),
    )
    calculated = base * (float(multiplier) ** level)
    capped = calculated > float(max_stake) + 1e-9
    return (
        ceil_cents(min(calculated, float(max_stake))),
        level,
        capped,
    )


def install_custom_martingale_worker() -> None:
    """Install the final account-level stake selector after core stake policies."""

    global _WORKER_INSTALLED
    if _WORKER_INSTALLED:
        return

    original_plan_stake = RFDir5Repository.plan_stake

    def plan_stake_with_account_profile(
        self: RFDir5Repository,
        *,
        managed_account_id: int,
        account_id_masked: str = "",
        current_balance: float,
        requested_stake: float,
        proposal_profit_ratio: float,
        recovery_enabled: bool,
        recovery_trigger_losses: int,
        minimum_stake: float,
        virtual_protection_enabled: bool = True,
        maximum_recovery_balance_fraction: float = 0.10,
        minimum_balance_reserve: float = 0.50,
    ) -> StakePlan:
        settings = read_account_martingale_settings(
            self.base,
            int(managed_account_id),
        )
        mode = str(settings["mode"])

        # System mode is the unchanged built-in exact-debt recovery planner.
        # Custom asks the core planner for base stake while retaining its recovery
        # and virtual-state gate, then replaces only the real recovery amount.
        # Flat mode keeps the base stake and does not arm Martingale recovery.
        plan = original_plan_stake(
            self,
            managed_account_id=managed_account_id,
            account_id_masked=account_id_masked,
            current_balance=current_balance,
            requested_stake=requested_stake,
            proposal_profit_ratio=proposal_profit_ratio,
            recovery_enabled=(recovery_enabled if mode == SYSTEM_MODE else False),
            recovery_trigger_losses=recovery_trigger_losses,
            minimum_stake=minimum_stake,
            virtual_protection_enabled=virtual_protection_enabled,
            maximum_recovery_balance_fraction=maximum_recovery_balance_fraction,
            minimum_balance_reserve=minimum_balance_reserve,
        )

        if mode != CUSTOM_MODE or plan.stake is None or not plan.is_recovery:
            return plan

        with self.database.session() as session:
            state = session.get(
                AccountRiskState,
                int(managed_account_id),
            )
            consecutive_losses = (
                int(state.consecutive_losses or 0) if state else 0
            )
            recovery_debt = (
                float(state.recovery_loss_debt or 0.0) if state else 0.0
            )

        stake, level, capped = custom_martingale_stake(
            base_stake=max(float(minimum_stake), float(requested_stake)),
            consecutive_losses=consecutive_losses,
            trigger_losses=int(settings["trigger_losses"]),
            multiplier=float(settings["multiplier"]),
            max_levels=int(settings["max_levels"]),
            max_stake=float(settings["max_stake"]),
        )
        if level <= 0:
            return plan

        return StakePlan(
            stake=stake,
            reason=(
                f"custom Martingale level {level}; "
                f"multiplier x{settings['multiplier']:.2f}"
                + ("; user maximum stake reached" if capped else "")
            ),
            is_recovery=True,
            recovery_debt=recovery_debt,
            required_recovery_stake=stake,
        )

    RFDir5Repository.plan_stake = plan_stake_with_account_profile
    RFDir5Repository._custom_martingale_worker_installed = True
    _WORKER_INSTALLED = True


def _remove_route(base_api: Any, path: str, method: str) -> None:
    expected = method.upper()
    base_api.app.router.routes[:] = [
        route
        for route in base_api.app.router.routes
        if not (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        )
    ]


def install_custom_martingale_api() -> None:
    """Expose per-account system/custom/flat controls without a schema migration."""

    global _API_INSTALLED
    if _API_INSTALLED:
        return

    import app.api as base_api
    from fastapi import HTTPException, Request
    from fastapi.responses import FileResponse

    original_get_me = base_api.get_me

    _remove_route(base_api, "/me", "GET")
    _remove_route(base_api, "/me/trading-settings", "POST")

    @base_api.app.get("/me")
    def get_me_with_martingale(request: Request) -> dict[str, Any]:
        payload = original_get_me(request)
        if not payload.get("authenticated"):
            return payload
        account = base_api.get_current_account(request)
        if not account:
            return payload
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
        return payload

    @base_api.app.post("/me/trading-settings")
    def update_advanced_trading_settings(
        request: Request,
        body: AdvancedTradingSettingsRequest,
    ) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not account.get("has_trading_api_token", False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Save a Deriv trading credential before configuring "
                    "trading controls."
                ),
            )

        numeric_values = (
            body.stake_amount,
            body.take_profit,
            body.stop_loss,
            body.martingale_multiplier,
            body.martingale_max_stake,
        )
        if not all(
            math.isfinite(float(value)) for value in numeric_values
        ):
            raise HTTPException(
                status_code=400,
                detail="Trading settings must be finite numbers.",
            )

        stake_amount = round(float(body.stake_amount), 2)
        take_profit = round(float(body.take_profit), 2)
        stop_loss = round(abs(float(body.stop_loss)), 2)
        if not 0.35 <= stake_amount <= 1_000_000:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Stake amount must be between 0.35 and 1,000,000."
                ),
            )
        if (
            not 0 <= take_profit <= 1_000_000
            or not 0 <= stop_loss <= 1_000_000
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Take profit and stop loss must be between 0 and "
                    "1,000,000."
                ),
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
                "PERSONAL_TRADING_SETTINGS_UPDATED",
                "account-dashboard",
                request.client.host if request.client else "unknown",
                {
                    "account_id_masked": account["account_id_masked"],
                    **settings,
                },
            )
        except Exception:
            base_api.LOGGER.exception(
                "CUSTOM_MARTINGALE_SETTINGS_AUDIT_FAILED account=%s",
                account.get("account_id_masked", "account"),
            )
        return {"success": True, "settings": settings}

    @base_api.app.get("/custom-martingale.js", include_in_schema=False)
    def custom_martingale_javascript() -> FileResponse:
        return FileResponse(
            base_api.ROOT / "dashboard" / "custom-martingale.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    base_api.app.state.custom_martingale_api_installed = True
    _API_INSTALLED = True
