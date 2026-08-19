from __future__ import annotations

import hashlib
import json
import math
import os
import socket
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, case, exists, func, select, update

from app.config import Test2Config
from app.database import Database
from app.models import (
    AccountSnapshot,
    AccountRiskState,
    AuditEvent,
    BotState,
    BulkExecutionBatch,
    BulkExecutionMember,
    CandidateSignalRecord,
    ClientSession,
    DashboardSnapshot,
    ManagedAccount,
    ModelArtifact,
    ModelDecisionRecord,
    OAuthLoginState,
    ProposalRecord,
    RuntimePreference,
    SystemModelTrade,
    TestRun,
    Tick,
    Trade,
    TraderLease,
    VirtualTrade,
    utc_now,
)
from app.recovery import calculate_recovery_stake
from app.observed_performance import ObservedExecution, observed_martingale_cohort
from app.model.bayesian_probability import BayesianSnapshot
from app.model.hmm_regime import HmmInference
from app.strategy.decision_engine import ProposalEconomics, TradeDecision
from app.strategy.signal_detector import CandidateSignal
from app.strategy.rise_fall_strategy import shadow_outcome
from app.token_store import (
    decrypt_auth_payload,
    encrypt_auth_payload,
    remove_trading_api_token,
)


def mask_account_id(account_id: str) -> str:
    value = str(account_id)
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


class Test2Repository:
    def __init__(self, database: Database, config: Test2Config) -> None:
        self.database = database
        self.config = config
        self.run_id = self._ensure_run()

    def _ensure_run(self) -> int:
        config_json = json.dumps(
            self.config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        config_hash = hashlib.sha256(config_json.encode()).hexdigest()
        with self.database.session() as session:
            run = session.scalar(
                select(TestRun).where(TestRun.run_name == self.config.model.run_id)
            )
            if run is None:
                run = TestRun(
                    run_name=self.config.model.run_id,
                    model_version=self.config.model.version,
                    strategy_version=self.config.model.version,
                    configuration_hash=config_hash,
                    environment=self.config.deriv.environment,
                    symbol=self.config.rf_strategy.markets[0],
                    stake=self.config.strategy.initial_stake,
                    barrier="",
                    trigger=self.config.rf_strategy.name,
                    notes=self.config.model.brand,
                )
                session.add(run)
                session.flush()
            state = session.get(BotState, run.id)
            if state is None:
                session.add(BotState(run_id=run.id))
            return int(run.id)

    def _current_run_signal_ids(self):
        return select(CandidateSignalRecord.signal_id).where(
            CandidateSignalRecord.run_id == self.run_id
        )

    def _current_run_trade_filter(self):
        return Trade.signal_id.in_(self._current_run_signal_ids())

    def _reporting_timezone(self) -> ZoneInfo:
        name = (
            os.getenv("TRADING_REPORT_TIMEZONE")
            or os.getenv("DASHBOARD_TIMEZONE")
            or "Africa/Nairobi"
        ).strip()
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def _local_period_bounds(
        self,
        period: str,
        *,
        now: datetime | None = None,
    ) -> tuple[datetime, datetime]:
        tz = self._reporting_timezone()
        current = now or utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        local_now = current.astimezone(tz)
        today_start = datetime(
            local_now.year,
            local_now.month,
            local_now.day,
            tzinfo=tz,
        )
        normalized = str(period or "today").strip().lower()
        if normalized == "yesterday":
            start = today_start - timedelta(days=1)
            end = today_start
        elif normalized == "week":
            start = today_start - timedelta(days=today_start.weekday())
            end = local_now
        elif normalized == "month":
            start = datetime(local_now.year, local_now.month, 1, tzinfo=tz)
            end = local_now
        else:
            start = today_start
            end = local_now
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    def _trade_period_filter(
        self,
        start: datetime,
        end: datetime,
        *,
        account_masked: str = "",
    ):
        filters = [
            self._current_run_trade_filter(),
            Trade.purchase_time >= start,
            Trade.purchase_time < end,
        ]
        if account_masked:
            filters.append(Trade.account_id_masked == account_masked)
        return filters

    def _period_trade_stats(
        self,
        session,
        start: datetime,
        end: datetime,
        *,
        account_masked: str = "",
    ) -> dict[str, Any]:
        row = session.execute(
            select(
                func.count().label("trades"),
                func.sum(case((Trade.outcome == "WIN", 1), else_=0)).label("wins"),
                func.sum(case((Trade.outcome == "LOSS", 1), else_=0)).label("losses"),
                func.sum(Trade.profit).label("profit"),
            ).where(*self._trade_period_filter(start, end, account_masked=account_masked))
        ).one()
        wins = int(row.wins or 0)
        losses = int(row.losses or 0)
        return {
            "trades": int(row.trades or 0),
            "wins": wins,
            "losses": losses,
            "profit": float(row.profit or 0.0),
            "win_rate": wins / (wins + losses) if wins + losses else 0.0,
        }

    def runtime_mode(self) -> str:
        with self.database.session() as session:
            row = session.get(RuntimePreference, "trading_mode")
            value = (row.preference_value if row else self.config.deriv.environment).strip().lower()
            return value if value in {"demo", "real"} else "demo"

    def set_runtime_mode(self, mode: str) -> str:
        normalized = str(mode or "demo").strip().lower()
        if normalized not in {"demo", "real"}:
            raise ValueError("Mode must be demo or real")
        with self.database.session() as session:
            row = session.get(RuntimePreference, "trading_mode")
            if row is None:
                row = RuntimePreference(preference_key="trading_mode")
                session.add(row)
            row.preference_value = normalized
            row.updated_at = utc_now()
        return normalized

    def runtime_preference(self, key: str) -> str:
        with self.database.session() as session:
            row = session.get(RuntimePreference, str(key))
            return str(row.preference_value if row else "")

    def set_runtime_preference(self, key: str, value: str) -> None:
        with self.database.session() as session:
            row = session.get(RuntimePreference, str(key))
            if row is None:
                row = RuntimePreference(preference_key=str(key))
                session.add(row)
            row.preference_value = str(value)
            row.updated_at = utc_now()

    def managed_accounts_revision(self) -> str:
        with self.database.session() as session:
            latest = session.scalar(select(func.max(ManagedAccount.updated_at)))
        return latest.isoformat() if latest else ""

    def list_managed_accounts(self) -> list[ManagedAccount]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(ManagedAccount).order_by(ManagedAccount.created_at, ManagedAccount.id)
                ).all()
            )

    def managed_account_count(self) -> int:
        """Return the persistent registered population without decrypting tokens."""
        with self.database.session() as session:
            return int(session.scalar(select(func.count(ManagedAccount.id))) or 0)

    def add_managed_account(
        self, *, label: str, token_secret: str, enabled: bool = True
    ) -> dict[str, Any]:
        with self.database.session() as session:
            row = ManagedAccount(
                label=str(label or "").strip()[:120],
                token_secret=str(token_secret),
                enabled=bool(enabled),
            )
            session.add(row)
            session.flush()
            return {
                "id": int(row.id),
                "label": row.label,
                "enabled": bool(row.enabled),
                "stake_amount": float(row.stake_amount),
                "take_profit": float(row.take_profit),
                "stop_loss": float(row.stop_loss),
                "martingale_enabled": bool(row.martingale_enabled),
                "execution_status": row.execution_status,
                "execution_status_reason": row.execution_status_reason,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }

    def update_managed_account(
        self,
        account_id: int,
        *,
        label: str | None = None,
        token_secret: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            if row is None:
                raise ValueError(f"Managed account {account_id} not found")
            if label is not None:
                row.label = str(label or row.label or "").strip()[:120]
            if token_secret is not None:
                row.token_secret = str(token_secret)
            if enabled is not None:
                row.enabled = bool(enabled)
            row.updated_at = utc_now()
            return {
                "id": int(row.id),
                "label": row.label,
                "enabled": bool(row.enabled),
                "stake_amount": float(row.stake_amount),
                "take_profit": float(row.take_profit),
                "stop_loss": float(row.stop_loss),
                "martingale_enabled": bool(row.martingale_enabled),
                "execution_status": row.execution_status,
                "execution_status_reason": row.execution_status_reason,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }

    def managed_account(self, account_id: int) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            if row is None:
                return None
            return {
                "id": int(row.id),
                "label": row.label,
                "token_secret": row.token_secret,
                "enabled": bool(row.enabled),
                "stake_amount": float(row.stake_amount),
                "take_profit": float(row.take_profit),
                "stop_loss": float(row.stop_loss),
                "martingale_enabled": bool(row.martingale_enabled),
                "execution_status": row.execution_status,
                "execution_status_reason": row.execution_status_reason,
                "execution_status_updated_at": row.execution_status_updated_at,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }

    def set_managed_account_enabled(self, account_id: int, enabled: bool) -> dict[str, Any]:
        result = self.update_managed_account(account_id, enabled=bool(enabled))
        if not enabled:
            self.resume_managed_account(account_id, reset_recovery=True)
        self.set_managed_account_execution_status(
            account_id,
            "connecting" if enabled else "disabled",
            "Auto trading enabled" if enabled else "Auto trading disabled",
        )
        return result

    def resume_managed_account(self, account_id: int, *, reset_recovery: bool) -> None:
        with self.database.session() as session:
            state = session.get(AccountRiskState, int(account_id))
            if state is None:
                return
            if reset_recovery:
                state.consecutive_losses = 0
                state.recovery_loss_debt = 0.0
                state.recovery_pending = False
                state.recovery_attempt_active = False
                state.recovery_pending_since = None
            state.protection_mode = "NORMAL_MODE"
            state.entered_virtual_mode_at = None
            state.updated_at = utc_now()

    def update_account_execution_settings(
        self,
        account_id: int,
        *,
        stake_amount: float,
        take_profit: float,
        stop_loss: float,
        martingale_enabled: bool = True,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            if row is None:
                raise ValueError(f"Managed account {account_id} not found")
            row.stake_amount = float(stake_amount)
            row.take_profit = float(take_profit)
            row.stop_loss = float(stop_loss)
            row.martingale_enabled = bool(martingale_enabled)
            row.updated_at = utc_now()
            return {
                "stake_amount": float(row.stake_amount),
                "take_profit": float(row.take_profit),
                "stop_loss": float(row.stop_loss),
                "martingale_enabled": bool(row.martingale_enabled),
            }

    def set_managed_account_execution_status(
        self,
        account_id: int,
        execution_status: str,
        reason: str = "",
    ) -> None:
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            if row is None:
                return
            row.execution_status = str(execution_status or "inactive")[:30]
            row.execution_status_reason = str(reason or "")[:160]
            row.execution_status_updated_at = utc_now()

    def quarantine_managed_account(
        self,
        account_id: int,
        execution_status: str,
        reason: str,
    ) -> None:
        """Exclude one unsafe account while preserving credentials and audit data."""
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            if row is None:
                return
            row.enabled = False
            row.execution_status = str(execution_status or "disabled")[:30]
            row.execution_status_reason = str(reason or "Account excluded")[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()

    def discard_rejected_trading_token(
        self,
        account_id: int,
        *,
        reason: str,
    ) -> list[int]:
        """Forget a rejected PAT everywhere it is shared and request replacement."""
        encryption_key = self.config.deriv.token_encryption_key
        if not encryption_key:
            return []
        with self.database.session() as session:
            target = session.get(ManagedAccount, int(account_id), with_for_update=True)
            if target is None:
                return []
            try:
                target_payload = decrypt_auth_payload(target.token_secret, encryption_key)
            except Exception:
                return []
            rejected_token = str(
                target_payload.get("pat_token")
                or (
                    target_payload.get("access_token")
                    if str(target_payload.get("auth_type", "")).lower() != "oauth"
                    else ""
                )
                or ""
            ).strip()
            if not rejected_token:
                return []

            affected: list[int] = []
            rows = session.scalars(select(ManagedAccount).with_for_update()).all()
            for row in rows:
                try:
                    payload = decrypt_auth_payload(row.token_secret, encryption_key)
                except Exception:
                    continue
                stored_token = str(
                    payload.get("pat_token")
                    or (
                        payload.get("access_token")
                        if str(payload.get("auth_type", "")).lower() != "oauth"
                        else ""
                    )
                    or ""
                ).strip()
                if stored_token != rejected_token:
                    continue
                row.token_secret = encrypt_auth_payload(
                    remove_trading_api_token(payload),
                    encryption_key,
                )
                row.enabled = False
                row.execution_status = "token_required"
                row.execution_status_reason = str(reason or "Deriv API token expired")[:160]
                row.execution_status_updated_at = utc_now()
                row.updated_at = utc_now()
                affected.append(int(row.id))
            return affected

    def touch_managed_account_execution(self, account_ids: list[int]) -> None:
        normalized = sorted({int(account_id) for account_id in account_ids if account_id})
        if not normalized:
            return
        with self.database.session() as session:
            session.execute(
                update(ManagedAccount)
                .where(
                    ManagedAccount.id.in_(normalized),
                    ManagedAccount.enabled.is_(True),
                    ManagedAccount.execution_status == "active",
                )
                .values(execution_status_updated_at=utc_now())
            )

    def create_client_session(
        self, *, session_hash: str, managed_account_id: int, expires_at: datetime
    ) -> None:
        with self.database.session() as session:
            now = utc_now()
            session.query(ClientSession).filter(ClientSession.expires_at <= now).delete()
            row = ClientSession(
                session_hash=str(session_hash),
                managed_account_id=int(managed_account_id),
                expires_at=expires_at,
            )
            session.merge(row)

    def set_client_session_account(self, session_hash: str, managed_account_id: int) -> None:
        with self.database.session() as session:
            row = session.get(ClientSession, str(session_hash))
            if row is None:
                raise ValueError("Client session was not found")
            account = session.get(ManagedAccount, int(managed_account_id))
            if account is None:
                raise ValueError("Managed account was not found")
            row.managed_account_id = int(managed_account_id)
            row.last_seen_at = utc_now()

    def client_session_account(self, session_hash: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(ClientSession, str(session_hash))
            now = utc_now()
            if row is None:
                return None
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                session.delete(row)
                return None
            account = session.get(ManagedAccount, int(row.managed_account_id))
            if account is None:
                session.delete(row)
                return None
            row.last_seen_at = now
            return {
                "id": int(account.id),
                "label": account.label,
                "token_secret": account.token_secret,
                "enabled": bool(account.enabled),
                "stake_amount": float(account.stake_amount),
                "take_profit": float(account.take_profit),
                "stop_loss": float(account.stop_loss),
                "martingale_enabled": bool(account.martingale_enabled),
                "execution_status": account.execution_status,
                "execution_status_reason": account.execution_status_reason,
                "execution_status_updated_at": account.execution_status_updated_at,
                "created_at": account.created_at,
                "updated_at": account.updated_at,
                "expires_at": row.expires_at,
            }

    def delete_client_session(self, session_hash: str) -> None:
        with self.database.session() as session:
            row = session.get(ClientSession, str(session_hash))
            if row is not None:
                session.delete(row)

    def create_oauth_login_state(
        self,
        *,
        state_hash: str,
        code_verifier_secret: str,
        redirect_uri: str,
        expires_at: datetime,
    ) -> None:
        with self.database.session() as session:
            now = utc_now()
            session.query(OAuthLoginState).filter(
                OAuthLoginState.expires_at <= now
            ).delete()
            session.merge(
                OAuthLoginState(
                    state_hash=str(state_hash),
                    code_verifier_secret=str(code_verifier_secret),
                    redirect_uri=str(redirect_uri or ""),
                    expires_at=expires_at,
                )
            )

    def oauth_login_state(self, state_hash: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(OAuthLoginState, str(state_hash))
            now = utc_now()
            if row is None:
                return None
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                session.delete(row)
                return None
            return {
                "state_hash": row.state_hash,
                "code_verifier_secret": row.code_verifier_secret,
                "redirect_uri": row.redirect_uri,
                "expires_at": row.expires_at,
            }

    def delete_oauth_login_state(self, state_hash: str) -> None:
        with self.database.session() as session:
            row = session.get(OAuthLoginState, str(state_hash))
            if row is not None:
                session.delete(row)

    def account_summary(
        self,
        account_id: str,
        *,
        managed_account_id: int | None = None,
    ) -> dict[str, Any]:
        masked = mask_account_id(account_id)
        today_start, today_end = self._local_period_bounds("today")
        with self.database.session() as session:
            snapshot = session.scalar(
                select(AccountSnapshot).where(
                    AccountSnapshot.run_id == self.run_id,
                    AccountSnapshot.account_id_masked == masked,
                )
            )
            trade_stats = self._period_trade_stats(
                session,
                today_start,
                today_end,
                account_masked=masked,
            )
            settled_rows = session.execute(
                select(Trade.outcome, Trade.settlement_time)
                .where(
                    Trade.settlement_time.is_not(None),
                    *self._trade_period_filter(
                        today_start,
                        today_end,
                        account_masked=masked,
                    ),
                )
                .order_by(Trade.settlement_time.asc(), Trade.id.asc())
            ).all()
            longest_win_streak = 0
            longest_loss_streak = 0
            current_outcome = ""
            current_length = 0
            for settled in settled_rows:
                outcome = str(settled.outcome or "").upper()
                if outcome == current_outcome:
                    current_length += 1
                else:
                    current_outcome = outcome
                    current_length = 1
                if outcome == "WIN":
                    longest_win_streak = max(longest_win_streak, current_length)
                elif outcome == "LOSS":
                    longest_loss_streak = max(longest_loss_streak, current_length)
            open_rows = session.scalars(
                select(Trade)
                .where(
                    Trade.account_id_masked == masked,
                    Trade.settlement_time.is_(None),
                    self._current_run_trade_filter(),
                )
                .order_by(Trade.purchase_time.asc())
            ).all()
            oldest_open_trade_seconds = 0
            if open_rows:
                now = utc_now()
                oldest_open_trade_seconds = max(
                    0,
                    int(max((now - row.purchase_time).total_seconds() for row in open_rows)),
                )
            wins = int(trade_stats["wins"])
            losses = int(trade_stats["losses"])
            protection_state = (
                session.get(AccountRiskState, int(managed_account_id))
                if managed_account_id is not None
                else None
            )
            virtual_protection = (
                {
                    "mode": (
                        "VIRTUAL_MODE"
                        if protection_state.protection_mode == "VIRTUAL_WAITING_FOR_WIN"
                        else "RECOVERY_PENDING"
                        if protection_state.protection_mode == "REAL_RECOVERY_PENDING"
                        else "NORMAL_MODE"
                    ),
                    "state": protection_state.protection_mode,
                    "account": masked,
                    "consecutive_actual_losses": int(
                        protection_state.consecutive_losses or 0
                    ),
                    "actual_recovery_debt": float(
                        protection_state.recovery_loss_debt or 0.0
                    ),
                    "virtual_observations": int(
                        protection_state.virtual_observation_count or 0
                    ),
                    "virtual_wins": int(protection_state.virtual_win_count or 0),
                    "virtual_losses": int(protection_state.virtual_loss_count or 0),
                    "current_virtual_loss_streak": int(
                        protection_state.current_virtual_loss_streak or 0
                    ),
                    "entered_virtual_mode_at": (
                        protection_state.entered_virtual_mode_at.isoformat()
                        if protection_state.entered_virtual_mode_at
                        else None
                    ),
                    "recovery_pending_since": (
                        protection_state.recovery_pending_since.isoformat()
                        if protection_state.recovery_pending_since
                        else None
                    ),
                }
                if protection_state is not None
                else {
                    "mode": "NORMAL_MODE",
                    "state": "NORMAL_MODE",
                    "account": masked,
                    "consecutive_actual_losses": 0,
                    "actual_recovery_debt": 0.0,
                    "virtual_observations": 0,
                    "virtual_wins": 0,
                    "virtual_losses": 0,
                    "current_virtual_loss_streak": 0,
                    "entered_virtual_mode_at": None,
                    "recovery_pending_since": None,
                }
            )
            return {
                "account": masked,
                "balance": float(snapshot.balance if snapshot else 0.0),
                "currency": str(snapshot.currency if snapshot else "USD"),
                "status": str(snapshot.status if snapshot else "linked"),
                "updated_at": snapshot.updated_at.isoformat() if snapshot else None,
                "trades": int(trade_stats["trades"]),
                "wins": wins,
                "losses": losses,
                "profit": float(trade_stats["profit"]),
                "win_rate": float(trade_stats["win_rate"]),
                "longest_win_streak": longest_win_streak,
                "longest_loss_streak": longest_loss_streak,
                "open_trades": len(open_rows),
                "oldest_open_trade_seconds": oldest_open_trade_seconds,
                "virtual_protection": virtual_protection,
            }

    def account_group_period_summary(
        self,
        account_ids: list[str] | set[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        masked_accounts = sorted(
            {
                mask_account_id(str(account_id).strip())
                for account_id in account_ids
                if str(account_id).strip()
            }
        )
        empty = {
            "today_trades": 0,
            "today_profit": 0.0,
            "yesterday_trades": 0,
            "yesterday_profit": 0.0,
            "week_profit": 0.0,
            "month_profit": 0.0,
            "open_trades": 0,
            "oldest_open_trade_seconds": 0,
        }
        if not masked_accounts:
            return empty

        periods = {
            "today": self._local_period_bounds("today"),
            "yesterday": self._local_period_bounds("yesterday"),
            "week": self._local_period_bounds("week"),
            "month": self._local_period_bounds("month"),
        }
        with self.database.session() as session:
            aggregates: dict[str, dict[str, Any]] = {}
            for name, (start, end) in periods.items():
                row = session.execute(
                    select(
                        func.count().label("trades"),
                        func.sum(Trade.profit).label("profit"),
                    ).where(
                        *self._trade_period_filter(start, end),
                        Trade.account_id_masked.in_(masked_accounts),
                    )
                ).one()
                aggregates[name] = {
                    "trades": int(row.trades or 0),
                    "profit": float(row.profit or 0.0),
                }

            open_rows = session.scalars(
                select(Trade)
                .where(
                    self._current_run_trade_filter(),
                    Trade.account_id_masked.in_(masked_accounts),
                    Trade.settlement_time.is_(None),
                )
                .order_by(Trade.purchase_time.asc())
            ).all()

        oldest_open_trade_seconds = 0
        if open_rows:
            now = utc_now()
            oldest_open_trade_seconds = max(
                0,
                int(
                    max(
                        (now - trade.purchase_time).total_seconds()
                        for trade in open_rows
                    )
                ),
            )
        return {
            "today_trades": aggregates["today"]["trades"],
            "today_profit": aggregates["today"]["profit"],
            "yesterday_trades": aggregates["yesterday"]["trades"],
            "yesterday_profit": aggregates["yesterday"]["profit"],
            "week_profit": aggregates["week"]["profit"],
            "month_profit": aggregates["month"]["profit"],
            "open_trades": len(open_rows),
            "oldest_open_trade_seconds": oldest_open_trade_seconds,
        }

    def record_tick(
        self,
        *,
        sequence_id: int,
        symbol: str,
        epoch: int,
        tick_id: str,
        quote: float,
        final_digit: int,
        connection_session_id: str,
    ) -> None:
        with self.database.session() as session:
            session.add(
                Tick(
                    sequence_id=sequence_id,
                    run_id=self.run_id,
                    symbol=symbol,
                    epoch=epoch,
                    tick_id=tick_id,
                    quote=quote,
                    final_digit=final_digit,
                    low_high_class="LOW" if final_digit <= 4 else "HIGH",
                    connection_session_id=connection_session_id,
                )
            )
            state = session.get(BotState, self.run_id)
            if state:
                state.current_sequence = sequence_id
                state.current_connection_id = connection_session_id
                state.last_heartbeat = utc_now()

    def recent_digits(
        self,
        limit: int = 6000,
        *,
        symbol: str | None = None,
    ) -> list[int]:
        with self.database.session() as session:
            query = select(Tick.final_digit).where(Tick.run_id == self.run_id)
            if symbol:
                query = query.where(Tick.symbol == symbol)
            rows = session.scalars(
                query.order_by(Tick.sequence_id.desc()).limit(limit)
            ).all()
        return list(reversed([int(value) for value in rows]))

    def current_tick_sequence(self, *, symbol: str | None = None) -> int:
        with self.database.session() as session:
            query = select(func.max(Tick.sequence_id)).where(Tick.run_id == self.run_id)
            if symbol:
                query = query.where(Tick.symbol == symbol)
            value = session.scalar(query)
        return int(value or 0)

    def record_candidate(self, signal: CandidateSignal) -> None:
        with self.database.session() as session:
            session.add(
                CandidateSignalRecord(
                    signal_id=signal.signal_id,
                    run_id=self.run_id,
                    symbol=signal.symbol,
                    contract_type=signal.contract_type,
                    barrier=signal.barrier,
                    trigger_digits=list(signal.trigger_digits),
                    trigger_name=signal.trigger_name,
                    signal_tick_epoch=signal.signal_tick_epoch,
                    signal_tick_id=signal.signal_tick_id,
                    signal_last_digit=signal.signal_last_digit,
                    generated_timestamp=datetime.fromisoformat(signal.generated_at),
                    connection_session_id=signal.connection_session_id,
                    tick_sequence=signal.tick_sequence,
                )
            )

    def mark_signal(
        self,
        signal_id: str,
        *,
        status: str,
        stale: bool = False,
        proposal_requested: bool = False,
        proposal_received: bool = False,
        purchase_requested: bool = False,
        purchase_confirmed: bool = False,
        ticks_between: int | None = None,
        expected_account_masks: list[str] | None = None,
        registered_account_masks: list[str] | None = None,
    ) -> None:
        with self.database.session() as session:
            signal = session.get(CandidateSignalRecord, signal_id)
            if signal is None:
                return
            now = utc_now()
            signal.final_status = status
            signal.stale = stale
            if proposal_requested:
                signal.proposal_request_timestamp = now
            if proposal_received:
                signal.proposal_response_timestamp = now
            if purchase_requested:
                signal.purchase_request_timestamp = now
            if purchase_confirmed:
                signal.purchase_confirmation_timestamp = now
            if ticks_between is not None:
                signal.ticks_between_signal_and_purchase = ticks_between
            if expected_account_masks is not None:
                signal.expected_account_masks = sorted(set(expected_account_masks))
            if registered_account_masks is not None:
                signal.registered_account_masks = sorted(set(registered_account_masks))

    def consume_signal(self, signal_id: str) -> bool:
        with self.database.session() as session:
            result = session.execute(
                update(CandidateSignalRecord)
                .where(
                    CandidateSignalRecord.signal_id == signal_id,
                    CandidateSignalRecord.consumed.is_(False),
                )
                .values(consumed=True, final_status="PURCHASE_REQUESTED")
            )
            return result.rowcount == 1

    def signal_symbol(self, signal_id: str) -> str:
        with self.database.session() as session:
            value = session.scalar(
                select(CandidateSignalRecord.symbol).where(
                    CandidateSignalRecord.signal_id == signal_id,
                    CandidateSignalRecord.run_id == self.run_id,
                )
            )
        return str(value or "")

    def record_proposal(
        self, signal: CandidateSignal, economics: ProposalEconomics
    ) -> None:
        now = utc_now()
        latency = max(0.0, economics.received_monotonic - economics.requested_monotonic)
        with self.database.session() as session:
            session.add(
                ProposalRecord(
                    proposal_id=economics.proposal_id,
                    signal_id=signal.signal_id,
                    contract_type=signal.contract_type,
                    barrier=signal.barrier,
                    symbol=signal.symbol,
                    stake=economics.stake,
                    payout=economics.payout,
                    potential_profit=economics.potential_profit,
                    potential_loss=economics.potential_loss,
                    break_even_probability=economics.break_even_probability,
                    predicted_win_probability=economics.predicted_win_probability,
                    expected_value=economics.expected_value,
                    expected_return_on_stake=economics.expected_return_on_stake,
                    request_timestamp=now - timedelta(seconds=latency),
                    response_timestamp=now,
                )
            )

    def record_decision(
        self,
        decision: TradeDecision,
        *,
        hmm: HmmInference,
        bayesian: BayesianSnapshot,
    ) -> None:
        with self.database.session() as session:
            session.add(
                ModelDecisionRecord(
                    decision_id=decision.decision_id,
                    signal_id=decision.signal_id,
                    hmm_output=hmm.to_dict(),
                    bayesian_output=bayesian.to_dict(),
                    break_even_rate=decision.break_even_probability,
                    expected_value=decision.expected_value,
                    final_decision=decision.final_action,
                    rejection_reasons=decision.rejection_reasons,
                )
            )

    def register_purchase(
        self,
        *,
        signal_id: str,
        contract_id: str,
        transaction_id: str,
        account_id: str,
        purchase_time: datetime,
        aligned_with_signal: bool,
        buy_price: float | None = None,
        payout: float | None = None,
        provider_purchase_time: datetime | None = None,
        provider_start_time: datetime | None = None,
        contract_duration: int = 1,
        contract_duration_unit: str = "t",
        managed_account_id: int | None = None,
        bulk_batch_id: str | None = None,
    ) -> None:
        with self.database.session() as session:
            session.add(
                Trade(
                    managed_account_id=managed_account_id,
                    bulk_batch_id=bulk_batch_id,
                    trade_id=transaction_id or contract_id,
                    signal_id=signal_id,
                    contract_id=contract_id,
                    account_id_masked=mask_account_id(account_id),
                    purchase_time=purchase_time,
                    provider_purchase_time=provider_purchase_time,
                    provider_start_time=provider_start_time,
                    contract_duration=int(contract_duration),
                    contract_duration_unit=str(contract_duration_unit),
                    buy_price=buy_price,
                    payout=payout,
                    aligned_with_signal=aligned_with_signal,
                    model_version=self.config.model.version,
                )
            )

    def create_bulk_execution_batch(
        self,
        *,
        signal_id: str,
        account_type: str,
        martingale_enabled: bool,
        stake: float,
        shard_index: int,
        leader_managed_account_id: int | None,
        pre_trade_profit_ratio: float,
        members: list[dict[str, Any]],
        request_metadata: dict[str, Any],
        request_started_at: datetime,
    ) -> str:
        batch_id = str(uuid4())
        with self.database.session() as session:
            session.add(
                BulkExecutionBatch(
                    id=batch_id,
                    signal_id=str(signal_id),
                    run_id=self.run_id,
                    account_type=str(account_type),
                    martingale_enabled=bool(martingale_enabled),
                    stake=round(float(stake), 2),
                    shard_index=int(shard_index),
                    account_count=len(members),
                    leader_managed_account_id=leader_managed_account_id,
                    pre_trade_profit_ratio=float(pre_trade_profit_ratio),
                    request_metadata=dict(request_metadata),
                    request_started_at=request_started_at,
                    status="DISPATCHING",
                )
            )
            session.add_all(
                BulkExecutionMember(
                    batch_id=batch_id,
                    managed_account_id=int(member["managed_account_id"]),
                    account_id_masked=str(member["account_id_masked"]),
                    status="PENDING",
                )
                for member in members
            )
        return batch_id

    def complete_bulk_execution_batch(
        self,
        batch_id: str,
        *,
        response_received_at: datetime,
        latency_ms: float,
        results: list[dict[str, Any]],
    ) -> None:
        with self.database.session() as session:
            batch = session.get(BulkExecutionBatch, str(batch_id), with_for_update=True)
            if batch is None:
                return
            members = {
                int(row.managed_account_id): row
                for row in session.scalars(
                    select(BulkExecutionMember).where(
                        BulkExecutionMember.batch_id == str(batch_id)
                    )
                ).all()
            }
            successes = 0
            for result in results:
                member = members.get(int(result["managed_account_id"]))
                if member is None:
                    continue
                error = result.get("error") or {}
                if result.get("contract_id") and not error:
                    member.status = "SUCCESS"
                    member.contract_id = str(result["contract_id"])
                    member.trade_id = str(result.get("transaction_id") or result["contract_id"])
                    member.buy_price = result.get("buy_price")
                    member.payout = result.get("payout")
                    member.purchase_timestamp = result.get("purchase_timestamp")
                    successes += 1
                else:
                    member.status = "FAILED"
                    member.error_code = str(error.get("code") or "BULK_MEMBER_FAILED")[:80]
                    member.error_message = str(error.get("message") or "Bulk purchase failed")[:240]
            batch.response_received_at = response_received_at
            batch.latency_ms = max(0.0, float(latency_ms))
            batch.successful_count = successes
            batch.failed_count = len(members) - successes
            batch.status = "SUCCESS" if successes == len(members) else "PARTIAL" if successes else "FAILED"

    def bulk_execution_consistency(self, contract_id: str) -> dict[str, Any]:
        """Return token-free signal and shard execution diagnostics."""
        with self.database.session() as session:
            trade = session.scalar(select(Trade).where(Trade.contract_id == str(contract_id)))
            if trade is None or not trade.signal_id:
                return {}
            trades = session.scalars(select(Trade).where(Trade.signal_id == trade.signal_id)).all()
            batches = session.scalars(
                select(BulkExecutionBatch).where(BulkExecutionBatch.signal_id == trade.signal_id)
            ).all()
            canonical = session.scalar(
                select(SystemModelTrade).where(
                    SystemModelTrade.signal_id == trade.signal_id,
                    SystemModelTrade.run_id == self.run_id,
                )
            )
            outcomes = [str(row.outcome).upper() for row in trades if row.outcome]
            canonical_outcome = str(canonical.outcome or "").upper() if canonical else ""
            matching = sum(value == canonical_outcome for value in outcomes) if canonical_outcome else 0
            different = len(outcomes) - matching if canonical_outcome else 0
            payout_ratios = [
                float(row.payout) / float(row.buy_price)
                for row in trades
                if row.payout is not None and row.buy_price and row.buy_price > 0
            ]
            return_ratios = [
                float(row.profit) / float(row.buy_price)
                for row in trades
                if row.profit is not None and row.buy_price and row.buy_price > 0
            ]
            dispatches = sorted(row.request_started_at for row in batches if row.request_started_at)
            return {
                "signal_id": trade.signal_id,
                "batch_id": trade.bulk_batch_id,
                "successful_contracts": sum(int(row.successful_count or 0) for row in batches),
                "failed_accounts": sum(int(row.failed_count or 0) for row in batches),
                "unique_entry_spots": len({row.entry_tick for row in trades if row.entry_tick is not None}),
                "unique_provider_start_times": len({row.provider_start_time for row in trades if row.provider_start_time is not None}),
                "unique_purchase_times": len({row.provider_purchase_time or row.purchase_time for row in trades}),
                "unique_outcomes": len(set(outcomes)),
                "matching": matching,
                "different": different,
                "execution_consistency_pct": round(matching / len(outcomes) * 100.0, 2) if outcomes and canonical_outcome else None,
                "minimum_payout_ratio": min(payout_ratios) if payout_ratios else None,
                "maximum_payout_ratio": max(payout_ratios) if payout_ratios else None,
                "minimum_realized_return_ratio": min(return_ratios) if return_ratios else None,
                "maximum_realized_return_ratio": max(return_ratios) if return_ratios else None,
                "dispatch_spread_ms": max(0.0, (dispatches[-1] - dispatches[0]).total_seconds() * 1000.0) if dispatches else 0.0,
            }

    def settle_trade(
        self,
        *,
        contract_id: str,
        profit: float,
        outcome: str,
        entry_tick: float | None,
        exit_tick: float | None,
        exit_digit: int | None,
        entry_spot_display: str | None = None,
        exit_spot_display: str | None = None,
        entry_digit: int | None = None,
        buy_price: float | None = None,
        payout: float | None = None,
        app_markup_amount: float | None = None,
        commission: float | None = None,
        provider_purchase_time: datetime | None = None,
        provider_start_time: datetime | None = None,
        provider_expiry_time: datetime | None = None,
        provider_settlement_time: datetime | None = None,
    ) -> bool:
        with self.database.session() as session:
            trade = session.scalar(
                select(Trade)
                .where(
                    Trade.contract_id == str(contract_id),
                    self._current_run_trade_filter(),
                )
                .with_for_update()
            )
            if trade is None or trade.settlement_time is not None:
                return False
            state = session.get(BotState, self.run_id)
            if state is None:
                state = BotState(run_id=self.run_id)
                session.add(state)
                session.flush()
            state.total_profit += profit
            state.session_profit += profit
            state.high_water_mark = max(state.high_water_mark, state.total_profit)
            state.current_drawdown = state.high_water_mark - state.total_profit
            if outcome == "win":
                state.consecutive_wins += 1
                state.consecutive_losses = 0
            else:
                state.consecutive_losses += 1
                state.consecutive_wins = 0
            state.last_heartbeat = utc_now()
            trade.settlement_time = utc_now()
            if provider_purchase_time is not None:
                trade.provider_purchase_time = provider_purchase_time
            if provider_start_time is not None:
                trade.provider_start_time = provider_start_time
            if provider_expiry_time is not None:
                trade.provider_expiry_time = provider_expiry_time
            if provider_settlement_time is not None:
                trade.provider_settlement_time = provider_settlement_time
            trade.profit = profit
            trade.outcome = outcome.upper()
            trade.entry_tick = entry_tick
            trade.exit_tick = exit_tick
            trade.entry_spot_display = entry_spot_display
            trade.exit_spot_display = exit_spot_display
            trade.entry_digit = entry_digit
            trade.exit_digit = exit_digit
            if buy_price is not None:
                trade.buy_price = buy_price
            if payout is not None:
                trade.payout = payout
            trade.app_markup_amount = app_markup_amount
            trade.commission = commission
            trade.cumulative_profit = state.total_profit
            trade.drawdown = state.current_drawdown
            session.flush()
            if trade.bulk_batch_id and trade.managed_account_id is not None:
                member = session.scalar(
                    select(BulkExecutionMember).where(
                        BulkExecutionMember.batch_id == trade.bulk_batch_id,
                        BulkExecutionMember.managed_account_id == trade.managed_account_id,
                    )
                )
                if member is not None:
                    member.profit = float(profit)
                    member.outcome = str(outcome).upper()
                    member.buy_price = trade.buy_price
                    member.payout = trade.payout
                    member.provider_start_time = trade.provider_start_time
                    member.provider_expiry_time = trade.provider_expiry_time
                    member.entry_spot = entry_tick
                batch = session.get(BulkExecutionBatch, trade.bulk_batch_id, with_for_update=True)
                batch_trades = session.scalars(
                    select(Trade).where(Trade.bulk_batch_id == trade.bulk_batch_id)
                ).all()
                if batch is not None:
                    starts = {row.provider_start_time for row in batch_trades if row.provider_start_time is not None}
                    entries = {row.entry_tick for row in batch_trades if row.entry_tick is not None}
                    expiries = {row.provider_expiry_time for row in batch_trades if row.provider_expiry_time is not None}
                    outcomes = {row.outcome for row in batch_trades if row.outcome}
                    purchases = sorted(
                        row.provider_purchase_time or row.purchase_time
                        for row in batch_trades
                        if row.provider_purchase_time or row.purchase_time
                    )
                    batch.unique_start_time_count = len(starts) if starts else None
                    batch.unique_entry_spot_count = len(entries) if entries else None
                    batch.unique_expiry_time_count = len(expiries) if expiries else None
                    batch.unique_outcome_count = len(outcomes) if outcomes else None
                    if purchases:
                        batch.first_purchase_timestamp = purchases[0]
                        batch.last_purchase_timestamp = purchases[-1]
                        batch.execution_spread_ms = max(0.0, (purchases[-1] - purchases[0]).total_seconds() * 1000.0)
            if trade.signal_id:
                system_trade = session.scalar(
                    select(SystemModelTrade)
                    .where(
                        SystemModelTrade.signal_id == trade.signal_id,
                        SystemModelTrade.run_id == self.run_id,
                    )
                    .with_for_update()
                )
                # One copied model signal may settle on many user accounts. Use
                # only the earliest valid monetary settlement to calibrate the
                # canonical $0.50 result, even when tick settlement happened
                # first. Deriv's Trade.profit already includes quote/markup
                # economics, so app_markup_amount must never be deducted again.
                earliest_actual = session.scalar(
                    select(Trade)
                    .where(
                        Trade.signal_id == trade.signal_id,
                        Trade.settlement_time.is_not(None),
                        Trade.profit.is_not(None),
                        Trade.buy_price.is_not(None),
                        Trade.buy_price > 0,
                    )
                    .order_by(
                        func.coalesce(
                            Trade.provider_purchase_time,
                            Trade.purchase_time,
                        ).asc(),
                        Trade.id.asc(),
                    )
                    .limit(1)
                )
                if system_trade is not None and earliest_actual is not None:
                    canonical_stake = 0.50
                    actual_stake = float(earliest_actual.buy_price or 0.0)
                    normalized_profit = (
                        float(earliest_actual.profit or 0.0)
                        * canonical_stake
                        / actual_stake
                    )
                    system_trade.reference_base_stake = canonical_stake
                    system_trade.is_virtual = False
                    system_trade.fixed_stake_profit = round(normalized_profit, 8)
                    # Canonical direction remains tick/model determined. Copied
                    # account outcomes are retained for consistency diagnostics.
                    if system_trade.settlement_timestamp is None:
                        system_trade.settlement_timestamp = (
                            earliest_actual.provider_settlement_time
                            or earliest_actual.settlement_time
                            or utc_now()
                        )
            return True

    def completed_outcomes(self) -> tuple[int, int]:
        """Return one outcome per fully settled copy-trade signal."""
        with self.database.session() as session:
            rows = session.execute(
                select(
                    Trade.signal_id,
                    func.sum(
                        case((Trade.settlement_time.is_(None), 1), else_=0)
                    ).label("open_count"),
                    func.sum(
                        case((Trade.outcome == "LOSS", 1), else_=0)
                    ).label("loss_count"),
                )
                .where(
                    self._current_run_trade_filter(),
                    Trade.model_version == self.config.model.version,
                )
                .group_by(Trade.signal_id)
            ).all()
        wins = sum(
            int(row.open_count or 0) == 0 and int(row.loss_count or 0) == 0
            for row in rows
        )
        losses = sum(
            int(row.open_count or 0) == 0 and int(row.loss_count or 0) > 0
            for row in rows
        )
        return int(wins), int(losses)

    def unresolved_contracts(self) -> list[Trade]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(Trade).where(
                        Trade.settlement_time.is_(None),
                        self._current_run_trade_filter(),
                    )
                ).all()
            )

    def unresolved_contract_ids(self) -> set[int]:
        """Return durable open contract IDs for runtime lock reconciliation."""
        with self.database.session() as session:
            values = session.scalars(
                select(Trade.contract_id).where(
                    Trade.settlement_time.is_(None),
                    self._current_run_trade_filter(),
                )
            ).all()
        result: set[int] = set()
        for value in values:
            try:
                result.add(int(value))
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _normalize_control_status(status: str, pause_reason: str = "") -> tuple[str, str]:
        if str(status or "").upper() in {"EMERGENCY_STOP", "MANUAL_PAUSE", "STOPPED"}:
            return "RUNNING", ""
        return str(status or "STOPPED"), str(pause_reason or "")

    def set_status(self, status: str, pause_reason: str = "") -> None:
        status, pause_reason = self._normalize_control_status(status, pause_reason)
        with self.database.session() as session:
            state = session.get(BotState, self.run_id)
            if state:
                state.status = status
                state.pause_reason = pause_reason
                state.last_heartbeat = utc_now()

    def heartbeat(self, connection_id: str = "") -> None:
        with self.database.session() as session:
            state = session.get(BotState, self.run_id)
            if state:
                state.last_heartbeat = utc_now()
                if connection_id:
                    state.current_connection_id = connection_id

    def worker_heartbeat(self) -> str | None:
        with self.database.session() as session:
            heartbeat = session.scalar(
                select(BotState.last_heartbeat).where(BotState.run_id == self.run_id)
            )
        return heartbeat.isoformat() if heartbeat else None

    def update_account_balance(
        self,
        *,
        account_id: str,
        balance: float,
        currency: str,
        status: str = "active",
    ) -> None:
        masked = mask_account_id(account_id)
        with self.database.session() as session:
            row = session.scalar(
                select(AccountSnapshot).where(
                    AccountSnapshot.run_id == self.run_id,
                    AccountSnapshot.account_id_masked == masked,
                )
            )
            if row is None:
                row = AccountSnapshot(
                    run_id=self.run_id,
                    account_id_masked=masked,
                )
                session.add(row)
            row.balance = float(balance)
            row.currency = str(currency or "USD")
            row.status = str(status or "active")
            row.updated_at = utc_now()

    def control_state(self) -> tuple[str, str]:
        with self.database.session() as session:
            state = session.get(BotState, self.run_id)
            if not state:
                return ("RUNNING", "")
            return self._normalize_control_status(state.status, state.pause_reason)

    def _runtime_guard_state(
        self,
        status: str,
        pause_reason: str = "",
    ) -> dict[str, Any]:
        guard_paused = False
        guard_reason = ""
        updated_at = ""
        shadow_outcomes: list[str] = []
        state_path = Path(self.config.files.state)
        if not state_path.is_absolute():
            state_path = Path.cwd() / state_path

        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            bot_state = payload.get("bot", {}) if isinstance(payload, dict) else {}
            guard_paused = bool(bot_state.get("regime_guard_paused", False))
            guard_reason = str(bot_state.get("regime_guard_reason", ""))
            updated_at = str(bot_state.get("updated_at", ""))
            shadow_outcomes = [
                str(value).upper()
                for value in bot_state.get("shadow_outcomes", [])
                if str(value).upper() in {"WIN", "LOSS"}
            ]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        guard_paused = guard_paused and self.config.recovery.regime_guard_enabled

        shadow_sample_target = self.config.recovery.shadow_min_samples
        latest_shadow_outcomes = shadow_outcomes[-shadow_sample_target:]
        shadow_wins = sum(value == "WIN" for value in latest_shadow_outcomes)
        shadow_losses = sum(value == "LOSS" for value in latest_shadow_outcomes)
        shadow_samples = len(latest_shadow_outcomes)
        shadow_win_rate = shadow_wins / shadow_samples if shadow_samples else 0.0

        status = str(status or "STOPPED").upper()
        running = status == "RUNNING"
        if running and guard_paused:
            activity_mode = "learning"
            activity_label = "Market analysis"
            activity_message = "Risk filter is evaluating the market"
            activity_detail = "New entries wait while the risk filter evaluates market conditions."
        elif running:
            activity_mode = "trading"
            activity_label = "Bot online"
            activity_message = "Scanning all configured markets"
            activity_detail = "The worker is online and evaluating incoming ticks."
        elif status == "RECONNECTING":
            activity_mode = "reconnecting"
            activity_label = "Connection recovery"
            activity_message = "Market stream reconnecting"
            activity_detail = (
                "Trading waits for a fresh stream before evaluating another entry."
            )
        else:
            activity_mode = "trading"
            activity_label = "Bot online"
            activity_message = "Scanning all configured markets"
            activity_detail = "The worker is online and evaluating incoming ticks."

        return {
            "regime_guard_paused": guard_paused,
            "regime_guard_reason": guard_reason,
            "regime_guard_updated_at": updated_at,
            "shadow_latest_samples": shadow_samples,
            "shadow_latest_wins": shadow_wins,
            "shadow_latest_losses": shadow_losses,
            "shadow_latest_win_rate": shadow_win_rate,
            "shadow_required_win_rate": self.config.recovery.resume_above_shadow_win_rate,
            "ai_activity_mode": activity_mode,
            "ai_activity_label": activity_label,
            "ai_activity_message": activity_message,
            "ai_activity_detail": activity_detail,
        }

    def summary(self) -> dict[str, Any]:
        today_start, today_end = self._local_period_bounds("today")
        yesterday_start, yesterday_end = self._local_period_bounds("yesterday")
        week_start, week_end = self._local_period_bounds("week")
        month_start, month_end = self._local_period_bounds("month")
        tz = self._reporting_timezone()
        local_now = utc_now().astimezone(tz)
        next_reset = (
            datetime(local_now.year, local_now.month, local_now.day, tzinfo=tz)
            + timedelta(days=1)
        )
        with self.database.session() as session:
            state = session.get(BotState, self.run_id)
            status, pause_reason = (
                self._normalize_control_status(state.status, state.pause_reason)
                if state
                else ("UNKNOWN", "")
            )
            runtime_guard_state = self._runtime_guard_state(status, pause_reason)
            candidates = session.scalar(
                select(func.count()).select_from(CandidateSignalRecord).where(
                    CandidateSignalRecord.run_id == self.run_id,
                    CandidateSignalRecord.generated_timestamp >= today_start,
                    CandidateSignalRecord.generated_timestamp < today_end,
                )
            )
            today_trade_filter = self._trade_period_filter(today_start, today_end)
            purchased = session.scalar(
                select(func.count()).select_from(Trade).where(*today_trade_filter)
            )
            wins = session.scalar(
                select(func.count()).select_from(Trade).where(
                    Trade.outcome == "WIN",
                    *today_trade_filter,
                )
            )
            losses = session.scalar(
                select(func.count()).select_from(Trade).where(
                    Trade.outcome == "LOSS",
                    *today_trade_filter,
                )
            )
            open_trades = session.scalar(
                select(func.count()).select_from(Trade).where(
                    Trade.settlement_time.is_(None),
                    self._current_run_trade_filter(),
                )
            )
            open_trade_rows = session.scalars(
                select(Trade)
                .where(
                    Trade.settlement_time.is_(None),
                    self._current_run_trade_filter(),
                )
                .order_by(Trade.purchase_time.asc())
            ).all()
            skipped = session.scalar(
                select(func.count()).select_from(CandidateSignalRecord).where(
                    CandidateSignalRecord.run_id == self.run_id,
                    CandidateSignalRecord.final_status.like("SKIP%"),
                    CandidateSignalRecord.generated_timestamp >= today_start,
                    CandidateSignalRecord.generated_timestamp < today_end,
                )
            )
            total_managed_accounts = session.scalar(
                select(func.count()).select_from(ManagedAccount).where(
                    ManagedAccount.enabled.is_(True)
                )
            )
            accounts = session.scalars(
                select(AccountSnapshot)
                .where(AccountSnapshot.run_id == self.run_id)
                .order_by(AccountSnapshot.account_id_masked)
            ).all()
            account_trade_rows = session.execute(
                select(
                    Trade.account_id_masked,
                    func.count().label("trades"),
                    func.sum(case((Trade.outcome == "WIN", 1), else_=0)).label("wins"),
                    func.sum(case((Trade.outcome == "LOSS", 1), else_=0)).label("losses"),
                    func.sum(Trade.profit).label("profit"),
                )
                .where(*today_trade_filter)
                .group_by(Trade.account_id_masked)
                .order_by(Trade.account_id_masked)
            ).all()
            trade_stats_by_account = {
                str(row.account_id_masked): {
                    "trades": int(row.trades or 0),
                    "wins": int(row.wins or 0),
                    "losses": int(row.losses or 0),
                    "profit": float(row.profit or 0.0),
                }
                for row in account_trade_rows
            }
            protection_rows = session.scalars(select(AccountRiskState)).all()
            protection_by_account = {
                str(row.account_id_masked): {
                    "mode": (
                        "VIRTUAL_MODE"
                        if row.protection_mode == "VIRTUAL_WAITING_FOR_WIN"
                        else "RECOVERY_PENDING"
                        if row.protection_mode == "REAL_RECOVERY_PENDING"
                        else "NORMAL_MODE"
                    ),
                    "state": row.protection_mode,
                    "account": row.account_id_masked,
                    "consecutive_actual_losses": int(row.consecutive_losses or 0),
                    "actual_recovery_debt": float(row.recovery_loss_debt or 0.0),
                    "virtual_observations": int(row.virtual_observation_count or 0),
                    "virtual_wins": int(row.virtual_win_count or 0),
                    "virtual_losses": int(row.virtual_loss_count or 0),
                    "current_virtual_loss_streak": int(
                        row.current_virtual_loss_streak or 0
                    ),
                    "entered_virtual_mode_at": (
                        row.entered_virtual_mode_at.isoformat()
                        if row.entered_virtual_mode_at
                        else None
                    ),
                    "recovery_pending_since": (
                        row.recovery_pending_since.isoformat()
                        if row.recovery_pending_since
                        else None
                    ),
                }
                for row in protection_rows
                if row.account_id_masked
            }
            virtual_row = session.execute(
                select(
                    func.count().label("observations"),
                    func.sum(case((VirtualTrade.result == "VIRTUAL_WIN", 1), else_=0)).label("wins"),
                    func.sum(case((VirtualTrade.result == "VIRTUAL_LOSS", 1), else_=0)).label("losses"),
                ).where(
                    VirtualTrade.run_id == self.run_id,
                    VirtualTrade.created_at >= today_start,
                    VirtualTrade.created_at < today_end,
                )
            ).one()
            active_virtual_accounts = session.scalar(
                select(func.count())
                .select_from(AccountRiskState)
                .where(AccountRiskState.protection_mode == "VIRTUAL_WAITING_FOR_WIN")
            )
            recovery_pending_accounts = session.scalar(
                select(func.count())
                .select_from(AccountRiskState)
                .where(AccountRiskState.protection_mode == "REAL_RECOVERY_PENDING")
            )
            settled_trades = session.scalars(
                select(Trade)
                .where(
                    Trade.settlement_time.is_not(None),
                    *today_trade_filter,
                )
                .order_by(Trade.settlement_time.asc(), Trade.id.asc())
            ).all()
            longest_win_streak = 0
            longest_loss_streak = 0
            current_outcome = ""
            current_length = 0
            computed_net_profit = 0.0
            computed_high_water_mark = 0.0
            computed_max_drawdown = 0.0
            for trade in settled_trades:
                outcome = str(trade.outcome or "").upper()
                computed_net_profit += float(trade.profit or 0.0)
                computed_high_water_mark = max(computed_high_water_mark, computed_net_profit)
                computed_max_drawdown = max(
                    computed_max_drawdown,
                    computed_high_water_mark - computed_net_profit,
                )
                if outcome == current_outcome:
                    current_length += 1
                else:
                    current_outcome = outcome
                    current_length = 1
                if outcome == "WIN":
                    longest_win_streak = max(longest_win_streak, current_length)
                elif outcome == "LOSS":
                    longest_loss_streak = max(longest_loss_streak, current_length)
            now = utc_now()
            oldest_open_trade_seconds = 0
            stale_open_trades = 0
            max_open_trade_seconds = max(1, int(self.config.trade.max_open_trade_seconds))
            if open_trade_rows:
                oldest_open_trade_seconds = max(
                    0,
                    int(
                        max(
                            (now - trade.purchase_time).total_seconds()
                            for trade in open_trade_rows
                        )
                    ),
                )
                stale_open_trades = sum(
                    (now - trade.purchase_time).total_seconds() > max_open_trade_seconds
                    for trade in open_trade_rows
                )
            master_account_id = os.getenv("COPYTRADING_MASTER_ACCOUNT_ID", "").strip()
            master_account_masked = mask_account_id(master_account_id) if master_account_id else ""
            primary_account = (
                next(
                    (
                        account
                        for account in accounts
                        if account.account_id_masked == master_account_masked
                    ),
                    None,
                )
                if master_account_masked
                else None
            )
            if primary_account is None:
                primary_account = accounts[0] if accounts else None
            yesterday_stats = self._period_trade_stats(
                session,
                yesterday_start,
                yesterday_end,
            )
            week_stats = self._period_trade_stats(session, week_start, week_end)
            month_stats = self._period_trade_stats(session, month_start, month_end)
            runtime_mode_row = session.get(RuntimePreference, "trading_mode")
            runtime_mode = (
                runtime_mode_row.preference_value
                if runtime_mode_row
                else self.config.deriv.environment
            ).strip().lower()
            if runtime_mode not in {"demo", "real"}:
                runtime_mode = "demo"
            return {
                "run_id": self.config.model.run_id,
                "status": status,
                "pause_reason": state.pause_reason if state else "",
                "mode": runtime_mode,
                **runtime_guard_state,
                "candidate_signals": int(candidates or 0),
                "purchased_trades": int(purchased or 0),
                "open_trades": int(open_trades or 0),
                "stale_open_trades": int(stale_open_trades),
                "oldest_open_trade_seconds": int(oldest_open_trade_seconds),
                "max_open_trade_seconds": max_open_trade_seconds,
                "skipped_signals": int(skipped or 0),
                "wins": int(wins or 0),
                "losses": int(losses or 0),
                "longest_win_streak": longest_win_streak,
                "longest_loss_streak": longest_loss_streak,
                "win_rate": (
                    int(wins or 0) / (int(wins or 0) + int(losses or 0))
                    if int(wins or 0) + int(losses or 0)
                    else 0.0
                ),
                "net_profit": computed_net_profit,
                "maximum_drawdown": computed_max_drawdown,
                "total_traders": int(len(accounts) or total_managed_accounts or 0),
                "accounts": [
                    {
                        "account": account.account_id_masked,
                        "balance": account.balance,
                        "currency": account.currency,
                        "status": account.status,
                        "updated_at": account.updated_at.isoformat(),
                        **trade_stats_by_account.get(
                            account.account_id_masked,
                            {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},
                        ),
                        "virtual_protection": protection_by_account.get(
                            account.account_id_masked,
                            {
                                "mode": "NORMAL_MODE",
                                "state": "NORMAL_MODE",
                                "account": account.account_id_masked,
                                "consecutive_actual_losses": 0,
                                "actual_recovery_debt": 0.0,
                                "virtual_observations": 0,
                                "virtual_wins": 0,
                                "virtual_losses": 0,
                                "current_virtual_loss_streak": 0,
                                "entered_virtual_mode_at": None,
                                "recovery_pending_since": None,
                            },
                        ),
                    }
                    for account in accounts
                ],
                "virtual_protection": {
                    "enabled": bool(self.config.virtual_protection.enabled),
                    "trigger_actual_losses": int(
                        self.config.virtual_protection.trigger_actual_losses
                    ),
                    "exit_after_wins": int(
                        self.config.virtual_protection.exit_after_wins
                    ),
                    "max_observations": int(
                        self.config.virtual_protection.max_observations
                    ),
                    "scope": self.config.virtual_protection.scope,
                    "observations": int(virtual_row.observations or 0),
                    "wins": int(virtual_row.wins or 0),
                    "losses": int(virtual_row.losses or 0),
                    "active_accounts": int(active_virtual_accounts or 0),
                    "recovery_pending_accounts": int(recovery_pending_accounts or 0),
                },
                "primary_account": primary_account.account_id_masked if primary_account else "",
                "primary_account_balance": primary_account.balance if primary_account else 0.0,
                "primary_account_currency": primary_account.currency if primary_account else "USD",
                "account_balance_total": sum(account.balance for account in accounts),
                "reporting_timezone": str(tz.key),
                "today_started_at": today_start.isoformat(),
                "next_daily_reset_at": next_reset.astimezone(timezone.utc).isoformat(),
                "yesterday_profit": float(yesterday_stats["profit"]),
                "yesterday_combined_runs": int(yesterday_stats["trades"]),
                "this_week_profit": float(week_stats["profit"]),
                "this_month_profit": float(month_stats["profit"]),
                "last_heartbeat": (
                    state.last_heartbeat.isoformat() if state and state.last_heartbeat else None
                ),
            }

    def hourly_execution_report(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        timezone_name: str = "Africa/Nairobi",
    ) -> dict[str, Any]:
        tz = ZoneInfo(timezone_name)
        start = window_start.astimezone(timezone.utc)
        end = window_end.astimezone(timezone.utc)
        local_end = end.astimezone(tz)
        today_start = datetime(
            local_end.year,
            local_end.month,
            local_end.day,
            tzinfo=tz,
        ).astimezone(timezone.utc)
        canonical_trades = self.system_model_trades(
            start=min(today_start, start),
            end=end,
            include_virtual=False,
        )
        hourly = self.system_performance_summary(
            start=start,
            end=end,
            simulated_base_stake=0.50,
            include_virtual=False,
            trades=canonical_trades,
        )
        today = self.system_performance_summary(
            start=today_start,
            end=end,
            simulated_base_stake=0.50,
            include_virtual=False,
            trades=canonical_trades,
        )
        with self.database.session() as session:
            active_accounts = session.scalar(
                select(func.count()).select_from(ManagedAccount).where(
                    ManagedAccount.enabled.is_(True),
                    ManagedAccount.execution_status == "active",
                )
            )
        return {
            "timezone": str(tz.key),
            "window_start": start.astimezone(tz).isoformat(),
            "window_end": end.astimezone(tz).isoformat(),
            "hourly_trades": int(hourly["total_trades"]),
            "hourly_martingale_pnl": float(hourly["martingale_pnl"]),
            "hourly_flat_pnl": float(hourly["fixed_pnl"]),
            "active_accounts": int(active_accounts or 0),
            "today_martingale_pnl": float(today["martingale_pnl"]),
        }

    @staticmethod
    def current_consecutive_streaks(outcomes: list[str]) -> tuple[int, int]:
        normalized = [str(outcome or "").upper() for outcome in outcomes]
        if not normalized or normalized[0] not in {"WIN", "LOSS"}:
            return 0, 0
        latest = normalized[0]
        length = 0
        for outcome in normalized:
            if outcome != latest:
                break
            length += 1
        return (length, 0) if latest == "WIN" else (0, length)

    def recent_trades(
        self,
        limit: int = 50,
        *,
        account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        account_masked = mask_account_id(account_id) if account_id else ""
        with self.database.session() as session:
            query = (
                select(
                    Trade,
                    CandidateSignalRecord.symbol,
                    CandidateSignalRecord.contract_type,
                )
                .join(
                    CandidateSignalRecord,
                    CandidateSignalRecord.signal_id == Trade.signal_id,
                )
                .where(self._current_run_trade_filter())
            )
            if account_masked:
                query = query.where(Trade.account_id_masked == account_masked)
            trade_rows = session.execute(
                query
                .order_by(Trade.purchase_time.desc())
                .limit(limit)
            ).all()
            settlement_sla = float(self.config.trade.settlement_sla_seconds)
            results: list[dict[str, Any]] = []
            for trade, symbol, contract_type in trade_rows:
                lifecycle_seconds = max(
                    0.0,
                    ((trade.settlement_time or utc_now()) - trade.purchase_time).total_seconds(),
                )
                provider_lifecycle_seconds = None
                if trade.provider_purchase_time and trade.provider_settlement_time:
                    provider_lifecycle_seconds = max(
                        0.0,
                        (
                            trade.provider_settlement_time
                            - trade.provider_purchase_time
                        ).total_seconds(),
                    )
                settlement_delivery_seconds = None
                if trade.settlement_time and trade.provider_settlement_time:
                    settlement_delivery_seconds = max(
                        0.0,
                        (
                            trade.settlement_time - trade.provider_settlement_time
                        ).total_seconds(),
                    )
                duration_value = max(1, int(trade.contract_duration or 1))
                duration_unit = str(trade.contract_duration_unit or "t")
                duration_label = (
                    f"{duration_value} tick{'s' if duration_value != 1 else ''}"
                    if duration_unit == "t"
                    else f"{duration_value}{duration_unit}"
                )
                results.append({
                    "contract_id": trade.contract_id,
                    "symbol": str(symbol),
                    "contract_type": str(contract_type),
                    "account": trade.account_id_masked,
                    "purchase_time": trade.purchase_time.isoformat(),
                    "settlement_time": (
                        trade.settlement_time.isoformat() if trade.settlement_time else None
                    ),
                    "provider_purchase_time": (
                        trade.provider_purchase_time.isoformat()
                        if trade.provider_purchase_time
                        else None
                    ),
                    "provider_start_time": (
                        trade.provider_start_time.isoformat()
                        if trade.provider_start_time
                        else None
                    ),
                    "provider_expiry_time": (
                        trade.provider_expiry_time.isoformat()
                        if trade.provider_expiry_time
                        else None
                    ),
                    "provider_settlement_time": (
                        trade.provider_settlement_time.isoformat()
                        if trade.provider_settlement_time
                        else None
                    ),
                    "contract_duration": duration_value,
                    "contract_duration_unit": duration_unit,
                    "duration_label": duration_label,
                    "outcome": trade.outcome,
                    "profit": trade.profit,
                    "buy_price": trade.buy_price,
                    "payout": trade.payout,
                    "app_markup_amount": trade.app_markup_amount,
                    "commission": trade.commission,
                    "entry_tick": trade.entry_tick,
                    "exit_tick": trade.exit_tick,
                    "entry_spot": trade.entry_spot_display or trade.entry_tick,
                    "exit_spot": trade.exit_spot_display or trade.exit_tick,
                    "entry_digit": trade.entry_digit,
                    "exit_digit": trade.exit_digit,
                    "actual_last_digit": trade.exit_digit,
                    "closure_summary": (
                        f"{trade.outcome} exit digit {trade.exit_digit}"
                        if trade.settlement_time is not None and trade.exit_digit is not None
                        else (
                            f"{trade.outcome} settled"
                            if trade.settlement_time is not None
                            else "Awaiting settlement"
                        )
                    ),
                    # Retain age_seconds for API compatibility, but no longer
                    # truncate it or present it as contractual duration.
                    "age_seconds": round(lifecycle_seconds, 3),
                    "lifecycle_seconds": round(lifecycle_seconds, 3),
                    "provider_lifecycle_seconds": (
                        round(provider_lifecycle_seconds, 3)
                        if provider_lifecycle_seconds is not None
                        else None
                    ),
                    "settlement_delivery_seconds": (
                        round(settlement_delivery_seconds, 3)
                        if settlement_delivery_seconds is not None
                        else None
                    ),
                    "settlement_sla_seconds": settlement_sla,
                    "settlement_sla_status": (
                        "OPEN"
                        if trade.settlement_time is None
                        else "MET"
                        if lifecycle_seconds <= settlement_sla
                        else "LATE"
                    ),
                    "aligned_with_signal": trade.aligned_with_signal,
                })
            return results

    def recent_virtual_trades(
        self,
        limit: int = 50,
        *,
        account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        account_masked = mask_account_id(account_id) if account_id else ""
        with self.database.session() as session:
            query = select(VirtualTrade).where(VirtualTrade.run_id == self.run_id)
            if account_masked:
                query = query.where(VirtualTrade.account_id_masked == account_masked)
            rows = session.scalars(
                query.order_by(VirtualTrade.created_at.desc()).limit(limit)
            ).all()
        def elapsed_seconds(start: datetime, end: datetime | None) -> float:
            stop = end or utc_now()
            if start.tzinfo is None and stop.tzinfo is not None:
                start = start.replace(tzinfo=timezone.utc)
            if stop.tzinfo is None and start.tzinfo is not None:
                stop = stop.replace(tzinfo=timezone.utc)
            return max(0.0, (stop - start).total_seconds())

        return [
            {
                "contract_id": row.virtual_trade_id,
                "symbol": row.market,
                "contract_type": row.contract_type,
                "account": row.account_id_masked,
                "purchase_time": row.created_at.isoformat(),
                "settlement_time": row.settled_at.isoformat() if row.settled_at else None,
                "contract_duration": int(row.duration or 1),
                "contract_duration_unit": row.duration_unit,
                "duration_label": (
                    f"{int(row.duration or 1)} tick"
                    f"{'s' if int(row.duration or 1) != 1 else ''}"
                ),
                "outcome": row.result,
                "mode": "VIRTUAL",
                "activity_type": "VIRTUAL_TRADE",
                "profit": 0.0,
                "buy_price": 0.0,
                "payout": None,
                "app_markup_amount": None,
                "commission": None,
                "entry_tick": row.entry_spot,
                "exit_tick": row.exit_spot,
                "exit_digit": row.actual_last_digit,
                "closure_summary": (
                    f"{row.result.replace('_', ' ').title()} - $0 financial impact"
                    if row.result != "OPEN"
                    else "Virtual observation open"
                ),
                "age_seconds": round(elapsed_seconds(row.created_at, row.settled_at), 3),
                "lifecycle_seconds": round(
                    elapsed_seconds(row.created_at, row.settled_at),
                    3,
                ),
                "provider_lifecycle_seconds": None,
                "settlement_delivery_seconds": None,
                "settlement_sla_seconds": float(self.config.trade.settlement_sla_seconds),
                "settlement_sla_status": "VIRTUAL",
                "aligned_with_signal": True,
                "simulated_stake": row.simulated_stake,
                "expected_payout": row.expected_payout,
                "actual_profit_loss": row.actual_profit_loss,
                "recovery_debt_change": row.recovery_debt_change,
            }
            for row in rows
        ]

    def recent_activity(
        self,
        limit: int = 50,
        *,
        account_id: str | None = None,
        activity_type: str = "actual",
    ) -> list[dict[str, Any]]:
        normalized = str(activity_type or "actual").strip().lower()
        if normalized == "virtual":
            return self.recent_virtual_trades(limit, account_id=account_id)
        if normalized == "all":
            actual = [
                {**row, "mode": "ACTUAL", "activity_type": "ACTUAL_TRADE"}
                for row in self.recent_trades(limit, account_id=account_id)
            ]
            virtual = self.recent_virtual_trades(limit, account_id=account_id)
            combined = actual + virtual
            combined.sort(
                key=lambda row: str(row.get("purchase_time") or ""),
                reverse=True,
            )
            return combined[:limit]
        return [
            {**row, "mode": "ACTUAL", "activity_type": "ACTUAL_TRADE"}
            for row in self.recent_trades(limit, account_id=account_id)
        ]

    def markup_summary(self, *, account_id: str) -> dict[str, Any]:
        account_masked = mask_account_id(account_id)
        with self.database.session() as session:
            row = session.execute(
                select(
                    func.count().label("contract_count"),
                    func.sum(
                        case(
                            (Trade.app_markup_amount > 0, 1),
                            else_=0,
                        )
                    ).label("confirmed_contract_count"),
                    func.sum(Trade.app_markup_amount).label("app_markup_total"),
                    func.sum(Trade.commission).label("commission_total"),
                ).where(
                    Trade.account_id_masked == account_masked,
                    Trade.settlement_time.is_not(None),
                    self._current_run_trade_filter(),
                )
            ).one()
        contract_count = int(row.contract_count or 0)
        confirmed_contract_count = int(row.confirmed_contract_count or 0)
        if contract_count == 0:
            status = "AWAITING_CONTRACT"
        elif confirmed_contract_count == contract_count:
            status = "CONFIRMED"
        elif confirmed_contract_count > 0:
            status = "PARTIAL"
        else:
            status = "NOT_CONFIRMED"
        return {
            "account": account_masked,
            "contract_count": contract_count,
            "confirmed_contract_count": confirmed_contract_count,
            "unconfirmed_contract_count": contract_count - confirmed_contract_count,
            "status": status,
            "expected_percentage": float(self.config.deriv.app_markup_percentage),
            "app_markup_total": float(row.app_markup_total or 0.0),
            "commission_total": float(row.commission_total or 0.0),
        }

    def recent_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.session() as session:
            signals = session.scalars(
                select(CandidateSignalRecord)
                .where(CandidateSignalRecord.run_id == self.run_id)
                .order_by(CandidateSignalRecord.generated_timestamp.desc())
                .limit(limit)
            ).all()
            results: list[dict[str, Any]] = []
            for signal in signals:
                decision = session.scalar(
                    select(ModelDecisionRecord).where(
                        ModelDecisionRecord.signal_id == signal.signal_id
                    )
                )
                results.append(
                    {
                        "signal_id": signal.signal_id,
                        "symbol": signal.symbol,
                        "generated_at": signal.generated_timestamp.isoformat(),
                        "trigger_name": signal.trigger_name,
                        "trigger_digits": signal.trigger_digits,
                        "final_status": signal.final_status,
                        "stale": signal.stale,
                        "consumed": signal.consumed,
                        "signal_last_digit": signal.signal_last_digit,
                        "ticks_between_signal_and_purchase": signal.ticks_between_signal_and_purchase,
                        "decision": decision.final_decision if decision else None,
                        "rejection_reasons": decision.rejection_reasons if decision else [],
                    }
                )
            return results

    def audit(self, action: str, actor: str, source_ip: str, details: dict) -> None:
        with self.database.session() as session:
            session.add(
                AuditEvent(
                    action=action,
                    actor=actor,
                    source_ip=source_ip,
                    details=details,
                )
            )

    def record_model_artifact(
        self,
        *,
        model_type: str,
        model_version: str,
        storage_location: str,
        metadata: dict,
        checksum: str,
    ) -> None:
        with self.database.session() as session:
            session.add(
                ModelArtifact(
                    model_type=model_type,
                    model_version=model_version,
                    storage_location=storage_location,
                    artifact_metadata=metadata,
                    checksum=checksum,
                    active_status=True,
                )
            )

    def acquire_lease(
        self,
        *,
        lease_key: str,
        worker_id: str,
        host_name: str,
        process_id: int,
        deployment_id: str,
        ttl_seconds: int = 30,
    ) -> bool:
        now = utc_now()
        with self.database.session() as session:
            lease = session.scalar(
                select(TraderLease)
                .where(TraderLease.lease_key == lease_key)
                .with_for_update()
            )
            if lease and lease.worker_id != worker_id:
                expiry = lease.expires_at
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                owner_alive = True
                if lease.host_name == socket.gethostname():
                    try:
                        os.kill(lease.process_id, 0)
                    except OSError:
                        owner_alive = False
                if expiry > now and owner_alive:
                    return False
            if lease is None:
                lease = TraderLease(
                    lease_key=lease_key,
                    worker_id=worker_id,
                    host_name=host_name,
                    process_id=process_id,
                    deployment_id=deployment_id,
                    heartbeat_at=now,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                )
                session.add(lease)
            else:
                lease.worker_id = worker_id
                lease.host_name = host_name
                lease.process_id = process_id
                lease.deployment_id = deployment_id
                lease.heartbeat_at = now
                lease.expires_at = now + timedelta(seconds=ttl_seconds)
            return True

    def release_lease(self, lease_key: str, worker_id: str) -> None:
        with self.database.session() as session:
            lease = session.get(TraderLease, lease_key)
            if lease and lease.worker_id == worker_id:
                session.delete(lease)

    def record_system_model_trade(
        self,
        *,
        signal_id: str,
        symbol: str,
        direction: str,
        contract_type: str,
        duration_ticks: int,
        entry_tick_sequence: int,
        entry_spot: float,
        expected_profit_ratio: float,
        reference_base_stake: float = 0.50,
        is_virtual: bool = False,
    ) -> bool:
        now = utc_now()
        with self.database.session() as session:
            trade = SystemModelTrade(
                run_id=self.run_id,
                signal_id=str(signal_id),
                symbol=str(symbol),
                direction=str(direction),
                contract_type=str(contract_type),
                duration_ticks=int(duration_ticks),
                entry_tick_sequence=int(entry_tick_sequence),
                expiry_tick_sequence=int(entry_tick_sequence) + int(duration_ticks),
                entry_spot=float(entry_spot),
                signal_timestamp=now,
                entry_timestamp=now,
                # The canonical ledger is account-independent and permanently
                # denominated at $0.50. Viewer stakes are replay inputs only.
                reference_base_stake=0.50,
                expected_profit_ratio=max(0.0, float(expected_profit_ratio)),
                # Canonical rows are created only after a real contract is
                # registered. Account virtual protection lives exclusively in
                # AccountRiskState/VirtualTrade and cannot classify this row.
                is_virtual=False,
            )
            session.merge(trade)
            return False

    def settle_due_system_model_trades(
        self,
        *,
        symbol: str,
        tick_sequence: int,
        exit_spot: float,
    ) -> list[dict[str, Any]]:
        """Settle canonical model events once, independently of user contracts."""
        now = utc_now()
        settled: list[dict[str, Any]] = []
        with self.database.session() as session:
            rows = session.scalars(
                select(SystemModelTrade).where(
                    SystemModelTrade.run_id == self.run_id,
                    SystemModelTrade.symbol == str(symbol),
                    SystemModelTrade.outcome.is_(None),
                    SystemModelTrade.expiry_tick_sequence <= int(tick_sequence),
                    exists().where(Trade.signal_id == SystemModelTrade.signal_id),
                ).with_for_update()
            ).all()
            if not rows:
                return settled
            for trade in rows:
                outcome = shadow_outcome(
                    trade.direction,
                    Decimal(str(trade.entry_spot)),
                    Decimal(str(exit_spot)),
                )
                trade.outcome = outcome
                trade.is_virtual = False
                trade.exit_spot = float(exit_spot)
                trade.settlement_timestamp = now
                ratio = max(0.0, float(trade.expected_profit_ratio or 0.0))
                trade.fixed_stake_profit = ratio * 0.50 if outcome == "WIN" else -0.50
                settled.append(
                    {
                        "signal_id": trade.signal_id,
                        "outcome": outcome,
                        "is_virtual": False,
                    }
                )
        return settled

    def system_model_trades(
        self,
        *,
        start: datetime,
        end: datetime,
        include_virtual: bool = True,
        viewer_managed_account_id: int | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(SystemModelTrade)
                .where(
                    SystemModelTrade.run_id == self.run_id,
                    SystemModelTrade.signal_timestamp >= start,
                    SystemModelTrade.signal_timestamp < end,
                    SystemModelTrade.outcome.in_(["WIN", "LOSS"]),
                    exists().where(Trade.signal_id == SystemModelTrade.signal_id),
                )
                .order_by(SystemModelTrade.signal_timestamp.asc(), SystemModelTrade.id.asc())
            ).all()
            signal_ids = [row.signal_id for row in rows]
            earliest_actual_by_signal: dict[str, Trade] = {}
            viewer_actual_by_signal: dict[str, Trade] = {}
            if signal_ids:
                actual_rows = session.scalars(
                    select(Trade)
                    .where(
                        Trade.signal_id.in_(signal_ids),
                        Trade.settlement_time.is_not(None),
                        Trade.profit.is_not(None),
                        Trade.buy_price.is_not(None),
                        Trade.buy_price > 0,
                    )
                    .order_by(
                        Trade.signal_id.asc(),
                        func.coalesce(
                            Trade.provider_purchase_time,
                            Trade.purchase_time,
                        ).asc(),
                        Trade.id.asc(),
                    )
                ).all()
                for actual in actual_rows:
                    earliest_actual_by_signal.setdefault(actual.signal_id, actual)
                    if (
                        viewer_managed_account_id is not None
                        and actual.managed_account_id == int(viewer_managed_account_id)
                    ):
                        viewer_actual_by_signal.setdefault(actual.signal_id, actual)
        results = []
        for row in rows:
            canonical_actual = earliest_actual_by_signal.get(row.signal_id)
            viewer_actual = viewer_actual_by_signal.get(row.signal_id)
            actual = viewer_actual or canonical_actual
            reference_base_stake = 0.50
            if actual is not None:
                fixed_stake_profit = (
                    float(actual.profit or 0.0)
                    * reference_base_stake
                    / float(actual.buy_price or reference_base_stake)
                )
                outcome = (
                    str(actual.outcome or row.outcome).upper()
                    if viewer_actual is not None
                    else str(row.outcome).upper()
                )
            else:
                fixed_stake_profit = float(row.fixed_stake_profit or 0.0)
                outcome = row.outcome
            results.append(
                {
                    "signal_id": row.signal_id,
                    "symbol": row.symbol,
                    "direction": row.direction,
                    "contract_type": row.contract_type,
                    "duration_ticks": row.duration_ticks,
                    "signal_timestamp": row.signal_timestamp.isoformat() if row.signal_timestamp else None,
                    "settlement_timestamp": row.settlement_timestamp.isoformat() if row.settlement_timestamp else None,
                    "outcome": outcome,
                    "is_virtual": False,
                    "reference_base_stake": reference_base_stake,
                    "fixed_stake_profit": round(fixed_stake_profit, 8),
                    "execution_source": "viewer_actual" if viewer_actual is not None else "canonical",
                    "expected_profit_ratio": float(row.expected_profit_ratio or 0.90),
                    "martingale_stake": float(row.martingale_stake or 0.0),
                    "martingale_profit": float(row.martingale_profit or 0.0),
                    "martingale_level": int(row.martingale_level or 0),
                    "recovery_debt_before": float(row.recovery_debt_before or 0.0),
                    "recovery_debt_after": float(row.recovery_debt_after or 0.0),
                }
            )
        return results

    def system_performance_summary(
        self,
        *,
        start: datetime,
        end: datetime,
        simulated_base_stake: float = 0.50,
        include_virtual: bool = False,
        trades: list[dict[str, Any]] | None = None,
        viewer_managed_account_id: int | None = None,
        observed_executions: list[ObservedExecution] | None = None,
    ) -> dict[str, Any]:
        if trades is None:
            period_trades = self.system_model_trades(
                start=start,
                end=end,
                include_virtual=include_virtual,
                viewer_managed_account_id=viewer_managed_account_id,
            )
        else:
            period_trades = []
            for trade in trades:
                timestamp_value = trade.get("signal_timestamp")
                if not timestamp_value:
                    continue
                timestamp = datetime.fromisoformat(str(timestamp_value))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if start <= timestamp < end and (
                    include_virtual or not trade.get("is_virtual")
                ):
                    period_trades.append(trade)
        real_trades = [trade for trade in period_trades if not trade["is_virtual"]]
        observed = self.observed_martingale_performance(
            start=start,
            end=end,
            executions=observed_executions,
        )
        fixed_profit = 0.0
        martingale_profit = 0.0
        current_fixed_drawdown = 0.0
        current_martingale_drawdown = 0.0
        fixed_peak = 0.0
        martingale_peak = 0.0
        max_fixed_drawdown = 0.0
        max_martingale_drawdown = 0.0
        real_wins = 0
        real_losses = 0
        current_real_win_streak = 0
        longest_real_win_streak = 0
        current_real_loss_streak = 0
        longest_real_loss_streak = 0
        fixed_stake = min(1000.0, max(0.50, float(simulated_base_stake)))
        maximum_martingale_stake = fixed_stake
        recovery_debt = 0.0
        recovery_wins_remaining = 2
        martingale_level = 0
        total_fixed_staked = 0.0
        total_martingale_staked = 0.0
        for trade in real_trades:
            outcome = str(trade["outcome"]).upper()
            ref_stake = max(0.50, float(trade.get("reference_base_stake") or 0.0))
            canonical_pnl = float(trade.get("fixed_stake_profit") or 0.0)
            realized_return_ratio = canonical_pnl / ref_stake if ref_stake > 1e-9 else 0.0
            # Recovery sizing must use proposal economics captured before this
            # contract was bought; realized profit is settlement information.
            profit_ratio = max(0.01, float(trade.get("expected_profit_ratio") or 0.90))
            fixed_pnl = fixed_stake * realized_return_ratio
            # Select the stake from debt carried into this trade.  The previous
            # implementation increased the stake after observing this trade's
            # result, retroactively multiplying the loss that triggered it.
            if recovery_debt > 0.01:
                calculation = calculate_recovery_stake(
                    base_stake=fixed_stake,
                    recovery_debt=recovery_debt,
                    pre_trade_profit_ratio=profit_ratio,
                    minimum_stake=0.50,
                )
                martingale_stake = calculation.requested_stake
            else:
                martingale_stake = fixed_stake
            maximum_martingale_stake = max(
                maximum_martingale_stake,
                martingale_stake,
            )
            martingale_pnl = (
                fixed_pnl * (martingale_stake / fixed_stake)
                if fixed_stake > 1e-9
                else 0.0
            )
            total_fixed_staked += fixed_stake
            total_martingale_staked += martingale_stake
            fixed_profit += fixed_pnl
            fixed_peak = max(fixed_peak, fixed_profit)
            current_fixed_drawdown = fixed_peak - fixed_profit
            max_fixed_drawdown = max(max_fixed_drawdown, current_fixed_drawdown)
            if outcome == "WIN":
                real_wins += 1
                current_real_loss_streak = 0
                current_real_win_streak += 1
                longest_real_win_streak = max(
                    longest_real_win_streak, current_real_win_streak
                )
                if recovery_debt > 0.01:
                    recovered = max(0.0, martingale_pnl)
                    recovery_debt = max(0.0, round(recovery_debt - recovered, 2))
                    recovery_wins_remaining = max(0, recovery_wins_remaining - 1)
                    if recovery_debt <= 0.01:
                        recovery_debt = 0.0
                        recovery_wins_remaining = 2
                        martingale_level = 0
                    else:
                        martingale_level = max(1, martingale_level - 1)
                else:
                    martingale_level = 0
            else:
                real_losses += 1
                current_real_win_streak = 0
                current_real_loss_streak += 1
                longest_real_loss_streak = max(longest_real_loss_streak, current_real_loss_streak)
                recovery_debt = round(recovery_debt + martingale_stake, 2)
                recovery_wins_remaining = 2
                martingale_level = min(martingale_level + 1, 10)
            martingale_profit += martingale_pnl
            martingale_peak = max(martingale_peak, martingale_profit)
            current_martingale_drawdown = martingale_peak - martingale_profit
            max_martingale_drawdown = max(max_martingale_drawdown, current_martingale_drawdown)
        total = real_wins + real_losses
        viewer_actual_trades = sum(
            trade.get("execution_source") == "viewer_actual" for trade in real_trades
        )
        version_material = "|".join(
            f"{trade.get('signal_id', '')}:{trade.get('outcome', '')}:"
            f"{float(trade.get('fixed_stake_profit') or 0.0):.8f}"
            for trade in real_trades
        )
        observed_stake_volume = float(observed["observed_martingale_stake_volume"])
        observed_current_drawdown = float(observed["observed_current_drawdown"])
        observed_max_drawdown = float(observed["observed_max_drawdown"])
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "simulated_base_stake": round(fixed_stake, 2),
            "flat_stake": round(fixed_stake, 2),
            # Main dashboard compatibility fields are OBSERVED actual Deriv
            # economics. Synthetic recovery replay is explicitly separate.
            "maximum_martingale_stake": observed["observed_maximum_stake"],
            "observed_maximum_stake": observed["observed_maximum_stake"],
            "simulated_maximum_martingale_stake": round(maximum_martingale_stake, 2),
            "model_data_version": hashlib.sha256(
                version_material.encode("utf-8")
            ).hexdigest()[:16],
            "total_trades": total,
            "viewer_actual_trades": viewer_actual_trades,
            "simulated_trades": total - viewer_actual_trades,
            "wins": real_wins,
            "losses": real_losses,
            "win_rate": real_wins / total if total else 0.0,
            "fixed_pnl": round(fixed_profit, 2),
            "martingale_pnl": observed["observed_martingale_pnl"],
            "observed_martingale_pnl": observed["observed_martingale_pnl"],
            "simulated_martingale_pnl": round(martingale_profit, 2),
            "observed_martingale_stake_volume": observed_stake_volume,
            "martingale_return_pct": round(
                float(observed["observed_martingale_pnl"])
                / observed_stake_volume
                * 100.0,
                2,
            ) if observed_stake_volume else 0.0,
            "martingale_cohort_size": observed["martingale_cohort_size"],
            "martingale_population": observed["martingale_population"],
            "martingale_cohort_confidence": observed["martingale_cohort_confidence"],
            "martingale_cohort_trade_count": observed["martingale_cohort_trade_count"],
            "martingale_cohort_status": observed["martingale_cohort_status"],
            "martingale_cohort_sample_sufficient": observed["martingale_cohort_sample_sufficient"],
            "martingale_dominant_signature": observed["martingale_dominant_signature"],
            "max_drawdown_fixed": round(max_fixed_drawdown, 2),
            "max_drawdown_martingale": round(observed_max_drawdown, 2),
            "simulated_max_drawdown_martingale": round(max_martingale_drawdown, 2),
            "current_drawdown_fixed": round(current_fixed_drawdown, 2),
            "current_drawdown_martingale": round(observed_current_drawdown, 2),
            "simulated_current_drawdown_martingale": round(current_martingale_drawdown, 2),
            "longest_win_streak": longest_real_win_streak,
            "longest_loss_streak": longest_real_loss_streak,
            "current_loss_streak": current_real_loss_streak,
            "current_drawdown_fixed_pct": round(
                current_fixed_drawdown / total_fixed_staked * 100.0, 2
            ) if total_fixed_staked else 0.0,
            "current_drawdown_martingale_pct": round(
                observed_current_drawdown / observed_stake_volume * 100.0, 2
            ) if observed_stake_volume else 0.0,
            "simulated_current_drawdown_martingale_pct": round(
                current_martingale_drawdown / total_martingale_staked * 100.0, 2
            ) if total_martingale_staked else 0.0,
            "max_drawdown_fixed_pct": round(
                max_fixed_drawdown / total_fixed_staked * 100.0, 2
            ) if total_fixed_staked else 0.0,
            "max_drawdown_martingale_pct": round(
                observed_max_drawdown / observed_stake_volume * 100.0, 2
            ) if observed_stake_volume else 0.0,
            "simulated_max_drawdown_martingale_pct": round(
                max_martingale_drawdown / total_martingale_staked * 100.0, 2
            ) if total_martingale_staked else 0.0,
        }

    def observed_martingale_performance(
        self,
        *,
        start: datetime,
        end: datetime,
        include_signal_diagnostics: bool = False,
        executions: list[ObservedExecution] | None = None,
    ) -> dict[str, Any]:
        """Observed actual path from the largest identical Martingale cohort.

        Future bulk trades use the immutable execution-time team stored on the
        batch. Historical trades without a batch cautiously fall back to the
        managed account's current Martingale setting. Rows without stable
        managed-account identity are excluded rather than grouped by a possibly
        colliding display mask.
        """
        if executions is None:
            executions = self.observed_martingale_executions(start=start, end=end)
        else:
            executions = [row for row in executions if start <= row.purchased_at < end]
        result = observed_martingale_cohort(executions)
        # Account membership is used only to select a stable representative;
        # neither public metrics nor protected aggregate diagnostics expose IDs.
        result.pop("dominant_cohort_account_ids", None)
        result.pop("representative_account_id", None)
        if not include_signal_diagnostics:
            result.pop("per_signal_consistency", None)
        return result

    def observed_martingale_executions(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> list[ObservedExecution]:
        """Load/decrypt observed executions once for an entire dashboard snapshot."""
        legacy_candidates: dict[str, list[tuple[int, bool]]] = {}
        for account in self.list_managed_accounts():
            try:
                payload = decrypt_auth_payload(
                    account.token_secret,
                    self.config.deriv.token_encryption_key,
                )
            except Exception:
                continue
            account_id = str(payload.get("account_id") or "").strip()
            if not account_id:
                continue
            legacy_candidates.setdefault(mask_account_id(account_id), []).append(
                (int(account.id), bool(account.martingale_enabled))
            )
        # A masked historical identity is usable only when it resolves to one
        # managed account. Collisions are excluded rather than guessed.
        legacy_accounts = {
            masked: values[0]
            for masked, values in legacy_candidates.items()
            if len(values) == 1
        }
        purchased_at = func.coalesce(Trade.provider_purchase_time, Trade.purchase_time)
        with self.database.session() as session:
            rows = session.execute(
                select(
                    Trade,
                    BulkExecutionBatch.martingale_enabled,
                    ManagedAccount.martingale_enabled,
                    SystemModelTrade.symbol,
                )
                .join(
                    SystemModelTrade,
                    and_(
                        SystemModelTrade.signal_id == Trade.signal_id,
                        SystemModelTrade.run_id == self.run_id,
                    ),
                )
                .outerjoin(BulkExecutionBatch, BulkExecutionBatch.id == Trade.bulk_batch_id)
                .outerjoin(ManagedAccount, ManagedAccount.id == Trade.managed_account_id)
                .where(
                    Trade.settlement_time.is_not(None),
                    Trade.buy_price.is_not(None),
                    Trade.buy_price > 0,
                    Trade.payout.is_not(None),
                    Trade.profit.is_not(None),
                    Trade.outcome.in_(["WIN", "LOSS"]),
                    purchased_at >= start,
                    purchased_at < end,
                )
                .order_by(
                    Trade.managed_account_id.asc(),
                    purchased_at.asc(),
                    Trade.id.asc(),
                )
            ).all()

        executions: list[ObservedExecution] = []
        for trade, batch_martingale, current_martingale, symbol in rows:
            if trade.managed_account_id is not None:
                stable_account_id = int(trade.managed_account_id)
                inferred_martingale = bool(current_martingale)
            else:
                legacy = legacy_accounts.get(str(trade.account_id_masked or ""))
                if legacy is None:
                    continue
                stable_account_id, inferred_martingale = legacy
            martingale_at_purchase = (
                bool(batch_martingale)
                if batch_martingale is not None
                else inferred_martingale
            )
            if not martingale_at_purchase:
                continue
            execution_time = trade.provider_purchase_time or trade.purchase_time
            if execution_time.tzinfo is None:
                execution_time = execution_time.replace(tzinfo=timezone.utc)
            executions.append(
                ObservedExecution(
                    account_id=stable_account_id,
                    trade_id=int(trade.id),
                    signal_id=str(trade.signal_id),
                    symbol=str(symbol),
                    purchased_at=execution_time,
                    buy_price=float(trade.buy_price),
                    payout=float(trade.payout),
                    profit=float(trade.profit),
                    outcome=str(trade.outcome),
                )
            )
        return executions

    def load_dashboard_snapshots(self) -> dict[str, dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(select(DashboardSnapshot)).all()
            return {
                row.account_type: {
                    "data": dict(row.payload or {}),
                    "generated_at": row.generated_at,
                    "snapshot_version": int(row.snapshot_version),
                    "source_watermark": dict(row.source_watermark or {}),
                }
                for row in rows
            }

    def persist_dashboard_snapshot(
        self,
        *,
        account_type: str,
        payload: dict[str, Any],
        generated_at: datetime,
        snapshot_version: int,
        source_watermark: dict[str, Any],
    ) -> None:
        with self.database.session() as session:
            row = session.get(DashboardSnapshot, account_type)
            if row is None:
                row = DashboardSnapshot(account_type=account_type)
                session.add(row)
            row.payload = payload
            row.generated_at = generated_at
            row.snapshot_version = snapshot_version
            row.source_watermark = source_watermark

    def open_system_model_trade_count(self) -> int:
        with self.database.session() as session:
            return int(session.scalar(
                select(func.count()).select_from(SystemModelTrade).where(
                    SystemModelTrade.run_id == self.run_id,
                    SystemModelTrade.outcome.is_(None),
                    exists().where(Trade.signal_id == SystemModelTrade.signal_id),
                )
            ) or 0)
