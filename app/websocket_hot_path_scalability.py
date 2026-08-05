from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from sqlalchemy import select

import app.ai_digit_recovery_v1 as aidr
import app.guaranteed_signal_delivery as immediate
import app.aidr_loss_continuation_fix as continuation
import app.private_websocket_rate_limit as private_ws
import app.websocket_hot_path_hardening as hot
from app.models import AccountRiskState, RuntimePreference
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import ClientSession, mask_account_id, sanitize_account_ids


LOGGER = logging.getLogger(__name__)
_INSTALLED = False
SCALABILITY_VERSION = "batched-role-snapshot-v1"

_SETTLEMENT_LOCKS_GUARD = threading.Lock()
_SETTLEMENT_LOCKS: dict[tuple[int, str], threading.Lock] = {}


def _settlement_lock(owner: Any, state: Any) -> threading.Lock:
    with state.lock:
        pending = dict(state.pending or {})
    database = getattr(owner, "database", None)
    symbol = str(pending.get("symbol") or "global")
    key = (id(database), symbol)
    with _SETTLEMENT_LOCKS_GUARD:
        lock = _SETTLEMENT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SETTLEMENT_LOCKS[key] = lock
        return lock


def _install_serialized_settlement_drains() -> None:
    original_drain = hot._drain_coalesced

    def serialized_drain(
        owner: Any,
        state: Any,
        original: Any,
        method_name: str,
    ) -> None:
        # System-ledger, virtual and shadow settlement can touch related rows.
        # Keep those operations serialized per database and market while allowing
        # different markets to settle in parallel.
        with _settlement_lock(owner, state):
            original_drain(owner, state, original, method_name)

    hot._drain_coalesced = serialized_drain


def _unique_connected_private_sessions(bot: Any) -> list[ClientSession]:
    by_account: dict[str, ClientSession] = {}
    for session in list(getattr(bot, "sessions", {}).values()):
        account_id = str(getattr(session, "account_id", "") or "")
        if not account_id or not bool(getattr(session, "is_connected", False)):
            continue
        if getattr(session, "ws", None) is None:
            continue
        by_account.setdefault(account_id, session)
    sessions = sorted(
        by_account.values(),
        key=lambda session: (str(session.account_id), str(session.token_tag)),
    )
    if not sessions:
        return []
    offset = int(getattr(bot, "_proposal_fallback_offset", 0) or 0) % len(sessions)
    bot._proposal_fallback_offset = offset + hot.PROPOSAL_FALLBACK_SESSION_COUNT
    rotated = sessions[offset:] + sessions[:offset]
    return rotated[: hot.PROPOSAL_FALLBACK_SESSION_COUNT]


def _install_canonical_private_sessions() -> None:
    original_connect_and_run = ClientSession.connect_and_run
    original_ensure_session = immediate._ensure_session

    async def canonical_connect_and_run(self: ClientSession) -> None:
        canonical = getattr(self.bot, "_private_session_by_account_id", None)
        if not isinstance(canonical, dict):
            canonical = {}
            self.bot._private_session_by_account_id = canonical
        account_key = str(self.account_id)
        existing = canonical.get(account_key)
        if existing is not None and existing is not self:
            self.bot.sessions[self.token] = existing
            private_ws.wake_private_connection(existing)
            self.bot.logger.warning(
                "DUPLICATE_PRIVATE_SESSION_COALESCED account=%s "
                "new_connection_opened=false canonical_token=%s duplicate_token=%s",
                mask_account_id(account_key),
                str(existing.token_tag),
                str(self.token_tag),
            )
            return
        canonical[account_key] = self
        await original_connect_and_run(self)

    def canonical_ensure_session(
        bot: RFDir5TradingBot,
        token: str,
        account_id: str,
    ) -> ClientSession:
        canonical = getattr(bot, "_private_session_by_account_id", None)
        if not isinstance(canonical, dict):
            canonical = {}
            bot._private_session_by_account_id = canonical
        existing = canonical.get(str(account_id))
        if existing is not None:
            bot.sessions[token] = existing
            if existing.task is None or existing.task.done():
                existing.task = asyncio.create_task(
                    existing.connect_and_run(),
                    name=f"private_session_{mask_account_id(account_id)}",
                )
            private_ws.wake_private_connection(existing)
            return existing
        session = original_ensure_session(bot, token, account_id)
        canonical[str(account_id)] = session
        return session

    ClientSession.connect_and_run = canonical_connect_and_run
    immediate._ensure_session = canonical_ensure_session
    hot._connected_private_sessions = _unique_connected_private_sessions


def _load_account_recovery_groups_batched(
    bot: RFDir5TradingBot,
) -> tuple[set[int], set[int], set[int], set[int]]:
    """Load every account role with two SQL queries, never one per account."""

    accounts = aidr._enabled_accounts(bot)
    managed_ids = {managed_id for _token, _account, managed_id in accounts}
    if not managed_ids:
        return set(), set(), set(), set()

    split_keys = {aidr._split_key(managed_id): managed_id for managed_id in managed_ids}
    with bot.repository.database.session() as session:
        states = session.scalars(
            select(AccountRiskState).where(
                AccountRiskState.managed_account_id.in_(sorted(managed_ids))
            )
        ).all()
        preference_rows = session.scalars(
            select(RuntimePreference).where(
                RuntimePreference.preference_key.in_(sorted(split_keys))
            )
        ).all()

    state_by_id = {int(row.managed_account_id): row for row in states}
    post_virtual_ids: set[int] = set()
    for row in preference_rows:
        managed_id = split_keys.get(str(row.preference_key))
        if managed_id is None:
            continue
        try:
            if int(str(row.preference_value or "0")) > 0:
                post_virtual_ids.add(int(managed_id))
        except (TypeError, ValueError):
            continue

    normal: set[int] = set()
    initial_recovery: set[int] = set()
    post_virtual_recovery: set[int] = set()
    virtual: set[int] = set()
    for _token, _account, managed_id in accounts:
        state = state_by_id.get(int(managed_id))
        if state is None or (
            float(state.recovery_loss_debt or 0.0) <= 0.009
            and not state.recovery_pending
            and not state.recovery_attempt_active
            and state.protection_mode
            not in {aidr.VIRTUAL_WAITING_FOR_WIN, aidr.REAL_RECOVERY_PENDING}
        ):
            normal.add(int(managed_id))
            continue
        if state.protection_mode == aidr.VIRTUAL_WAITING_FOR_WIN:
            virtual.add(int(managed_id))
        elif int(managed_id) in post_virtual_ids:
            post_virtual_recovery.add(int(managed_id))
        else:
            initial_recovery.add(int(managed_id))
    return normal, initial_recovery, post_virtual_recovery, virtual


def _schedule_batched_group_cache_refresh(bot: RFDir5TradingBot) -> None:
    task = getattr(bot, "_aidr_group_cache_refresh_task", None)
    if isinstance(task, asyncio.Task) and not task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def refresh() -> None:
        try:
            groups = await asyncio.to_thread(_load_account_recovery_groups_batched, bot)
            bot._aidr_group_cache = tuple(set(group) for group in groups)
            bot._aidr_group_cache_updated_at = time.monotonic()
            bot.logger.info(
                "AIDR_ACCOUNT_GROUP_CACHE_REFRESHED normal=%s recovery_over3=%s "
                "recovery_over4=%s virtual=%s sql_queries=2",
                len(groups[0]),
                len(groups[1]),
                len(groups[2]),
                len(groups[3]),
            )
        except Exception as exc:
            bot.logger.warning(
                "AIDR_ACCOUNT_GROUP_CACHE_REFRESH_FAILED error_type=%s error=%s",
                type(exc).__name__,
                sanitize_account_ids(str(exc)),
            )

    bot._aidr_group_cache_refresh_task = loop.create_task(
        refresh(),
        name="aidr_account_group_cache_refresh_batched",
    )


def _install_background_model_training() -> None:
    original_train = RFDir5TradingBot._train_market_models

    def schedule_training(
        bot: RFDir5TradingBot,
        symbol: str,
        quotes: list[Any],
    ) -> None:
        lock = getattr(bot, "_hot_model_training_lock", None)
        if lock is None:
            lock = threading.Lock()
            bot._hot_model_training_lock = lock
            bot._hot_model_training_pending = {}
            bot._hot_model_training_running = False
        with lock:
            pending = getattr(bot, "_hot_model_training_pending", {})
            pending[str(symbol)] = list(quotes)
            bot._hot_model_training_pending = pending
            if bool(getattr(bot, "_hot_model_training_running", False)):
                return
            bot._hot_model_training_running = True

        def drain() -> None:
            while True:
                with lock:
                    pending = getattr(bot, "_hot_model_training_pending", {})
                    if not pending:
                        bot._hot_model_training_running = False
                        return
                    next_symbol, next_quotes = pending.popitem()
                try:
                    original_train(bot, next_symbol, next_quotes)
                except Exception as exc:  # pragma: no cover - defensive runtime path
                    bot.logger.warning(
                        "RF_MODEL_TRAINING_BACKGROUND_FAILED symbol=%s "
                        "error_type=%s error=%s",
                        next_symbol,
                        type(exc).__name__,
                        sanitize_account_ids(str(exc)),
                    )

        hot._HOT_EXECUTOR.submit(drain)

    RFDir5TradingBot._train_market_models = schedule_training


def install_websocket_hot_path_scalability() -> None:
    """Remove account-count and model-training work from the event loop."""

    global _INSTALLED
    if _INSTALLED:
        return

    _install_serialized_settlement_drains()
    _install_canonical_private_sessions()
    # Existing ready-session and stale-cache wrappers resolve this module global
    # dynamically, so replacing the scheduler upgrades every call without adding
    # another private-session wrapper.
    hot._schedule_group_cache_refresh = _schedule_batched_group_cache_refresh
    aidr._account_recovery_groups = hot._cached_account_recovery_groups
    continuation._account_recovery_groups = hot._cached_account_recovery_groups
    _install_background_model_training()

    RFDir5TradingBot._websocket_hot_path_scalability_installed = True
    _INSTALLED = True
    LOGGER.warning(
        "WEBSOCKET_HOT_PATH_SCALABILITY_INSTALLED version=%s "
        "account_role_sql_queries=2 n_plus_one=false model_training_off_loop=true "
        "duplicate_account_sessions=false settlement_rows_serialized=true "
        "private_websocket_only=true bulk_purchase=false copy_trading=false",
        SCALABILITY_VERSION,
    )
