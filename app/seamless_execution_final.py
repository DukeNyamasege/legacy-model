from __future__ import annotations

import json
import logging
import time
from typing import Any

import app.ai_digit_recovery_v1 as aidr
import app.aidr_strict_recovery_guard as strict_guard
import app.multi_strategy_runtime as multi
from app.models import (
    AccountRiskState,
    ManagedAccount,
    RuntimePreference,
    Trade,
    VirtualTrade,
    utc_now,
)
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy_v2_preferences import write_strategy
from enhanced_bot import TradingBot, sanitize_account_ids
from sqlalchemy import select


_INSTALLED = False
LOGGER = logging.getLogger(__name__)
VERSION = "seamless-execution-final-v1"
PENDING_STRATEGY_PREFIX = "pending_strategy:v1:"
SETTLEMENT_ONLY_STATUS = "settlement_only"
_PLACEHOLDER_LOGGED: set[int] = set()


def _settlement_ids(bot: TradingBot) -> set[int]:
    result: set[int] = set()
    for profile in list(getattr(bot, "user_profiles", {}).values()):
        if not bool(profile.get("settlement_only")):
            continue
        value = profile.get("managed_account_id")
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _token_is_settlement_only(bot: TradingBot, token: str) -> bool:
    return bool(
        getattr(bot, "user_profiles", {}).get(token, {}).get("settlement_only")
    )


def _open_counts(session: Any, managed_id: int) -> tuple[int, int]:
    actual = len(
        session.scalars(
            select(Trade.id).where(
                Trade.managed_account_id == int(managed_id),
                Trade.settlement_time.is_(None),
            )
        ).all()
    )
    virtual = len(
        session.scalars(
            select(VirtualTrade.id).where(
                VirtualTrade.managed_account_id == int(managed_id),
                VirtualTrade.result == "OPEN",
            )
        ).all()
    )
    return actual, virtual


def _reset_strategy_state(session: Any, managed_id: int) -> None:
    state = session.get(AccountRiskState, int(managed_id), with_for_update=True)
    if state is not None:
        state.trading_day = ""
        state.daily_start_balance = 0.0
        state.session_profit = 0.0
        state.consecutive_losses = 0
        state.recovery_loss_debt = 0.0
        state.recovery_pending = False
        state.recovery_attempt_active = False
        state.protection_mode = "NORMAL_MODE"
        state.virtual_observation_count = 0
        state.virtual_win_count = 0
        state.virtual_loss_count = 0
        state.current_virtual_loss_streak = 0
        state.entered_virtual_mode_at = None
        state.recovery_pending_since = None
        state.equity_high_water = 0.0
        state.updated_at = utc_now()

    prefixes = (
        f"aidr_split_remaining:{managed_id}",
        f"aidr_adaptive_trap:{managed_id}",
        f"aidr_over1_over3_v1:account_epoch:{managed_id}",
        f"hybrid_over2_put_v4:account_epoch:{managed_id}",
        f"hybrid_o2u7_put_v1:account_epoch:{managed_id}",
    )
    for preference in session.scalars(select(RuntimePreference)).all():
        key = str(preference.preference_key or "")
        if any(key.startswith(prefix) for prefix in prefixes):
            session.delete(preference)


def _apply_pending_strategies(bot: RFDir5TradingBot) -> int:
    applied = 0
    with bot.repository.database.session() as session:
        pending_rows = list(
            session.scalars(
                select(RuntimePreference).where(
                    RuntimePreference.preference_key.like(
                        f"{PENDING_STRATEGY_PREFIX}%"
                    )
                )
            ).all()
        )
        for preference in pending_rows:
            suffix = str(preference.preference_key).removeprefix(
                PENDING_STRATEGY_PREFIX
            )
            if not suffix.isdigit():
                session.delete(preference)
                continue
            managed_id = int(suffix)
            actual, virtual = _open_counts(session, managed_id)
            if actual or virtual:
                continue
            try:
                payload = json.loads(str(preference.preference_value or ""))
                if not isinstance(payload, dict):
                    raise ValueError("pending strategy must be an object")
                _reset_strategy_state(session, managed_id)
                selection = write_strategy(
                    session,
                    managed_id,
                    family=payload.get("family"),
                    side=payload.get("side"),
                    prediction=payload.get("prediction"),
                )
            except Exception as exc:
                LOGGER.error(
                    "PENDING_STRATEGY_INVALID managed_id=%s error_type=%s error=%s",
                    managed_id,
                    type(exc).__name__,
                    sanitize_account_ids(str(exc)),
                )
                session.delete(preference)
                continue

            row = session.get(ManagedAccount, managed_id, with_for_update=True)
            if row is not None:
                status = str(row.execution_status or "inactive").strip().lower()
                if bool(row.enabled) and status != SETTLEMENT_ONLY_STATUS:
                    row.execution_status = "connecting"
                row.execution_status_reason = (
                    f"Queued strategy activated: {selection.to_dict()['label']}. "
                    "The next qualifying cycle starts from base state."
                )[:160]
                row.execution_status_updated_at = utc_now()
                row.updated_at = utc_now()
            session.delete(preference)
            applied += 1
            LOGGER.warning(
                "PENDING_STRATEGY_ACTIVATED managed_id=%s family=%s side=%s "
                "prediction=%s open_contracts=0 recovery_reset=true",
                managed_id,
                selection.family,
                selection.side,
                selection.prediction,
            )
    if applied:
        bot._multi_strategy_snapshot = []
        bot._multi_strategy_snapshot_at = 0.0
    return applied


def _finalize_settlement_only(bot: TradingBot) -> int:
    finalized: list[int] = []
    with bot.repository.database.session() as session:
        rows = list(
            session.scalars(
                select(ManagedAccount).where(
                    ManagedAccount.execution_status == SETTLEMENT_ONLY_STATUS
                )
            ).all()
        )
        for row in rows:
            actual, virtual = _open_counts(session, int(row.id))
            if actual or virtual:
                continue
            row.enabled = False
            row.execution_status = "stopped"
            row.execution_status_reason = (
                "Existing contracts settled. Account remains stopped; Start is "
                "required for new base-stake execution."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            finalized.append(int(row.id))

    if finalized:
        wanted = set(finalized)
        bot.valid_clients = [
            item
            for item in list(getattr(bot, "valid_clients", []) or [])
            if bot._managed_account_id_for_token(item[0]) not in wanted
        ]
        LOGGER.warning(
            "SETTLEMENT_ONLY_FINALIZED accounts=%s new_entries=false next_start=base",
            len(finalized),
        )
    return len(finalized)


def _install_runtime_account_annotations() -> None:
    current = TradingBot._load_runtime_accounts
    if getattr(current, "_seamless_final_annotations", False):
        return

    def annotated_loader(self: TradingBot):
        tokens, profiles = current(self)
        managed_ids = {
            int(profile.get("managed_account_id"))
            for profile in profiles.values()
            if str(profile.get("managed_account_id") or "").isdigit()
        }
        statuses: dict[int, str] = {}
        if managed_ids:
            with self.repository.database.session() as session:
                for row in session.scalars(
                    select(ManagedAccount).where(
                        ManagedAccount.id.in_(sorted(managed_ids))
                    )
                ).all():
                    statuses[int(row.id)] = str(
                        row.execution_status or "inactive"
                    ).strip().lower()
        for profile in profiles.values():
            try:
                managed_id = int(profile.get("managed_account_id"))
            except (TypeError, ValueError):
                continue
            profile["settlement_only"] = (
                statuses.get(managed_id) == SETTLEMENT_ONLY_STATUS
            )
        return tokens, profiles

    annotated_loader._seamless_final_annotations = True
    TradingBot._load_runtime_accounts = annotated_loader


def _install_settlement_status_guard() -> None:
    current = TradingBot._set_account_execution_status
    if getattr(current, "_seamless_settlement_guard", False):
        return

    def guarded_status(
        self: TradingBot,
        managed_account_id: int | None,
        status: str,
        reason: str = "",
    ) -> None:
        if managed_account_id not in {None, ""}:
            with self.repository.database.session() as session:
                row = session.get(ManagedAccount, int(managed_account_id))
                current_status = str(
                    row.execution_status or ""
                ).strip().lower() if row is not None else ""
            if current_status == SETTLEMENT_ONLY_STATUS and str(status).lower() in {
                "validating",
                "connecting",
                "active",
                "running",
                "reconnecting",
            }:
                return
        return current(self, managed_account_id, status, reason)

    guarded_status._seamless_settlement_guard = True
    TradingBot._set_account_execution_status = guarded_status


def _install_financial_scope_filter() -> None:
    current_eligible = RFDir5TradingBot._eligible_purchase_accounts
    if not getattr(current_eligible, "_seamless_settlement_filter", False):
        def eligible_without_settlement(self: RFDir5TradingBot):
            return [
                (token, account_id)
                for token, account_id in current_eligible(self)
                if not _token_is_settlement_only(self, token)
            ]

        eligible_without_settlement._seamless_settlement_filter = True
        RFDir5TradingBot._eligible_purchase_accounts = eligible_without_settlement

    current_all = multi._all_eligible
    if not getattr(current_all, "_seamless_settlement_filter", False):
        def all_without_settlement(bot: RFDir5TradingBot):
            return [
                (token, account_id)
                for token, account_id in current_all(bot)
                if not _token_is_settlement_only(bot, token)
            ]

        all_without_settlement._seamless_settlement_filter = True
        multi._all_eligible = all_without_settlement

    current_groups = aidr._account_recovery_groups
    if not getattr(current_groups, "_seamless_settlement_filter", False):
        def groups_without_settlement(bot: RFDir5TradingBot):
            groups = current_groups(bot)
            excluded = _settlement_ids(bot)
            return tuple(set(group) - excluded for group in groups)

        groups_without_settlement._seamless_settlement_filter = True
        aidr._account_recovery_groups = groups_without_settlement


def _install_settlement_finalizer() -> None:
    current = TradingBot._finish_contract_transport_cleanup
    if getattr(current, "_seamless_settlement_finalizer", False):
        return

    async def cleanup_and_finalize(
        self: TradingBot,
        token: str,
        contract_id: int,
        *,
        refresh_balance: bool = True,
    ) -> None:
        await current(
            self,
            token,
            contract_id,
            refresh_balance=refresh_balance,
        )
        _finalize_settlement_only(self)

    cleanup_and_finalize._seamless_settlement_finalizer = True
    TradingBot._finish_contract_transport_cleanup = cleanup_and_finalize


def _install_pending_strategy_activation() -> None:
    current = multi._strategy_snapshot
    if getattr(current, "_seamless_pending_activation", False):
        return

    def snapshot_after_pending(
        bot: RFDir5TradingBot,
        *,
        force: bool = False,
    ):
        _finalize_settlement_only(bot)
        _apply_pending_strategies(bot)
        return current(bot, force=force)

    snapshot_after_pending._seamless_pending_activation = True
    multi._strategy_snapshot = snapshot_after_pending


def _install_placeholder_contract_filter() -> None:
    current = Test2Repository.unresolved_contracts
    if getattr(current, "_seamless_placeholder_filter", False):
        return

    def unresolved_without_placeholders(self: Test2Repository):
        rows = list(current(self))
        retained = []
        for row in rows:
            try:
                contract_id = int(row.contract_id)
            except (TypeError, ValueError):
                retained.append(row)
                continue
            if 0 < contract_id < 1_000_000:
                if contract_id not in _PLACEHOLDER_LOGGED:
                    _PLACEHOLDER_LOGGED.add(contract_id)
                    LOGGER.warning(
                        "LEGACY_PLACEHOLDER_CONTRACT_IGNORED contract_id=%s "
                        "financial_impact=0 manual_reconciliation=false",
                        contract_id,
                    )
                continue
            retained.append(row)
        return retained

    unresolved_without_placeholders._seamless_placeholder_filter = True
    Test2Repository.unresolved_contracts = unresolved_without_placeholders


def install_final_seamless_execution_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # A settlement-only row is logically stopped even while its private socket is
    # retained to receive the final provider settlement.
    strict_guard.STOPPED_STATUSES.add(SETTLEMENT_ONLY_STATUS)

    _install_runtime_account_annotations()
    _install_settlement_status_guard()
    _install_financial_scope_filter()
    _install_settlement_finalizer()
    _install_pending_strategy_activation()
    _install_placeholder_contract_filter()

    RFDir5TradingBot._final_seamless_execution_runtime_installed = True
    RFDir5TradingBot._final_seamless_execution_runtime_version = VERSION
    _INSTALLED = True
    LOGGER.warning(
        "FINAL_SEAMLESS_EXECUTION_RUNTIME_INSTALLED version=%s "
        "stop_base_reset=true settlement_socket_retained=true "
        "settlement_financial_scope=false queued_strategy_activation=true "
        "market_persistence=true placeholder_contracts_quarantined=true",
        VERSION,
    )
