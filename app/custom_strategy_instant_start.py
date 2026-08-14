from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import enhanced_bot as base_runtime
from app.account_mode_execution_lock import (
    STARTING_LIKE_STATUSES,
    account_allows_new_execution,
    account_lifecycle_from_row,
)
from app.account_scoped_websocket_runtime import _promote_embedded_oauth_payload
from app.rf_dir5_bot import RFDir5TradingBot
from app.token_store import decrypt_auth_payload
from enhanced_bot import (
    PublicMarketDataClient,
    mask_account_id,
    normalize_account_type,
    runtime_account_key,
)


_INSTALLED = False
_ORIGINAL_VALIDATE: Any = None
_ORIGINAL_FETCH_HISTORY: Any = None
_ORIGINAL_REST_REQUEST: Any = None

# Startup must never inherit aiohttp's multi-minute default timeout. These are
# discovery/session-bootstrap requests only; financial BUYs use the authenticated
# private WebSocket and are intentionally not routed through this timeout wrapper.
_STARTUP_REST_TIMEOUT_SECONDS = 5.0
_HISTORY_STARTUP_BUDGET_SECONDS = 4.0


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _oauth_scopes(payload: dict[str, Any]) -> set[str]:
    raw = payload.get("oauth_scope") or payload.get("scope") or payload.get("scopes")
    return {
        item.strip().lower()
        for item in str(raw or "").replace(",", " ").split()
        if item.strip()
    }


def _credential_from_saved_payload(payload: dict[str, Any]) -> str:
    """Return the already-authorized credential without doing provider I/O.

    Account ownership is still proved before any BUY by the account-specific OTP
    WebSocket. This function only removes the redundant blocking account-list REST
    sweep from the Start button path.
    """

    promoted = _promote_embedded_oauth_payload(dict(payload))
    auth_type = str(promoted.get("auth_type") or "pat").strip().lower() or "pat"
    if auth_type == "oauth":
        access = str(
            promoted.get("access_token") or promoted.get("oauth_access_token") or ""
        ).strip()
        return access if access and "trade" in _oauth_scopes(promoted) else ""
    return str(
        promoted.get("pat_token") or promoted.get("access_token") or ""
    ).strip()


def _fast_runtime_accounts(bot: RFDir5TradingBot) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Build the runnable account set from durable authenticated account rows.

    No Deriv REST request is made here. A stale or rejected credential cannot buy:
    AccountExecutionSession.prepare() requires the matching private WebSocket to be
    connected, and that socket can connect only after Deriv accepts the account OTP.
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
        credential = _credential_from_saved_payload(payload)
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
        profile = {
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
        profiles[runtime_key] = profile

        current_status = str(_row_value(row, "execution_status", "") or "").strip().lower()
        if lifecycle != "settlement" and current_status in STARTING_LIKE_STATUSES:
            bot._set_account_execution_status(
                managed_id,
                "connecting",
                "Market watcher is starting now; authenticated execution stream connects in background",
            )

    return tokens, profiles


async def _instant_validate_accounts(self: RFDir5TradingBot) -> None:
    """Admit saved accounts immediately and let private OTP prove execution access."""

    started = time.monotonic()
    self.environment = self.repository.runtime_mode()
    tokens, profiles = _fast_runtime_accounts(self)
    self.tokens = tokens
    self.user_profiles = profiles
    self.valid_clients = [
        (token, str(profiles[token]["account_id"]))
        for token in tokens
        if str(profiles[token].get("account_id") or "").strip()
    ]
    self._sync_clients_with_runtime_accounts()

    if self.valid_clients:
        self._sync_running_status_after_validation()
    else:
        self.logger.info(
            "CUSTOM_INSTANT_START_IDLE accounts=0 provider_account_sweep=false"
        )

    self.logger.info(
        "CUSTOM_INSTANT_ACCOUNT_ADMISSION accounts=%s elapsed_ms=%.1f "
        "provider_account_sweep=false purchase_gate=private_websocket_connected",
        len(self.valid_clients),
        (time.monotonic() - started) * 1000.0,
    )


async def _bounded_startup_rest_request(
    method: str,
    path: str,
    app_id: str,
    base_url: str,
    token: str | None = None,
    json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bound only non-financial account/OTP bootstrap requests."""

    original = _ORIGINAL_REST_REQUEST
    if original is None:
        return {"error": {"message": "REST runtime is unavailable", "code": "RUNTIME_UNAVAILABLE"}}

    startup_request = (
        str(path) == "/trading/v1/options/accounts"
        or str(path).endswith("/otp")
    )
    if not startup_request:
        return await original(
            method,
            path,
            app_id,
            base_url,
            token=token,
            json_data=json_data,
        )

    try:
        return await asyncio.wait_for(
            original(
                method,
                path,
                app_id,
                base_url,
                token=token,
                json_data=json_data,
            ),
            timeout=_STARTUP_REST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return {
            "error": {
                "message": "Deriv execution bootstrap timed out quickly; reconnecting automatically",
                "code": "STARTUP_TIMEOUT",
            }
        }


async def _fast_fetch_tick_history(self: PublicMarketDataClient) -> None:
    """Warm all strategy markets concurrently under one startup time budget.

    The public socket has no live tick subscriptions yet, so it is safe to send all
    history requests first and then consume their responses in any order. This
    changes N markets x 10-second serial waits into one bounded startup window.
    """

    if not self.ws:
        return
    count = int(self.bot._public_history_count())
    if count <= 0:
        return

    pending: dict[int, str] = {}
    for symbol in list(self.bot.symbols):
        req_id = self.next_req_id
        self.next_req_id += 1
        pending[req_id] = str(symbol)
        await self.ws.send(
            json.dumps(
                {
                    "ticks_history": str(symbol),
                    "end": "latest",
                    "count": count,
                    "style": "ticks",
                    "req_id": req_id,
                }
            )
        )

    started = time.monotonic()
    deadline = started + _HISTORY_STARTUP_BUDGET_SECONDS
    synced = 0
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            response = json.loads(raw)
        except (asyncio.TimeoutError, json.JSONDecodeError):
            break

        req_id = response.get("req_id")
        try:
            req_key = int(req_id)
        except (TypeError, ValueError):
            continue
        symbol = pending.pop(req_key, None)
        if symbol is None:
            continue
        if "error" in response:
            self.bot.logger.warning(
                "CUSTOM_FAST_HISTORY_DEFERRED symbol=%s error=%s",
                symbol,
                (response.get("error") or {}).get("message", "unknown"),
            )
            continue

        history = response.get("history") or {}
        prices = list(history.get("prices") or [])
        times = list(history.get("times") or [])
        if len(prices) != len(times) or not prices:
            self.bot.logger.warning(
                "CUSTOM_FAST_HISTORY_DEFERRED symbol=%s prices=%s times=%s",
                symbol,
                len(prices),
                len(times),
            )
            continue

        self.bot._on_public_history(
            symbol=symbol,
            prices=prices,
            times=times,
            pip_size=response.get("pip_size"),
        )
        synced += 1
        self.bot.logger.info(
            "PUBLIC_HISTORY_SYNCED symbol=%s ticks=%s startup_batch=true",
            symbol,
            len(prices),
        )

    for symbol in pending.values():
        self.bot.logger.warning(
            "CUSTOM_FAST_HISTORY_DEFERRED symbol=%s reason=startup_budget_exhausted "
            "live_subscription_continues=true",
            symbol,
        )

    self.bot.logger.info(
        "CUSTOM_FAST_HISTORY_READY requested=%s synced=%s deferred=%s elapsed_ms=%.1f "
        "startup_budget_seconds=%.1f next_action=subscribe_live_ticks",
        len(list(self.bot.symbols)),
        synced,
        len(pending),
        (time.monotonic() - started) * 1000.0,
        _HISTORY_STARTUP_BUDGET_SECONDS,
    )


def install_custom_strategy_instant_start() -> None:
    """Make Start responsive without weakening the financial execution boundary."""

    global _INSTALLED, _ORIGINAL_VALIDATE, _ORIGINAL_FETCH_HISTORY, _ORIGINAL_REST_REQUEST
    if _INSTALLED:
        return

    _ORIGINAL_VALIDATE = RFDir5TradingBot.validate_accounts
    _ORIGINAL_FETCH_HISTORY = PublicMarketDataClient._fetch_tick_history
    _ORIGINAL_REST_REQUEST = base_runtime._rest_request

    # Final Custom Strategy authority: provider account-list discovery must not sit
    # between the Start click and market watching. The private account WebSocket is
    # still mandatory before proposal/BUY execution can pass prepare().
    RFDir5TradingBot.validate_accounts = _instant_validate_accounts
    PublicMarketDataClient._fetch_tick_history = _fast_fetch_tick_history
    base_runtime._rest_request = _bounded_startup_rest_request

    RFDir5TradingBot._custom_strategy_instant_start_installed = True
    RFDir5TradingBot._custom_strategy_startup_account_sweep_blocking = False
    PublicMarketDataClient._custom_strategy_history_batching_installed = True
    _INSTALLED = True
