from __future__ import annotations

from typing import Any

from enhanced_bot import ClientSession, TradingBot, mask_account_id


_INSTALLED = False
_ORIGINAL_SEND_REQUEST = None


def _clean_contract_parameters(
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Return the provider contract schema without application-level fields."""

    clean = dict(parameters)
    clean.pop("app_markup_percentage", None)
    if "amount" in clean:
        clean["amount"] = round(float(clean["amount"]), 2)
    if "duration" in clean:
        clean["duration"] = int(clean["duration"])
    if "barrier" in clean:
        clean["barrier"] = str(clean["barrier"])
    return clean


def _clean_direct_buy_request(
    self: TradingBot,
    signal: Any,
    stake_amount: float,
) -> dict[str, Any]:
    """Build a direct buy request from the exact proposal-compatible schema.

    The public proposal request accepted these contract fields. The authenticated
    buy request must not place `app_markup_percentage` or any other non-contract
    field inside `parameters`, otherwise Deriv rejects the request with the generic
    `Input validation failed: parameters` response.
    """

    parameters = _clean_contract_parameters(
        self._contract_parameters(
            signal,
            stake_amount,
            symbol_key="underlying_symbol",
        )
    )
    if not getattr(self, "_private_buy_clean_parameters_logged", False):
        try:
            self.logger.info(
                "PRIVATE_BUY_PARAMETER_SCHEMA active=clean source=direct_builder "
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


async def _sanitized_send_request(
    self: ClientSession,
    req: dict[str, Any],
) -> dict[str, Any]:
    """Last safety boundary before the private WebSocket send.

    This catches any older direct-buy builder that still injected markup into the
    contract parameters. It makes the fix independent of strategy monkey-patch
    order and guarantees the exact JSON sent to Deriv is clean.
    """

    original = _ORIGINAL_SEND_REQUEST
    if original is None:
        raise RuntimeError("Original ClientSession.send_request is not installed")

    request = dict(req)
    parameters = request.get("parameters")
    if request.get("buy") and isinstance(parameters, dict):
        removed_markup = "app_markup_percentage" in parameters
        request["parameters"] = _clean_contract_parameters(parameters)
        bot = getattr(self, "bot", None)
        if bot is not None:
            try:
                bot.logger.info(
                    "PRIVATE_BUY_REQUEST_SANITIZED account=%s symbol=%s "
                    "contract_type=%s barrier=%s duration=%s duration_unit=%s "
                    "basis=%s amount=%.2f removed_app_markup=%s",
                    mask_account_id(getattr(self, "account_id", "")),
                    request["parameters"].get("underlying_symbol", ""),
                    request["parameters"].get("contract_type", ""),
                    request["parameters"].get("barrier", ""),
                    request["parameters"].get("duration", ""),
                    request["parameters"].get("duration_unit", ""),
                    request["parameters"].get("basis", ""),
                    float(request["parameters"].get("amount") or 0.0),
                    str(removed_markup).lower(),
                )
            except Exception:
                pass
    return await original(self, request)


def install_private_buy_parameter_hardening() -> None:
    """Install direct-buy parameter hardening before bot startup."""

    global _INSTALLED, _ORIGINAL_SEND_REQUEST
    if _INSTALLED:
        return
    TradingBot._direct_buy_request = _clean_direct_buy_request
    _ORIGINAL_SEND_REQUEST = ClientSession.send_request
    ClientSession.send_request = _sanitized_send_request
    TradingBot._private_buy_parameter_hardening_installed = True
    ClientSession._private_buy_parameter_hardening_installed = True
    _INSTALLED = True
