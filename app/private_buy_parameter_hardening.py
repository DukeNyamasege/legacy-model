from __future__ import annotations

from typing import Any

from enhanced_bot import TradingBot


_INSTALLED = False


def _clean_direct_buy_request(
    self: TradingBot,
    signal: Any,
    stake_amount: float,
) -> dict[str, Any]:
    """Build a direct buy request from the exact proposal-compatible schema.

    The public proposal request had already accepted these contract fields. The
    direct authenticated WebSocket buy must therefore not add non-contract fields
    inside `parameters`, otherwise Deriv can reject the contract with the generic
    `Input validation failed: parameters` response.
    """

    parameters = self._contract_parameters(
        signal,
        stake_amount,
        symbol_key="underlying_symbol",
    )
    if not getattr(self, "_private_buy_clean_parameters_logged", False):
        try:
            self.logger.info(
                "PRIVATE_BUY_PARAMETER_SCHEMA active=clean "
                "proposal_schema=matched app_markup_inside_parameters=false"
            )
        except Exception:
            pass
        self._private_buy_clean_parameters_logged = True
    return {
        "buy": "1",
        "price": round(float(stake_amount), 2),
        "parameters": parameters,
    }


def install_private_buy_parameter_hardening() -> None:
    """Install the clean direct-buy parameter builder before bot startup."""

    global _INSTALLED
    if _INSTALLED:
        return
    TradingBot._direct_buy_request = _clean_direct_buy_request
    TradingBot._private_buy_parameter_hardening_installed = True
    _INSTALLED = True
