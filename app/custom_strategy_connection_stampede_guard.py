from __future__ import annotations

import asyncio
import time
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app import custom_strategy_instant_start as instant
from app import execution_stop_reason_authority as stop_reason
from app import private_websocket_rate_limit as private_ws
from app import seamless_execution_recovery as seamless
from app.account_mode_execution_lock import (
    STARTING_LIKE_STATUSES,
    account_allows_new_execution,
    account_lifecycle_from_row,
)
from app.account_scoped_websocket_runtime import _promote_embedded_oauth_payload
from app.rf_dir5_bot import RFDir5TradingBot
from app.token_store import decrypt_auth_payload
from enhanced_bot import normalize_account_type, runtime_account_key


_INSTALLED = False
_ORIGINAL_REFRESH: Any = None

_WATCHDOG_INTERVAL_SECONDS = 2.0
_MISSING_GRACE_SECONDS = 4.0
_REPAIR_INTERVAL_SECONDS = 15.0
_RECONNECT_LOG_INTERVAL_SECONDS = 60.0
_OTP_BOOTSTRAP_TIMEOUT_SECONDS = 15.0


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _managed_row(bot: RFDir5TradingBot, managed_id: int) -> Any | None:
    for row in bot.repository.list_managed_accounts():
        try:
            if int(_row_value(row, "id")) == int(managed_id):
                return row
        except (TypeError, ValueError):
            continue
    return None


def _profile_managed_id(profile: dict[str, Any]) -> int | None:
    try:
        value = profile.get("managed_account_id")
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _runtime_token_for_account(bot: RFDir5TradingBot, managed_id: int) -> str:
    for token, profile in list(getattr(bot, "user_profiles", {}).items()):
        if _profile_managed_id(dict(profile or {})) == int(managed_id):
            return str(token)
    return ""


def _private_session_for_account(bot: RFDir5TradingBot, managed_id: int) -> Any | None:
    for session in list(getattr(bot, "sessions", {}).values()):
        try:
            if int(getattr(session, "managed_account_id", -1)) == int(managed_id):
                return session
        except (TypeError, ValueError):
            continue
    return None


def _session_task_alive(session: Any | None) -> bool:
    if session is None:
        return False
    task = getattr(session, "task", None)
    return bool(task is not None and not task.done())


def _direct_runtime_for_account(bot: RFDir5TradingBot, managed_id: int) -> Any | None:
    runtime = getattr(bot, "_custom_direct_accounts", {})
    if not isinstance(runtime, dict):
        return None
    return runtime.get(int(managed_id))


def _admit_one_runtime_account(bot: RFDir5TradingBot, managed_id: int) -> str:
    """Admit one durable account without sweeping or disturbing its siblings."""

    row = _managed_row(bot, int(managed_id))
    if row is None:
        return ""
    lifecycle = account_lifecycle_from_row(row)
    if not account_allows_new_execution(row) and lifecycle != "settlement":
        return ""

    try:
        payload = decrypt_auth_payload(
            str(_row_value(row, "token_secret", "") or ""),
            bot.encryption_key,
        )
        payload = _promote_embedded_oauth_payload(payload)
    except Exception:
        bot._set_account_execution_status(
            int(managed_id),
            "credential_error",
            "Stored Deriv credential could not be read for this account.",
        )
        return ""

    account_id = str(payload.get("account_id") or "").strip()
    credential = instant._credential_from_saved_payload(payload)
    account_type = normalize_account_type(
        payload.get("account_type") or payload.get("environment"),
        bot.environment,
    )
    if not account_id or not credential:
        bot._set_account_execution_status(
            int(managed_id),
            "token_required",
            "Authenticated trade permission is required before execution can connect.",
        )
        return ""

    for profile in list(getattr(bot, "user_profiles", {}).values()):
        profile = dict(profile or {})
        other_id = _profile_managed_id(profile)
        if other_id == int(managed_id):
            continue
        if str(profile.get("account_id") or "").strip() == account_id:
            bot._set_account_execution_status(
                int(managed_id),
                "duplicate",
                "This Deriv account is already represented by another active row.",
            )
            return ""

    runtime_key = runtime_account_key(credential, account_id)
    profile = {
        "id": str(managed_id),
        "name": str(_row_value(row, "label", "") or f"Account {managed_id}"),
        "enabled": True,
        "account_id": account_id,
        "account_type": account_type,
        "auth_type": str(payload.get("auth_type") or "oauth").strip().lower(),
        "source": "custom_strategy_targeted_start",
        "managed_account_id": int(managed_id),
        "stake_amount": float(_row_value(row, "stake_amount", 0.50) or 0.50),
        "take_profit": float(_row_value(row, "take_profit", 0.0) or 0.0),
        "stop_loss": float(_row_value(row, "stop_loss", 0.0) or 0.0),
        "martingale_enabled": bool(_row_value(row, "martingale_enabled", True)),
        "settlement_only": lifecycle == "settlement",
        "api_token": credential,
    }

    profiles = dict(getattr(bot, "user_profiles", {}) or {})
    stale_keys = {
        str(key)
        for key, existing in profiles.items()
        if _profile_managed_id(dict(existing or {})) == int(managed_id)
        and str(key) != runtime_key
    }
    for key in stale_keys:
        profiles.pop(key, None)

    tokens = [
        str(token)
        for token in list(getattr(bot, "tokens", []) or [])
        if str(token) not in stale_keys and str(token) != runtime_key
    ]
    valid_clients = [
        (str(token), str(account))
        for token, account in list(getattr(bot, "valid_clients", []) or [])
        if str(token) not in stale_keys and str(token) != runtime_key
    ]

    profiles[runtime_key] = profile
    tokens.append(runtime_key)
    valid_clients.append((runtime_key, account_id))
    bot.user_profiles = profiles
    bot.tokens = tokens
    bot.valid_clients = valid_clients
    instant._select_saved_strategy_markets(bot, profiles)
    bot._sync_clients_with_runtime_accounts()

    current_status = str(_row_value(row, "execution_status", "") or "").strip().lower()
    if lifecycle != "settlement" and current_status in STARTING_LIKE_STATUSES:
        bot._set_account_execution_status(
            int(managed_id),
            "connecting",
            "Account admitted immediately; authenticated execution stream is connecting.",
        )

    bot.logger.info(
        "CUSTOM_TARGETED_ACCOUNT_ADMISSION managed_id=%s siblings_rebuilt=false "
        "provider_account_sweep=false private_session_required_for_buy=true",
        int(managed_id),
    )
    return runtime_key


async def _ensure_one_private_session(
    bot: RFDir5TradingBot,
    managed_id: int,
    *,
    wake: bool,
) -> Any | None:
    token = _runtime_token_for_account(bot, int(managed_id))
    if not token:
        token = _admit_one_runtime_account(bot, int(managed_id))
    if not token:
        return None

    session = _private_session_for_account(bot, int(managed_id))
    if session is not None and not _session_task_alive(session):
        pending = getattr(session, "pending_contracts", set())
        if not pending:
            getattr(bot, "sessions", {}).pop(token, None)
            session = None

    if session is None:
        # This method loops over valid_clients, but healthy sibling sessions are
        # retained verbatim. Since only the target was added above, only the target
        # receives a new connection task.
        await bot._ensure_sessions_for_valid_clients()
        session = _private_session_for_account(bot, int(managed_id))

    if wake and session is not None and not bool(getattr(session, "is_connected", False)):
        private_ws.wake_private_connection(session)
        bot.logger.info(
            "CUSTOM_TARGETED_START_WAKEUP managed_id=%s sibling_sessions_woken=false",
            int(managed_id),
        )
    return session


def _repair_tasks(bot: RFDir5TradingBot) -> dict[int, asyncio.Task[Any]]:
    tasks = getattr(bot, "_custom_targeted_repair_tasks", None)
    if not isinstance(tasks, dict):
        tasks = {}
        bot._custom_targeted_repair_tasks = tasks
    return tasks


def _schedule_targeted_runtime_repair(bot: RFDir5TradingBot, managed_id: int) -> None:
    """Single-flight repair for one account; never call global validate_accounts()."""

    tasks = _repair_tasks(bot)
    current = tasks.get(int(managed_id))
    if current is not None and not current.done():
        return

    async def repair() -> None:
        try:
            account = bot.repository.managed_account(int(managed_id)) or {}
            if not bool(account.get("enabled")):
                return
            session = _private_session_for_account(bot, int(managed_id))
            if session is None or not _session_task_alive(session):
                await _ensure_one_private_session(bot, int(managed_id), wake=False)
            # Runtime registration is local PostgreSQL/in-memory work. It does not
            # request OTPs or reconnect sibling accounts.
            direct_runtime._refresh_direct_accounts(
                bot,
                require_connected=False,
                fail_invalid=False,
            )
            bot.logger.info(
                "CUSTOM_TARGETED_RUNTIME_REPAIR managed_id=%s global_validation=false "
                "sibling_sessions_rebuilt=false",
                int(managed_id),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            bot.logger.warning(
                "CUSTOM_TARGETED_RUNTIME_REPAIR_DEFERRED managed_id=%s error_type=%s",
                int(managed_id),
                type(exc).__name__,
            )
        finally:
            tasks.pop(int(managed_id), None)

    try:
        task = asyncio.create_task(
            repair(),
            name=f"custom_targeted_runtime_repair_{int(managed_id)}",
        )
    except RuntimeError:
        return
    tasks[int(managed_id)] = task


def _watchdog_state(bot: RFDir5TradingBot) -> dict[int, dict[str, float]]:
    state = getattr(bot, "_execution_liveness_watchdog_state", None)
    if not isinstance(state, dict):
        state = {}
        bot._execution_liveness_watchdog_state = state
    return state


async def _singleflight_liveness_watchdog(bot: RFDir5TradingBot) -> None:
    """Observe reconnects without waking all sessions or rebuilding all accounts."""

    while bot.is_running:
        try:
            rows = bot.repository.list_managed_accounts()
            now = time.monotonic()
            state = _watchdog_state(bot)
            enabled_ids: set[int] = set()

            for row in rows:
                managed_id = int(_row_value(row, "id"))
                enabled = bool(_row_value(row, "enabled", False))
                status = str(_row_value(row, "execution_status", "inactive") or "inactive").strip().lower()
                reason = str(_row_value(row, "execution_status_reason", "") or "").strip()

                if not enabled:
                    state.pop(managed_id, None)
                    if not reason:
                        stop_reason._repair_disabled_reason(bot.repository, managed_id)
                    continue

                enabled_ids.add(managed_id)
                session = _private_session_for_account(bot, managed_id)
                runtime = _direct_runtime_for_account(bot, managed_id)
                connected = bool(session is not None and getattr(session, "is_connected", False))
                registered = runtime is not None
                task_alive = _session_task_alive(session)

                if connected and registered:
                    state.pop(managed_id, None)
                    continue

                entry = state.setdefault(
                    managed_id,
                    {"missing_since": now, "last_repair": 0.0, "last_log": 0.0},
                )
                missing_for = now - float(entry.get("missing_since") or now)

                # A live ClientSession owns its own OTP/backoff loop. Waking it on
                # every watchdog pass defeats exponential backoff and creates the
                # multi-account OTP stampede seen in production. Leave it alone.
                if session is not None and task_alive and not connected:
                    if not registered and missing_for >= _MISSING_GRACE_SECONDS:
                        last_repair = float(entry.get("last_repair") or 0.0)
                        if now - last_repair >= _REPAIR_INTERVAL_SECONDS:
                            entry["last_repair"] = now
                            _schedule_targeted_runtime_repair(bot, managed_id)
                    last_log = float(entry.get("last_log") or 0.0)
                    if missing_for >= _MISSING_GRACE_SECONDS and now - last_log >= _RECONNECT_LOG_INTERVAL_SECONDS:
                        entry["last_log"] = now
                        bot.logger.info(
                            "ACCOUNT_PRIVATE_RECONNECT_OWNED managed_id=%s status=%s "
                            "missing_seconds=%.1f session_task_alive=true watchdog_wake=false",
                            managed_id,
                            status,
                            missing_for,
                        )
                    continue

                if missing_for < _MISSING_GRACE_SECONDS:
                    continue
                last_repair = float(entry.get("last_repair") or 0.0)
                if now - last_repair < _REPAIR_INTERVAL_SECONDS:
                    continue
                entry["last_repair"] = now

                if status not in {"connecting", "reconnecting"}:
                    bot._set_account_execution_status(
                        managed_id,
                        "reconnecting",
                        "Execution runtime is being reconstructed for this account only; Auto Trading remains active.",
                    )
                _schedule_targeted_runtime_repair(bot, managed_id)
                bot.logger.warning(
                    "ACCOUNT_EXECUTION_TARGETED_REPAIR managed_id=%s previous_status=%s "
                    "session_object=%s session_task_alive=%s session_connected=%s "
                    "runtime_registered=%s sibling_repair=false",
                    managed_id,
                    status,
                    session is not None,
                    task_alive,
                    connected,
                    registered,
                )

            for managed_id in list(state):
                if managed_id not in enabled_ids:
                    state.pop(managed_id, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            bot.logger.exception("ACCOUNT_EXECUTION_SINGLEFLIGHT_WATCHDOG_FAILED")
        await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)


def _fresh_start_ids(bot: RFDir5TradingBot) -> list[int]:
    started: list[int] = []
    for row in bot.repository.list_managed_accounts():
        try:
            managed_id = int(_row_value(row, "id"))
        except (TypeError, ValueError):
            continue
        if not bool(_row_value(row, "enabled", False)):
            continue
        status = str(_row_value(row, "execution_status", "inactive") or "inactive").strip().lower()
        if status == "starting":
            started.append(managed_id)
    return started


async def _refresh_with_targeted_start(self: RFDir5TradingBot) -> None:
    """Consume a fresh Start without globally rebuilding every enabled account."""

    started_ids = _fresh_start_ids(self)
    if not started_ids:
        original = _ORIGINAL_REFRESH
        if original is not None:
            await original(self)
        return

    async with self._runtime_account_refresh_lock:
        # Another refresh may have consumed the Start while we waited for the lock.
        started_ids = _fresh_start_ids(self)
        if not started_ids:
            return
        admitted: list[int] = []
        for managed_id in started_ids:
            if _admit_one_runtime_account(self, managed_id):
                admitted.append(managed_id)
        for managed_id in admitted:
            await _ensure_one_private_session(self, managed_id, wake=True)
        direct_runtime._refresh_direct_accounts(
            self,
            require_connected=False,
            fail_invalid=False,
        )
        self._managed_accounts_revision = self.repository.managed_accounts_revision()
        self._runtime_mode_cache = self.repository.runtime_mode()
        if admitted:
            self._sync_running_status_after_validation()
        self.logger.info(
            "CUSTOM_TARGETED_START_PICKUP managed_ids=%s global_validation=false "
            "sibling_sessions_rebuilt=false",
            ",".join(str(item) for item in admitted) or "none",
        )


def install_custom_strategy_connection_stampede_guard() -> None:
    """Final connection authority for account-isolated Start and reconnect."""

    global _INSTALLED, _ORIGINAL_REFRESH
    if _INSTALLED:
        return

    _ORIGINAL_REFRESH = RFDir5TradingBot._refresh_runtime_accounts_if_needed

    # Five seconds was too aggressive for OTP bootstrap and caused valid requests
    # to be cancelled into retry storms. This remains background work and cannot
    # authorize BUY until the private socket is actually connected.
    instant._STARTUP_REST_TIMEOUT_SECONDS = _OTP_BOOTSTRAP_TIMEOUT_SECONDS

    seamless._schedule_runtime_repair = _schedule_targeted_runtime_repair
    stop_reason._execution_liveness_watchdog = _singleflight_liveness_watchdog
    RFDir5TradingBot._refresh_runtime_accounts_if_needed = _refresh_with_targeted_start
    RFDir5TradingBot._admit_custom_runtime_account = _admit_one_runtime_account
    RFDir5TradingBot._custom_strategy_connection_stampede_guard_installed = True
    RFDir5TradingBot._custom_strategy_otp_bootstrap_timeout_seconds = _OTP_BOOTSTRAP_TIMEOUT_SECONDS
    _INSTALLED = True
