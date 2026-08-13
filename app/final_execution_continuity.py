from __future__ import annotations

import os
import time
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app import netlify_worker_bridge as bridge
from app import private_websocket_rate_limit as private_ws
from app import seamless_execution_recovery as seamless
from app.models import ManagedAccount, utc_now
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_ORIGINAL_FAIL_HANDLER = None
_ORIGINAL_NORMAL_BACKOFF = None

_STAKE_POLICY_MARKERS = (
    "insufficient account balance for configured stake and reserve",
    "recovery stake",
    "stake plan rejected execution",
    "debt retained",
    "exceeds account safety cap",
)

_TRANSIENT_SESSION_MARKERS = (
    "not connected",
    "connection interrupted",
    "connection closed",
    "connection lost",
    "request timed out",
    "timed out",
    "authenticated deriv trading session is not connected",
    "account balance could not be initialized",
    "account balance is unavailable",
    "rate limit",
    "temporarily unavailable",
    "service unavailable",
)

# These are hot-runtime identity/state faults that can legitimately be repaired
# once or twice after account validation. They must never be allowed to oscillate
# forever between validation and resynchronization.
_RUNTIME_SYNC_MARKERS = (
    "account runtime ownership could not be verified",
    "authenticated account does not match the runtime account",
    "account execution state is not registered",
    "registered account execution state belongs to another account",
    "private deriv session belongs to another account",
)
_RUNTIME_RESYNC_MAX_ATTEMPTS = 2
_RUNTIME_RESYNC_WINDOW_SECONDS = 20.0


def _is_stake_policy_reason(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    return any(marker in text for marker in _STAKE_POLICY_MARKERS)


def _is_transient_session_reason(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    return any(marker in text for marker in _TRANSIENT_SESSION_MARKERS)


def _is_runtime_sync_reason(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    return any(marker in text for marker in _RUNTIME_SYNC_MARKERS)


def _session_for_managed_account(bot: RFDir5TradingBot, managed_id: int) -> Any | None:
    runtime = getattr(bot, "_custom_direct_accounts", {})
    item = runtime.get(int(managed_id)) if isinstance(runtime, dict) else None
    token = str(getattr(item, "token", "") or "") if item is not None else ""
    if not token:
        for candidate, _account_id in list(getattr(bot, "valid_clients", []) or []):
            try:
                if bot._managed_account_id_for_token(candidate) == int(managed_id):
                    token = str(candidate)
                    break
            except Exception:
                continue
    return getattr(bot, "sessions", {}).get(token) if token else None


def _wake_existing_private_session(bot: RFDir5TradingBot, managed_id: int) -> bool:
    session = _session_for_managed_account(bot, managed_id)
    if session is None:
        return False
    try:
        private_ws.wake_private_connection(session)
        return True
    except Exception:
        return False


def _runtime_fault_attempt(
    bot: RFDir5TradingBot,
    managed_id: int,
    reason: str,
) -> tuple[int, bool]:
    state = getattr(bot, "_runtime_resync_faults", None)
    if not isinstance(state, dict):
        state = {}
        bot._runtime_resync_faults = state

    now = time.monotonic()
    key = str(reason or "runtime synchronization fault").strip().lower()[:180]
    previous = state.get(int(managed_id)) or {}
    same_fault = (
        str(previous.get("key") or "") == key
        and now - float(previous.get("at") or 0.0) <= _RUNTIME_RESYNC_WINDOW_SECONDS
    )
    attempts = int(previous.get("attempts") or 0) + 1 if same_fault else 1
    state[int(managed_id)] = {"key": key, "attempts": attempts, "at": now}
    return attempts, attempts <= _RUNTIME_RESYNC_MAX_ATTEMPTS


def _clear_runtime_fault(bot: RFDir5TradingBot, managed_id: int) -> None:
    state = getattr(bot, "_runtime_resync_faults", None)
    if isinstance(state, dict):
        state.pop(int(managed_id), None)


def _surface_runtime_error(
    bot: RFDir5TradingBot,
    managed_id: int,
    reason: str,
    *,
    log_event: str,
) -> None:
    safe_reason = str(reason or "Trading stopped: account execution could not initialize safely")[:160]
    if not safe_reason.lower().startswith("trading stopped"):
        safe_reason = f"Trading stopped: {safe_reason}"[:160]

    try:
        with bot.repository.database.session() as session:
            row = session.get(ManagedAccount, int(managed_id), with_for_update=True)
            if row is not None:
                row.enabled = False
                row.execution_status = "error"
                row.execution_status_reason = safe_reason
                row.execution_status_updated_at = utc_now()
                row.updated_at = utc_now()
    except Exception:
        bot.logger.exception(
            "CUSTOM_RUNTIME_VISIBLE_ERROR_WRITE_FAILED managed_id=%s",
            int(managed_id),
        )

    seamless._drop_stale_execution_runtime(bot, int(managed_id))
    bot.valid_clients = [
        item
        for item in list(getattr(bot, "valid_clients", []) or [])
        if bot._managed_account_id_for_token(item[0]) != int(managed_id)
    ]
    _clear_runtime_fault(bot, int(managed_id))
    bot.logger.error(
        "%s managed_id=%s lifecycle_stop=true auto_retry=false reason=%s",
        log_event,
        int(managed_id),
        safe_reason,
    )
    try:
        bridge._schedule_dashboard_wakeup(bot)
    except Exception:
        pass


def _continuity_backoff(session: Any, config: Any, attempt: int) -> float:
    """Reconnect ordinary private-session drops quickly without weakening 429 backoff."""

    try:
        base = float(os.getenv("PRIVATE_WS_NORMAL_RECONNECT_BASE_SECONDS", "1.0"))
    except (TypeError, ValueError):
        base = 1.0
    try:
        ceiling = float(os.getenv("PRIVATE_WS_NORMAL_RECONNECT_MAX_SECONDS", "12.0"))
    except (TypeError, ValueError):
        ceiling = 12.0
    base = max(0.5, min(5.0, base))
    ceiling = max(base, min(30.0, ceiling))
    return min(
        float(getattr(config, "maximum_backoff_seconds", 300.0)),
        ceiling,
        base * (1.5 ** min(max(0, int(attempt)), 8)),
    ) + private_ws._jitter(config)


def install_final_execution_continuity() -> None:
    """Keep recoverable private-session faults alive without infinite repair loops.

    Network/session interruptions keep the account enabled and wake its existing
    ClientSession. A narrowly defined runtime identity fault may be repaired twice.
    Repeated identity faults and deterministic strategy/contract execution failures
    become a durable account ERROR so the dashboard cannot remain in STARTING for
    hours while no purchase is possible.
    """

    global _INSTALLED, _ORIGINAL_FAIL_HANDLER, _ORIGINAL_NORMAL_BACKOFF
    if _INSTALLED:
        return

    _ORIGINAL_FAIL_HANDLER = direct_runtime._fail_closed
    _ORIGINAL_NORMAL_BACKOFF = private_ws._normal_backoff

    def continuity_without_forced_disconnect(
        bot: RFDir5TradingBot,
        managed_id: int,
        reason: str,
        *,
        log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
    ) -> None:
        previous = _ORIGINAL_FAIL_HANDLER
        if _is_stake_policy_reason(reason):
            if previous is not None:
                previous(bot, int(managed_id), reason, log_event=log_event)
            return

        account = bot.repository.managed_account(int(managed_id)) or {}
        if not bool(account.get("enabled")):
            bot.logger.info(
                "CUSTOM_EXECUTION_CONTINUITY_SKIPPED managed_id=%s account_enabled=false",
                int(managed_id),
            )
            return

        transient = _is_transient_session_reason(reason)
        runtime_sync = _is_runtime_sync_reason(reason)

        if not transient and not runtime_sync:
            # A deterministic strategy/contract/account-state failure cannot be
            # repaired by repeatedly recreating the same runtime. Surface it once.
            _surface_runtime_error(
                bot,
                int(managed_id),
                reason,
                log_event=log_event,
            )
            return

        if runtime_sync:
            attempts, retry = _runtime_fault_attempt(bot, int(managed_id), reason)
            if not retry:
                _surface_runtime_error(
                    bot,
                    int(managed_id),
                    (
                        "Trading stopped: account runtime could not stabilize after "
                        f"{_RUNTIME_RESYNC_MAX_ATTEMPTS} automatic repair attempts. "
                        f"{str(reason or '')}"
                    ),
                    log_event="CUSTOM_RUNTIME_RESYNC_LIMIT_REACHED",
                )
                return
            seamless._drop_stale_execution_runtime(bot, int(managed_id))
        else:
            # A genuine network interruption is not evidence that the strategy or
            # account runtime is corrupt. Preserve the hot runtime and reconnect it.
            attempts = 0

        woke_session = _wake_existing_private_session(bot, int(managed_id))
        bot._set_account_execution_status(
            int(managed_id),
            "reconnecting",
            (
                "Private trading connection is recovering automatically; Auto Trading remains active."
                if transient
                else "Account runtime is resynchronizing automatically; Auto Trading remains active."
            ),
        )
        bot.logger.warning(
            "%s managed_id=%s enabled_preserved=true lifecycle_stop=false "
            "forced_disconnect=false soft_wake=%s runtime_rebuild=%s resync_attempt=%s reason=%s",
            log_event,
            int(managed_id),
            woke_session,
            runtime_sync,
            attempts,
            str(reason or "runtime synchronization fault")[:140],
        )
        seamless._schedule_runtime_repair(bot, int(managed_id))
        try:
            bridge._schedule_dashboard_wakeup(bot)
        except Exception:
            pass

    # Non-rate-limited provider/network drops use a short progressive retry. The
    # dedicated 429/Cloudflare circuit remains unchanged and can still back off for
    # minutes when Deriv explicitly rate-limits the VPS.
    private_ws._normal_backoff = _continuity_backoff
    direct_runtime._fail_closed = continuity_without_forced_disconnect
    RFDir5TradingBot._final_execution_continuity_installed = True
    _INSTALLED = True
