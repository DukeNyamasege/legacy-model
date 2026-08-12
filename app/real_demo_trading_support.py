from __future__ import annotations

import os
from typing import Any

from enhanced_bot import (
    TradingBot,
    normalize_account_type,
    private_websocket_credential_from_payload,
)

_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "real"}


def _oauth_trade_credential(payload: dict[str, Any]) -> str:
    auth_type = str(payload.get("auth_type") or "").strip().lower()
    if auth_type == "oauth":
        token = str(
            payload.get("access_token") or payload.get("oauth_access_token") or ""
        ).strip()
        raw_scope = payload.get("scope") or payload.get("oauth_scope") or payload.get("scopes")
    else:
        token = str(payload.get("oauth_access_token") or "").strip()
        raw_scope = payload.get("oauth_scope") or payload.get("scope") or payload.get("scopes")
    scopes = {
        item.strip().lower()
        for item in str(raw_scope or "").replace(",", " ").split()
        if item.strip()
    }
    return token if token and "trade" in scopes else ""


def _account_purchase_token_from_payload(payload: dict[str, Any]) -> str:
    """Return this row's own trade-capable credential.

    The current Custom Strategy runtime uses OAuth with trade scope as the primary
    credential for the account-specific OTP/private-WebSocket flow. A legacy PAT
    may remain encrypted as a compatibility fallback, but it must not override a
    healthy OAuth session after the trader logs in through Deriv.
    """

    oauth_credential = _oauth_trade_credential(payload)
    if oauth_credential:
        return oauth_credential
    return private_websocket_credential_from_payload(payload)


def _production_acknowledged(config: Any) -> bool:
    configured = str(
        os.getenv(
            "PRODUCTION_ACKNOWLEDGEMENT",
            getattr(getattr(config, "deriv", None), "production_acknowledgement", ""),
        )
        or ""
    ).strip()
    return configured == "I_ACKNOWLEDGE_REAL_MONEY_TRADING"


def _hard_real_trading_disabled() -> bool:
    """Return true only for an explicit emergency/off switch.

    ALLOW_REAL_TRADING used to be treated as a hard runtime gate.  That made Real
    account execution look permanently disabled when a stale VPS .env value
    remained behind after the product moved to per-account Start/Stop controls.
    The supported model is now: Demo and Real are both valid account modes, and
    each trader chooses execution from their own personal panel.  Use
    DISABLE_REAL_TRADING=true only when the VPS owner intentionally wants an
    emergency global Real kill-switch.
    """

    return _truthy(os.getenv("DISABLE_REAL_TRADING"), default=False)


def _dual_account_environment_for_token(self: TradingBot, token: str) -> str:
    profile = getattr(self, "user_profiles", {}).get(token, {}) or {}
    return normalize_account_type(
        profile.get("account_type") or profile.get("environment"),
        getattr(self, "environment", "demo"),
    )


def _dual_real_trading_allowed(self: TradingBot) -> bool:
    config = getattr(self, "test2_config", None)
    execution = getattr(config, "execution", None)
    deriv = getattr(config, "deriv", None)
    execution_allows_real = bool(getattr(execution, "real_enabled", True))
    config_allows_real = bool(getattr(deriv, "allow_real_trading", True))
    acknowledged = _production_acknowledged(config)
    hard_disabled = _hard_real_trading_disabled()
    allowed = bool(execution_allows_real and config_allows_real and acknowledged and not hard_disabled)
    try:
        self.logger.info(
            "REAL_TRADING_GATE mode=per_account_user_controlled execution_real_enabled=%s "
            "config_allow_real=%s production_ack=%s hard_disabled=%s allowed=%s",
            str(execution_allows_real).lower(),
            str(config_allows_real).lower(),
            str(acknowledged).lower(),
            str(hard_disabled).lower(),
            str(allowed).lower(),
        )
    except Exception:
        pass
    return allowed


def _purchase_token_from_payload(self: TradingBot, payload: dict[str, Any]) -> str:
    del self
    return _account_purchase_token_from_payload(payload)


def install_dual_demo_real_trading_support() -> None:
    """Allow Demo and Real accounts to run side by side on the VPS.

    Runtime mode remains useful for dashboard filtering, but account execution is
    decided from the account's own `account_type`. A real account is blocked only
    by the emergency global DISABLE_REAL_TRADING switch or missing production
    acknowledgement, never because a Demo sibling is running or because a stale
    ALLOW_REAL_TRADING value exists in .env.
    """

    global _INSTALLED
    if _INSTALLED:
        return
    TradingBot._account_environment_for_token = _dual_account_environment_for_token
    TradingBot._real_trading_allowed = _dual_real_trading_allowed
    TradingBot._purchase_token_from_payload = _purchase_token_from_payload
    TradingBot._dual_demo_real_trading_support_installed = True
    TradingBot._account_scoped_purchase_token_installed = True
    _INSTALLED = True
