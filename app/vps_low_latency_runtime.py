from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import websockets

from app import custom_strategy_connection_stampede_guard as stampede
from app import custom_strategy_direct_runtime as direct_runtime
from app import custom_strategy_instant_start as instant
from app import private_websocket_rate_limit as private_ws
from app.account_mode_execution_lock import (
    account_allows_new_execution,
    account_lifecycle_from_row,
)
from app.account_scoped_websocket_runtime import _promote_embedded_oauth_payload
from app.rf_dir5_bot import RFDir5TradingBot
from app.token_store import decrypt_auth_payload
from enhanced_bot import (
    ClientSession,
    mask_account_id,
    normalize_account_type,
    runtime_account_key,
    sanitize_account_ids,
)


LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_ORIGINAL_ADMIT: Any = None

# Ordinary network/handshake faults must not make one account sleep for minutes.
# Provider rate limits are intentionally NOT capped by this value; the existing
# 60-300 second rate-limit circuit remains authoritative for actual 429/1015.
_TRANSIENT_BACKOFF_MAX_SECONDS = 12.0
_MARKET_SELECTION_RECHECK_SECONDS = 2.0
_CONTRACT_SNAPSHOT_OTP_TIMEOUT_SECONDS = 8.0
_CONTRACT_SNAPSHOT_OPEN_TIMEOUT_SECONDS = 12.0
_CONTRACT_SNAPSHOT_RESPONSE_TIMEOUT_SECONDS = 5.0


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value)


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _low_latency_fast_runtime_accounts(
    bot: RFDir5TradingBot,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Build all runnable accounts locally without redundant status writes.

    The b315 eager path is retained: credentials are decrypted from PostgreSQL and
    no Deriv account-list request is performed. The previous implementation also
    rewrote CONNECTING/RECONNECTING rows during every validation sweep; with a
    large account set that created seconds of avoidable row-lock/commit work.
    """

    tokens: list[str] = []
    profiles: dict[str, dict[str, Any]] = {}
    seen_accounts: set[str] = set()

    for row in bot.repository.list_managed_accounts():
        lifecycle = account_lifecycle_from_row(row)
        if not account_allows_new_execution(row) and lifecycle != "settlement":
            continue

        managed_id = int(_row_value(row, "id"))
        try:
            payload = decrypt_auth_payload(
                str(_row_value(row, "token_secret", "") or ""),
                bot.encryption_key,
            )
            payload = _promote_embedded_oauth_payload(payload)
        except Exception:
            bot._set_account_execution_status(
                managed_id,
                "credential_error",
                "Stored Deriv credential could not be read for this account.",
            )
            continue

        account_id = str(payload.get("account_id") or "").strip()
        account_type = normalize_account_type(
            payload.get("account_type") or payload.get("environment"),
            bot.environment,
        )
        credential = instant._credential_from_saved_payload(payload)
        if not account_id or not credential:
            bot._set_account_execution_status(
                managed_id,
                "token_required",
                "Authenticated trade permission is required before execution can connect.",
            )
            continue
        if account_id in seen_accounts:
            bot._set_account_execution_status(
                managed_id,
                "duplicate",
                "This Deriv account is already represented by another active row.",
            )
            continue

        seen_accounts.add(account_id)
        runtime_key = runtime_account_key(credential, account_id)
        profiles[runtime_key] = {
            "id": str(managed_id),
            "name": str(_row_value(row, "label", "") or f"Account {managed_id}"),
            "enabled": True,
            "account_id": account_id,
            "account_type": account_type,
            "auth_type": str(payload.get("auth_type") or "oauth").strip().lower(),
            "source": "custom_strategy_instant_start",
            "managed_account_id": managed_id,
            "stake_amount": float(_row_value(row, "stake_amount", 0.50) or 0.50),
            "take_profit": float(_row_value(row, "take_profit", 0.0) or 0.0),
            "stop_loss": float(_row_value(row, "stop_loss", 0.0) or 0.0),
            "martingale_enabled": bool(_row_value(row, "martingale_enabled", True)),
            "settlement_only": lifecycle == "settlement",
            "api_token": credential,
        }
        tokens.append(runtime_key)

        # Only a genuinely fresh Start needs the STARTING -> CONNECTING write.
        # CONNECTING/RECONNECTING rows already describe the correct state and are
        # left untouched, avoiding dozens of serial PostgreSQL commits at boot.
        current_status = str(
            _row_value(row, "execution_status", "") or ""
        ).strip().lower()
        if lifecycle != "settlement" and current_status in {"starting", "validating"}:
            bot._set_account_execution_status(
                managed_id,
                "connecting",
                "Market watcher is active; authenticated execution connection is starting",
            )

    return tokens, profiles


def _low_latency_select_saved_strategy_markets(
    bot: RFDir5TradingBot,
    profiles: dict[str, dict[str, Any]],
) -> None:
    """Select the market set once, not once per account admission."""

    managed_ids: set[int] = set()
    for profile in profiles.values():
        try:
            managed_ids.add(int(profile.get("managed_account_id")))
        except (TypeError, ValueError):
            continue
    if not managed_ids:
        return

    now = time.monotonic()
    last_scan = float(getattr(bot, "_vps_market_selection_last_scan", 0.0) or 0.0)
    last_ids = getattr(bot, "_vps_market_selection_ids", None)
    if (
        last_ids == frozenset(managed_ids)
        and list(getattr(bot, "symbols", []) or [])
        and now - last_scan < _MARKET_SELECTION_RECHECK_SECONDS
    ):
        return

    configs: dict[int, dict[str, Any]] = {}
    active_runtime = getattr(bot, "_custom_direct_accounts", {})
    if isinstance(active_runtime, dict) and managed_ids.issubset(set(active_runtime)):
        configs = {
            int(managed_id): dict(getattr(active_runtime[managed_id], "config", {}) or {})
            for managed_id in managed_ids
        }
    else:
        try:
            configs = direct_runtime._load_configs_for_ids(bot, managed_ids)
        except Exception as exc:
            bot.logger.warning(
                "CUSTOM_INSTANT_MARKET_SELECTION_DEFERRED error_type=%s",
                type(exc).__name__,
            )
            return

    requested = direct_runtime._required_symbols(list(configs.values()))
    bot._vps_market_selection_last_scan = now
    bot._vps_market_selection_ids = frozenset(managed_ids)
    if not requested:
        return

    signature = tuple(requested)
    previous = tuple(getattr(bot, "_vps_market_selection_signature", ()) or ())
    bot.symbols = list(requested)
    bot.symbol = str(requested[0])
    bot._vps_market_selection_signature = signature
    if signature != previous:
        bot.logger.info(
            "CUSTOM_INSTANT_MARKETS_READY count=%s symbols=%s "
            "private_session_required_for_buy=true deduplicated=true",
            len(requested),
            ",".join(requested),
        )


def _low_latency_admit_one_runtime_account(
    bot: RFDir5TradingBot,
    managed_id: int,
) -> str:
    """Reuse an already-admitted runtime instead of rebuilding it on every sweep."""

    existing = stampede._runtime_token_for_account(bot, int(managed_id))
    if existing:
        row = bot.repository.managed_account(int(managed_id))
        if row is None:
            return ""
        lifecycle = account_lifecycle_from_row(row)
        if not account_allows_new_execution(row) and lifecycle != "settlement":
            return ""
        status = str(_row_value(row, "execution_status", "") or "").strip().lower()
        if lifecycle != "settlement" and status in {"starting", "validating"}:
            bot._set_account_execution_status(
                int(managed_id),
                "connecting",
                "Account already admitted; private execution connection is starting",
            )
        return str(existing)

    original = _ORIGINAL_ADMIT
    if original is None:
        return ""
    return str(original(bot, int(managed_id)) or "")


async def _low_latency_open_websocket(
    gate: private_ws._PrivateConnectionGate,
    url: str,
):
    """Open Deriv's returned WSS URL using the VPS's fastest IP family."""

    async with gate._handshake_slots:
        kwargs = {
            "open_timeout": 20,
            "close_timeout": 5,
            "ping_interval": 20,
            "ping_timeout": 20,
        }
        try:
            return await websockets.connect(
                url,
                happy_eyeballs_delay=0.25,
                interleave=1,
                **kwargs,
            )
        except TypeError as exc:
            # Compatibility fallback for an older websockets/asyncio build. The
            # returned Deriv URL is unchanged; only address-family racing is lost.
            text = str(exc).lower()
            if "happy_eyeballs" not in text and "interleave" not in text:
                raise
            return await websockets.connect(url, **kwargs)


def _low_latency_normal_backoff(
    session: ClientSession,
    config: private_ws._PrivateConnectionConfig,
    attempt: int,
) -> float:
    cap = _env_float(
        "VPS_PRIVATE_WS_TRANSIENT_BACKOFF_MAX_SECONDS",
        _TRANSIENT_BACKOFF_MAX_SECONDS,
        minimum=2.0,
    )
    base = max(0.5, float(session.bot.reconnect_delay_seconds))
    delay = base * (1.5 ** min(max(0, int(attempt)), 6))
    return min(cap, delay + private_ws._jitter(config))


async def _low_latency_contract_snapshot_once(
    self: ClientSession,
    contract_id: int,
) -> dict[str, Any]:
    """Bound one-off settlement reconciliation so an open row cannot linger minutes."""

    try:
        url = await asyncio.wait_for(
            self.get_otp_url(),
            timeout=_CONTRACT_SNAPSHOT_OTP_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return {
            "error": {
                "message": "Authenticated contract reconciliation OTP timed out",
                "code": "OTP_TIMEOUT",
            }
        }
    if not url:
        return {
            "error": {
                "message": "Authenticated contract status connection unavailable",
                "code": "OTP_UNAVAILABLE",
            }
        }

    req_id = 920000 + int(contract_id) % 100000
    kwargs = {
        "open_timeout": _CONTRACT_SNAPSHOT_OPEN_TIMEOUT_SECONDS,
        "close_timeout": 3,
        "ping_interval": None,
    }
    try:
        try:
            connection = websockets.connect(
                url,
                happy_eyeballs_delay=0.25,
                interleave=1,
                **kwargs,
            )
        except TypeError:
            connection = websockets.connect(url, **kwargs)
        async with connection as ws:
            await ws.send(
                json.dumps(
                    {
                        "proposal_open_contract": 1,
                        "contract_id": int(contract_id),
                        "req_id": req_id,
                    }
                )
            )
            deadline = time.monotonic() + _CONTRACT_SNAPSHOT_RESPONSE_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                response = json.loads(raw)
                if response.get("req_id") == req_id:
                    return response
        return {
            "error": {
                "message": "Authenticated contract status response was not received",
                "code": "TIMEOUT",
            }
        }
    except asyncio.TimeoutError:
        return {
            "error": {
                "message": "Authenticated contract status request timed out",
                "code": "TIMEOUT",
            }
        }
    except Exception as exc:
        return {
            "error": {
                "message": sanitize_account_ids(str(exc)),
                "code": "CONNECTION_ERROR",
            }
        }


def install_vps_low_latency_runtime() -> None:
    """Install the minimal full-VPS latency corrections after b315 authorities."""

    global _INSTALLED, _ORIGINAL_ADMIT
    if _INSTALLED:
        return
    if str(os.getenv("FRONTEND_HOSTING_MODE", "")).strip().lower() != "vps":
        return

    _ORIGINAL_ADMIT = stampede._admit_one_runtime_account

    # Keep b315's architecture and ownership model. Only remove redundant local
    # work and latency amplification around that path.
    instant._fast_runtime_accounts = _low_latency_fast_runtime_accounts
    instant._select_saved_strategy_markets = _low_latency_select_saved_strategy_markets
    stampede._admit_one_runtime_account = _low_latency_admit_one_runtime_account
    RFDir5TradingBot._admit_custom_runtime_account = _low_latency_admit_one_runtime_account

    # Ordinary transport faults recover quickly. True provider rate limits still
    # use private_ws._rate_backoff and gate.penalize unchanged.
    private_ws._PrivateConnectionGate.open_websocket = _low_latency_open_websocket
    private_ws._normal_backoff = _low_latency_normal_backoff

    # A missing subscription may need one fresh authenticated snapshot. Keep that
    # query bounded so settlement/UI cannot remain stale for minutes.
    ClientSession.request_contract_snapshot_once = _low_latency_contract_snapshot_once

    RFDir5TradingBot._vps_low_latency_runtime_installed = True
    RFDir5TradingBot._vps_low_latency_rate_limit_backoff_preserved = True
    RFDir5TradingBot._vps_low_latency_happy_eyeballs = True
    ClientSession._vps_low_latency_contract_reconcile = True
    _INSTALLED = True

    LOGGER.warning(
        "VPS_LOW_LATENCY_RUNTIME_ACTIVE eager_credentials=true market_selection_deduplicated=true "
        "happy_eyeballs=true transient_backoff_cap_seconds=%.1f "
        "provider_rate_limit_backoff=preserved contract_reconcile=bounded",
        _env_float(
            "VPS_PRIVATE_WS_TRANSIENT_BACKOFF_MAX_SECONDS",
            _TRANSIENT_BACKOFF_MAX_SECONDS,
            minimum=2.0,
        ),
    )
