from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import app.scalable_group_execution as scalable_group_execution
import enhanced_bot
from app.deriv.http import mask_app_id
from app.repositories.rf_dir5_repository import VIRTUAL_MODE
from enhanced_bot import (
    CandidateSignal,
    TradingBot,
    is_permanent_credential_error,
    mask_account_id,
    normalize_account_type,
    optional_epoch_datetime,
    sanitize_account_ids,
    token_tag,
)


_INSTALLED = False
REST_BULK_PARTITIONING_VERSION = "strategy-contract-markup-v1"
REQUIRED_APP_MARKUP_PERCENTAGE = 3.0
MAX_BULK_ACCOUNTS_PER_REQUEST = 100
API_TOKEN_MESSAGE = (
    "Please link your Deriv API token with trade scope in Settings > Credentials. "
    "How to get it: open Deriv, go to Security & limits, open API token, create "
    "a token with trade permission, then paste it here."
)


@dataclass(frozen=True, slots=True)
class BulkPartitionKey:
    account_type: str
    family: str
    side: str
    role: str
    symbol: str
    contract_type: str
    barrier: str
    duration: int
    duration_unit: str
    stake: float
    martingale_enabled: bool

    @property
    def strategy_label(self) -> str:
        return f"{self.family}/{self.side}/{self.role}".strip("/")

    @property
    def contract_label(self) -> str:
        barrier = str(self.barrier or "").strip()
        return f"{self.contract_type} {barrier}".strip()


def _normalized_barrier(value: Any) -> str:
    rendered = str(value or "").strip()
    return rendered if rendered.lower() not in {"none", "null"} else ""


def _route_for_signal(bot: TradingBot, signal: CandidateSignal) -> Any | None:
    routes = getattr(bot, "_multi_strategy_signal_routes", {})
    return routes.get(str(getattr(signal, "signal_id", "") or ""))


def _system_role_from_signal(signal: CandidateSignal) -> str:
    barrier = _normalized_barrier(getattr(signal, "barrier", ""))
    if barrier == "1":
        return "normal"
    if barrier == "3":
        return "first_recovery"
    if barrier == "4":
        return "post_virtual"
    return "system"


def _partition_key(
    bot: TradingBot,
    signal: CandidateSignal,
    *,
    token: str,
    stake: float,
) -> BulkPartitionKey:
    route = _route_for_signal(bot, signal)
    if route is not None:
        family = str(getattr(route, "family", "manual") or "manual")
        side = str(getattr(route, "side", "") or "").lower()
        role = str(getattr(route, "role", "normal") or "normal")
    else:
        family = "system"
        side = "over"
        role = _system_role_from_signal(signal)
    return BulkPartitionKey(
        account_type=normalize_account_type(
            bot._account_environment_for_token(token),
            getattr(bot, "environment", "demo"),
        ),
        family=family,
        side=side,
        role=role,
        symbol=str(getattr(signal, "symbol", "") or ""),
        contract_type=str(getattr(signal, "contract_type", "") or "").upper(),
        barrier=_normalized_barrier(getattr(signal, "barrier", "")),
        duration=int(getattr(bot, "duration", 1) or 1),
        duration_unit=str(getattr(bot, "duration_unit", "t") or "t"),
        stake=round(float(stake), 2),
        martingale_enabled=bool(
            getattr(bot, "user_profiles", {}).get(token, {}).get(
                "martingale_enabled",
                True,
            )
        ),
    )


def _markup_configured(bot: TradingBot) -> bool:
    try:
        configured = float(getattr(bot, "app_markup_percentage", 0.0) or 0.0)
    except (TypeError, ValueError):
        configured = 0.0
    return abs(configured - REQUIRED_APP_MARKUP_PERCENTAGE) <= 1e-9


def _markup_error_results(
    bot: TradingBot,
    eligible_accounts: list[tuple[str, str]],
    stake_by_token: dict[str, float],
) -> list[dict[str, Any]]:
    configured = float(getattr(bot, "app_markup_percentage", 0.0) or 0.0)
    message = (
        f"Deriv App markup must be {REQUIRED_APP_MARKUP_PERCENTAGE:.2f}% before "
        "financial execution. Update the registered Deriv app markup and VPS config."
    )
    for token, account_id in eligible_accounts:
        bot._set_account_execution_status(
            bot._managed_account_id_for_token(token),
            "markup_required",
            message,
        )
        bot.logger.error(
            "APP_MARKUP_REQUIRED account=%s configured=%.2f required=%.2f "
            "app_id=%s bulk_purchase_blocked=true",
            mask_account_id(account_id),
            configured,
            REQUIRED_APP_MARKUP_PERCENTAGE,
            mask_app_id(getattr(bot, "app_id", "")),
        )
    return [
        {
            "account_id": account_id,
            "stake_amount": round(float(stake_by_token.get(token, 0.0) or 0.0), 2),
            "execution_transport": "REST_BULK_PURCHASE",
            "error": {"code": "APP_MARKUP_NOT_CONFIGURED", "message": message},
        }
        for token, account_id in eligible_accounts
    ]


def _virtual_rejection(
    bot: TradingBot,
    *,
    managed_id: int | None,
    account_id: str,
    stake: float,
) -> dict[str, Any] | None:
    if managed_id is None:
        return None
    protection = bot.rf_repository.virtual_protection_for_account(
        managed_account_id=managed_id,
        account_id_masked=mask_account_id(account_id),
    )
    if str(protection.get("mode") or "") != VIRTUAL_MODE:
        return None
    return {
        "account_id": account_id,
        "stake_amount": stake,
        "execution_transport": "REST_BULK_PURCHASE",
        "error": {
            "code": "VIRTUAL_MODE",
            "message": "Financial purchase blocked while virtual protection is active.",
        },
    }


async def _partitioned_purchase_accounts_by_stake(
    self: TradingBot,
    *,
    signal: CandidateSignal,
    eligible_accounts: list[tuple[str, str]],
    stake_by_token: dict[str, float],
    pre_trade_profit_ratio: float = 0.0,
) -> list[dict[str, Any]]:
    requested = list(eligible_accounts)
    if not requested:
        return []
    if not _markup_configured(self):
        results = _markup_error_results(self, requested, stake_by_token)
        scalable_group_execution._record_transport_outcomes(self, signal, requested, results)
        return results

    partitions: dict[BulkPartitionKey, list[tuple[str, str]]] = {}
    rejected: list[dict[str, Any]] = []
    for token, account_id in requested:
        managed_id = self._managed_account_id_for_token(token)
        stake = round(float(stake_by_token.get(token, 0.0) or 0.0), 2)
        virtual = _virtual_rejection(
            self,
            managed_id=managed_id,
            account_id=account_id,
            stake=stake,
        )
        if virtual is not None:
            rejected.append(virtual)
            continue
        if not self._bulk_purchase_token_capable(token):
            self._set_account_execution_status(
                managed_id,
                "bulk_execution_pat_required",
                API_TOKEN_MESSAGE,
            )
            rejected.append(
                {
                    "account_id": account_id,
                    "stake_amount": stake,
                    "execution_transport": "REST_BULK_PURCHASE",
                    "error": {"code": "API_TOKEN_REQUIRED", "message": API_TOKEN_MESSAGE},
                }
            )
            continue
        key = _partition_key(self, signal, token=token, stake=stake)
        partitions.setdefault(key, []).append((token, account_id))

    shards: list[tuple[BulkPartitionKey, int, list[tuple[str, str]]]] = []
    for key, accounts in sorted(
        partitions.items(),
        key=lambda item: (
            item[0].account_type,
            item[0].family,
            item[0].side,
            item[0].role,
            item[0].symbol,
            item[0].contract_type,
            item[0].barrier,
            item[0].stake,
        ),
    ):
        accounts.sort(
            key=lambda item: (self._managed_account_id_for_token(item[0]) or 2**63, item[1])
        )
        for offset in range(0, len(accounts), MAX_BULK_ACCOUNTS_PER_REQUEST):
            shards.append(
                (
                    key,
                    offset // MAX_BULK_ACCOUNTS_PER_REQUEST + 1,
                    accounts[offset : offset + MAX_BULK_ACCOUNTS_PER_REQUEST],
                )
            )
        self.logger.warning(
            "REST_BULK_PARTITION_READY signal_id=%s account_type=%s strategy_group=%s "
            "symbol=%s contract=%s stake=%.2f accounts=%s shards=%s "
            "same_contract_per_request=true markup_required=%.2f markup_configured=%.2f",
            str(getattr(signal, "signal_id", "") or ""),
            key.account_type,
            key.strategy_label,
            key.symbol,
            key.contract_label,
            key.stake,
            len(accounts),
            (len(accounts) + MAX_BULK_ACCOUNTS_PER_REQUEST - 1)
            // MAX_BULK_ACCOUNTS_PER_REQUEST,
            REQUIRED_APP_MARKUP_PERCENTAGE,
            float(getattr(self, "app_markup_percentage", 0.0) or 0.0),
        )

    tasks = [
        self._purchase_stake_group_for_environment(
            signal=signal,
            eligible_accounts=accounts,
            stake_amount=key.stake,
            environment=key.account_type,
            martingale_enabled=key.martingale_enabled,
            shard_index=shard_index,
            pre_trade_profit_ratio=pre_trade_profit_ratio,
            partition_key=key,
        )
        for key, shard_index, accounts in shards
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    transactions: list[dict[str, Any]] = list(rejected)
    for (key, _shard_index, accounts), result in zip(shards, results, strict=True):
        if isinstance(result, Exception):
            message = sanitize_account_ids(str(result))
            self.logger.error(
                "REST_BULK_PARTITION_FAILED signal_id=%s strategy_group=%s contract=%s "
                "stake=%.2f accounts=%s error=%s no_retry=true",
                str(getattr(signal, "signal_id", "") or ""),
                key.strategy_label,
                key.contract_label,
                key.stake,
                len(accounts),
                message,
            )
            result = [
                {
                    "account_id": account_id,
                    "execution_transport": "REST_BULK_PURCHASE",
                    "error": {"code": "BULK_PARTITION_FAILED", "message": message},
                }
                for _token, account_id in accounts
            ]
        for transaction in result:
            transaction["stake_amount"] = key.stake
            transaction["strategy_group"] = key.strategy_label
            transaction["partition_contract"] = key.contract_label
            transaction["partition_account_type"] = key.account_type
            transaction.setdefault("execution_transport", "REST_BULK_PURCHASE")
            transactions.append(transaction)

    scalable_group_execution._record_transport_outcomes(self, signal, requested, transactions)
    confirmed = sum(1 for item in transactions if item.get("contract_id") and not item.get("error"))
    self.logger.warning(
        "PURCHASE_EXECUTION_SUMMARY signal_id=%s partitions=%s confirmed=%s failed=%s "
        "markup_percentage=%.2f transport=REST_BULK_PURCHASE",
        str(getattr(signal, "signal_id", "") or ""),
        len(shards),
        confirmed,
        len(transactions) - confirmed,
        REQUIRED_APP_MARKUP_PERCENTAGE,
    )
    return transactions


async def _partitioned_purchase_stake_group_for_environment(
    self: TradingBot,
    *,
    signal: CandidateSignal,
    eligible_accounts: list[tuple[str, str]],
    stake_amount: float,
    environment: str,
    martingale_enabled: bool = True,
    shard_index: int = 1,
    pre_trade_profit_ratio: float = 0.0,
    partition_key: BulkPartitionKey | None = None,
) -> list[dict[str, Any]]:
    if len(eligible_accounts) > MAX_BULK_ACCOUNTS_PER_REQUEST:
        raise ValueError("Deriv bulk purchase shards cannot exceed 100 accounts")
    environment = normalize_account_type(environment, getattr(self, "environment", "demo"))
    if partition_key is None:
        partition_key = _partition_key(
            self,
            signal,
            token=eligible_accounts[0][0],
            stake=round(float(stake_amount), 2),
        )
    if environment == "real" and not self._real_trading_allowed():
        message = "Real trading is disabled on this VPS"
        for token, account_id in eligible_accounts:
            self._set_account_execution_status(
                self._managed_account_id_for_token(token),
                "real_disabled",
                message,
            )
            self.logger.warning(
                "REAL_PURCHASE_SKIPPED account=%s strategy_group=%s reason=%s",
                mask_account_id(account_id),
                partition_key.strategy_label,
                message,
                extra={"token_tag": token_tag(token)},
            )
        return [
            {
                "account_id": account_id,
                "execution_transport": "REST_BULK_PURCHASE",
                "error": {"code": "REAL_DISABLED", "message": message},
            }
            for _token, account_id in eligible_accounts
        ]

    bulk_path = f"/trading/v1/options/contracts/bulk-purchase/{environment}"
    request_started_at = datetime.now(timezone.utc)
    ordered_accounts = sorted(
        eligible_accounts,
        key=lambda item: (self._managed_account_id_for_token(item[0]) or 2**63, item[1]),
    )
    leader_token, leader_account = ordered_accounts[0]
    leader_id = self._managed_account_id_for_token(leader_token)
    members = [
        {
            "managed_account_id": self._managed_account_id_for_token(token),
            "account_id_masked": mask_account_id(account_id),
            "strategy_group": partition_key.strategy_label,
        }
        for token, account_id in ordered_accounts
    ]
    if any(member["managed_account_id"] is None for member in members):
        raise ValueError("Every bulk member must have a managed account identity")

    request_metadata = {
        "endpoint": bulk_path,
        "symbol": partition_key.symbol,
        "contract_type": partition_key.contract_type,
        "barrier": partition_key.barrier,
        "duration": partition_key.duration,
        "duration_unit": partition_key.duration_unit,
        "strategy_group": partition_key.strategy_label,
        "family": partition_key.family,
        "side": partition_key.side,
        "role": partition_key.role,
        "partition_account_type": partition_key.account_type,
        "same_contract_per_request": True,
        "max_accounts_per_request": MAX_BULK_ACCOUNTS_PER_REQUEST,
        "app_markup_percentage": REQUIRED_APP_MARKUP_PERCENTAGE,
        "markup_source": "registered_deriv_app_id",
    }
    batch_id = self.repository.create_bulk_execution_batch(
        signal_id=signal.signal_id,
        account_type=environment,
        martingale_enabled=martingale_enabled,
        stake=stake_amount,
        shard_index=shard_index,
        leader_managed_account_id=leader_id,
        pre_trade_profit_ratio=pre_trade_profit_ratio,
        members=members,
        request_metadata=request_metadata,
        request_started_at=request_started_at,
    )
    self.logger.warning(
        "BULK_MASTER_CONTEXT batch_id=%s leader=%s leader_managed_id=%s "
        "account_type=%s strategy_group=%s reason=first_active_member_in_partition",
        batch_id,
        mask_account_id(leader_account),
        leader_id,
        environment,
        partition_key.strategy_label,
    )
    self.logger.warning(
        "PURCHASE_EXECUTION_REQUEST batch_id=%s endpoint=%s app_id=%s account_count=%s "
        "strategy_group=%s symbol=%s contract_type=%s barrier=%s stake=%.2f "
        "markup_percentage=%.2f markup_source=registered_deriv_app_id "
        "accounts=%s pat_fingerprints=%s transport=REST_BULK_PURCHASE",
        batch_id,
        bulk_path,
        mask_app_id(self.app_id),
        len(ordered_accounts),
        partition_key.strategy_label,
        partition_key.symbol,
        partition_key.contract_type,
        partition_key.barrier,
        stake_amount,
        REQUIRED_APP_MARKUP_PERCENTAGE,
        [mask_account_id(account_id) for _token, account_id in ordered_accounts],
        [token_tag(token) for token, _account_id in ordered_accounts],
    )

    sent_monotonic = time.monotonic()
    response = await enhanced_bot._rest_request(
        "POST",
        bulk_path,
        self.app_id,
        self.rest_base_url,
        token=None,
        json_data={
            "contract_parameters": self._contract_parameters(
                signal,
                stake_amount,
                symbol_key="underlying_symbol",
            ),
            "accounts": [
                {
                    "token": self._credential_for_token(token),
                    "account_id": account_id,
                }
                for token, account_id in ordered_accounts
            ],
        },
    )
    received_at = datetime.now(timezone.utc)
    latency_ms = (time.monotonic() - sent_monotonic) * 1000.0
    if "error" in response:
        message = sanitize_account_ids(
            response["error"].get("message", "Bulk purchase request failed")
        )
        permanent = is_permanent_credential_error(response["error"])
        for token, _account_id in ordered_accounts:
            self._set_account_execution_status(
                self._managed_account_id_for_token(token),
                "credential_error" if permanent else "error",
                message,
            )
        results = [
            {
                "account_id": account_id,
                "managed_account_id": self._managed_account_id_for_token(token),
                "bulk_batch_id": batch_id,
                "strategy_group": partition_key.strategy_label,
                "execution_transport": "REST_BULK_PURCHASE",
                "error": {
                    "code": response["error"].get("code", "BULK_REQUEST_FAILED"),
                    "message": message,
                },
            }
            for token, account_id in ordered_accounts
        ]
        self.repository.complete_bulk_execution_batch(
            batch_id,
            response_received_at=received_at,
            latency_ms=latency_ms,
            results=results,
        )
        self.logger.error(
            "PURCHASE_EXECUTION_RESULT batch_id=%s strategy_group=%s success=0 failed=%s "
            "latency_ms=%.2f request_level_error=true",
            batch_id,
            partition_key.strategy_label,
            len(results),
            latency_ms,
        )
        return results

    transactions = list(response.get("data", {}).get("transactions", []))
    errors = list(response.get("errors") or [])
    transaction_by_account = {str(item.get("account_id") or ""): item for item in transactions}
    error_by_account = {str(item.get("account_id") or ""): item for item in errors}
    results: list[dict[str, Any]] = []
    for token, account_id in ordered_accounts:
        item = dict(transaction_by_account.get(account_id) or {})
        error = item.get("error") or error_by_account.get(account_id)
        item.update(
            {
                "account_id": account_id,
                "managed_account_id": self._managed_account_id_for_token(token),
                "bulk_batch_id": batch_id,
                "strategy_group": partition_key.strategy_label,
                "partition_contract": partition_key.contract_label,
                "partition_account_type": partition_key.account_type,
                "execution_transport": "REST_BULK_PURCHASE",
            }
        )
        item["purchase_timestamp"] = optional_epoch_datetime(item.get("purchase_time"))
        if not item.get("contract_id"):
            item["error"] = error or {
                "code": "BULK_MEMBER_MISSING",
                "message": "Deriv returned no transaction for this account",
            }
            permanent = is_permanent_credential_error(item["error"])
            self._set_account_execution_status(
                self._managed_account_id_for_token(token),
                "credential_error" if permanent else "error",
                sanitize_account_ids(str(item["error"].get("message", "Bulk purchase failed"))),
            )
            self.logger.error(
                "PURCHASE_EXECUTION_MEMBER_FAILED batch_id=%s account=%s strategy_group=%s "
                "code=%s reason=%s",
                batch_id,
                mask_account_id(account_id),
                partition_key.strategy_label,
                item["error"].get("code", "unknown"),
                sanitize_account_ids(str(item["error"].get("message", "Bulk purchase failed"))),
            )
        else:
            self._set_account_execution_status(
                self._managed_account_id_for_token(token),
                "active",
                "Bulk contract purchased successfully",
            )
        results.append(item)

    successful_transactions = [item for item in results if item.get("contract_id") and not item.get("error")]
    self.repository.complete_bulk_execution_batch(
        batch_id,
        response_received_at=received_at,
        latency_ms=latency_ms,
        results=results,
    )
    self.logger.warning(
        "PURCHASE_EXECUTION_RESULT batch_id=%s strategy_group=%s symbol=%s contract_type=%s "
        "barrier=%s stake=%.2f success=%s failed=%s latency_ms=%.2f "
        "markup_percentage=%.2f contract_ids=%s",
        batch_id,
        partition_key.strategy_label,
        partition_key.symbol,
        partition_key.contract_type,
        partition_key.barrier,
        stake_amount,
        len(successful_transactions),
        len(results) - len(successful_transactions),
        latency_ms,
        REQUIRED_APP_MARKUP_PERCENTAGE,
        [str(transaction.get("contract_id")) for transaction in successful_transactions],
    )
    return results


def install_rest_bulk_partitioning() -> None:
    """Partition REST bulk purchases by strategy, contract, stake and account mode."""

    global _INSTALLED
    already_final = (
        getattr(TradingBot._purchase_accounts_by_stake, "__name__", "")
        == "_partitioned_purchase_accounts_by_stake"
    )
    TradingBot._purchase_accounts_by_stake = _partitioned_purchase_accounts_by_stake
    TradingBot._purchase_stake_group_for_environment = _partitioned_purchase_stake_group_for_environment
    scalable_group_execution._BASE_REST_BULK_PURCHASE = _partitioned_purchase_accounts_by_stake
    if _INSTALLED and already_final:
        return
    _INSTALLED = True
    logging.getLogger(__name__).warning(
        "REST_BULK_PARTITIONING_INSTALLED version=%s group_by=strategy,side,role,symbol,contract,barrier,account_type,stake "
        "max_accounts_per_request=%s required_app_markup_percentage=%.2f token_language=api_token",
        REST_BULK_PARTITIONING_VERSION,
        MAX_BULK_ACCOUNTS_PER_REQUEST,
        REQUIRED_APP_MARKUP_PERCENTAGE,
    )
