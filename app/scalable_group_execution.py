from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from typing import Any

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.guaranteed_signal_delivery as immediate
import app.standardized_execution_runtime as standardized
from app.repositories.rf_dir5_repository import RFDir5Repository, VIRTUAL_MODE
from enhanced_bot import TradingBot, mask_account_id, sanitize_account_ids
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
SCALABLE_GROUP_EXECUTION_VERSION = "rest-bulk-purchase-v1"

# Keep the already-implemented Deriv New API REST bulk-purchase transport from
# enhanced_bot.py. This module must never replace it with per-account buy loops.
_BASE_REST_BULK_PURCHASE = TradingBot._purchase_accounts_by_stake
TRANSPORT_OUTCOME_TTL_SECONDS = max(
    60.0,
    float(__import__("os").getenv("TRANSPORT_OUTCOME_TTL_SECONDS", "600")),
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
    return code in {"HTTP_502", "BULK_UPSTREAM_UNAVAILABLE", "TIMEOUT"} or (
        "upstream dependency" in message or "timed out" in message
    )


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
            "transport": "REST_BULK_PURCHASE",
            "bulk_batch_id": item.get("bulk_batch_id"),
            "contract_id": item.get("contract_id"),
            "transaction_id": item.get("transaction_id"),
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


async def _grouped_purchase_accounts_by_stake(
    self: RFDir5TradingBot,
    *,
    signal: Any,
    eligible_accounts: list[tuple[str, str]],
    stake_by_token: dict[str, float],
    pre_trade_profit_ratio: float = 0.0,
) -> list[dict[str, Any]]:
    """Final financial transport: Deriv New API REST bulk purchase.

    Public WebSockets still analyse ticks and existing private sockets may still
    monitor settlement. Contract opening no longer fans out per-account buys over
    many private WebSocket sessions; accounts are grouped by uniform contract
    parameters and submitted through the official bulk-purchase endpoint.
    """

    if not eligible_accounts:
        return []

    requested = list(eligible_accounts)
    rejected: list[dict[str, Any]] = []
    purchasable: list[tuple[str, str]] = []

    for token, account_id in requested:
        managed_id = self._managed_account_id_for_token(token)
        stake = round(float(stake_by_token.get(token, 0.0) or 0.0), 2)
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
                    "stake_amount": stake,
                    "execution_transport": "REST_BULK_PURCHASE",
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
                    "stake_amount": stake,
                    "execution_transport": "REST_BULK_PURCHASE",
                    "error": {"code": "REAL_DISABLED", "message": message},
                }
            )
            continue

        if not self._bulk_purchase_token_capable(token):
            message = (
                "Link your Deriv Personal Access Token with trade scope in "
                "Settings > Credentials to enable bulk purchase trading."
            )
            self._set_account_execution_status(
                managed_id,
                "bulk_execution_pat_required",
                message,
            )
            rejected.append(
                {
                    "account_id": account_id,
                    "stake_amount": stake,
                    "execution_transport": "REST_BULK_PURCHASE",
                    "error": {"code": "PAT_REQUIRED", "message": message},
                }
            )
            continue

        purchasable.append((token, account_id))

    if not purchasable:
        _record_transport_outcomes(self, signal, requested, rejected)
        self.logger.warning(
            "REST_BULK_PURCHASE_SKIPPED signal_id=%s rejected=%s reason=no_pat_ready_accounts",
            str(getattr(signal, "signal_id", "") or ""),
            len(rejected),
        )
        return rejected

    self.logger.warning(
        "REST_BULK_PURCHASE_PLAN signal_id=%s symbol=%s contract_type=%s "
        "barrier=%s accounts=%s rejected=%s transport=REST_BULK_PURCHASE "
        "private_websocket_buy=false max_accounts_per_request=100 "
        "global_stop_on_account_error=false",
        str(getattr(signal, "signal_id", "") or ""),
        str(getattr(signal, "symbol", "") or ""),
        str(getattr(signal, "contract_type", "") or ""),
        str(getattr(signal, "barrier", "") or ""),
        len(purchasable),
        len(rejected),
    )

    try:
        transactions = await _BASE_REST_BULK_PURCHASE(
            self,
            signal=signal,
            eligible_accounts=purchasable,
            stake_by_token=stake_by_token,
            pre_trade_profit_ratio=pre_trade_profit_ratio,
        )
    except Exception as exc:
        message = sanitize_account_ids(str(exc))
        transactions = [
            {
                "account_id": account_id,
                "stake_amount": round(float(stake_by_token.get(token, 0.0) or 0.0), 2),
                "execution_transport": "REST_BULK_PURCHASE",
                "error": {"code": "REST_BULK_PURCHASE_FAILED", "message": message},
            }
            for token, account_id in purchasable
        ]
        self.logger.exception(
            "REST_BULK_PURCHASE_FAILED signal_id=%s accounts=%s error=%s",
            str(getattr(signal, "signal_id", "") or ""),
            len(purchasable),
            message,
        )

    normalized: list[dict[str, Any]] = []
    for item in list(rejected) + [dict(value) for value in transactions]:
        item.setdefault("execution_transport", "REST_BULK_PURCHASE")
        normalized.append(item)

    _record_transport_outcomes(self, signal, requested, normalized)
    confirmed = sum(
        1
        for item in normalized
        if item.get("contract_id") and not item.get("error")
    )
    unknown = sum(1 for item in normalized if _outcome_unknown(item))
    self.logger.warning(
        "REST_BULK_PURCHASE_RESULT signal_id=%s confirmed=%s failed=%s "
        "outcome_unknown=%s global_execution_continues=true",
        str(getattr(signal, "signal_id", "") or ""),
        confirmed,
        len(normalized) - confirmed,
        unknown,
    )
    return normalized


def _public_contract_cache_on_private_ready(
    self: RFDir5TradingBot,
    session: Any,
) -> None:
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
            import asyncio

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
        "barrier=%s accounts=%s transport=REST_BULK_PURCHASE",
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
    """Install scalable REST-bulk execution, public metadata caching and isolation."""

    global _INSTALLED
    if _INSTALLED:
        return

    RFDir5TradingBot._on_private_session_ready = _public_contract_cache_on_private_ready
    RFDir5TradingBot._validate_account_contracts = _public_only_account_contract_validation
    RFDir5TradingBot._account_supports_contract = _public_contract_support

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

    # Final financial transport authority: Deriv REST bulk-purchase. The old
    # PRIVATE_WS grouping is intentionally disabled to remove multi-connection
    # purchase interruptions. Public WS remains for ticks and private WS may only
    # be used after purchase for settlement reconciliation.
    RFDir5TradingBot._purchase_accounts_by_stake = _grouped_purchase_accounts_by_stake

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
                        "Deriv REST bulk purchase returned a contract ID, but "
                        "the local Trade row was not visible to the cycle receipt. "
                        "Registration reconciliation is active for this account only."
                    ),
                    True,
                )
            code = str(outcome.get("error_code") or "REST_BULK_PURCHASE_FAILED").upper()
            message = sanitize_account_ids(
                str(outcome.get("error_message") or "Deriv REST bulk purchase failed")
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
                f"rest_bulk_{code.lower()}",
                message,
                code not in {
                    "INVALID_TOKEN",
                    "UNAUTHORIZED",
                    "ACCESS_DENIED",
                    "REAL_DISABLED",
                    "INSUFFICIENT_BALANCE",
                    "PAT_REQUIRED",
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
        "SCALABLE_GROUP_EXECUTION_INSTALLED version=%s "
        "transport=REST_BULK_PURCHASE private_websocket_buy=false "
        "bulk_purchase=true copy_trading=false public_contract_cache=true "
        "task_local_role_scopes=true global_stop_on_account_error=false",
        SCALABLE_GROUP_EXECUTION_VERSION,
    )
