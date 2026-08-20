from __future__ import annotations

"""Financial fence between browser-direct execution and the persistent worker.

This module is worker-only. It removes fresh browser-owned accounts from the
server Custom Strategy scanner, rechecks ownership immediately before the final
server purchase scope, explicitly wakes the worker when a browser lease ages out,
and prevents a takeover BUY while the last browser checkpoint still reports an
open provider contract.

A durable independent hard-stop sentinel is checked on every final financial
scope. A user Stop therefore forbids the next server BUY even if slower
ManagedAccount lifecycle cleanup is still waiting on another database transaction.
"""

import json
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

import app.custom_strategy_runtime as custom_runtime
import app.shared_system_strategy_clock as shared_clock
from app import custom_strategy_connection_stampede_guard as stampede
from app import private_websocket_rate_limit as private_ws
from app.account_mode_execution_lock import account_allows_new_execution
from app.direct_execution_hard_stop_state import direct_hard_stop_active
from app.direct_execution_lease import DIRECT_BROWSER_STATUS, direct_browser_lease_fresh
from app.models import ManagedAccount, RuntimePreference, utc_now
from app.rf_dir5_bot import RFDir5TradingBot

_INSTALLED = False
CACHE_SECONDS = 1.0
TAKEOVER_SCAN_SECONDS = 2.0
OPEN_CONTRACT_HANDOFF_GRACE_SECONDS = 300.0
OWNER_PREFIX = "direct_execution:v1:"
CHECKPOINT_PREFIX = "direct_execution:checkpoint:v1:"


def _json_payload(row: RuntimePreference | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        value = json.loads(str(row.preference_value or "{}"))
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _aware(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _browser_contract_handoff_hold(session: Any, managed_id: int) -> bool:
    owner = _json_payload(session.get(RuntimePreference, f"{OWNER_PREFIX}{int(managed_id)}"))
    checkpoint = _json_payload(
        session.get(RuntimePreference, f"{CHECKPOINT_PREFIX}{int(managed_id)}")
    )
    if not owner or not checkpoint:
        return False
    if str(owner.get("epoch") or "") != str(checkpoint.get("epoch") or ""):
        return False
    try:
        open_contracts = int(checkpoint.get("open_contracts") or 0)
    except (TypeError, ValueError):
        open_contracts = 0
    if open_contracts <= 0:
        return False
    checkpointed_at = _aware(checkpoint.get("checkpointed_at"))
    if checkpointed_at is None:
        return True
    age = (datetime.now(timezone.utc) - checkpointed_at).total_seconds()
    return age < OPEN_CONTRACT_HANDOFF_GRACE_SECONDS


def _eligible_map(bot: Any, managed_ids: set[int], *, force: bool = False) -> dict[int, bool]:
    ids = {int(value) for value in managed_ids}
    if not ids:
        return {}
    now = time.monotonic()
    cached_at = float(getattr(bot, "_direct_execution_fence_at", 0.0) or 0.0)
    cached = dict(getattr(bot, "_direct_execution_fence_cache", {}) or {})
    if not force and now - cached_at <= CACHE_SECONDS and ids.issubset(cached):
        return {managed_id: bool(cached.get(managed_id)) for managed_id in ids}

    with bot.repository.database.session() as session:
        rows = session.scalars(
            select(ManagedAccount).where(ManagedAccount.id.in_(sorted(ids)))
        ).all()
        values = {
            int(row.id): bool(
                not direct_hard_stop_active(session, int(row.id))
                and account_allows_new_execution(row)
                and not _browser_contract_handoff_hold(session, int(row.id))
            )
            for row in rows
        }
    for managed_id in ids:
        values.setdefault(managed_id, False)
    bot._direct_execution_fence_cache = values
    bot._direct_execution_fence_at = now
    return values


def _server_ids(bot: Any, managed_ids: set[int], *, force: bool = False) -> set[int]:
    values = _eligible_map(bot, managed_ids, force=force)
    return {managed_id for managed_id, allowed in values.items() if allowed}


def _promote_expired_browser_leases(bot: Any) -> list[int]:
    now_monotonic = time.monotonic()
    previous = float(getattr(bot, "_direct_takeover_scan_at", 0.0) or 0.0)
    if now_monotonic - previous < TAKEOVER_SCAN_SECONDS:
        return []
    bot._direct_takeover_scan_at = now_monotonic

    promoted: list[int] = []
    with bot.repository.database.session() as session:
        rows = session.scalars(
            select(ManagedAccount).where(
                ManagedAccount.enabled.is_(True),
                ManagedAccount.execution_status == DIRECT_BROWSER_STATUS,
            )
        ).all()
        for row in rows:
            if direct_hard_stop_active(session, int(row.id)):
                continue
            if direct_browser_lease_fresh(row):
                continue
            if _browser_contract_handoff_hold(session, int(row.id)):
                continue
            row.execution_status = "connecting"
            row.execution_status_reason = (
                "Browser heartbeat expired; VPS continuity worker is taking ownership"
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            promoted.append(int(row.id))
    if promoted:
        bot.logger.warning(
            "DIRECT_EXECUTION_OFFLINE_TAKEOVER accounts=%s browser_buy=false server_owner=true",
            sorted(promoted),
        )
    return promoted


async def _targeted_takeover(self: RFDir5TradingBot, promoted: list[int]) -> None:
    """Admit and urgently connect only browser leases that actually expired."""

    admitted: list[int] = []
    admit = getattr(self, "_admit_custom_runtime_account", None)
    for managed_id in promoted:
        try:
            token = admit(int(managed_id)) if callable(admit) else stampede._admit_one_runtime_account(self, int(managed_id))
        except Exception as exc:
            self.logger.warning(
                "DIRECT_EXECUTION_TARGETED_TAKEOVER_DEFERRED managed_id=%s stage=admit error_type=%s",
                int(managed_id),
                type(exc).__name__,
            )
            continue
        if token:
            admitted.append(int(managed_id))

    if admitted:
        # Existing healthy siblings are retained. Only newly admitted accounts get
        # a new ClientSession task; then each promoted account is explicitly woken.
        await self._ensure_sessions_for_valid_clients()
        for managed_id in admitted:
            session = stampede._private_session_for_account(self, int(managed_id))
            if session is not None and not bool(getattr(session, "is_connected", False)):
                private_ws.wake_private_connection(session)

    # Register local strategy/runtime state without provider-wide discovery.
    try:
        from app import custom_strategy_direct_runtime as direct_runtime

        direct_runtime._refresh_direct_accounts(
            self,
            require_connected=False,
            fail_invalid=False,
        )
    except Exception as exc:
        self.logger.warning(
            "DIRECT_EXECUTION_TARGETED_TAKEOVER_DEFERRED accounts=%s stage=runtime error_type=%s",
            sorted(admitted),
            type(exc).__name__,
        )

    self._managed_accounts_revision = self.repository.managed_accounts_revision()
    self._runtime_mode_cache = self.repository.runtime_mode()
    self.logger.warning(
        "DIRECT_EXECUTION_TARGETED_TAKEOVER_READY promoted=%s admitted=%s "
        "global_validation=false sibling_rebuild=false urgent_private_wake=true",
        sorted(promoted),
        sorted(admitted),
    )


def install_direct_execution_worker_fence() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_routes = custom_runtime._custom_routes
    original_exact_scope_buy = shared_clock._exact_scope_buy
    original_refresh = RFDir5TradingBot._refresh_runtime_accounts_if_needed

    def server_owned_custom_routes(bot: Any) -> list[Any]:
        routes = list(original_routes(bot) or [])
        if not routes:
            return routes
        ids = {int(route.managed_id) for route in routes}
        allowed = _server_ids(bot, ids, force=False)
        if len(allowed) == len(ids):
            return routes
        return [route for route in routes if int(route.managed_id) in allowed]

    async def refresh_with_direct_takeover(self: RFDir5TradingBot) -> None:
        promoted = _promote_expired_browser_leases(self)
        if promoted:
            await _targeted_takeover(self, promoted)
            return
        await original_refresh(self)

    async def fenced_exact_scope_buy(
        bot: Any,
        signal: Any,
        economics: Any,
        scope_ids: set[int],
        *,
        recovery_enabled: bool,
        virtual_protection_enabled: bool = True,
    ) -> None:
        requested = {int(value) for value in scope_ids}
        allowed = _server_ids(bot, requested, force=True)
        blocked = requested - allowed
        if blocked:
            bot.logger.info(
                "DIRECT_EXECUTION_SERVER_SCOPE_FENCED signal_id=%s blocked_ids=%s "
                "purchase=false reason=hard_stop_browser_owner_stopped_or_open_handoff",
                str(getattr(signal, "signal_id", "-")),
                sorted(blocked),
            )
        if not allowed:
            try:
                bot.repository.mark_signal(
                    str(getattr(signal, "signal_id", "")),
                    status="SKIP_DIRECT_BROWSER_OWNER",
                )
            except Exception:
                pass
            return
        await original_exact_scope_buy(
            bot,
            signal,
            economics,
            allowed,
            recovery_enabled=bool(recovery_enabled),
            virtual_protection_enabled=bool(virtual_protection_enabled),
        )

    custom_runtime._custom_routes = server_owned_custom_routes
    shared_clock._exact_scope_buy = fenced_exact_scope_buy
    RFDir5TradingBot._refresh_runtime_accounts_if_needed = refresh_with_direct_takeover
    RFDir5TradingBot._direct_execution_worker_fence_installed = True
    RFDir5TradingBot._direct_execution_hard_stop_fence = "uncached_final_pre_buy"
    RFDir5TradingBot._direct_execution_takeover = "targeted_urgent_no_global_validation"
    _INSTALLED = True

    # Final worker-side authorities. Older cap/fail-closed/quarantine/global-P&L
    # layers are captured underneath and cannot regain execution lifecycle control.
    from app.account_identity_canonical_authority import (
        install_account_identity_canonical_authority,
    )
    from app.account_trade_metrics_authority import (
        install_account_trade_metrics_authority,
    )
    from app.global_recovery_execution_policy import (
        install_global_recovery_execution_policy,
    )
    from app.never_auto_stop_repository_authority import (
        install_never_auto_stop_repository_authority,
    )
    from app.stale_split_basis_reconciliation_authority import (
        install_stale_split_basis_reconciliation_authority,
    )

    install_account_identity_canonical_authority()
    install_account_trade_metrics_authority()
    install_never_auto_stop_repository_authority()
    install_global_recovery_execution_policy()
    # Install absolutely last around the final global planner. It only repairs an
    # impossible stale basis before delegating; all sizing/lifecycle decisions stay
    # inside the global recovery authority.
    install_stale_split_basis_reconciliation_authority()
