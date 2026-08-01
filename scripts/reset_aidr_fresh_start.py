#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

# Running this file as `python scripts/reset_aidr_fresh_start.py` makes Python
# place /app/scripts at sys.path[0]. Add the repository root explicitly so the
# application package can always be imported both on the VPS and in Docker.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

TRUNCATE_MODELS = (
    BulkExecutionMember,
    BulkExecutionBatch,
    Trade,
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
)

PRESERVED_MESSAGE = (
    "managed_accounts,client_sessions,oauth_credentials,balances,settings,"
    "enabled_account_rows"
)


def _table_name(model: object) -> str:
    table = getattr(model, "__table__")
    return f'public."{table.name}"'


def _count_rows(session, model: object) -> int:
    column = next(iter(getattr(model, "__table__").primary_key.columns), None)
    if column is None:
        return 0
    return len(session.scalars(select(column)).all())


def _truncate_statement() -> str:
    table_names = []
    seen: set[str] = set()
    for model in TRUNCATE_MODELS:
        name = _table_name(model)
        if name not in seen:
            seen.add(name)
            table_names.append(name)
    return "TRUNCATE TABLE " + ", ".join(table_names) + " RESTART IDENTITY CASCADE"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Clear trading history and recovery state while preserving managed "
            "accounts, OAuth/client sessions, balances and personal settings."
        )
    )
    parser.add_argument("--confirm", required=True)
    parser.add_argument(
        "--force-open-trades",
        action="store_true",
        help=(
            "Dangerous: also clears locally open provider-trade rows. Use only "
            "after manually confirming no live Deriv contracts are still open."
        ),
    )
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        raise SystemExit(f"Confirmation must be exactly {CONFIRMATION}")

    with DATABASE.session() as session:
        open_trades = session.scalars(
            select(Trade.id).where(Trade.settlement_time.is_(None))
        ).all()
        if open_trades and not args.force_open_trades:
            raise SystemExit(
                "Refusing reset: "
                f"{len(open_trades)} provider trade(s) are still locally open. "
                "Wait for settlement or rerun only after manual Deriv verification "
                "with --force-open-trades."
            )

        before = {
            "trades": _count_rows(session, Trade),
            "virtual_trades": _count_rows(session, VirtualTrade),
            "signals": _count_rows(session, CandidateSignalRecord),
            "directional_signals": _count_rows(session, DirectionalSignal),
            "system_model_trades": _count_rows(session, SystemModelTrade),
            "dashboard_snapshots": _count_rows(session, DashboardSnapshot),
        }

        # This reset is intentionally blunt. The old row-by-row delete failed
        # because some virtual trades reference directional_signals through a
        # foreign key. TRUNCATE ... CASCADE is the correct maintenance operation
        # for a fresh strategy cutover: it removes only the volatile trading
        # ledger and its dependent rows, never the managed account table.
        session.execute(text(_truncate_statement()))

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
        removed_preferences = 0
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
                removed_preferences += 1

    print("AIDR_FRESH_START_COMPLETE")
    print("reset_method=truncate_restart_identity_cascade")
    print(f"force_open_trades={bool(args.force_open_trades)}")
    for key, value in before.items():
        print(f"cleared_{key}={value}")
    print(f"cleared_runtime_preferences={removed_preferences}")
    print(f"preserved={PRESERVED_MESSAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
