from __future__ import annotations

import math
from typing import Any

from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import ClientSession, TradingBot, mask_account_id


_INSTALLED = False
_ORIGINAL_SEND_REQUEST = None
_ORIGINAL_PROPOSAL_REQUEST = None

# Every digit-family contract used by System Strategy, manual Over/Under and
# Even/Odd is a one-tick contract. Rise/Fall remains strategy-controlled.
_ONE_TICK_DIGIT_CONTRACTS = frozenset(
    {
        "DIGITOVER",
        "DIGITUNDER",
        "DIGITEVEN",
        "DIGITODD",
        "DIGITMATCH",
        "DIGITDIFF",
    }
)
_BARRIER_DIGIT_CONTRACTS = frozenset(
    {"DIGITOVER", "DIGITUNDER", "DIGITMATCH", "DIGITDIFF"}
)


def _clean_contract_parameters(
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Return a validated provider contract schema.

    This function is the final contract boundary shared by proposal-compatible
    direct buys and the authenticated private WebSocket send. Digit contracts are
    always normalized to one tick so an older five-tick strategy value cannot
    leak into System Strategy or a manual digit selection.
    """

    clean = dict(parameters)
    clean.pop("app_markup_percentage", None)

    contract_type = str(clean.get("contract_type") or "").strip().upper()
    if contract_type:
        clean["contract_type"] = contract_type

    if "amount" in clean:
        amount = float(clean["amount"])
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("Contract amount must be a finite positive number")
        clean["amount"] = round(amount, 2)

    if contract_type in _ONE_TICK_DIGIT_CONTRACTS:
        clean["duration"] = 1
        clean["duration_unit"] = "t"
    else:
        if "duration" in clean:
            duration = int(clean["duration"])
            if duration <= 0:
                raise ValueError("Contract duration must be positive")
            clean["duration"] = duration
        if "duration_unit" in clean:
            clean["duration_unit"] = str(clean["duration_unit"]).strip().lower()

    barrier = clean.get("barrier")
    if barrier not in {None, ""}:
        barrier_text = str(barrier).strip()
        if contract_type in _BARRIER_DIGIT_CONTRACTS:
            if not barrier_text.isdigit() or not 0 <= int(barrier_text) <= 9:
                raise ValueError(
                    f"{contract_type} requires a prediction digit from 0 to 9"
                )
        clean["barrier"] = barrier_text
    elif contract_type in _BARRIER_DIGIT_CONTRACTS:
        raise ValueError(f"{contract_type} requires a prediction barrier")

    return clean


def _one_tick_proposal_request(
    self: RFDir5TradingBot,
    signal: Any,
    stake_amount: float,
    duration_ticks: int,
) -> dict[str, Any]:
    """Keep public proposal economics aligned with the private one-tick buy."""

    original = _ORIGINAL_PROPOSAL_REQUEST
    if original is None:
        raise RuntimeError("Original RFDir5TradingBot._proposal_request_for is not installed")

    contract_type = str(getattr(signal, "contract_type", "") or "").upper()
    requested_duration = (
        1 if contract_type in _ONE_TICK_DIGIT_CONTRACTS else int(duration_ticks)
    )
    if contract_type in _ONE_TICK_DIGIT_CONTRACTS:
        try:
            signal.duration_ticks = 1
        except Exception:
            pass

    request = dict(original(self, signal, stake_amount, requested_duration))
    if contract_type in _ONE_TICK_DIGIT_CONTRACTS:
        request["duration"] = 1
        request["duration_unit"] = "t"
    return request


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
                "proposal_schema=matched app_markup_inside_parameters=false "
                "digit_duration=1_tick"
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

    This catches any older direct-buy builder that still injected markup or an
    incorrect digit duration into the contract parameters. The request actually
    sent to Deriv is therefore independent of strategy monkey-patch order.
    """

    original = _ORIGINAL_SEND_REQUEST
    if original is None:
        raise RuntimeError("Original ClientSession.send_request is not installed")

    request = dict(req)
    parameters = request.get("parameters")
    if request.get("buy") and isinstance(parameters, dict):
        removed_markup = "app_markup_percentage" in parameters
        original_duration = parameters.get("duration")
        original_duration_unit = parameters.get("duration_unit")
        request["parameters"] = _clean_contract_parameters(parameters)
        cleaned = request["parameters"]
        one_tick_enforced = (
            str(cleaned.get("contract_type") or "").upper()
            in _ONE_TICK_DIGIT_CONTRACTS
            and (
                str(original_duration) != "1"
                or str(original_duration_unit or "").lower() != "t"
            )
        )
        bot = getattr(self, "bot", None)
        if bot is not None:
            try:
                bot.logger.info(
                    "PRIVATE_BUY_REQUEST_SANITIZED account=%s symbol=%s "
                    "contract_type=%s barrier=%s duration=%s duration_unit=%s "
                    "basis=%s amount=%.2f removed_app_markup=%s one_tick_enforced=%s",
                    mask_account_id(getattr(self, "account_id", "")),
                    cleaned.get("underlying_symbol", ""),
                    cleaned.get("contract_type", ""),
                    cleaned.get("barrier", ""),
                    cleaned.get("duration", ""),
                    cleaned.get("duration_unit", ""),
                    cleaned.get("basis", ""),
                    float(cleaned.get("amount") or 0.0),
                    str(removed_markup).lower(),
                    str(one_tick_enforced).lower(),
                )
            except Exception:
                pass
    return await original(self, request)


def install_private_buy_parameter_hardening() -> None:
    """Install proposal and direct-buy hardening before bot startup.

    `_proposal_request_for` belongs to RFDir5TradingBot, not its TradingBot base.
    Targeting the base class caused the candidate worker to crash before its first
    heartbeat. The installer now patches the exact live proposal builder and
    validates its presence before changing any method.
    """

    global _INSTALLED, _ORIGINAL_SEND_REQUEST, _ORIGINAL_PROPOSAL_REQUEST
    if _INSTALLED:
        return

    proposal_builder = getattr(RFDir5TradingBot, "_proposal_request_for", None)
    if not callable(proposal_builder):
        raise RuntimeError(
            "RFDir5TradingBot._proposal_request_for is required before private buy hardening"
        )
    send_request = getattr(ClientSession, "send_request", None)
    if not callable(send_request):
        raise RuntimeError("ClientSession.send_request is required before buy hardening")

    _ORIGINAL_PROPOSAL_REQUEST = proposal_builder
    RFDir5TradingBot._proposal_request_for = _one_tick_proposal_request
    RFDir5TradingBot._direct_buy_request = _clean_direct_buy_request
    _ORIGINAL_SEND_REQUEST = send_request
    ClientSession.send_request = _sanitized_send_request
    RFDir5TradingBot._private_buy_parameter_hardening_installed = True
    ClientSession._private_buy_parameter_hardening_installed = True
    _INSTALLED = True
