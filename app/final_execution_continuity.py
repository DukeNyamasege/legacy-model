from __future__ import annotations

import os
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app import netlify_worker_bridge as bridge
from app import private_websocket_rate_limit as private_ws
from app import seamless_execution_recovery as seamless
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
    "authenticated deriv trading session is not connected",
    "account balance could not be initialized",
    "account balance is unavailable",
)


def _is_stake_policy_reason(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    return any(marker in text for marker in _STAKE_POLICY_MARKERS)


def _is_transient_session_reason(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    return any(marker in text for marker in _TRANSIENT_SESSION_MARKERS)


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
    """Keep Auto Trading alive through recoverable private-session/runtime faults.

    The private ClientSession already owns its reconnect loop. Earlier recovery
    layers could additionally close that same WebSocket with code 1012 and then
    rebuild hot runtime state, creating avoidable connected/disconnected loops.

    This final authority is installed last. It never issues an application-driven
    WebSocket close. Recoverable transport faults wake the existing session and
    resynchronize account runtime in place. Ownership/runtime-state faults rebuild
    only the direct Custom Strategy references while preserving the underlying
    authenticated account session. Manual Stop and the existing TP/SL authorities
    remain the only normal lifecycle stops.
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
        if not transient:
            # Rebuild stale direct strategy references, but deliberately leave the
            # authenticated ClientSession/WebSocket itself untouched.
            seamless._drop_stale_execution_runtime(bot, int(managed_id))

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
            "forced_disconnect=false soft_wake=%s runtime_rebuild=%s reason=%s",
            log_event,
            int(managed_id),
            woke_session,
            not transient,
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
