from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Any

import app.guaranteed_signal_delivery as immediate
import app.standardized_execution_runtime as standardized
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy.decision_engine import parse_proposal_economics
from enhanced_bot import sanitize_account_ids


_INSTALLED = False
VERSION = "aidr-qualified-proposal-recovery-v2"
PROPOSAL_TIMEOUT_SECONDS = 6.0
BARRIER_CONTRACTS = {
    "DIGITOVER",
    "DIGITUNDER",
    "DIGITMATCH",
    "DIGITDIFF",
}
LOGGER = logging.getLogger(__name__)


def _proposal_request(bot: RFDir5TradingBot, signal: Any) -> dict[str, Any]:
    """Build the exact one-tick public proposal without wrapper-only attributes."""

    contract_type = str(getattr(signal, "contract_type", "") or "").upper()
    request: dict[str, Any] = {
        "proposal": 1,
        "amount": 0.50,
        "basis": "stake",
        "contract_type": contract_type,
        "currency": str(getattr(bot, "currency", "USD") or "USD"),
        "duration": 1,
        "duration_unit": "t",
        "underlying_symbol": str(getattr(signal, "symbol", "") or ""),
    }
    barrier = str(getattr(signal, "barrier", "") or "").strip()
    if contract_type in BARRIER_CONTRACTS:
        if not barrier:
            raise ValueError(f"{contract_type} requires a barrier")
        request["barrier"] = barrier
    return request


async def _raw_public_request(
    bot: RFDir5TradingBot,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Use the existing public socket and its req_id response dispatcher.

    This bypasses the broken post-qualification proposal wrapper only. It opens no
    additional WebSocket and never sends a financial buy request.
    """

    client = bot.public_client
    websocket = getattr(client, "ws", None)
    if websocket is None or not bool(getattr(client, "is_connected", False)):
        return {
            "error": {
                "code": "PUBLIC_PROPOSAL_NOT_CONNECTED",
                "message": "The live public market socket was not connected",
            }
        }

    request_id = int(getattr(client, "next_req_id", 1) or 1)
    client.next_req_id = request_id + 1
    payload = dict(request)
    payload["req_id"] = request_id
    future = asyncio.get_running_loop().create_future()
    client.pending_requests[request_id] = future
    try:
        await websocket.send(json.dumps(payload))
        response = await asyncio.wait_for(
            future,
            timeout=PROPOSAL_TIMEOUT_SECONDS,
        )
        return dict(response) if isinstance(response, dict) else {
            "error": {
                "code": "INVALID_PROPOSAL_RESPONSE",
                "message": "Deriv returned a non-object proposal response",
            }
        }
    except asyncio.TimeoutError:
        return {
            "error": {
                "code": "PUBLIC_PROPOSAL_TIMEOUT",
                "message": "The qualified proposal timed out before purchase",
            }
        }
    except Exception as exc:
        return {
            "error": {
                "code": "PUBLIC_PROPOSAL_SEND_FAILED",
                "message": sanitize_account_ids(str(exc)),
            }
        }
    finally:
        client.pending_requests.pop(request_id, None)
        if not future.done():
            future.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.sleep(0)


async def _qualified_provider_proposal(
    bot: RFDir5TradingBot,
    signal: Any,
) -> tuple[Any, Any] | None:
    """Create a fresh proposal only after AIDR selected an executable role."""

    signal_id = str(getattr(signal, "signal_id", "") or "")
    symbol = str(getattr(signal, "symbol", "") or "")
    barrier = str(getattr(signal, "barrier", "") or "")
    requested_monotonic = time.monotonic()
    try:
        request = _proposal_request(bot, signal)
        response = await _raw_public_request(bot, request)
        received_monotonic = time.monotonic()
        error = response.get("error") if isinstance(response, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code") or "PROPOSAL_FAILED").upper()
            message = sanitize_account_ids(
                str(error.get("message") or "Deriv did not return a proposal")
            )
            bot.repository.mark_signal(
                signal_id,
                status=f"SKIP_PROVIDER_{code}"[:64],
            )
            bot.logger.warning(
                "AIDR_QUALIFIED_PROPOSAL_FAILED signal_id=%s symbol=%s "
                "barrier=%s code=%s reason=%s purchase_sent=false",
                signal_id,
                symbol,
                barrier,
                code,
                message,
            )
            return None

        economics = parse_proposal_economics(
            response,
            stake=0.50,
            predicted_probability=float(
                getattr(signal, "weighted_probability", 0.0) or 0.0
            ),
            requested_monotonic=requested_monotonic,
            received_monotonic=received_monotonic,
            app_markup_percentage=float(
                getattr(bot, "app_markup_percentage", 0.0) or 0.0
            ),
            commission_in_ask=True,
        )
        break_even = float(economics.break_even_probability)
        edge = float(getattr(signal, "weighted_probability", 0.0) or 0.0) - break_even
        standardized._mark_proposal_fields(signal, economics, edge)
        bot.repository.record_proposal(signal, economics)
        bot.logger.warning(
            "AIDR_QUALIFIED_PROPOSAL_READY signal_id=%s symbol=%s barrier=%s "
            "ask=%.2f payout=%.2f break_even=%.5f edge=%.5f "
            "raw_public_socket=true purchase_next=true",
            signal_id,
            symbol,
            barrier,
            float(economics.stake),
            float(economics.payout),
            break_even,
            edge,
        )
        return signal, economics
    except Exception as exc:
        bot.repository.mark_signal(
            signal_id,
            status="SKIP_PROVIDER_PROPOSAL_EXCEPTION",
        )
        bot.logger.exception(
            "AIDR_QUALIFIED_PROPOSAL_EXCEPTION signal_id=%s symbol=%s "
            "barrier=%s error_type=%s error=%s purchase_sent=false",
            signal_id,
            symbol,
            barrier,
            type(exc).__name__,
            sanitize_account_ids(str(exc)),
        )
        return None


def install_proposal_execution_recovery() -> None:
    """Make the direct public proposal path final for qualified AIDR roles."""

    global _INSTALLED
    if _INSTALLED:
        return
    # Only the fresh role-subcycle path is replaced. The ordinary AIDR scanner and
    # its live-edge checks keep their existing proposal implementation and cadence.
    immediate._provider_proposal = _qualified_provider_proposal
    RFDir5TradingBot._proposal_execution_recovery_installed = True
    RFDir5TradingBot._proposal_execution_recovery_version = VERSION
    _INSTALLED = True
    LOGGER.warning(
        "PROPOSAL_EXECUTION_RECOVERY_INSTALLED version=%s "
        "qualified_roles_only=true scanner_unchanged=true raw_public_socket=true "
        "extra_socket=false financial_buy=false exact_exception_logging=true",
        VERSION,
    )
