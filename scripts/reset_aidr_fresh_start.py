#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.api import DATABASE
from app.models import (
    AccountRiskState,
    BotState,
    BulkExecutionBatch,
    BulkExecutionMember,
    CandidateSignalRecord,
    DashboardSnapshot,
    DirectionalSignal,
    ModelDecisionRecord,
    ProposalRecord,
    RuntimePreference,
    Streak,
    SystemModelState,
    SystemModelTrade,
    Trade,
    VirtualGuardState,
    VirtualTrade,
)

CONFIRMATION = "RESET_ALL_TRADING_HISTORY"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Clear trading history and recovery state while preserving managed "
            "accounts, OAuth/client sessions, balances and personal settings."
        )
    )
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        raise SystemExit(f"Confirmation must be exactly {CONFIRMATION}")

    with DATABASE.session() as session:
        open_trades = session.scalars(
            select(Trade.id).where(Trade.settlement_time.is_(None))
        ).all()
        if open_trades:
            raise SystemExit(
                f"Refusing reset: {len(open_trades)} provider trade(s) are still open."
            )

        before = {
            "trades": len(session.scalars(select(Trade.id)).all()),
            "virtual_trades": len(session.scalars(select(VirtualTrade.virtual_trade_id)).all()),
            "signals": len(session.scalars(select(CandidateSignalRecord.signal_id)).all()),
            "system_model_trades": len(session.scalars(select(SystemModelTrade.id)).all()),
        }

        for model in (
            BulkExecutionMember,
            Trade,
            BulkExecutionBatch,
            ProposalRecord,
            ModelDecisionRecord,
            SystemModelTrade,
            DirectionalSignal,
            CandidateSignalRecord,
            VirtualTrade,
            Streak,
            SystemModelState,
            VirtualGuardState,
            DashboardSnapshot,
        ):
            session.execute(delete(model))

        for risk in session.scalars(select(AccountRiskState)).all():
            risk.trading_day = ""
            risk.daily_start_balance = 0.0
            risk.session_profit = 0.0
            risk.consecutive_losses = 0
            risk.recovery_loss_debt = 0.0
            risk.recovery_pending = False
            risk.recovery_attempt_active = False
            risk.protection_mode = "NORMAL_MODE"
            risk.virtual_observation_count = 0
            risk.virtual_win_count = 0
            risk.virtual_loss_count = 0
            risk.current_virtual_loss_streak = 0
            risk.entered_virtual_mode_at = None
            risk.recovery_pending_since = None
            risk.equity_high_water = 0.0
            risk.updated_at = datetime.now(timezone.utc)

        for bot in session.scalars(select(BotState)).all():
            bot.current_sequence = 0
            bot.current_streak = 0
            bot.current_streak_type = ""
            bot.current_drawdown = 0.0
            bot.session_profit = 0.0
            bot.total_profit = 0.0
            bot.high_water_mark = 0.0
            bot.consecutive_wins = 0
            bot.consecutive_losses = 0
            bot.cooldown_ticks_remaining = 0
            bot.pause_reason = ""
            bot.last_heartbeat = datetime.now(timezone.utc)

        preferences = session.scalars(select(RuntimePreference)).all()
        for preference in preferences:
            key = str(preference.preference_key or "")
            if (
                key in {
                    "hybrid_o2u7_put_v1:state",
                    "hybrid_over2_put_v4:state",
                    "aidr_over1_over3_v1:state",
                }
                or key.startswith("hybrid_o2u7_put_v1:account_epoch:")
                or key.startswith("hybrid_over2_put_v4:account_epoch:")
                or key.startswith("aidr_over1_over3_v1:account_epoch:")
                or key.startswith("aidr_split_remaining:")
            ):
                session.delete(preference)

    print("AIDR_FRESH_START_COMPLETE")
    for key, value in before.items():
        print(f"cleared_{key}={value}")
    print("preserved=managed_accounts,client_sessions,oauth_credentials,balances,settings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
