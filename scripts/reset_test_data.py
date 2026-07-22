from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select

from app.config import load_test2_config
from app.database import Database
from app.models import (
    AccountRiskState,
    BotState,
    CandidateSignalRecord,
    DirectionalSignal,
    ModelDecisionRecord,
    ProposalRecord,
    ShadowContract,
    Streak,
    TestRun,
    Tick,
    Trade,
    TraderLease,
    VirtualGuardState,
    VirtualTrade,
    utc_now,
)

ROOT = Path(__file__).resolve().parents[1]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_expired_tick_contract(trade: Trade, *, stale_after_seconds: int) -> bool:
    age_seconds = (utc_now() - _aware_utc(trade.purchase_time)).total_seconds()
    return (
        str(trade.contract_duration_unit or "") == "t"
        and int(trade.contract_duration or 0) > 0
        and age_seconds >= stale_after_seconds
    )


def reset_database(
    database: Database,
    run_name: str,
    *,
    all_runs: bool = False,
    allow_expired_one_tick: bool = False,
    stale_after_seconds: int = 300,
) -> dict[str, int]:
    """Reset trading history without touching identities, controls, or balances."""
    database.create_schema()
    stale_after_seconds = max(60, int(stale_after_seconds))

    with database.session() as session:
        run_query = select(TestRun)
        if not all_runs:
            run_query = run_query.where(TestRun.run_name == run_name)
        runs = list(session.scalars(run_query).all())
        run_ids = [int(run.id) for run in runs]
        if not run_ids:
            return {
                "runs": 0,
                "trades": 0,
                "signals": 0,
                "ticks": 0,
                "expired_unresolved": 0,
            }

        signal_ids_query = select(CandidateSignalRecord.signal_id).where(
            CandidateSignalRecord.run_id.in_(run_ids)
        )
        unresolved = list(
            session.scalars(
                select(Trade).where(
                    Trade.signal_id.in_(signal_ids_query),
                    Trade.settlement_time.is_(None),
                )
            ).all()
        )
        blocking = [
            trade
            for trade in unresolved
            if not (
                allow_expired_one_tick
                and _is_expired_tick_contract(
                    trade,
                    stale_after_seconds=stale_after_seconds,
                )
            )
        ]
        if blocking:
            contract_ids = ", ".join(str(trade.contract_id) for trade in blocking[:10])
            raise RuntimeError(
                "Potentially active contracts block reset: "
                f"{contract_ids}. Let the worker reconcile them first."
            )

        counts = {
            "runs": len(run_ids),
            "trades": int(
                session.scalar(
                    select(func.count()).select_from(Trade).where(
                        Trade.signal_id.in_(signal_ids_query)
                    )
                )
                or 0
            ),
            "signals": int(
                session.scalar(
                    select(func.count()).select_from(CandidateSignalRecord).where(
                        CandidateSignalRecord.run_id.in_(run_ids)
                    )
                )
                or 0
            ),
            "ticks": int(
                session.scalar(
                    select(func.count()).select_from(Tick).where(Tick.run_id.in_(run_ids))
                )
                or 0
            ),
            "expired_unresolved": len(unresolved),
        }

        session.execute(
            delete(ModelDecisionRecord).where(
                ModelDecisionRecord.signal_id.in_(signal_ids_query)
            )
        )
        session.execute(
            delete(ShadowContract).where(ShadowContract.run_id.in_(run_ids))
        )
        session.execute(
            delete(VirtualTrade).where(VirtualTrade.run_id.in_(run_ids))
        )
        session.execute(
            delete(DirectionalSignal).where(DirectionalSignal.run_id.in_(run_ids))
        )
        session.execute(
            delete(ProposalRecord).where(ProposalRecord.signal_id.in_(signal_ids_query))
        )
        session.execute(delete(Trade).where(Trade.signal_id.in_(signal_ids_query)))
        session.execute(
            delete(CandidateSignalRecord).where(
                CandidateSignalRecord.run_id.in_(run_ids)
            )
        )
        session.execute(delete(Tick).where(Tick.run_id.in_(run_ids)))
        session.execute(delete(Streak).where(Streak.run_id.in_(run_ids)))

        for state in session.scalars(
            select(BotState).where(BotState.run_id.in_(run_ids))
        ).all():
            state.status = "STOPPED"
            state.current_sequence = 0
            state.current_streak = 0
            state.current_streak_type = ""
            state.current_drawdown = 0.0
            state.session_profit = 0.0
            state.total_profit = 0.0
            state.high_water_mark = 0.0
            state.pause_reason = ""
            state.current_connection_id = ""
            state.consecutive_wins = 0
            state.consecutive_losses = 0
            state.cooldown_ticks_remaining = 0
            state.last_heartbeat = utc_now()

        for guard in session.scalars(
            select(VirtualGuardState).where(VirtualGuardState.run_id.in_(run_ids))
        ).all():
            guard.state = "DEMO_LIVE"
            guard.active_signal_id = ""
            guard.active_shadow_duration = 0
            guard.demo_losses = 0
            guard.virtual_wins = 0
            guard.updated_at = utc_now()

        session.execute(delete(AccountRiskState))

        if all_runs:
            session.execute(delete(TraderLease))
        else:
            session.execute(
                delete(TraderLease).where(
                    TraderLease.lease_key.like(f"{run_name}:%")
                )
            )

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset trading history while preserving accounts and sessions."
    )
    parser.add_argument("--target", required=True, choices=["test2"])
    parser.add_argument("--confirm", required=True)
    parser.add_argument(
        "--all-runs",
        action="store_true",
        help="Clear trading history for every historical run.",
    )
    parser.add_argument(
        "--allow-expired-one-tick",
        action="store_true",
        help="Permit unresolved tick contracts older than the safety threshold.",
    )
    parser.add_argument("--stale-after-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.confirm != "RESET_TEST2":
        raise SystemExit("Reset confirmation must be RESET_TEST2")

    config = load_test2_config(os.getenv("DERIV_BOT_CONFIG", ROOT / "config.yaml"))
    database = Database(config.database_url)
    counts = reset_database(
        database,
        config.model.run_id,
        all_runs=args.all_runs,
        allow_expired_one_tick=args.allow_expired_one_tick,
        stale_after_seconds=args.stale_after_seconds,
    )
    print(
        "RESET_COMPLETED "
        f"runs={counts['runs']} removed_trades={counts['trades']} "
        f"removed_signals={counts['signals']} removed_ticks={counts['ticks']} "
        f"expired_unresolved={counts['expired_unresolved']} "
        "preserved=accounts,sessions,controls,balances,models"
    )


if __name__ == "__main__":
    main()
