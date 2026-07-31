from __future__ import annotations

import os
from typing import Any

from enhanced_bot import TradingBot, normalize_account_type

_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "real"}


def _production_acknowledged(config: Any) -> bool:
    configured = str(
        os.getenv(
            "PRODUCTION_ACKNOWLEDGEMENT",
            getattr(getattr(config, "deriv", None), "production_acknowledgement", ""),
        )
        or ""
    ).strip()
    return configured == "I_ACKNOWLEDGE_REAL_MONEY_TRADING"


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
    execution_allows_real = bool(getattr(execution, "real_enabled", False))
    config_allows_real = bool(getattr(deriv, "allow_real_trading", False))
    env_allows_real = _truthy(
        os.getenv("ALLOW_REAL_TRADING"),
        default=config_allows_real,
    )
    acknowledged = _production_acknowledged(config)
    allowed = bool(execution_allows_real and config_allows_real and env_allows_real and acknowledged)
    try:
        self.logger.info(
            "REAL_TRADING_GATE execution_real_enabled=%s config_allow_real=%s "
            "env_allow_real=%s production_ack=%s allowed=%s",
            str(execution_allows_real).lower(),
            str(config_allows_real).lower(),
            str(env_allows_real).lower(),
            str(acknowledged).lower(),
            str(allowed).lower(),
        )
    except Exception:
        pass
    return allowed


def install_dual_demo_real_trading_support() -> None:
    """Allow Demo and Real accounts to run side by side on the VPS.

    Runtime mode remains useful for dashboard filtering, but account execution is
    decided from the account's own `account_type`. A real account is blocked only
    when the explicit real-money switches are missing.
    """

    global _INSTALLED
    if _INSTALLED:
        return
    TradingBot._account_environment_for_token = _dual_account_environment_for_token
    TradingBot._real_trading_allowed = _dual_real_trading_allowed
    TradingBot._dual_demo_real_trading_support_installed = True
    _INSTALLED = True
