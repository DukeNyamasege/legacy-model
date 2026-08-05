from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

import enhanced_bot
import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.guaranteed_signal_delivery as immediate
import app.scalable_group_execution as grouped
import app.shared_system_strategy_clock as shared_clock
import app.standardized_execution_runtime as standardized
from app.models import RuntimePreference
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import TradingBot, sanitize_account_ids
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError


_INSTALLED = False
LOGGER = logging.getLogger(__name__)
VERSION = "seamless-execution-runtime-v1"
MARKET_PREFERENCE_PREFIX = "personal_execution_market:"
_VOLATILE_BATCHES: dict[str, float] = {}
_VOLATILE_LOCK = threading.Lock()
_MARKET_CACHE: dict[int, tuple[float, str]] = {}
_MARKET_CACHE_TTL_SECONDS = 1.0


def _is_bulk_path(path: str) -> bool:
    return "/trading/v1/options/contracts/bulk-purchase/" in str(path or "").lower()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _account_id(item: dict[str, Any]) -> str:
    for key in ("account_id", "loginid", "login_id", "account"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    for key in ("transaction", "buy", "contract", "data", "result"):
        nested = item.get(key)
        if isinstance(nested, dict):
            value = _account_id(nested)
            if value:
                return value
    return ""


def _flatten_member(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    nested: dict[str, Any] = {}
    for key in ("transaction", "buy", "contract", "result"):
        value = result.get(key)
        if isinstance(value, dict):
            nested.update(value)
    data = result.get("data")
    if isinstance(data, dict):
        nested.update(data)
    if nested:
        combined = dict(nested)
        combined.update(
            {
                key: value
                for key, value in result.items()
                if key not in {"transaction", "buy", "contract", "result", "data"}
            }
        )
        result = combined

    account_id = _account_id(result)
    if account_id:
        result["account_id"] = account_id

    if not result.get("contract_id"):
        for key in ("contractId", "contractid", "id"):
            value = result.get(key)
            if value not in {None, ""}:
                result["contract_id"] = value
                break

    if not result.get("transaction_id"):
        for key in ("transactionId", "transactionid", "buy_transaction_id"):
            value = result.get(key)
            if value not in {None, ""}:
                result["transaction_id"] = value
                break
    return result


def _mapping_members(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    known = {
        "transactions",
        "results",
        "contracts",
        "purchases",
        "items",
        "errors",
        "failures",
        "meta",
    }
    if any(key in value for key in known):
        return []
    members: list[dict[str, Any]] = []
    for account_id, item in value.items():
        if not isinstance(item, dict):
            continue
        member = dict(item)
        member.setdefault("account_id", str(account_id))
        members.append(member)
    return members


def _member_lists(response: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = response.get("data")
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    containers = [response]
    if isinstance(data, dict):
        containers.append(data)

    if isinstance(data, list):
        for item in _list(data):
            if isinstance(item.get("error"), dict) or (
                item.get("code") and item.get("message") and not item.get("contract_id")
            ):
                failures.append(item)
            else:
                successes.append(item)

    for container in containers:
        for key in ("transactions", "results", "contracts", "purchases", "items"):
            for item in _list(container.get(key)):
                if isinstance(item.get("error"), dict):
                    failures.append(item)
                else:
                    successes.append(item)
        for key in ("errors", "failures"):
            failures.extend(_list(container.get(key)))

    if not successes and isinstance(data, dict):
        successes.extend(_mapping_members(data))

    return (
        [_flatten_member(item) for item in successes],
        [_flatten_member(item) for item in failures],
    )


def _normalize_error(item: dict[str, Any]) -> dict[str, Any]:
    nested = item.get("error")
    error = dict(nested) if isinstance(nested, dict) else {}
    code = (
        error.get("code")
        or item.get("code")
        or item.get("error_code")
        or item.get("status")
        or "BULK_MEMBER_FAILED"
    )
    message = (
        error.get("message")
        or item.get("message")
        or item.get("error_message")
        or "Deriv rejected this account's bulk purchase"
    )
    return {
        "account_id": _account_id(item),
        "code": str(code),
        "message": sanitize_account_ids(str(message)),
        **({"status": item.get("status")} if item.get("status") is not None else {}),
    }


def _normalize_bulk_response(
    response: dict[str, Any],
    request_body: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(response, dict) or isinstance(response.get("error"), dict):
        return response

    requested = [
        str(item.get("account_id") or "").strip()
        for item in list((request_body or {}).get("accounts") or [])
        if isinstance(item, dict) and str(item.get("account_id") or "").strip()
    ]
    successes, failures = _member_lists(response)

    if successes and all(not _account_id(item) for item in successes):
        if len(successes) == len(requested):
            for account_id, item in zip(requested, successes, strict=True):
                item["account_id"] = account_id

    normalized_errors = [_normalize_error(item) for item in failures]
    if normalized_errors and all(not item.get("account_id") for item in normalized_errors):
        if not successes and len(normalized_errors) == len(requested):
            for account_id, item in zip(requested, normalized_errors, strict=True):
                item["account_id"] = account_id
        elif not successes:
            first = normalized_errors[0]
            return {
                **response,
                "error": {
                    "code": first["code"],
                    "message": first["message"],
                },
            }

    normalized_successes: list[dict[str, Any]] = []
    for item in successes:
        flattened = _flatten_member(item)
        if flattened.get("contract_id"):
            normalized_successes.append(flattened)
        elif isinstance(flattened.get("error"), dict):
            normalized_errors.append(_normalize_error(flattened))
        elif flattened.get("code") and flattened.get("message"):
            normalized_errors.append(_normalize_error(flattened))
        else:
            normalized_successes.append(flattened)

    LOGGER.warning(
        "REST_BULK_RESPONSE_NORMALIZED requested=%s transactions=%s errors=%s "
        "top_level_keys=%s data_type=%s credentials_logged=false",
        len(requested),
        len(normalized_successes),
        len(normalized_errors),
        sorted(str(key) for key in response.keys()),
        type(response.get("data")).__name__,
    )
    return {
        **response,
        "data": {
            **(_dict(response.get("data")) if isinstance(response.get("data"), dict) else {}),
            "transactions": normalized_successes,
        },
        "errors": normalized_errors,
    }


def _sql_diagnostics(exc: ProgrammingError) -> tuple[str, str, str]:
    original = getattr(exc, "orig", None)
    sqlstate = str(
        getattr(original, "sqlstate", None)
        or getattr(original, "pgcode", None)
        or ""
    )
    diag = getattr(original, "diag", None)
    constraint = str(getattr(diag, "constraint_name", None) or "")
    message = sanitize_account_ids(str(original or exc))
    return sqlstate, constraint, message


def _remember_volatile(batch_id: str) -> None:
    cutoff = time.monotonic() - 3600.0
    with _VOLATILE_LOCK:
        for value, created in list(_VOLATILE_BATCHES.items()):
            if created < cutoff:
                _VOLATILE_BATCHES.pop(value, None)
        _VOLATILE_BATCHES[batch_id] = time.monotonic()


def _is_volatile(batch_id: Any) -> bool:
    with _VOLATILE_LOCK:
        return str(batch_id or "") in _VOLATILE_BATCHES


def _install_bulk_audit_degradation() -> None:
    if getattr(Test2Repository, "_seamless_bulk_audit_installed", False):
        return

    original_create = Test2Repository.create_bulk_execution_batch
    original_complete = Test2Repository.complete_bulk_execution_batch

    def create_batch_or_continue(
        self: Test2Repository,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        try:
            return str(original_create(self, *args, **kwargs))
        except ProgrammingError as exc:
            batch_id = f"volatile-{uuid.uuid4()}"
            _remember_volatile(batch_id)
            sqlstate, constraint, message = _sql_diagnostics(exc)
            LOGGER.exception(
                "BULK_AUDIT_SCHEMA_DEGRADED batch_id=%s sqlstate=%s constraint=%s "
                "error=%s financial_execution_continues=true duplicate_retry=false",
                batch_id,
                sqlstate or "-",
                constraint or "-",
                message,
            )
            return batch_id

    def complete_batch_or_continue(
        self: Test2Repository,
        batch_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if _is_volatile(batch_id):
            LOGGER.warning(
                "BULK_AUDIT_COMPLETION_SKIPPED batch_id=%s reason=volatile_schema_fallback "
                "contract_registration_continues=true",
                batch_id,
            )
            return None
        return original_complete(self, batch_id, *args, **kwargs)

    Test2Repository.create_bulk_execution_batch = create_batch_or_continue
    Test2Repository.complete_bulk_execution_batch = complete_batch_or_continue
    Test2Repository._seamless_bulk_audit_installed = True


def _install_purchase_result_cleanup() -> None:
    current = RFDir5TradingBot._purchase_stake_group_for_environment
    if getattr(current, "_seamless_result_cleanup", False):
        return

    async def purchase_with_optional_audit(
        self: TradingBot,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        results = await current(self, **kwargs)
        cleaned: list[dict[str, Any]] = []
        for raw in list(results or []):
            item = dict(raw)
            if _is_volatile(item.get("bulk_batch_id")):
                item["bulk_batch_id"] = None
                item["bulk_audit_degraded"] = True
            cleaned.append(item)
        return cleaned

    purchase_with_optional_audit._seamless_result_cleanup = True
    TradingBot._purchase_stake_group_for_environment = purchase_with_optional_audit
    RFDir5TradingBot._purchase_stake_group_for_environment = purchase_with_optional_audit


def _install_bulk_response_normalizer() -> None:
    current = enhanced_bot._rest_request
    if getattr(current, "_seamless_bulk_response_normalizer", False):
        return

    async def normalized_request(
        method: str,
        path: str,
        app_id: str,
        base_url: str,
        token: str | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await current(
            method,
            path,
            app_id,
            base_url,
            token=token,
            json_data=json_data,
        )
        if str(method).upper() != "POST" or not _is_bulk_path(path):
            return response
        return _normalize_bulk_response(response, json_data)

    normalized_request._seamless_bulk_response_normalizer = True
    enhanced_bot._rest_request = normalized_request


def _install_exact_dispatch_logging() -> None:
    if getattr(grouped._dispatch_aidr_role, "_seamless_exact_logging", False):
        return

    async def exact_dispatch(
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
            original = getattr(exc, "orig", None)
            sqlstate = str(
                getattr(original, "sqlstate", None)
                or getattr(original, "pgcode", None)
                or ""
            )
            message = sanitize_account_ids(str(original or exc))
            bot.logger.exception(
                "AIDR_ROLE_DISPATCH_FAILED parent_cycle_id=%s role=%s symbol=%s "
                "barrier=%s accounts=%s error_type=%s sqlstate=%s error=%s "
                "global_execution_continues=true",
                parent_cycle_id,
                role,
                signal.symbol,
                barrier,
                len(scope),
                type(exc).__name__,
                sqlstate or "-",
                message,
            )
            return role, f"exception_{type(exc).__name__}"
        return role, "submitted"

    exact_dispatch._seamless_exact_logging = True
    grouped._dispatch_aidr_role = exact_dispatch


def _market_preferences(bot: RFDir5TradingBot, managed_ids: set[int]) -> dict[int, str]:
    now = time.monotonic()
    missing = [
        int(value)
        for value in managed_ids
        if int(value) not in _MARKET_CACHE
        or _MARKET_CACHE[int(value)][0] < now
    ]
    if missing:
        keys = [f"{MARKET_PREFERENCE_PREFIX}{value}" for value in missing]
        loaded: dict[str, str] = {}
        with bot.repository.database.session() as session:
            rows = session.scalars(
                select(RuntimePreference).where(RuntimePreference.preference_key.in_(keys))
            ).all()
            loaded = {
                str(row.preference_key): str(row.preference_value or "ALL").strip().upper()
                for row in rows
            }
        for managed_id in missing:
            key = f"{MARKET_PREFERENCE_PREFIX}{managed_id}"
            _MARKET_CACHE[managed_id] = (
                now + _MARKET_CACHE_TTL_SECONDS,
                loaded.get(key, "ALL") or "ALL",
            )
    return {
        int(value): _MARKET_CACHE.get(int(value), (now, "ALL"))[1]
        for value in managed_ids
    }


def _install_market_scope_filter() -> None:
    current = shared_clock._shared_clock_buy_for_scope
    if getattr(current, "_seamless_market_filter", False):
        return

    async def market_scoped_buy(
        bot: RFDir5TradingBot,
        source: Any,
        source_economics: Any,
        managed_ids: set[int],
        *,
        recovery_enabled: bool,
    ) -> None:
        symbol = str(getattr(source, "symbol", "") or "").upper()
        preferences = _market_preferences(bot, {int(value) for value in managed_ids})
        scoped = {
            int(value)
            for value in managed_ids
            if preferences.get(int(value), "ALL") in {"", "ALL", "AUTO", symbol}
        }
        if not scoped:
            return
        await current(
            bot,
            source,
            source_economics,
            scoped,
            recovery_enabled=recovery_enabled,
        )

    market_scoped_buy._seamless_market_filter = True
    shared_clock._shared_clock_buy_for_scope = market_scoped_buy


def install_seamless_execution_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    _install_bulk_audit_degradation()
    _install_purchase_result_cleanup()
    _install_bulk_response_normalizer()
    _install_exact_dispatch_logging()
    _install_market_scope_filter()

    RFDir5TradingBot._seamless_execution_runtime_installed = True
    RFDir5TradingBot._seamless_execution_runtime_version = VERSION
    _INSTALLED = True
    LOGGER.warning(
        "SEAMLESS_EXECUTION_RUNTIME_INSTALLED version=%s "
        "bulk_response_normalized=true audit_schema_nonblocking=true "
        "exact_dispatch_errors=true stop_rejoin_state_preserved=true "
        "account_market_filter=true financial_retry=false",
        VERSION,
    )
