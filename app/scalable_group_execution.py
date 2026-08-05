from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextvars import ContextVar
from typing import Any

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.guaranteed_signal_delivery as immediate
import app.hybrid_digit_put as hybrid
import app.standardized_execution_runtime as standardized
from app.repositories.rf_dir5_repository import RFDir5Repository, VIRTUAL_MODE
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import TradingBot, mask_account_id, sanitize_account_ids


_INSTALLED = False
SCALABLE_GROUP_EXECUTION_VERSION = "websocket-groups-v2"

# These are logical dispatch groups only. Every account keeps its own authenticated
# Deriv WebSocket and receives its own buy request/response. No REST bulk purchase,
# copy-trading transport, shared trading credential, or PAT-only path is used.
WS_GROUP_SIZE = max(1, int(os.getenv("DERIV_WS_GROUP_SIZE", "20")))
WS_GROUP_CONCURRENCY = max(
    1,
    int(os.getenv("DERIV_WS_GROUP_CONCURRENCY", "4")),
)
WS_GROUP_START_INTERVAL_SECONDS = max(
    0.01,
    float(os.getenv("DERIV_WS_GROUP_START_INTERVAL_SECONDS", "0.05")),
)
WS_ACCOUNT_BUY_TIMEOUT_SECONDS = max(
    10.5,
    float(os.getenv("DERIV_WS_ACCOUNT_BUY_TIMEOUT_SECONDS", "12")),
)
WS_CONFIRMATION_RECONCILE_SECONDS = max(
    0.0,
    float(os.getenv("DERIV_WS_CONFIRMATION_RECONCILE_SECONDS", "0.20")),
)
TRANSPORT_OUTCOME_TTL_SECONDS = max(
    60.0,
    float(os.getenv("TRANSPORT_OUTCOME_TTL_SECONDS", "600")),
)

_AIDR_SCOPE_IDS: ContextVar[frozenset[int] | None] = ContextVar(
    "aidr_scope_ids",
    default=None,
)
_AIDR_RECOVERY_ENABLED: ContextVar[bool | None] = ContextVar(
    "aidr_recovery_enabled",
    default=None,
)
_ACTIVE_RECEIPT_SIGNAL_ID: ContextVar[str] = ContextVar(
    "active_receipt_signal_id",
    default="",
)
_BUY_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "private_buy_context",
    default=None,
)


def _chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _error_payload(item: dict[str, Any]) -> dict[str, Any]:
    error = item.get("error")
    return dict(error) if isinstance(error, dict) else {}


def _error_code(item: dict[str, Any], default: str = "UNKNOWN") -> str:
    return str(_error_payload(item).get("code") or default).strip().upper()


def _error_message(item: dict[str, Any], default: str = "Unknown provider error") -> str:
    return sanitize_account_ids(
        str(_error_payload(item).get("message") or default)
    )


def _outcome_unknown(item: dict[str, Any]) -> bool:
    code = _error_code(item)
    message = _error_message(item).lower()
    return code in {
        "TIMEOUT",
        "PRIVATE_BUY_OUTCOME_UNKNOWN",
        "PRIVATE_CONFIRMATION_UNKNOWN",
    } or "request timed out" in message or "confirmation timed out" in message


def _safe_connection_retry(item: dict[str, Any]) -> bool:
    """Retry only failures proving the buy was not sent on a live socket."""

    code = _error_code(item)
    message = _error_message(item).strip().lower()
    return code in {"NOT_CONNECTED", "PRIVATE_CONNECTION_NOT_READY"} or message in {
        "private websocket is not connected",
        "private trading connection is not ready",
    }


def _normalize_private_result(
    item: dict[str, Any],
    *,
    account_id: str,
    stake: float,
    group_id: str,
) -> dict[str, Any]:
    result = dict(item or {})
    result.setdefault("account_id", account_id)
    result.setdefault("stake_amount", stake)
    result["execution_transport"] = "PRIVATE_WS"
    result["websocket_group_id"] = group_id
    error = _error_payload(result)
    if error:
        message = sanitize_account_ids(str(error.get("message") or "Private buy failed"))
        code = str(error.get("code") or "").strip().upper()
        if not code:
            lower = message.lower()
            if "not connected" in lower:
                code = "NOT_CONNECTED"
            elif "timed out" in lower:
                code = "PRIVATE_BUY_OUTCOME_UNKNOWN"
            else:
                code = "PRIVATE_BUY_FAILED"
        result["error"] = {"code": code, "message": message}
    return result


def _transport_store(bot: RFDir5TradingBot) -> dict[str, dict[str, Any]]:
    store = getattr(bot, "_transport_outcomes_by_signal", None)
    if not isinstance(store, dict):
        store = {}
        bot._transport_outcomes_by_signal = store
    cutoff = time.monotonic() - TRANSPORT_OUTCOME_TTL_SECONDS
    for signal_id in list(store):
        state = store.get(signal_id) or {}
        if float(state.get("updated_monotonic", 0.0) or 0.0) < cutoff:
            store.pop(signal_id, None)
    return store


def _record_transport_outcomes(
    bot: RFDir5TradingBot,
    signal: Any,
    requested_accounts: list[tuple[str, str]],
    transactions: list[dict[str, Any]],
) -> None:
    signal_id = str(getattr(signal, "signal_id", "") or "")
    if not signal_id:
        return
    result_by_account = {
        str(item.get("account_id") or ""): dict(item)
        for item in transactions
        if str(item.get("account_id") or "")
    }
    account_outcomes: dict[int, dict[str, Any]] = {}
    for token, account_id in requested_accounts:
        managed_id = bot._managed_account_id_for_token(token)
        if managed_id is None:
            continue
        item = dict(result_by_account.get(account_id) or {})
        error = _error_payload(item)
        account_outcomes[int(managed_id)] = {
            "account_id": account_id,
            "transport": "PRIVATE_WS",
            "websocket_group_id": item.get("websocket_group_id"),
            "contract_id": item.get("contract_id"),
            "transaction_id": item.get("transaction_id"),
            "confirmation_recovered": bool(item.get("confirmation_recovered")),
            "error_code": str(error.get("code") or ""),
            "error_message": sanitize_account_ids(str(error.get("message") or "")),
            "outcome_unknown": _outcome_unknown(item),
            "updated_monotonic": time.monotonic(),
        }
    _transport_store(bot)[signal_id] = {
        "updated_monotonic": time.monotonic(),
        "accounts": account_outcomes,
    }


def _latest_transport_outcome(
    bot: RFDir5TradingBot,
    signal_id: str,
    managed_id: int,
) -> dict[str, Any] | None:
    state = _transport_store(bot).get(str(signal_id)) or {}
    accounts = state.get("accounts") or {}
    outcome = accounts.get(int(managed_id))
    return dict(outcome) if isinstance(outcome, dict) else None


def _group_limiter(bot: RFDir5TradingBot) -> asyncio.Semaphore:
    limiter = getattr(bot, "_private_ws_group_limiter", None)
    if not isinstance(limiter, asyncio.Semaphore):
        limiter = asyncio.Semaphore(WS_GROUP_CONCURRENCY)
        bot._private_ws_group_limiter = limiter
    return limiter


def _account_buy_lock(bot: RFDir5TradingBot, token: str) -> asyncio.Lock:
    locks = getattr(bot, "_private_ws_account_buy_locks", None)
    if not isinstance(locks, dict):
        locks = {}
        bot._private_ws_account_buy_locks = locks
    lock = locks.get(str(token))
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        locks[str(token)] = lock
    return lock


async def _wait_group_start_slot(bot: RFDir5TradingBot) -> None:
    lock = getattr(bot, "_private_ws_group_start_lock", None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        bot._private_ws_group_start_lock = lock
        bot._private_ws_next_group_start = 0.0
    async with lock:
        now = time.monotonic()
        next_start = float(
            getattr(bot, "_private_ws_next_group_start", 0.0) or 0.0
        )
        if next_start > now:
            await asyncio.sleep(next_start - now)
            now = time.monotonic()
        bot._private_ws_next_group_start = now + WS_GROUP_START_INTERVAL_SECONDS


def _extract_contract_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    portfolio = response.get("portfolio")
    if isinstance(portfolio, dict):
        values = portfolio.get("contracts") or []
        if isinstance(values, list):
            rows.extend(item for item in values if isinstance(item, dict))
    elif isinstance(portfolio, list):
        rows.extend(item for item in portfolio if isinstance(item, dict))

    table = response.get("profit_table")
    if isinstance(table, dict):
        values = table.get("transactions") or []
        if isinstance(values, list):
            rows.extend(item for item in values if isinstance(item, dict))
    return rows


def _candidate_contract_id(row: dict[str, Any]) -> int | None:
    raw = row.get("contract_id") or row.get("id")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _row_matches_unknown_buy(
    row: dict[str, Any],
    *,
    signal: Any,
    stake: float,
    sent_epoch: float,
) -> bool:
    contract_id = _candidate_contract_id(row)
    if contract_id is None:
        return False
    symbol = str(
        row.get("underlying")
        or row.get("underlying_symbol")
        or row.get("symbol")
        or ""
    )
    if symbol and symbol != str(getattr(signal, "symbol", "") or ""):
        return False
    contract_type = str(row.get("contract_type") or "").upper()
    expected_type = str(getattr(signal, "contract_type", "") or "").upper()
    if contract_type and expected_type and contract_type != expected_type:
        return False
    try:
        buy_price = float(row.get("buy_price"))
    except (TypeError, ValueError):
        buy_price = None
    if buy_price is not None and abs(buy_price - float(stake)) > 0.011:
        return False
    try:
        purchase_time = float(
            row.get("purchase_time")
            or row.get("date_start")
            or row.get("start_time")
            or 0
        )
    except (TypeError, ValueError):
        purchase_time = 0.0
    if purchase_time and purchase_time < sent_epoch - 5.0:
        return False
    return True


async def _recover_unknown_confirmation(
    bot: RFDir5TradingBot,
    *,
    token: str,
    account_id: str,
    signal: Any,
    stake: float,
    sent_epoch: float,
    group_id: str,
) -> dict[str, Any] | None:
    """Recover a lost buy response without replaying the financial request."""

    session = getattr(bot, "sessions", {}).get(token)
    if session is None or not bool(getattr(session, "is_connected", False)):
        return None
    if WS_CONFIRMATION_RECONCILE_SECONDS:
        await asyncio.sleep(WS_CONFIRMATION_RECONCILE_SECONDS)

    requests = (
        {"portfolio": 1},
        {
            "profit_table": 1,
            "limit": 10,
            "sort": "DESC",
            "date_from": str(max(0, int(sent_epoch) - 30)),
        },
    )
    matches: dict[int, dict[str, Any]] = {}
    for request in requests:
        try:
            response = await session.send_request(dict(request))
        except Exception:
            continue
        if "error" in response:
            continue
        for row in _extract_contract_rows(response):
            if not _row_matches_unknown_buy(
                row,
                signal=signal,
                stake=stake,
                sent_epoch=sent_epoch,
            ):
                continue
            contract_id = _candidate_contract_id(row)
            if contract_id is not None:
                matches[contract_id] = row

    if len(matches) != 1:
        bot.logger.warning(
            "PRIVATE_WS_CONFIRMATION_RECONCILIATION_UNRESOLVED account=%s "
            "signal_id=%s group_id=%s candidates=%s replay=false",
            mask_account_id(account_id),
            str(getattr(signal, "signal_id", "") or ""),
            group_id,
            len(matches),
        )
        return None

    contract_id, row = next(iter(matches.items()))
    bot.logger.warning(
        "PRIVATE_WS_CONFIRMATION_RECOVERED account=%s signal_id=%s "
        "group_id=%s contract_id=%s source=portfolio_or_profit_table "
        "buy_replayed=false",
        mask_account_id(account_id),
        str(getattr(signal, "signal_id", "") or ""),
        group_id,
        contract_id,
    )
    return {
        "account_id": account_id,
        "contract_id": contract_id,
        "transaction_id": row.get("transaction_id") or contract_id,
        "buy_price": row.get("buy_price") or stake,
        "payout": row.get("payout"),
        "purchase_time": row.get("purchase_time") or row.get("date_start"),
        "start_time": row.get("date_start") or row.get("start_time"),
        "stake_amount": stake,
        "execution_transport": "PRIVATE_WS",
        "websocket_group_id": group_id,
        "confirmation_recovered": True,
    }


async def _buy_one_serialized(
    bot: RFDir5TradingBot,
    *,
    signal: Any,
    token: str,
    account_id: str,
    stake: float,
    group_id: str,
) -> dict[str, Any]:
    managed_id = bot._managed_account_id_for_token(token)
    context_token = _BUY_CONTEXT.set(
        {
            "signal_id": str(getattr(signal, "signal_id", "") or ""),
            "managed_account_id": int(managed_id) if managed_id is not None else None,
            "websocket_group_id": group_id,
        }
    )
    sent_epoch = time.time()
    try:
        async with _account_buy_lock(bot, token):
            try:
                values = await asyncio.wait_for(
                    bot._purchase_via_private_sessions(
                        signal=signal,
                        eligible_accounts=[(token, account_id)],
                        stake_amount=stake,
                    ),
                    timeout=WS_ACCOUNT_BUY_TIMEOUT_SECONDS,
                )
                item = dict(values[0]) if values else {
                    "account_id": account_id,
                    "error": {
                        "code": "PRIVATE_RESULT_MISSING",
                        "message": "No private WebSocket buy result was returned.",
                    },
                }
            except asyncio.TimeoutError:
                item = {
                    "account_id": account_id,
                    "error": {
                        "code": "PRIVATE_BUY_OUTCOME_UNKNOWN",
                        "message": (
                            "Private WebSocket buy confirmation timed out. The buy "
                            "will not be replayed automatically."
                        ),
                    },
                }
            except Exception as exc:
                item = {
                    "account_id": account_id,
                    "error": {
                        "code": "PRIVATE_BUY_FAILED",
                        "message": sanitize_account_ids(str(exc)),
                    },
                }

            normalized = _normalize_private_result(
                item,
                account_id=account_id,
                stake=stake,
                group_id=group_id,
            )
            if _outcome_unknown(normalized):
                recovered = await _recover_unknown_confirmation(
                    bot,
                    token=token,
                    account_id=account_id,
                    signal=signal,
                    stake=stake,
                    sent_epoch=sent_epoch,
                    group_id=group_id,
                )
                if recovered is not None:
                    if managed_id is not None:
                        bot._set_account_execution_status(
                            managed_id,
                            "active",
                            "WebSocket contract confirmation recovered safely",
                        )
                    return recovered
                if managed_id is not None:
                    bot._set_account_execution_status(
                        managed_id,
                        "confirmation_pending",
                        (
                            "Provider confirmation was not received. The buy was not "
                            "replayed to prevent a duplicate contract; reconciliation "
                            "will continue for this account only."
                        ),
                    )
            return normalized
    finally:
        _BUY_CONTEXT.reset(context_token)


async def _dispatch_ws_group(
    bot: RFDir5TradingBot,
    *,
    signal: Any,
    accounts: list[tuple[str, str]],
    stake: float,
    environment: str,
    group_index: int,
) -> list[dict[str, Any]]:
    group_id = (
        f"{str(getattr(signal, 'signal_id', '') or '')[:8]}-"
        f"{environment}-{group_index}"
    )
    async with _group_limiter(bot):
        await _wait_group_start_slot(bot)
        bot.logger.warning(
            "PRIVATE_WS_GROUP_DISPATCH signal_id=%s group_id=%s "
            "environment=%s stake=%.2f accounts=%s "
            "one_authenticated_socket_per_account=true",
            str(getattr(signal, "signal_id", "") or ""),
            group_id,
            environment,
            stake,
            len(accounts),
        )

        ready, blocked = await immediate._ready_accounts(
            bot,
            accounts,
            timeout=immediate.PRIVATE_READY_TIMEOUT_SECONDS,
            phase="group_grace",
        )
        result_by_account: dict[str, dict[str, Any]] = {}
        if ready:
            values = await asyncio.gather(
                *(
                    _buy_one_serialized(
                        bot,
                        signal=signal,
                        token=token,
                        account_id=account_id,
                        stake=stake,
                        group_id=group_id,
                    )
                    for token, account_id in ready
                ),
                return_exceptions=True,
            )
            for (token, account_id), value in zip(ready, values, strict=True):
                if isinstance(value, Exception):
                    value = {
                        "account_id": account_id,
                        "error": {
                            "code": "PRIVATE_ACCOUNT_TASK_FAILED",
                            "message": sanitize_account_ids(str(value)),
                        },
                    }
                result_by_account[account_id] = _normalize_private_result(
                    dict(value),
                    account_id=account_id,
                    stake=stake,
                    group_id=group_id,
                )

        retry_accounts = [
            (token, account_id)
            for token, account_id in accounts
            if account_id in blocked
            or _safe_connection_retry(result_by_account.get(account_id, {}))
        ]
        final_blocked = dict(blocked)
        if retry_accounts:
            retry_ready, retry_blocked = await immediate._ready_accounts(
                bot,
                retry_accounts,
                timeout=immediate.PRIVATE_RETRY_TIMEOUT_SECONDS,
                phase="group_retry",
            )
            final_blocked.update(retry_blocked)
            if retry_ready:
                bot.logger.warning(
                    "PRIVATE_WS_GROUP_CONNECTION_RETRY signal_id=%s group_id=%s "
                    "accounts=%s attempt=2 reason=pre_send_connection_not_ready",
                    str(getattr(signal, "signal_id", "") or ""),
                    group_id,
                    len(retry_ready),
                )
                retry_values = await asyncio.gather(
                    *(
                        _buy_one_serialized(
                            bot,
                            signal=signal,
                            token=token,
                            account_id=account_id,
                            stake=stake,
                            group_id=group_id,
                        )
                        for token, account_id in retry_ready
                    ),
                    return_exceptions=True,
                )
                for (_token, account_id), value in zip(
                    retry_ready,
                    retry_values,
                    strict=True,
                ):
                    if isinstance(value, Exception):
                        value = {
                            "account_id": account_id,
                            "error": {
                                "code": "PRIVATE_ACCOUNT_TASK_FAILED",
                                "message": sanitize_account_ids(str(value)),
                            },
                        }
                    result_by_account[account_id] = _normalize_private_result(
                        dict(value),
                        account_id=account_id,
                        stake=stake,
                        group_id=group_id,
                    )
                    if not result_by_account[account_id].get("error"):
                        final_blocked.pop(account_id, None)

        transactions: list[dict[str, Any]] = []
        for _token, account_id in accounts:
            item = result_by_account.get(account_id)
            if item is None:
                blocked_item = final_blocked.get(account_id) or {
                    "account_id": account_id,
                    "error": {
                        "code": "PRIVATE_CONNECTION_NOT_READY",
                        "message": "Private trading connection is not ready",
                    },
                }
                item = _normalize_private_result(
                    dict(blocked_item),
                    account_id=account_id,
                    stake=stake,
                    group_id=group_id,
                )
            transactions.append(item)

        confirmed = sum(
            1
            for item in transactions
            if item.get("contract_id") and not item.get("error")
        )
        unknown = sum(1 for item in transactions if _outcome_unknown(item))
        bot.logger.warning(
            "PRIVATE_WS_GROUP_RESULT signal_id=%s group_id=%s confirmed=%s "
            "failed=%s outcome_unknown=%s global_execution_continues=true",
            str(getattr(signal, "signal_id", "") or ""),
            group_id,
            confirmed,
            len(transactions) - confirmed,
            unknown,
        )
        return transactions


async def _grouped_purchase_accounts_by_stake(
    self: RFDir5TradingBot,
    *,
    signal: Any,
    eligible_accounts: list[tuple[str, str]],
    stake_by_token: dict[str, float],
    pre_trade_profit_ratio: float = 0.0,
) -> list[dict[str, Any]]:
    """Bound private-WebSocket fan-out without changing the trading transport."""

    del pre_trade_profit_ratio
    if not eligible_accounts:
        return []

    groups: dict[tuple[str, float], list[tuple[str, str]]] = {}
    rejected: list[dict[str, Any]] = []
    requested: list[tuple[str, str]] = []

    for token, account_id in eligible_accounts:
        managed_id = self._managed_account_id_for_token(token)
        protection = (
            self.rf_repository.virtual_protection_for_account(
                managed_account_id=managed_id,
                account_id_masked=mask_account_id(account_id),
            )
            if managed_id is not None
            else {"mode": "UNKNOWN"}
        )
        requested.append((token, account_id))
        if str(protection.get("mode") or "") == VIRTUAL_MODE:
            rejected.append(
                {
                    "account_id": account_id,
                    "execution_transport": "PRIVATE_WS",
                    "error": {
                        "code": "VIRTUAL_MODE",
                        "message": (
                            "Financial purchase blocked while virtual protection is active."
                        ),
                    },
                }
            )
            continue

        environment = self._account_environment_for_token(token)
        if environment == "real" and not self._real_trading_allowed():
            message = "Real trading is disabled on this VPS"
            self._set_account_execution_status(managed_id, "real_disabled", message)
            rejected.append(
                {
                    "account_id": account_id,
                    "execution_transport": "PRIVATE_WS",
                    "error": {"code": "REAL_DISABLED", "message": message},
                }
            )
            continue

        stake = round(float(stake_by_token[token]), 2)
        groups.setdefault((environment, stake), []).append((token, account_id))

    tasks: list[Any] = []
    task_meta: list[tuple[str, float, int, list[tuple[str, str]]]] = []
    group_index = 0
    for (environment, stake), accounts in sorted(groups.items(), key=lambda item: item[0]):
        accounts.sort(
            key=lambda item: (
                self._managed_account_id_for_token(item[0]) or 2**63,
                item[1],
            )
        )
        for members in _chunks(accounts, WS_GROUP_SIZE):
            group_index += 1
            task_meta.append((environment, stake, group_index, members))
            tasks.append(
                _dispatch_ws_group(
                    self,
                    signal=signal,
                    accounts=members,
                    stake=stake,
                    environment=environment,
                    group_index=group_index,
                )
            )

    self.logger.warning(
        "PRIVATE_WS_EXECUTION_PLAN signal_id=%s symbol=%s contract_type=%s "
        "barrier=%s accounts=%s groups=%s group_size=%s concurrency=%s "
        "transport=PRIVATE_WEBSOCKET_ONLY copy_trading=false "
        "bulk_purchase=false global_stop_on_error=false",
        str(getattr(signal, "signal_id", "") or ""),
        str(getattr(signal, "symbol", "") or ""),
        str(getattr(signal, "contract_type", "") or ""),
        str(getattr(signal, "barrier", "") or ""),
        len(requested),
        len(tasks),
        WS_GROUP_SIZE,
        WS_GROUP_CONCURRENCY,
    )

    values = await asyncio.gather(*tasks, return_exceptions=True)
    transactions: list[dict[str, Any]] = list(rejected)
    for (environment, stake, index, members), value in zip(
        task_meta,
        values,
        strict=True,
    ):
        if isinstance(value, Exception):
            message = sanitize_account_ids(str(value))
            value = [
                {
                    "account_id": account_id,
                    "stake_amount": stake,
                    "execution_transport": "PRIVATE_WS",
                    "websocket_group_id": f"failed-{environment}-{index}",
                    "error": {
                        "code": "PRIVATE_WS_GROUP_FAILED",
                        "message": message,
                    },
                }
                for _token, account_id in members
            ]
        transactions.extend(dict(item) for item in value)

    _record_transport_outcomes(self, signal, requested, transactions)
    confirmed = sum(
        1
        for item in transactions
        if item.get("contract_id") and not item.get("error")
    )
    unknown = sum(1 for item in transactions if _outcome_unknown(item))
    self.logger.warning(
        "PRIVATE_WS_EXECUTION_RESULT signal_id=%s confirmed=%s failed=%s "
        "outcome_unknown=%s global_execution_continues=true",
        str(getattr(signal, "signal_id", "") or ""),
        confirmed,
        len(transactions) - confirmed,
        unknown,
    )
    return transactions


def _public_contract_cache_on_private_ready(
    self: RFDir5TradingBot,
    session: Any,
) -> None:
    """Use one public contract cache; never query every account and market."""

    TradingBot._on_private_session_ready(self, session)
    for symbol, types in dict(getattr(self, "rf_supported_contracts", {}) or {}).items():
        self.rf_account_supported_contracts[(session.account_id, symbol)] = set(types)
    verified = sum(
        1
        for symbol in self.symbols
        if bool(self.rf_supported_contracts.get(symbol))
    )
    self.logger.info(
        "RF_ACCOUNT_USES_PUBLIC_CONTRACT_CACHE account=%s markets=%s/%s "
        "authenticated_contract_metadata_requests=0",
        mask_account_id(session.account_id),
        verified,
        len(self.symbols),
    )


async def _public_only_account_contract_validation(
    self: RFDir5TradingBot,
    session: Any,
) -> None:
    for symbol, types in dict(getattr(self, "rf_supported_contracts", {}) or {}).items():
        self.rf_account_supported_contracts[(session.account_id, symbol)] = set(types)
    self.logger.info(
        "RF_ACCOUNT_CONTRACTS_PUBLIC_CACHE account=%s markets=%s "
        "private_metadata_requests=0",
        mask_account_id(session.account_id),
        len(self.rf_supported_contracts),
    )


def _public_contract_support(
    self: RFDir5TradingBot,
    *,
    account_id: str,
    symbol: str,
    contract_type: str,
) -> bool:
    del account_id
    supported = self.rf_supported_contracts.get(str(symbol))
    if not supported:
        # Do not exclude a healthy account merely because the public cache is still
        # warming. The account's authenticated buy response remains authoritative.
        return True
    return str(contract_type or "").upper() in set(supported)


def _current_scope_for_signal(
    bot: RFDir5TradingBot,
    signal: Any,
) -> set[int]:
    normal, recovery, post_virtual, virtual = aidr._account_recovery_groups(bot)
    try:
        barrier = int(str(getattr(signal, "barrier", "") or "-1"))
    except (TypeError, ValueError):
        barrier = -1
    if barrier == int(aidr.NORMAL_BARRIER):
        return set(normal)
    if barrier == int(aidr.RECOVERY_BARRIER):
        return set(recovery)
    if barrier == int(aidr.POST_VIRTUAL_BARRIER):
        return set(post_virtual) | set(virtual)
    return set()


async def _context_safe_buy_for_scope(
    bot: RFDir5TradingBot,
    signal: Any,
    economics: Any,
    managed_ids: set[int],
    *,
    recovery_enabled: bool,
) -> None:
    current = _current_scope_for_signal(bot, signal)
    scope = current if current else {int(value) for value in managed_ids}
    if not scope:
        bot.repository.mark_signal(signal.signal_id, status="SKIP_NO_SCOPE_ACCOUNTS")
        return
    scope_token = _AIDR_SCOPE_IDS.set(frozenset(scope))
    recovery_token = _AIDR_RECOVERY_ENABLED.set(bool(recovery_enabled))
    try:
        await bot._buy_selected_accounts(signal, economics)
    finally:
        _AIDR_RECOVERY_ENABLED.reset(recovery_token)
        _AIDR_SCOPE_IDS.reset(scope_token)


async def _role_proposal_with_retry(
    bot: RFDir5TradingBot,
    *,
    role: str,
    symbol: str,
) -> tuple[Any, Any] | None:
    for attempt in (1, 2):
        signal = immediate._role_signal(bot, symbol=symbol, role=role)
        if signal is None:
            return None
        bot.repository.record_candidate(signal)
        result = await immediate._provider_proposal(bot, signal)
        if result is not None:
            if attempt > 1:
                bot.logger.info(
                    "AIDR_ROLE_PROPOSAL_RECOVERED role=%s symbol=%s attempt=%s",
                    role,
                    symbol,
                    attempt,
                )
            return result
        if attempt == 1:
            await asyncio.sleep(0.15)
    return None


async def _dispatch_aidr_role(
    bot: RFDir5TradingBot,
    *,
    parent_cycle_id: str,
    role: str,
    signal: Any,
    economics: Any,
    scope: set[int],
) -> tuple[str, str]:
    barrier, recovery_enabled = standardized._role_spec(role)
    signal._standardized_cycle_id = f"{parent_cycle_id}:{role}"
    if not immediate.refresh_signal_for_delivery(bot, signal):
        return role, "immediate_deadline_missed"
    continuation._ensure_directional_signal(bot, signal, role=role)
    bot.logger.warning(
        "AIDR_ROLE_DISPATCH_STARTED parent_cycle_id=%s role=%s symbol=%s "
        "barrier=%s accounts=%s transport=PRIVATE_WEBSOCKET_ONLY",
        parent_cycle_id,
        role,
        signal.symbol,
        barrier,
        len(scope),
    )
    try:
        await aidr._buy_for_scope(
            bot,
            signal,
            economics,
            scope,
            recovery_enabled=recovery_enabled,
        )
    except Exception as exc:
        bot.logger.exception(
            "AIDR_ROLE_DISPATCH_FAILED parent_cycle_id=%s role=%s "
            "symbol=%s barrier=%s accounts=%s error=%s "
            "global_execution_continues=true",
            parent_cycle_id,
            role,
            signal.symbol,
            barrier,
            len(scope),
            type(exc).__name__,
        )
        return role, f"exception_{type(exc).__name__}"
    return role, "submitted"


def install_scalable_group_execution() -> None:
    """Install WebSocket-only grouping, public metadata caching and isolation."""

    global _INSTALLED
    if _INSTALLED:
        return

    # contracts_for is public and account-independent. Never multiply this market
    # metadata request by account count or reconnect count.
    RFDir5TradingBot._on_private_session_ready = _public_contract_cache_on_private_ready
    RFDir5TradingBot._validate_account_contracts = _public_only_account_contract_validation
    RFDir5TradingBot._account_supports_contract = _public_contract_support

    # Scope and recovery flags are task-local, so NORMAL/recovery/post-virtual
    # account membership cannot overwrite another System role.
    original_eligible = RFDir5TradingBot._eligible_purchase_accounts

    def context_scoped_eligible(
        self: RFDir5TradingBot,
    ) -> list[tuple[str, str]]:
        accounts = list(original_eligible(self))
        scope = _AIDR_SCOPE_IDS.get()
        if scope is None:
            return accounts
        return [
            (token, account_id)
            for token, account_id in accounts
            if self._managed_account_id_for_token(token) in scope
        ]

    RFDir5TradingBot._eligible_purchase_accounts = context_scoped_eligible

    original_plan_stake = RFDir5Repository.plan_stake

    def context_recovery_plan(self: RFDir5Repository, **kwargs: Any):
        override = _AIDR_RECOVERY_ENABLED.get()
        if override is not None:
            kwargs["recovery_enabled"] = bool(override)
        return original_plan_stake(self, **kwargs)

    RFDir5Repository.plan_stake = context_recovery_plan

    original_scope_ids = standardized._signal_scope_ids

    def context_signal_scope_ids(
        bot: RFDir5TradingBot,
        signal: Any,
    ) -> set[int]:
        scope = _AIDR_SCOPE_IDS.get()
        if scope is not None:
            return {int(value) for value in scope}
        return original_scope_ids(bot, signal)

    standardized._signal_scope_ids = context_signal_scope_ids
    aidr._buy_for_scope = _context_safe_buy_for_scope

    # Final financial transport authority: every account is purchased through its
    # own persistent private WebSocket. Grouping controls scheduling only.
    RFDir5TradingBot._purchase_accounts_by_stake = _grouped_purchase_accounts_by_stake

    # Attach immutable correlation metadata to the provider echo without exposing
    # credentials or changing contract economics.
    original_direct_buy = RFDir5TradingBot._direct_buy_request

    def correlated_direct_buy(
        self: RFDir5TradingBot,
        signal: Any,
        stake_amount: float,
    ) -> dict[str, Any]:
        request = dict(original_direct_buy(self, signal, stake_amount))
        context = _BUY_CONTEXT.get() or {}
        request["passthrough"] = {
            "signal_id": str(
                context.get("signal_id")
                or getattr(signal, "signal_id", "")
                or ""
            )[:64],
            "managed_account_id": context.get("managed_account_id"),
            "websocket_group_id": str(context.get("websocket_group_id") or "")[:64],
            "transport": "private_websocket",
        }
        return request

    RFDir5TradingBot._direct_buy_request = correlated_direct_buy

    # Exact private-transport outcomes replace the generic
    # provider_confirmation_missing message whenever possible.
    original_missing_reason = standardized._missing_reason

    def exact_missing_reason(
        bot: RFDir5TradingBot,
        managed_id: int,
        *,
        pending_before: bool,
    ) -> tuple[str, str, bool]:
        signal_id = _ACTIVE_RECEIPT_SIGNAL_ID.get()
        outcome = _latest_transport_outcome(bot, signal_id, managed_id)
        if outcome is not None:
            if outcome.get("contract_id"):
                return (
                    "provider_confirmed_registration_missing",
                    (
                        "Deriv returned a private-WebSocket contract ID, but the "
                        "local Trade row was not visible to the cycle receipt. "
                        "Registration reconciliation is active for this account only."
                    ),
                    True,
                )
            code = str(outcome.get("error_code") or "PRIVATE_BUY_FAILED").upper()
            message = sanitize_account_ids(
                str(outcome.get("error_message") or "Private WebSocket buy failed")
            )
            if bool(outcome.get("outcome_unknown")):
                return (
                    "provider_confirmation_unknown",
                    (
                        f"{message} Automatic replay was suppressed to prevent a "
                        "duplicate contract."
                    ),
                    True,
                )
            return (
                f"private_ws_{code.lower()}",
                message,
                code not in {
                    "INVALID_TOKEN",
                    "UNAUTHORIZED",
                    "ACCESS_DENIED",
                    "REAL_DISABLED",
                    "INSUFFICIENT_BALANCE",
                },
            )
        return original_missing_reason(
            bot,
            managed_id,
            pending_before=pending_before,
        )

    standardized._missing_reason = exact_missing_reason

    original_buy = RFDir5TradingBot._buy_selected_accounts

    async def buy_with_receipt_signal_context(
        self: RFDir5TradingBot,
        signal: Any,
        economics: Any,
    ) -> None:
        token = _ACTIVE_RECEIPT_SIGNAL_ID.set(
            str(getattr(signal, "signal_id", "") or "")
        )
        try:
            await original_buy(self, signal, economics)
        finally:
            _ACTIVE_RECEIPT_SIGNAL_ID.reset(token)

    RFDir5TradingBot._buy_selected_accounts = buy_with_receipt_signal_context

    RFDir5TradingBot._scalable_group_execution_installed = True
    _INSTALLED = True
    logging.getLogger(__name__).warning(
        "SCALABLE_GROUP_EXECUTION_INSTALLED version=%s group_size=%s "
        "group_concurrency=%s private_websocket_only=true "
        "bulk_purchase=false copy_trading=false public_contract_cache=true "
        "task_local_role_scopes=true global_stop_on_account_error=false",
        SCALABLE_GROUP_EXECUTION_VERSION,
        WS_GROUP_SIZE,
        WS_GROUP_CONCURRENCY,
    )
