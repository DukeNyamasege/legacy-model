from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from sqlalchemy import select

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.websocket_hot_path_hardening as hot
from app.models import AccountRiskState, RuntimePreference
from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import sanitize_account_ids


LOGGER = logging.getLogger(__name__)
_INSTALLED = False
SCALABILITY_VERSION = "batched-role-snapshot-v1"


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
        "private_websocket_only=true bulk_purchase=false copy_trading=false",
        SCALABILITY_VERSION,
    )
