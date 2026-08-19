from __future__ import annotations

from app.route_utils import remove_route as _remove_route

import copy
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Query, Request
from sqlalchemy import case, func, or_, select

import app.api as base_api
from app.aidr_adaptive_virtual import adaptive_virtual_wins_required
from app.ai_digit_recovery_v1 import VIRTUAL_WINS_REQUIRED
from app.custom_martingale import read_account_martingale_settings
from app.final_personal_trade_stream import (
    _aidr_summary,
    _trade_to_payload,
    _virtual_rows_with_progress,
)
from app.final_public_controls import (
    PAUSED_STATUSES,
    STOPPED_STATUSES,
    _reporting_timezone,
    _today_bounds_utc,
)
from app.models import (
    AccountRiskState,
    AccountSnapshot,
    CandidateSignalRecord,
    ClientSession,
    DirectionalSignal,
    ManagedAccount,
    Trade,
    VirtualTrade,
    utc_now,
)
from app.repositories.rf_dir5_repository import (
    REAL_RECOVERY_PENDING,
    VIRTUAL_WAITING_FOR_WIN,
)
from app.strategy_preferences import read_strategy
from app.token_store import decrypt_auth_payload

_INSTALLED = False

# The previous personal request path repeatedly decrypted every managed account,
# wrote client_sessions.last_seen_at on every poll, and sometimes waited for a
# Deriv REST balance request. These short-lived caches are process-local only;
# authoritative execution, balances, trades and sessions remain in PostgreSQL.
_SESSION_CACHE_TTL_SECONDS = 1.5
_IDENTITY_CACHE_TTL_SECONDS = 30.0
_ME_CACHE_TTL_SECONDS = 1.25
_TRADES_CACHE_TTL_SECONDS = 1.5
_SESSION_TOUCH_SECONDS = 60.0

_SESSION_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_IDENTITY_CACHE: dict[str, Any] = {
    "expires_at": 0.0,
    "by_managed_id": {},
    "by_identity": {},
}
_ME_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_TRADES_CACHE: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}
_PUBLIC_STATS_CACHE: tuple[float, dict[str, Any]] | None = None

_SESSION_LOCK = threading.RLock()
_IDENTITY_LOCK = threading.RLock()
_RESPONSE_LOCK = threading.RLock()




def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _clear_response_caches(*, session_hash_value: str = "") -> None:
    global _PUBLIC_STATS_CACHE
    with _SESSION_LOCK:
        if session_hash_value:
            _SESSION_CACHE.pop(str(session_hash_value), None)
        else:
            _SESSION_CACHE.clear()
    with _RESPONSE_LOCK:
        _ME_CACHE.clear()
        _TRADES_CACHE.clear()
        _PUBLIC_STATS_CACHE = None


def _invalidate_identity_cache() -> None:
    with _IDENTITY_LOCK:
        _IDENTITY_CACHE["expires_at"] = 0.0
        _IDENTITY_CACHE["by_managed_id"] = {}
        _IDENTITY_CACHE["by_identity"] = {}


def _identity_index(*, force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    with _IDENTITY_LOCK:
        if not force and now < float(_IDENTITY_CACHE.get("expires_at") or 0.0):
            return _IDENTITY_CACHE

        by_managed_id: dict[int, dict[str, Any]] = {}
        by_identity: dict[str, list[dict[str, Any]]] = {}
        for row in base_api.REPOSITORY.list_managed_accounts():
            try:
                payload = decrypt_auth_payload(
                    row.token_secret,
                    base_api.CONFIG.deriv.token_encryption_key,
                )
            except Exception:
                continue
            account_id = str(payload.get("account_id") or "").strip()
            if not account_id:
                continue
            identity = base_api.login_identity_from_payload(payload) or f"account:{account_id}"
            account_type = base_api.account_type_from_payload(payload)
            entry = {
                "managed_account_id": int(row.id),
                "identity": identity,
                "account_id": account_id,
                "account_id_masked": base_api.mask_account_id(account_id),
                "account_type": account_type,
                "token_ready": bool(base_api.has_trading_api_token(payload)),
                "enabled": bool(row.enabled),
                "execution_status": str(row.execution_status or "inactive"),
            }
            by_managed_id[int(row.id)] = entry
            by_identity.setdefault(identity, []).append(entry)

        for entries in by_identity.values():
            entries.sort(
                key=lambda item: (
                    {"demo": 0, "real": 1}.get(str(item["account_type"]), 9),
                    int(item["managed_account_id"]),
                )
            )

        _IDENTITY_CACHE.update(
            {
                "expires_at": now + _IDENTITY_CACHE_TTL_SECONDS,
                "by_managed_id": by_managed_id,
                "by_identity": by_identity,
            }
        )
        return _IDENTITY_CACHE


def _session_account_row(session_hash_value: str) -> dict[str, Any] | None:
    now = utc_now()
    with base_api.DATABASE.session() as session:
        client = session.get(ClientSession, str(session_hash_value))
        if client is None:
            return None
        expires_at = _aware(client.expires_at)
        if expires_at <= now:
            session.delete(client)
            return None
        account = session.get(ManagedAccount, int(client.managed_account_id))
        if account is None:
            session.delete(client)
            return None
        last_seen = _aware(client.last_seen_at)
        # Browser polling used to create a PostgreSQL UPDATE for every GET. Touch
        # the durable session at most once per minute instead.
        if (now - last_seen).total_seconds() >= _SESSION_TOUCH_SECONDS:
            client.last_seen_at = now
        return {
            "id": int(account.id),
            "label": str(account.label or ""),
            "token_secret": account.token_secret,
            "enabled": bool(account.enabled),
            "stake_amount": float(account.stake_amount or 0.50),
            "take_profit": float(account.take_profit or 0.0),
            "stop_loss": float(account.stop_loss or 0.0),
            "martingale_enabled": bool(account.martingale_enabled),
            "execution_status": str(account.execution_status or "inactive"),
            "execution_status_reason": str(account.execution_status_reason or ""),
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        }


def _build_current_account(session_hash_value: str) -> dict[str, Any] | None:
    row = _session_account_row(session_hash_value)
    if row is None:
        return None
    try:
        payload = decrypt_auth_payload(
            row["token_secret"],
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception:
        return None
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        return None

    index = _identity_index()
    selected_meta = dict(index.get("by_managed_id", {}).get(int(row["id"])) or {})
    identity = (
        str(selected_meta.get("identity") or "")
        or base_api.login_identity_from_payload(payload)
        or f"account:{account_id}"
    )
    linked = list(index.get("by_identity", {}).get(identity) or [])
    if not linked:
        linked = [
            {
                "managed_account_id": int(row["id"]),
                "identity": identity,
                "account_id": account_id,
                "account_id_masked": base_api.mask_account_id(account_id),
                "account_type": base_api.account_type_from_payload(payload),
                "token_ready": bool(base_api.has_trading_api_token(payload)),
                "enabled": bool(row["enabled"]),
                "execution_status": str(row["execution_status"]),
            }
        ]

    account_type = base_api.account_type_from_payload(payload)
    available_modes = sorted(
        {str(item.get("account_type") or "demo") for item in linked},
        key=lambda value: {"demo": 0, "real": 1}.get(value, 9),
    )
    shared_token_ready = any(bool(item.get("token_ready")) for item in linked)
    requires_token = base_api.execution_requires_new_token(row["execution_status"])
    rejected = base_api.execution_token_was_rejected(
        row["execution_status"],
        row["execution_status_reason"],
    )
    token_ready = shared_token_ready and not requires_token

    return {
        "id": int(row["id"]),
        "managed_account_id": int(row["id"]),
        "account_generation": f"{int(row['id'])}:{account_type}",
        "account_id": account_id,
        "account_id_masked": base_api.mask_account_id(account_id),
        "account_type": account_type,
        "available_account_types": available_modes or [account_type],
        "label": row["label"],
        "enabled": bool(row["enabled"]),
        "stake_amount": float(row["stake_amount"]),
        "take_profit": float(row["take_profit"]),
        "stop_loss": float(row["stop_loss"]),
        "martingale_enabled": bool(row["martingale_enabled"]),
        "execution_status": str(row["execution_status"]),
        "execution_status_reason": str(row["execution_status_reason"]),
        "has_trading_api_token": bool(token_ready),
        "requires_api_token": not bool(token_ready),
        "trading_api_token_invalid": bool(rejected),
        "created_at": row["created_at"],
    }


def _fast_current_account(request: Request) -> dict[str, Any] | None:
    session_token = str(request.cookies.get(base_api.CLIENT_SESSION_COOKIE, "") or "")
    if not session_token:
        return _local_dev_current_account(request)
    session_hash_value = base_api.session_hash(session_token)
    now = time.monotonic()
    with _SESSION_LOCK:
        cached = _SESSION_CACHE.get(session_hash_value)
        if cached and now < cached[0]:
            return copy.deepcopy(cached[1])
    account = _build_current_account(session_hash_value)
    if account is None:
        account = _local_dev_current_account(request)
    with _SESSION_LOCK:
        _SESSION_CACHE[session_hash_value] = (
            now + _SESSION_CACHE_TTL_SECONDS,
            copy.deepcopy(account),
        )
    return account


def _local_dev_current_account(request: Request) -> dict[str, Any] | None:
    account = base_api.local_dev_preview_account(request)
    if not account:
        return None
    account.setdefault("managed_account_id", int(account.get("id") or 0))
    account.setdefault(
        "account_generation",
        f"local-preview:{account.get('account_type', 'demo')}",
    )
    account.setdefault("trading_api_token_invalid", False)
    return account


def _virtual_protection_payload(state: AccountRiskState | None, account_masked: str) -> dict[str, Any]:
    if state is None:
        return {
            "mode": "NORMAL_MODE",
            "state": "NORMAL_MODE",
            "account": account_masked,
            "consecutive_actual_losses": 0,
            "actual_recovery_debt": 0.0,
            "virtual_observations": 0,
            "virtual_wins": 0,
            "virtual_losses": 0,
            "current_virtual_loss_streak": 0,
            "entered_virtual_mode_at": None,
            "recovery_pending_since": None,
        }
    raw_mode = str(state.protection_mode or "NORMAL_MODE")
    return {
        "mode": (
            "VIRTUAL_MODE"
            if raw_mode == VIRTUAL_WAITING_FOR_WIN
            else "RECOVERY_PENDING"
            if raw_mode == REAL_RECOVERY_PENDING
            else "NORMAL_MODE"
        ),
        "state": raw_mode,
        "account": account_masked,
        "consecutive_actual_losses": int(state.consecutive_losses or 0),
        "actual_recovery_debt": float(state.recovery_loss_debt or 0.0),
        "virtual_observations": int(state.virtual_observation_count or 0),
        "virtual_wins": int(state.virtual_win_count or 0),
        "virtual_losses": int(state.virtual_loss_count or 0),
        "current_virtual_loss_streak": int(state.current_virtual_loss_streak or 0),
        "entered_virtual_mode_at": (
            state.entered_virtual_mode_at.isoformat()
            if state.entered_virtual_mode_at
            else None
        ),
        "recovery_pending_since": (
            state.recovery_pending_since.isoformat()
            if state.recovery_pending_since
            else None
        ),
    }


def _fast_personal_summary(account: dict[str, Any]) -> dict[str, Any]:
    managed_id = int(account["id"])
    masked = str(account["account_id_masked"])
    start, end = _today_bounds_utc()
    period = or_(
        Trade.purchase_time.between(start, end),
        Trade.settlement_time.between(start, end),
        Trade.provider_purchase_time.between(start, end),
    )
    with base_api.DATABASE.session() as session:
        snapshot = session.scalar(
            select(AccountSnapshot).where(
                AccountSnapshot.run_id == base_api.REPOSITORY.run_id,
                AccountSnapshot.account_id_masked == masked,
            )
        )
        stats = session.execute(
            select(
                func.count(Trade.id).label("trades"),
                func.sum(case((Trade.outcome == "WIN", 1), else_=0)).label("wins"),
                func.sum(case((Trade.outcome == "LOSS", 1), else_=0)).label("losses"),
                func.sum(Trade.profit).label("profit"),
                func.sum(
                    case((Trade.settlement_time.is_(None), 1), else_=0)
                ).label("open_trades"),
            ).where(
                Trade.managed_account_id == managed_id,
                period,
            )
        ).one()
        state = session.get(AccountRiskState, managed_id)

    wins = int(stats.wins or 0)
    losses = int(stats.losses or 0)
    return {
        "account": masked,
        "balance": float(snapshot.balance if snapshot else 0.0),
        "currency": str(snapshot.currency if snapshot else "USD"),
        "status": str(snapshot.status if snapshot else "linked"),
        "updated_at": snapshot.updated_at.isoformat() if snapshot else None,
        "trades": int(stats.trades or 0),
        "wins": wins,
        "losses": losses,
        "profit": float(stats.profit or 0.0),
        "open_trades": int(stats.open_trades or 0),
        "win_rate": wins / (wins + losses) if wins + losses else 0.0,
        "virtual_protection": _virtual_protection_payload(state, masked),
    }


def _me_payload(account: dict[str, Any]) -> dict[str, Any]:
    base_api.schedule_personal_account_refresh(account)
    personal = _fast_personal_summary(account)
    martingale = read_account_martingale_settings(
        base_api.REPOSITORY,
        int(account["id"]),
    )
    strategy = read_strategy(base_api.DATABASE, int(account["id"]))
    full_id = str(account["account_id"])
    return {
        "authenticated": True,
        "managed_account_id": int(account["id"]),
        "account_generation": str(account["account_generation"]),
        "account_id": personal["account"],
        "account_id_masked": personal["account"],
        "account_id_full": full_id,
        "login_id": full_id,
        "display_account_id": full_id,
        "account_type": str(account["account_type"]),
        "account_type_label": (
            "Real" if str(account["account_type"]) == "real" else "Demo"
        ),
        "account_prefix": full_id[:3].upper(),
        "available_account_types": list(account["available_account_types"]),
        "label": f"Account {personal['account']}",
        "enabled": bool(account["enabled"]),
        "has_trading_api_token": bool(account["has_trading_api_token"]),
        "requires_api_token": bool(account["requires_api_token"]),
        "trading_api_token_invalid": bool(account["trading_api_token_invalid"]),
        "balance": personal["balance"],
        "currency": personal["currency"],
        "status": personal["status"],
        "balance_updated_at": personal["updated_at"],
        "execution_status": str(account["execution_status"]),
        "execution_status_reason": str(account["execution_status_reason"]),
        "settings": {
            "stake_amount": float(account["stake_amount"]),
            "take_profit": float(account["take_profit"]),
            "stop_loss": float(account["stop_loss"]),
            "martingale_enabled": bool(martingale["martingale_enabled"]),
            "martingale_mode": martingale["mode"],
            "martingale_trigger_losses": martingale["trigger_losses"],
            "martingale_multiplier": martingale["multiplier"],
            "martingale_max_levels": martingale["max_levels"],
            "martingale_max_stake": martingale["max_stake"],
            "martingale_policy": martingale["policy"],
        },
        "strategy": strategy.to_dict(),
        "stats": {
            "trades": personal["trades"],
            "settled_trades": personal["wins"] + personal["losses"],
            "open_trades": personal["open_trades"],
            "wins": personal["wins"],
            "losses": personal["losses"],
            "profit": personal["profit"],
        },
        "virtual_protection": personal["virtual_protection"],
        "performance_profile": "fast-personal-v1",
    }


def _cached_me(account: dict[str, Any]) -> dict[str, Any]:
    managed_id = int(account["id"])
    now = time.monotonic()
    with _RESPONSE_LOCK:
        cached = _ME_CACHE.get(managed_id)
        if cached and now < cached[0]:
            return copy.deepcopy(cached[1])
    payload = _me_payload(account)
    with _RESPONSE_LOCK:
        _ME_CACHE[managed_id] = (
            now + _ME_CACHE_TTL_SECONDS,
            copy.deepcopy(payload),
        )
    return payload


def _trade_period():
    start, end = _today_bounds_utc()
    return start, end, or_(
        Trade.purchase_time.between(start, end),
        Trade.settlement_time.between(start, end),
        Trade.provider_purchase_time.between(start, end),
    )


def _fast_trade_payload(account: dict[str, Any], limit: int) -> dict[str, Any]:
    managed_id = int(account["id"])
    start, end, period = _trade_period()
    with base_api.DATABASE.session() as session:
        aggregate = session.execute(
            select(
                func.count(Trade.id).label("total"),
                func.sum(case((Trade.outcome == "WIN", 1), else_=0)).label("wins"),
                func.sum(case((Trade.outcome == "LOSS", 1), else_=0)).label("losses"),
                func.sum(Trade.profit).label("profit"),
                func.sum(
                    case((Trade.settlement_time.is_(None), 1), else_=0)
                ).label("open_trades"),
            ).where(
                Trade.managed_account_id == managed_id,
                period,
            )
        ).one()
        actual_rows = session.execute(
            select(Trade, CandidateSignalRecord, DirectionalSignal)
            .outerjoin(
                CandidateSignalRecord,
                CandidateSignalRecord.signal_id == Trade.signal_id,
            )
            .outerjoin(
                DirectionalSignal,
                DirectionalSignal.signal_id == Trade.signal_id,
            )
            .where(
                Trade.managed_account_id == managed_id,
                period,
            )
            .order_by(Trade.purchase_time.desc(), Trade.id.desc())
            .limit(limit)
        ).all()
        virtual_rows = session.scalars(
            select(VirtualTrade)
            .where(VirtualTrade.managed_account_id == managed_id)
            .where(
                or_(
                    VirtualTrade.created_at.between(start, end),
                    VirtualTrade.settled_at.between(start, end),
                )
            )
            .order_by(VirtualTrade.created_at.asc())
            .limit(500)
        ).all()
        state = session.get(AccountRiskState, managed_id)
        virtual_aggregate = session.execute(
            select(
                func.count(VirtualTrade.id).label("total"),
                func.sum(
                    case((VirtualTrade.result.ilike("%WIN%"), 1), else_=0)
                ).label("wins"),
                func.sum(
                    case((VirtualTrade.result.ilike("%LOSS%"), 1), else_=0)
                ).label("losses"),
                func.sum(
                    case((VirtualTrade.result == "OPEN", 1), else_=0)
                ).label("open_trades"),
            ).where(
                VirtualTrade.managed_account_id == managed_id,
                or_(
                    VirtualTrade.created_at.between(start, end),
                    VirtualTrade.settled_at.between(start, end),
                ),
            )
        ).one()

    actual_trades = [
        {
            **_trade_to_payload(trade, candidate, directional),
            "is_virtual": False,
            "trade_kind": "actual",
            "history_retained": True,
        }
        for trade, candidate, directional in actual_rows
    ]
    virtual_trades = _virtual_rows_with_progress(list(virtual_rows))
    rows = sorted(
        [*actual_trades, *virtual_trades],
        key=lambda item: str(
            item.get("purchase_time")
            or item.get("created_at")
            or item.get("settlement_time")
            or ""
        ),
        reverse=True,
    )[:limit]
    wins = int(aggregate.wins or 0)
    losses = int(aggregate.losses or 0)
    total = int(aggregate.total or 0)
    aidr = _aidr_summary(state, managed_id)
    virtual_total = int(virtual_aggregate.total or 0)
    return {
        "authenticated": True,
        "managed_account_id": managed_id,
        "account_generation": str(account["account_generation"]),
        "account": str(account["account_id_masked"]),
        "account_type": str(account["account_type"]),
        "timezone": str(_reporting_timezone()),
        "date": start.astimezone(_reporting_timezone()).date().isoformat(),
        "history_preserved_across_stop": True,
        "row_limit": limit,
        "truncated": total + virtual_total > len(rows),
        "trades": rows,
        "aidr": aidr,
        "summary": {
            "total": total,
            "settled": wins + losses,
            "wins": wins,
            "losses": losses,
            "open": int(aggregate.open_trades or 0),
            "profit": round(float(aggregate.profit or 0.0), 8),
            "win_rate": wins / (wins + losses) if wins + losses else 0.0,
            "virtual_observations": virtual_total,
            "virtual_wins": int(virtual_aggregate.wins or 0),
            "virtual_losses": int(virtual_aggregate.losses or 0),
            "virtual_open": int(virtual_aggregate.open_trades or 0),
            "history_rows": total + virtual_total,
            "returned_rows": len(rows),
        },
        "performance_profile": "bounded-personal-history-v1",
    }


def _cached_trades(account: dict[str, Any], limit: int) -> dict[str, Any]:
    key = (int(account["id"]), int(limit))
    now = time.monotonic()
    with _RESPONSE_LOCK:
        cached = _TRADES_CACHE.get(key)
        if cached and now < cached[0]:
            return copy.deepcopy(cached[1])
    payload = _fast_trade_payload(account, limit)
    with _RESPONSE_LOCK:
        _TRADES_CACHE[key] = (
            now + _TRADES_CACHE_TTL_SECONDS,
            copy.deepcopy(payload),
        )
    return payload


def _aidr_status_payload(account: dict[str, Any]) -> dict[str, Any]:
    managed_id = int(account["id"])
    with base_api.DATABASE.session() as session:
        managed = session.get(ManagedAccount, managed_id)
        state = session.get(AccountRiskState, managed_id)
    enabled = bool(managed.enabled) if managed is not None else False
    status = (
        str(managed.execution_status or "inactive").strip().lower()
        if managed is not None
        else "missing"
    )
    debt = float(state.recovery_loss_debt or 0.0) if state is not None else 0.0
    raw_mode = str(state.protection_mode or "NORMAL_MODE") if state is not None else "NORMAL_MODE"
    wins = int(state.virtual_win_count or 0) if state is not None else 0
    required = adaptive_virtual_wins_required(
        base_api.REPOSITORY,
        managed_id,
        default_wins=VIRTUAL_WINS_REQUIRED,
        recovery_debt=debt,
    )
    try:
        split = 1 if int(
            base_api.REPOSITORY.runtime_preference(
                f"aidr_split_remaining:{managed_id}"
            )
            or "0"
        ) > 0 else 0
    except Exception:
        split = 0

    if status in STOPPED_STATUSES:
        lifecycle, mode = "stopped", "stopped"
        next_action = "Auto Trade is stopped. Press Start to begin fresh from base stake."
    elif not enabled or status in PAUSED_STATUSES:
        lifecycle, mode = "paused", "paused"
        next_action = "Auto Trade is paused. Resume continues the preserved state."
    elif raw_mode == VIRTUAL_WAITING_FOR_WIN:
        lifecycle, mode = "running", "virtual"
        next_action = (
            f"Virtual confirmation: {wins}/{required} wins. "
            f"Waiting for {max(0, required - wins)} more."
        )
    elif raw_mode == REAL_RECOVERY_PENDING and split > 0:
        lifecycle, mode = "running", "full_recovery"
        next_action = "One real recovery trade will target the full recovery debt."
    elif raw_mode == REAL_RECOVERY_PENDING:
        lifecycle, mode = "running", "exact_recovery"
        next_action = "The next qualifying trade is the account's exact recovery."
    else:
        lifecycle, mode = "running", "normal"
        next_action = "Normal selected-strategy execution."

    return {
        "authenticated": True,
        "managed_account_id": managed_id,
        "account_generation": str(account["account_generation"]),
        "account": str(account["account_id_masked"]),
        "account_type": str(account["account_type"]),
        "timezone": str(_reporting_timezone()),
        "lifecycle": lifecycle,
        "enabled": enabled,
        "execution_status": status,
        "mode": mode,
        "raw_mode": raw_mode,
        "recovery_debt": round(debt, 2),
        "consecutive_losses": int(state.consecutive_losses or 0) if state is not None else 0,
        "virtual_wins": wins,
        "virtual_wins_required": required,
        "virtual_losses": int(state.virtual_loss_count or 0) if state is not None else 0,
        "virtual_observations": int(state.virtual_observation_count or 0) if state is not None else 0,
        "split_recovery_remaining": split,
        "full_recovery_remaining": split,
        "next_action": next_action,
        # Unified history already comes from /me/trades/today. Returning another
        # 250 rows every three seconds was pure duplicate database and JSON work.
        "virtual_trades": [],
        "virtual_history_endpoint": "/me/trades/today",
        "performance_profile": "compact-aidr-status-v1",
    }


def _public_stats_payload() -> dict[str, Any]:
    index = _identity_index()
    identities: dict[str, list[dict[str, Any]]] = dict(index.get("by_identity") or {})
    active_identities: set[str] = set()
    linked_accounts = 0
    enabled_accounts = 0
    inactive_statuses = {
        "stopped",
        "disabled",
        "inactive",
        "manual_pause",
        "take_profit",
        "stop_loss",
        "credential_error",
        "invalid_account",
        "token_required",
        "bulk_execution_pat_required",
        "insufficient_balance",
    }
    for identity, entries in identities.items():
        linked_accounts += len(entries)
        active = False
        for entry in entries:
            status = str(entry.get("execution_status") or "inactive").strip().lower()
            if bool(entry.get("enabled")) and status not in inactive_statuses:
                enabled_accounts += 1
                active = True
        if active:
            active_identities.add(identity)
    registered = len(identities)
    return {
        "registered_traders": registered,
        "total_registered_traders": registered,
        "trading_now": len(active_identities),
        "active_traders": len(active_identities),
        "linked_accounts": linked_accounts,
        "enabled_accounts": enabled_accounts,
        "public": True,
        "cache_seconds": int(_IDENTITY_CACHE_TTL_SECONDS),
        "performance_profile": "cached-public-counts-v1",
    }


def install_api_performance_hardening(app: Any) -> None:
    """Install final low-latency personal/dashboard API authorities."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_get_current_account = base_api.get_current_account
    original_set_session_account = base_api.REPOSITORY.set_client_session_account

    def current_account_fast(request: Request) -> dict[str, Any] | None:
        account = _fast_current_account(request)
        if account is not None:
            return account
        # Retain the one-time encrypted-cookie fallback used by very old browser
        # sessions. New server-side sessions never take this slower path.
        return original_get_current_account(request)

    def set_session_account_and_invalidate(
        session_hash_value: str,
        managed_account_id: int,
    ) -> None:
        original_set_session_account(session_hash_value, managed_account_id)
        _clear_response_caches(session_hash_value=session_hash_value)

    base_api.get_current_account = current_account_fast
    base_api.REPOSITORY.set_client_session_account = set_session_account_and_invalidate

    for path, method in (
        ("/me", "GET"),
        ("/me/trades/today", "GET"),
        ("/me/aidr-status", "GET"),
        ("/me/switch-account", "POST"),
        ("/metrics/public-traders", "GET"),
    ):
        _remove_route(app, path, method)

    @app.get("/me")
    def personal_me_fast(request: Request) -> dict[str, Any]:
        account = current_account_fast(request)
        if not account:
            return {"authenticated": False, "performance_profile": "fast-personal-v1"}
        return _cached_me(account)

    @app.get("/me/trades/today")
    def personal_trades_fast(
        request: Request,
        limit: int = Query(default=200, ge=25, le=500),
    ) -> dict[str, Any]:
        account = current_account_fast(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return _cached_trades(account, int(limit))

    @app.get("/me/aidr-status")
    def personal_aidr_status_fast(request: Request) -> dict[str, Any]:
        account = current_account_fast(request)
        if not account:
            return {
                "authenticated": False,
                "mode": "logged_out",
                "virtual_trades": [],
            }
        return _aidr_status_payload(account)

    @app.post("/me/switch-account")
    def switch_account_fast(
        request: Request,
        body: base_api.PersonalAccountSwitchRequest,
    ) -> dict[str, Any]:
        session_token = str(
            request.cookies.get(base_api.CLIENT_SESSION_COOKIE, "") or ""
        )
        current = current_account_fast(request)
        target_type = base_api.normalize_account_type(body.account_type)
        if current and current.get("local_dev_preview") and base_api.local_dev_auth_allowed(request):
            response = base_api.JSONResponse({"success": True, "account_type": target_type})
            response.set_cookie(
                key=base_api.LOCAL_DEV_ACCOUNT_TYPE_COOKIE,
                value=target_type,
                httponly=False,
                secure=False,
                samesite="lax",
                max_age=86400,
            )
            return response
        if not session_token or not current:
            raise HTTPException(status_code=401, detail="Not authenticated")
        index = _identity_index(force=True)
        selected = index.get("by_managed_id", {}).get(int(current["id"])) or {}
        identity = str(selected.get("identity") or "")
        linked = list(index.get("by_identity", {}).get(identity) or [])
        target = next(
            (
                item
                for item in linked
                if str(item.get("account_type")) == target_type
            ),
            None,
        )
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"No linked {target_type} account was found for this Deriv login.",
            )
        session_hash_value = base_api.session_hash(session_token)
        set_session_account_and_invalidate(
            session_hash_value,
            int(target["managed_account_id"]),
        )
        base_api.REPOSITORY.audit(
            "PERSONAL_ACCOUNT_TYPE_SWITCHED",
            str(current.get("account_id_masked") or "account"),
            request.client.host if request.client else "unknown",
            {
                "from": current.get("account_type", "demo"),
                "to": target_type,
                "managed_account_id": int(target["managed_account_id"]),
                "stale_requests_cancelled_by_ui": True,
            },
        )
        return {
            "success": True,
            "account_type": target_type,
            "managed_account_id": int(target["managed_account_id"]),
            "account_id": str(target["account_id_masked"]),
            "account_generation": (
                f"{int(target['managed_account_id'])}:{target_type}"
            ),
        }

    @app.get("/metrics/public-traders")
    def public_trader_stats_fast() -> dict[str, Any]:
        global _PUBLIC_STATS_CACHE
        now = time.monotonic()
        with _RESPONSE_LOCK:
            if _PUBLIC_STATS_CACHE and now < _PUBLIC_STATS_CACHE[0]:
                return copy.deepcopy(_PUBLIC_STATS_CACHE[1])
        payload = _public_stats_payload()
        with _RESPONSE_LOCK:
            _PUBLIC_STATS_CACHE = (
                now + _IDENTITY_CACHE_TTL_SECONDS,
                copy.deepcopy(payload),
            )
        return payload

    app.state.api_performance_hardening_installed = True
    app.state.personal_api_profile = "fast-personal-v1"
    _INSTALLED = True
