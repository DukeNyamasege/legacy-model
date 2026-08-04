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
SCALABLE_GROUP_EXECUTION_VERSION = "grouped-execution-v1"

# Deriv's official bulk-purchase endpoint accepts at most 100 accounts for one
# identical contract request. Keep the configured value at or below that limit.
BULK_SHARD_SIZE = min(
    100,
    max(1, int(os.getenv("DERIV_BULK_SHARD_SIZE", "100"))),
)
BULK_CONCURRENCY = max(
    1,
    int(os.getenv("DERIV_BULK_CONCURRENCY", "2")),
)
BULK_START_INTERVAL_SECONDS = max(
    0.05,
    float(os.getenv("DERIV_BULK_START_INTERVAL_SECONDS", "0.20")),
)
BULK_TIMEOUT_SECONDS = max(
    10.0,
    float(os.getenv("DERIV_BULK_TIMEOUT_SECONDS", "25")),
)

# OAuth accounts cannot use the PAT-only bulk member format. They retain their
# account-scoped private WebSockets, but are dispatched in bounded groups so one
# cycle cannot create an unbounded gather across hundreds of sockets.
PRIVATE_GROUP_SIZE = max(
    1,
    int(os.getenv("DERIV_PRIVATE_GROUP_SIZE", "25")),
)
PRIVATE_GROUP_CONCURRENCY = max(
    1,
    int(os.getenv("DERIV_PRIVATE_GROUP_CONCURRENCY", "4")),
)
PRIVATE_GROUP_TIMEOUT_SECONDS = max(
    12.0,
    float(os.getenv("DERIV_PRIVATE_GROUP_TIMEOUT_SECONDS", "25")),
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


def _safe_bulk_retry(item: dict[str, Any]) -> bool:
    """Retry only explicit pre-execution throttling responses.

    A network timeout is deliberately not retried because the provider may have
    accepted the trade while the response was lost. Avoiding duplicate contracts
    is more important than blindly replaying an outcome-unknown buy request.
    """

    code = _error_code(item).replace("-", "_")
    message = _error_message(item).lower()
    return code in {
        "RATE_LIMITED",
        "HTTP_429",
        "TOO_MANY_REQUESTS",
    } or "rate limit" in message or "too many requests" in message


def _connection_error(item: dict[str, Any]) -> bool:
    code = _error_code(item)
    message = _error_message(item).lower()
    return code in {"NOT_CONNECTED", "CONNECTION_ERROR"} or any(
        marker in message
        for marker in (
            "private websocket is not connected",
            "connection closed",
            "connection lost",
            "not connected",
        )
    )


def _outcome_unknown(item: dict[str, Any]) -> bool:
    code = _error_code(item)
    message = _error_message(item).lower()
    return code in {
        "TIMEOUT",
        "CONNECTION_ERROR",
        "BULK_OUTCOME_UNKNOWN",
        "PRIVATE_BUY_OUTCOME_UNKNOWN",
    } or "request timed out" in message or "timed out" in message


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
    token_by_account = {
        str(account_id): str(token)
        for token, account_id in requested_accounts
    }
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
        transport = str(
            item.get("execution_transport")
            or item.get("transport")
            or (
                "BULK_REST"
                if item.get("bulk_batch_id") is not None
                else "PRIVATE_WS"
            )
        )
        account_outcomes[int(managed_id)] = {
            "account_id": account_id,
            "transport": transport,
            "contract_id": item.get("contract_id"),
            "transaction_id": item.get("transaction_id"),
            "bulk_batch_id": item.get("bulk_batch_id"),
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


def _bulk_limiter(bot: RFDir5TradingBot) -> asyncio.Semaphore:
    limiter = getattr(bot, "_grouped_bulk_limiter", None)
    if not isinstance(limiter, asyncio.Semaphore):
        limiter = asyncio.Semaphore(BULK_CONCURRENCY)
        bot._grouped_bulk_limiter = limiter
    return limiter


def _private_limiter(bot: RFDir5TradingBot) -> asyncio.Semaphore:
    limiter = getattr(bot, "_grouped_private_limiter", None)
    if not isinstance(limiter, asyncio.Semaphore):
        limiter = asyncio.Semaphore(PRIVATE_GROUP_CONCURRENCY)
        bot._grouped_private_limiter = limiter
    return limiter


async def _wait_bulk_start_slot(bot: RFDir5TradingBot) -> None:
    lock = getattr(bot, "_grouped_bulk_start_lock", None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        bot._grouped_bulk_start_lock = lock
        bot._grouped_bulk_next_start = 0.0
    async with lock:
        now = time.monotonic()
        next_start = float(getattr(bot, "_grouped_bulk_next_start", 0.0) or 0.0)
        if next_start > now:
            await asyncio.sleep(next_start - now)
            now = time.monotonic()
        bot._grouped_bulk_next_start = now + BULK_START_INTERVAL_SECONDS


async def _dispatch_bulk_shard(
    bot: RFDir5TradingBot,
    *,
    signal: Any,
    accounts: list[tuple[str, str]],
    stake: float,
    environment: str,
    martingale_enabled: bool,
    shard_index: int,
    pre_trade_profit_ratio: float,
) -> list[dict[str, Any]]:
    async def send(
        members: list[tuple[str, str]],
        index: int,
    ) -> list[dict[str, Any]]:
        async with _bulk_limiter(bot):
            await _wait_bulk_start_slot(bot)
            try:
                return await asyncio.wait_for(
                    bot._purchase_stake_group_for_environment(
                        signal=signal,
                        eligible_accounts=members,
                        stake_amount=stake,
                        environment=environment,
                        martingale_enabled=martingale_enabled,
                        shard_index=index,
                        pre_trade_profit_ratio=pre_trade_profit_ratio,
                    ),
                    timeout=BULK_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                bot.logger.error(
                    "GROUP_BULK_OUTCOME_UNKNOWN signal_id=%s environment=%s "
                    "stake=%.2f accounts=%s timeout_seconds=%.1f "
                    "automatic_replay=false duplicate_protection=true",
                    str(getattr(signal, "signal_id", "") or ""),
                    environment,
                    stake,
                    len(members),
                    BULK_TIMEOUT_SECONDS,
                )
                return [
                    {
                        "account_id": account_id,
                        "stake_amount": stake,
                        "execution_transport": "BULK_REST",
                        "error": {
                            "code": "BULK_OUTCOME_UNKNOWN",
                            "message": (
                                "Bulk response timed out. The request is not replayed "
                                "automatically to prevent duplicate contracts."
                            ),
                        },
                    }
                    for _token, account_id in members
                ]
            except Exception as exc:
                message = sanitize_account_ids(str(exc))
                bot.logger.error(
                    "GROUP_BULK_SHARD_FAILED signal_id=%s environment=%s "
                    "stake=%.2f accounts=%s error=%s",
                    str(getattr(signal, "signal_id", "") or ""),
                    environment,
                    stake,
                    len(members),
                    message,
                )
                return [
                    {
                        "account_id": account_id,
                        "stake_amount": stake,
                        "execution_transport": "BULK_REST",
                        "error": {
                            "code": "BULK_SHARD_FAILED",
                            "message": message,
                        },
                    }
                    for _token, account_id in members
                ]

    first = await send(accounts, shard_index)
    first_by_account = {
        str(item.get("account_id") or ""): dict(item)
        for item in first
    }
    safe_retry_accounts = [
        (token, account_id)
        for token, account_id in accounts
        if _safe_bulk_retry(first_by_account.get(account_id, {}))
    ]
    if safe_retry_accounts:
        await asyncio.sleep(min(2.0, 0.35 * max(1, shard_index)))
        bot.logger.warning(
            "GROUP_BULK_SAFE_RETRY signal_id=%s environment=%s stake=%.2f "
            "accounts=%s reason=explicit_rate_limit attempt=2",
            str(getattr(signal, "signal_id", "") or ""),
            environment,
            stake,
            len(safe_retry_accounts),
        )
        retried = await send(safe_retry_accounts, 1000 + shard_index)
        for item in retried:
            first_by_account[str(item.get("account_id") or "")] = dict(item)

    results: list[dict[str, Any]] = []
    for _token, account_id in accounts:
        item = dict(first_by_account.get(account_id) or {})
        item.setdefault("account_id", account_id)
        item.setdefault("stake_amount", stake)
        item["execution_transport"] = "BULK_REST"
        results.append(item)
    return results


async def _dispatch_private_chunk(
    bot: RFDir5TradingBot,
    *,
    signal: Any,
    accounts: list[tuple[str, str]],
    stake: float,
) -> list[dict[str, Any]]:
    async with _private_limiter(bot):
        ready, initially_blocked = await immediate._ready_accounts(
            bot,
            accounts,
            timeout=immediate.PRIVATE_READY_TIMEOUT_SECONDS,
            phase="grace",
        )

        async def buy(members: list[tuple[str, str]]) -> list[dict[str, Any]]:
            if not members:
                return []
            try:
                return await asyncio.wait_for(
                    bot._purchase_via_private_sessions(
                        signal=signal,
                        eligible_accounts=members,
                        stake_amount=stake,
                    ),
                    timeout=PRIVATE_GROUP_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                return [
                    {
                        "account_id": account_id,
                        "error": {
                            "code": "PRIVATE_BUY_OUTCOME_UNKNOWN",
                            "message": (
                                "Private buy confirmation timed out. The buy is not "
                                "replayed automatically to prevent a duplicate contract."
                            ),
                        },
                    }
                    for _token, account_id in members
                ]
            except Exception as exc:
                message = sanitize_account_ids(str(exc))
                return [
                    {
                        "account_id": account_id,
                        "error": {
                            "code": "PRIVATE_GROUP_FAILED",
                            "message": message,
                        },
                    }
                    for _token, account_id in members
                ]

        results_by_account = {
            str(item.get("account_id") or ""): dict(item)
            for item in await buy(ready)
        }
        retry_candidates = [
            (token, account_id)
            for token, account_id in accounts
            if account_id in initially_blocked
            or _connection_error(results_by_account.get(account_id, {}))
        ]
        final_blocked = dict(initially_blocked)
        if retry_candidates:
            retry_ready, retry_blocked = await immediate._ready_accounts(
                bot,
                retry_candidates,
                timeout=immediate.PRIVATE_RETRY_TIMEOUT_SECONDS,
                phase="retry",
            )
            final_blocked.update(retry_blocked)
            if retry_ready:
                bot.logger.warning(
                    "GROUP_PRIVATE_CONNECTION_RETRY signal_id=%s stake=%.2f "
                    "accounts=%s attempt=2",
                    str(getattr(signal, "signal_id", "") or ""),
                    stake,
                    len(retry_ready),
                )
                for item in await buy(retry_ready):
                    account_id = str(item.get("account_id") or "")
                    results_by_account[account_id] = dict(item)
                    if not item.get("error"):
                        final_blocked.pop(account_id, None)

        transactions: list[dict[str, Any]] = []
        for _token, account_id in accounts:
            item = dict(
                results_by_account.get(account_id)
                or final_blocked.get(account_id)
                or {
                    "account_id": account_id,
                    "error": {
                        "code": "PRIVATE_RESULT_MISSING",
                        "message": "No private purchase result was returned.",
                    },
                }
            )
            item.setdefault("account_id", account_id)
            item.setdefault("stake_amount", stake)
            item["execution_transport"] = "PRIVATE_WS"
            transactions.append(item)
        return transactions


async def _grouped_purchase_accounts_by_stake(
    self: RFDir5TradingBot,
    *,
    signal: Any,
    eligible_accounts: list[tuple[str, str]],
    stake_by_token: dict[str, float],
    pre_trade_profit_ratio: float = 0.0,
) -> list[dict[str, Any]]:
    """Dispatch identical account contracts as bounded provider groups.

    PAT accounts use the official bulk endpoint in shards of at most 100. OAuth
    accounts keep their required account-scoped WebSocket, but are bounded by
    stake group and concurrency. Every exception is converted into account-level
    outcomes; no transport failure changes the global worker state.
    """

    if not eligible_accounts:
        return []

    bulk_groups: dict[tuple[str, bool, float], list[tuple[str, str]]] = {}
    private_groups: dict[tuple[str, float], list[tuple[str, str]]] = {}
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
        if str(protection.get("mode") or "") == VIRTUAL_MODE:
            rejected.append(
                {
                    "account_id": account_id,
                    "execution_transport": "NONE",
                    "error": {
                        "code": "VIRTUAL_MODE",
                        "message": "Financial purchase blocked while virtual protection is active.",
                    },
                }
            )
            requested.append((token, account_id))
            continue

        environment = self._account_environment_for_token(token)
        if environment == "real" and not self._real_trading_allowed():
            message = "Real trading is disabled on this VPS"
            self._set_account_execution_status(managed_id, "real_disabled", message)
            rejected.append(
                {
                    "account_id": account_id,
                    "execution_transport": "NONE",
                    "error": {
                        "code": "REAL_DISABLED",
                        "message": message,
                    },
                }
            )
            requested.append((token, account_id))
            continue

        stake = round(float(stake_by_token[token]), 2)
        requested.append((token, account_id))
        if self._bulk_purchase_token_capable(token):
            martingale = bool(
                self.user_profiles.get(token, {}).get("martingale_enabled", True)
            )
            bulk_groups.setdefault(
                (environment, martingale, stake),
                [],
            ).append((token, account_id))
        else:
            private_groups.setdefault((environment, stake), []).append(
                (token, account_id)
            )

    bulk_tasks: list[Any] = []
    bulk_task_meta: list[tuple[str, float, int, list[tuple[str, str]]]] = []
    shard_index = 0
    for (environment, martingale, stake), accounts in sorted(
        bulk_groups.items(),
        key=lambda item: item[0],
    ):
        accounts.sort(
            key=lambda item: (
                self._managed_account_id_for_token(item[0]) or 2**63,
                item[1],
            )
        )
        for members in _chunks(accounts, BULK_SHARD_SIZE):
            shard_index += 1
            bulk_task_meta.append((environment, stake, shard_index, members))
            bulk_tasks.append(
                _dispatch_bulk_shard(
                    self,
                    signal=signal,
                    accounts=members,
                    stake=stake,
                    environment=environment,
                    martingale_enabled=martingale,
                    shard_index=shard_index,
                    pre_trade_profit_ratio=pre_trade_profit_ratio,
                )
            )

    private_tasks: list[Any] = []
    private_task_meta: list[tuple[str, float, list[tuple[str, str]]]] = []
    for (environment, stake), accounts in sorted(
        private_groups.items(),
        key=lambda item: item[0],
    ):
        accounts.sort(
            key=lambda item: (
                self._managed_account_id_for_token(item[0]) or 2**63,
                item[1],
            )
        )
        for members in _chunks(accounts, PRIVATE_GROUP_SIZE):
            private_task_meta.append((environment, stake, members))
            private_tasks.append(
                _dispatch_private_chunk(
                    self,
                    signal=signal,
                    accounts=members,
                    stake=stake,
                )
            )

    self.logger.warning(
        "GROUP_EXECUTION_PLAN signal_id=%s symbol=%s contract_type=%s barrier=%s "
        "accounts=%s bulk_accounts=%s bulk_shards=%s private_accounts=%s "
        "private_groups=%s bulk_shard_limit=%s global_stop_on_error=false",
        str(getattr(signal, "signal_id", "") or ""),
        str(getattr(signal, "symbol", "") or ""),
        str(getattr(signal, "contract_type", "") or ""),
        str(getattr(signal, "barrier", "") or ""),
        len(requested),
        sum(len(values) for values in bulk_groups.values()),
        len(bulk_tasks),
        sum(len(values) for values in private_groups.values()),
        len(private_tasks),
        BULK_SHARD_SIZE,
    )

    results = await asyncio.gather(
        *(bulk_tasks + private_tasks),
        return_exceptions=True,
    )
    transactions: list[dict[str, Any]] = list(rejected)
    combined_meta: list[tuple[str, float, list[tuple[str, str]]]] = [
        (environment, stake, members)
        for environment, stake, _shard, members in bulk_task_meta
    ] + private_task_meta
    for (environment, stake, members), result in zip(
        combined_meta,
        results,
        strict=True,
    ):
        if isinstance(result, Exception):
            message = sanitize_account_ids(str(result))
            result = [
                {
                    "account_id": account_id,
                    "stake_amount": stake,
                    "error": {
                        "code": "GROUP_TASK_FAILED",
                        "message": message,
                    },
                }
                for _token, account_id in members
            ]
        transactions.extend(dict(item) for item in result)

    _record_transport_outcomes(self, signal, requested, transactions)
    confirmed = sum(
        1
        for item in transactions
        if item.get("contract_id") and not item.get("error")
    )
    failed = len(transactions) - confirmed
    unknown = sum(1 for item in transactions if _outcome_unknown(item))
    self.logger.warning(
        "GROUP_EXECUTION_RESULT signal_id=%s confirmed=%s failed=%s "
        "outcome_unknown=%s global_execution_continues=true",
        str(getattr(signal, "signal_id", "") or ""),
        confirmed,
        failed,
        unknown,
    )
    return transactions


def _public_contract_cache_on_private_ready(
    self: RFDir5TradingBot,
    session: Any,
) -> None:
    """Use the one public contract cache; never query every account and market."""

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
        "authenticated_contract_requests=0",
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
        "private_requests=0",
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
    return str(contract_type or "").upper() in set(
        self.rf_supported_contracts.get(str(symbol), set())
    )


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
        "barrier=%s accounts=%s grouped_transport=true",
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
            "symbol=%s barrier=%s accounts=%s error=%s",
            parent_cycle_id,
            role,
            signal.symbol,
            barrier,
            len(scope),
            type(exc).__name__,
        )
        return role, f"exception_{type(exc).__name__}"
    return role, "submitted"


async def _concurrent_aidr_arbitrate(bot: RFDir5TradingBot) -> None:
    """Prepare all System roles together and dispatch each as an isolated task."""

    cfg = bot.test2_config.hybrid_strategy
    await asyncio.sleep(float(getattr(cfg, "candidate_window_ms", 75)) / 1000.0)
    queued = list(getattr(bot, "hybrid_digit_candidates", {}).values())
    bot.hybrid_digit_candidates.clear()
    if not queued:
        return

    async with standardized._cycle_gate(bot):
        bot._prune_stale_pending_contracts("scalable_aidr_pre_proposal")
        if continuation._cadence_blocked(bot, queued):
            return

        normal, recovery, post_virtual, virtual = aidr._account_recovery_groups(bot)
        scopes = {
            continuation.NORMAL_ROLE: set(normal),
            continuation.FIRST_RECOVERY_ROLE: set(recovery),
            continuation.POST_VIRTUAL_ROLE: set(post_virtual) | set(virtual),
        }
        if not any(scopes.values()):
            return

        # Only private-only OAuth accounts require connection readiness before a
        # buy. PAT members can be purchased in bulk without waiting for a socket.
        all_scope_ids = set().union(*scopes.values())
        for token, account_id in list(getattr(bot, "valid_clients", []) or []):
            managed_id = bot._managed_account_id_for_token(token)
            if (
                managed_id is not None
                and int(managed_id) in all_scope_ids
                and not bot._bulk_purchase_token_capable(token)
            ):
                immediate._ensure_session(bot, token, account_id)

        fresh = [
            candidate
            for candidate in queued
            if (
                getattr(bot, "market_states", {}).get(str(candidate.symbol)) is not None
                and int(bot.market_states[candidate.symbol].tick_sequence)
                == int(candidate.tick_sequence)
            )
        ]
        if not fresh:
            return

        trigger_results = await asyncio.gather(
            *(
                continuation._proposal_ok(
                    bot,
                    candidate,
                    continuation.AIDR_MINIMUM_LIVE_EDGE,
                )
                for candidate in fresh
            ),
            return_exceptions=True,
        )
        qualified: list[tuple[float, Any, Any]] = []
        for result in trigger_results:
            if isinstance(result, Exception) or result is None:
                continue
            signal, economics = result
            score = float(signal.validated_edge or 0.0) + 0.05 * float(
                signal.lower95 or 0.0
            )
            qualified.append((score, signal, economics))
        if not qualified:
            return

        qualified.sort(
            key=lambda item: (
                -float(item[0]),
                -float(getattr(item[1], "weighted_probability", 0.0) or 0.0),
                str(getattr(item[1], "symbol", "") or ""),
            )
        )
        _score, trigger_signal, _economics = qualified[0]
        symbol = str(trigger_signal.symbol)
        trigger_role = continuation._candidate_role(trigger_signal)
        parent_cycle_id = str(uuid.uuid4())

        role_results = await asyncio.gather(
            *(
                _role_proposal_with_retry(bot, role=role, symbol=symbol)
                for role in standardized.AIDR_EXECUTION_ORDER
                if scopes[role]
            ),
            return_exceptions=True,
        )
        active_roles = [
            role
            for role in standardized.AIDR_EXECUTION_ORDER
            if scopes[role]
        ]
        dispatch_tasks: list[Any] = []
        dispatch_roles: list[str] = []
        result_by_role: dict[str, str] = {}
        for role, result in zip(active_roles, role_results, strict=True):
            barrier, _recovery = standardized._role_spec(role)
            if isinstance(result, Exception):
                result_by_role[role] = f"proposal_exception_{type(result).__name__}"
            elif result is None:
                result_by_role[role] = "provider_proposal_unavailable"
            else:
                signal, economics = result
                dispatch_roles.append(role)
                dispatch_tasks.append(
                    _dispatch_aidr_role(
                        bot,
                        parent_cycle_id=parent_cycle_id,
                        role=role,
                        signal=signal,
                        economics=economics,
                        scope=scopes[role],
                    )
                )
                continue

            standardized.notify_scope_waiting(
                bot,
                scopes[role],
                strategy="system",
                role=role,
                contract=f"DIGITOVER {barrier}",
                reason_code=result_by_role[role],
                reason=(
                    "The role proposal was unavailable after a bounded immediate retry."
                ),
            )

        dispatched = await asyncio.gather(*dispatch_tasks, return_exceptions=True)
        for role, result in zip(dispatch_roles, dispatched, strict=True):
            if isinstance(result, Exception):
                result_by_role[role] = f"dispatch_exception_{type(result).__name__}"
            else:
                _returned_role, status = result
                result_by_role[role] = status

        if any(status == "submitted" for status in result_by_role.values()):
            bot.rf_last_purchase_monotonic = time.monotonic()

        for role in standardized.AIDR_EXECUTION_ORDER:
            if not scopes[role]:
                continue
            barrier, _recovery = standardized._role_spec(role)
            bot.logger.warning(
                "AIDR_ROLE_DISPATCH_RESULT parent_cycle_id=%s trigger_role=%s "
                "role=%s barrier=%s accounts=%s result=%s "
                "global_execution_continues=true",
                parent_cycle_id,
                trigger_role,
                role,
                barrier,
                len(scopes[role]),
                result_by_role.get(role, "not_attempted"),
            )

        bot.logger.warning(
            "AIDR_GROUPED_CYCLE_COMPLETE parent_cycle_id=%s symbol=%s "
            "trigger_role=%s role_results=%s normal_accounts=%s "
            "first_recovery_accounts=%s post_virtual_accounts=%s "
            "role_scope_context=task_local global_stop_on_role_error=false",
            parent_cycle_id,
            symbol,
            trigger_role,
            result_by_role,
            len(scopes[continuation.NORMAL_ROLE]),
            len(scopes[continuation.FIRST_RECOVERY_ROLE]),
            len(scopes[continuation.POST_VIRTUAL_ROLE]),
        )


async def _drain_aidr(bot: RFDir5TradingBot) -> None:
    while getattr(bot, "hybrid_digit_candidates", {}):
        await _concurrent_aidr_arbitrate(bot)
        await asyncio.sleep(0)


def install_scalable_group_execution() -> None:
    """Install public metadata caching, grouped transport and role isolation."""

    global _INSTALLED
    if _INSTALLED:
        return

    # contracts_for is public and account-independent. Never multiply those
    # requests by account count or reconnect count.
    RFDir5TradingBot._on_private_session_ready = _public_contract_cache_on_private_ready
    RFDir5TradingBot._validate_account_contracts = _public_only_account_contract_validation
    RFDir5TradingBot._account_supports_contract = _public_contract_support

    # Scope and recovery flags become task-local so System roles can be prepared
    # together without overwriting another role's account membership.
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

    # This final transport replaces the earlier private-only fan-out. It is still
    # account-isolated, but identical PAT contracts are sent through Deriv's bulk
    # endpoint and OAuth buys remain bounded private groups.
    RFDir5TradingBot._purchase_accounts_by_stake = _grouped_purchase_accounts_by_stake

    # Exact transport results replace the generic provider_confirmation_missing
    # message whenever the provider supplied a concrete account-level outcome.
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
                        "Deriv returned a contract ID, but the local Trade row was "
                        "not visible to the cycle receipt. Registration reconciliation "
                        "is required for this account only."
                    ),
                    True,
                )
            code = str(outcome.get("error_code") or "TRANSPORT_FAILED").strip().upper()
            message = sanitize_account_ids(
                str(outcome.get("error_message") or "Provider purchase failed")
            )
            if bool(outcome.get("outcome_unknown")):
                return (
                    "provider_outcome_unknown",
                    (
                        f"{message} Automatic replay was suppressed to prevent a "
                        "duplicate contract."
                    ),
                    True,
                )
            return (
                f"provider_{code.lower()}",
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

    # Make the grouped, task-isolated System cycle authoritative after the former
    # sequential immediate layer.
    standardized._standardized_aidr_arbitrate = _concurrent_aidr_arbitrate
    hybrid._arbitrate_digits = _drain_aidr
    continuation._recovery_aware_arbitrate = _drain_aidr

    RFDir5TradingBot._scalable_group_execution_installed = True
    _INSTALLED = True
    logging.getLogger(__name__).warning(
        "SCALABLE_GROUP_EXECUTION_INSTALLED version=%s bulk_shard_size=%s "
        "bulk_concurrency=%s private_group_size=%s private_concurrency=%s "
        "public_contract_cache=true task_local_role_scopes=true "
        "global_stop_on_account_error=false",
        SCALABLE_GROUP_EXECUTION_VERSION,
        BULK_SHARD_SIZE,
        BULK_CONCURRENCY,
        PRIVATE_GROUP_SIZE,
        PRIVATE_GROUP_CONCURRENCY,
    )
