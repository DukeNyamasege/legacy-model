from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from typing import Any, Awaitable, Callable

import app.guaranteed_signal_delivery as immediate
import app.private_websocket_rate_limit as private_ws
import app.rotating_execution_cohorts as cohorts
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import (
    ClientSession,
    PublicMarketDataClient,
    mask_account_id,
    sanitize_account_ids,
)


LOGGER = logging.getLogger(__name__)
_INSTALLED = False
PROPOSAL_RELAY_VERSION = "two-socket-proposal-relay-v2"

RELAY_COUNT = max(1, int(os.getenv("DERIV_PROPOSAL_RELAY_COUNT", "2")))
PUBLIC_PROPOSAL_PRIMARY_TIMEOUT_SECONDS = max(
    0.75,
    float(os.getenv("DERIV_PUBLIC_PROPOSAL_PRIMARY_TIMEOUT_SECONDS", "2.5")),
)
RELAY_READY_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("DERIV_PROPOSAL_RELAY_READY_TIMEOUT_SECONDS", "2.5")),
)
RELAY_REQUEST_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("DERIV_PROPOSAL_RELAY_REQUEST_TIMEOUT_SECONDS", "2.5")),
)
RELAY_WARM_RETRIES = max(
    1,
    int(os.getenv("DERIV_PROPOSAL_RELAY_WARM_RETRIES", "5")),
)

_ORIGINAL_STILL_CONFIGURED: Callable[[ClientSession], bool] | None = None
_ORIGINAL_PUBLIC_SEND_REQUEST: Callable[
    [PublicMarketDataClient, dict[str, Any]], Awaitable[dict[str, Any]]
] | None = None
_ORIGINAL_ACTIVATE_CYCLE: Callable[..., Awaitable[None]] | None = None
_ORIGINAL_BOT_INIT: Callable[..., None] | None = None


def _relay_ids(bot: RFDir5TradingBot) -> set[int]:
    value = getattr(bot, "_rotating_proposal_relay_ids", None)
    if not isinstance(value, set):
        value = set()
        bot._rotating_proposal_relay_ids = value
    return value


def _relay_lock(bot: RFDir5TradingBot) -> asyncio.Lock:
    value = getattr(bot, "_proposal_relay_lock", None)
    if not isinstance(value, asyncio.Lock):
        value = asyncio.Lock()
        bot._proposal_relay_lock = value
    return value


def _session_managed_id(session: ClientSession) -> int | None:
    raw = getattr(session, "managed_account_id", None)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _valid_account_rows(
    bot: RFDir5TradingBot,
) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    for token, account_id in list(getattr(bot, "valid_clients", []) or []):
        managed_id = bot._managed_account_id_for_token(token)
        if managed_id is None:
            continue
        managed_id = int(managed_id)
        if managed_id in seen:
            continue
        seen.add(managed_id)
        rows.append((managed_id, str(token), str(account_id)))
    rows.sort(key=lambda item: (item[0], item[2]))
    return rows


def _relay_rows(
    bot: RFDir5TradingBot,
) -> list[tuple[int, str, str]]:
    rows = _valid_account_rows(bot)
    if not rows:
        return []

    valid_ids = {managed_id for managed_id, _token, _account_id in rows}
    selected_ids = {
        managed_id
        for managed_id in _relay_ids(bot)
        if managed_id in valid_ids
    }
    for managed_id, _token, _account_id in rows:
        if len(selected_ids) >= RELAY_COUNT:
            break
        selected_ids.add(managed_id)
    selected_ids = set(sorted(selected_ids)[:RELAY_COUNT])
    bot._rotating_proposal_relay_ids = set(selected_ids)
    return [row for row in rows if row[0] in selected_ids]


async def ensure_proposal_relays(
    bot: RFDir5TradingBot,
) -> list[ClientSession]:
    """Keep a tiny nonfinancial fallback pool ready for proposal economics."""

    async with _relay_lock(bot):
        rows = _relay_rows(bot)
        if not rows:
            return []

        relay_ids = {managed_id for managed_id, _token, _account_id in rows}
        active = {
            int(value)
            for value in set(
                getattr(bot, "_rotating_active_managed_ids", set()) or set()
            )
        }
        bot._rotating_active_managed_ids = active | relay_ids

        sessions: list[ClientSession] = []
        for _managed_id, token, account_id in rows:
            sessions.append(immediate._ensure_session(bot, token, account_id))

        outcomes = await asyncio.gather(
            *(
                private_ws.wait_until_connected(
                    session,
                    timeout=RELAY_READY_TIMEOUT_SECONDS,
                )
                for session in sessions
            ),
            return_exceptions=True,
        )
        connected = [
            session
            for session, outcome in zip(sessions, outcomes, strict=True)
            if outcome is True
            and bool(getattr(session, "is_connected", False))
            and getattr(session, "ws", None) is not None
        ]
        bot.logger.info(
            "PROPOSAL_RELAY_READY configured=%s connected=%s "
            "financial_requests=0 persistent_account_population=false",
            len(sessions),
            len(connected),
        )
        return connected


async def _warm_proposal_relays(bot: RFDir5TradingBot) -> None:
    for attempt in range(1, RELAY_WARM_RETRIES + 1):
        if not bool(getattr(bot, "is_running", True)):
            return
        sessions = await ensure_proposal_relays(bot)
        if sessions:
            return
        bot.logger.info(
            "PROPOSAL_RELAY_WARM_RETRY attempt=%s max_attempts=%s",
            attempt,
            RELAY_WARM_RETRIES,
        )
        await asyncio.sleep(min(10.0, 1.5 * attempt))


def _relay_still_configured(session: ClientSession) -> bool:
    managed_id = _session_managed_id(session)
    if managed_id is not None and managed_id in _relay_ids(session.bot):
        return True
    original = _ORIGINAL_STILL_CONFIGURED
    return bool(original(session)) if original is not None else False


async def _activate_cycle_with_relays(
    bot: RFDir5TradingBot,
    managed_ids: set[int],
    *,
    strategy: str,
) -> None:
    original = _ORIGINAL_ACTIVATE_CYCLE
    if original is None:
        raise RuntimeError("Rotating cohort activation is not installed")
    await original(bot, managed_ids, strategy=strategy)
    relay_ids = set(_relay_ids(bot))
    if relay_ids:
        active = {
            int(value)
            for value in set(
                getattr(bot, "_rotating_active_managed_ids", set()) or set()
            )
        }
        bot._rotating_active_managed_ids = active | relay_ids


def _proposal_error_code(response: dict[str, Any]) -> str:
    error = response.get("error")
    if not isinstance(error, dict):
        return ""
    return str(error.get("code") or "").strip().upper()


def _proposal_error_is_relayable(response: dict[str, Any]) -> bool:
    code = _proposal_error_code(response)
    if code in {
        "PUBLIC_PROPOSAL_TIMEOUT",
        "PUBLIC_PROPOSAL_SEND_FAILED",
        "PROPOSAL_ROUTES_UNAVAILABLE",
        "NOT_CONNECTED",
        "PUBLIC_CONNECTION_LOST",
        "TIMEOUT",
        "ERROR",
    }:
        return True
    error = response.get("error")
    message = (
        str(error.get("message") or "").lower()
        if isinstance(error, dict)
        else ""
    )
    return any(
        marker in message
        for marker in (
            "timed out",
            "not connected",
            "connection closed",
            "connection lost",
            "closed without",
        )
    )


async def _primary_proposal_request(
    self: PublicMarketDataClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Bound only proposal latency; market-data/control requests remain unchanged."""

    original = _ORIGINAL_PUBLIC_SEND_REQUEST
    if original is None:
        raise RuntimeError("Public proposal transport is not installed")
    try:
        return await asyncio.wait_for(
            original(self, dict(payload)),
            timeout=PUBLIC_PROPOSAL_PRIMARY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return {
            "error": {
                "code": "PUBLIC_PROPOSAL_TIMEOUT",
                "message": (
                    "The public proposal path exceeded the fast execution deadline"
                ),
            }
        }
    except Exception as exc:
        return {
            "error": {
                "code": "PUBLIC_PROPOSAL_SEND_FAILED",
                "message": sanitize_account_ids(str(exc)),
            }
        }


async def _proposal_with_on_demand_relay(
    self: PublicMarketDataClient,
    request: dict[str, Any],
) -> dict[str, Any]:
    original = _ORIGINAL_PUBLIC_SEND_REQUEST
    if original is None:
        raise RuntimeError("Public proposal transport is not installed")

    payload = dict(request)
    if not payload.get("proposal"):
        return await original(self, dict(payload))

    response = await _primary_proposal_request(self, payload)
    if "error" not in response:
        return response
    if not _proposal_error_is_relayable(response):
        return response

    public_error = _proposal_error_code(response) or "unknown"
    started = asyncio.get_running_loop().time()
    sessions = await ensure_proposal_relays(self.bot)
    if not sessions:
        self.bot.logger.warning(
            "PROPOSAL_RELAY_UNAVAILABLE public_error=%s "
            "primary_timeout_seconds=%.2f financial_requests=0",
            public_error,
            PUBLIC_PROPOSAL_PRIMARY_TIMEOUT_SECONDS,
        )
        return response

    for session in sessions[:RELAY_COUNT]:
        relay_payload = dict(payload)
        relay_payload.pop("req_id", None)
        try:
            relay_response = await asyncio.wait_for(
                session.send_request(relay_payload),
                timeout=RELAY_REQUEST_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            continue
        except Exception as exc:
            self.bot.logger.warning(
                "PROPOSAL_RELAY_REQUEST_FAILED account=%s error_type=%s "
                "error=%s financial_requests=0",
                mask_account_id(getattr(session, "account_id", "")),
                type(exc).__name__,
                sanitize_account_ids(str(exc)),
            )
            continue
        if "error" in relay_response:
            continue
        self.bot.logger.warning(
            "PROPOSAL_RELAY_RECOVERED account=%s symbol=%s "
            "public_error=%s elapsed_ms=%.1f financial_requests=0 buy_sent=false",
            mask_account_id(getattr(session, "account_id", "")),
            payload.get("underlying_symbol") or payload.get("symbol") or "unknown",
            public_error,
            (asyncio.get_running_loop().time() - started) * 1000.0,
        )
        return relay_response

    self.bot.logger.warning(
        "PROPOSAL_RELAY_EXHAUSTED relays=%s public_error=%s "
        "elapsed_ms=%.1f financial_requests=0 buy_sent=false",
        len(sessions[:RELAY_COUNT]),
        public_error,
        (asyncio.get_running_loop().time() - started) * 1000.0,
    )
    return response


def _relay_aware_bot_init(
    self: RFDir5TradingBot,
    *args: Any,
    **kwargs: Any,
) -> None:
    original = _ORIGINAL_BOT_INIT
    if original is None:
        raise RuntimeError("RFDir5TradingBot initializer is not installed")
    original(self, *args, **kwargs)
    self._rotating_proposal_relay_ids: set[int] = set()
    self._proposal_relay_warm_task = asyncio.create_task(
        _warm_proposal_relays(self),
        name="proposal_relay_warmup",
    )


def install_proposal_relay_runtime() -> None:
    """Install two stable proposal-only fallback sockets."""

    global _INSTALLED
    global _ORIGINAL_STILL_CONFIGURED
    global _ORIGINAL_PUBLIC_SEND_REQUEST
    global _ORIGINAL_ACTIVATE_CYCLE
    global _ORIGINAL_BOT_INIT
    if _INSTALLED:
        return

    _ORIGINAL_STILL_CONFIGURED = private_ws._still_configured
    _ORIGINAL_PUBLIC_SEND_REQUEST = PublicMarketDataClient.send_request
    _ORIGINAL_ACTIVATE_CYCLE = cohorts.activate_cycle_accounts
    _ORIGINAL_BOT_INIT = RFDir5TradingBot.__init__

    private_ws._still_configured = _relay_still_configured
    PublicMarketDataClient.send_request = _proposal_with_on_demand_relay
    cohorts.activate_cycle_accounts = _activate_cycle_with_relays
    RFDir5TradingBot.__init__ = _relay_aware_bot_init

    RFDir5TradingBot._proposal_relay_runtime_installed = True
    _INSTALLED = True
    LOGGER.warning(
        "PROPOSAL_RELAY_RUNTIME_INSTALLED version=%s relays=%s "
        "proposal_only=true direct_buy_parameters=true "
        "primary_timeout_seconds=%.2f relay_ready_timeout_seconds=%.2f "
        "relay_request_timeout_seconds=%.2f "
        "send_failures_relayable=true financial_requests=0 "
        "persistent_private_sockets_for_all_accounts=false",
        PROPOSAL_RELAY_VERSION,
        RELAY_COUNT,
        PUBLIC_PROPOSAL_PRIMARY_TIMEOUT_SECONDS,
        RELAY_READY_TIMEOUT_SECONDS,
        RELAY_REQUEST_TIMEOUT_SECONDS,
    )
