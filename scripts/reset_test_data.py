from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select

from app.config import load_test2_config
from app.database import Database
from app.models import (
    AccountSnapshot,
    BotState,
    CandidateSignalRecord,
    ModelDecisionRecord,
    ProposalRecord,
    Streak,
    TestRun,
    Tick,
    Trade,
)
from app.repositories.test2_repository import Test2Repository
from app.services.analytics_service import export_test2

ROOT = Path(__file__).resolve().parents[1]


def redact_legacy_state(source: Path, destination: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    for item in data.get("unresolved_contracts", []):
        item.pop("token", None)
        account = str(item.pop("account_id", ""))
        if account:
            item["account_id_masked"] = f"{account[:3]}***{account[-3:]}"
    destination.write_text(json.dumps(data, indent=2), encoding="utf-8")


def archive_test1() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = ROOT / "archives" / f"test1_{timestamp}"
    archive.mkdir(parents=True, exist_ok=False)

    state = ROOT / "bot_state.json"
    if state.exists():
        redact_legacy_state(state, archive / state.name)

    log = ROOT / "trading_bot.log"
    if log.exists():
        shutil.copy2(log, archive / log.name)

    analysis = ROOT / "analysis"
    if analysis.exists():
        shutil.copytree(analysis, archive / "analysis")

    exports = ROOT / "exports"
    if exports.exists():
        shutil.copytree(exports, archive / "exports")

    artifacts = ROOT / "model_artifacts"
    if artifacts.exists():
        shutil.copytree(artifacts, archive / "model_artifacts")

    for path in archive.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IREAD)
    return archive


def clear_active_files() -> None:
    for path in (ROOT / "analysis", ROOT / "exports", ROOT / "model_artifacts"):
        if path.exists():
            shutil.rmtree(path)
    for path in (ROOT / "bot_state.json", ROOT / "trading_bot.log"):
        if path.exists():
            path.unlink()


def reset_database(database: Database, run_name: str) -> None:
    database.create_schema()
    with database.session() as session:
        run = session.scalar(select(TestRun).where(TestRun.run_name == run_name))
        if run is None:
            return
        unresolved = session.scalars(
            select(Trade).where(Trade.settlement_time.is_(None))
        ).all()
        if unresolved:
            raise RuntimeError(
                "Database has unresolved contracts; reconcile them before reset"
            )
        signal_ids = session.scalars(
            select(CandidateSignalRecord.signal_id).where(
                CandidateSignalRecord.run_id == run.id
            )
        ).all()
        if signal_ids:
            session.execute(
                delete(ModelDecisionRecord).where(
                    ModelDecisionRecord.signal_id.in_(signal_ids)
                )
            )
            session.execute(
                delete(ProposalRecord).where(ProposalRecord.signal_id.in_(signal_ids))
            )
            session.execute(delete(Trade).where(Trade.signal_id.in_(signal_ids)))
        session.execute(
            delete(CandidateSignalRecord).where(CandidateSignalRecord.run_id == run.id)
        )
        session.execute(delete(Tick).where(Tick.run_id == run.id))
        session.execute(delete(Streak).where(Streak.run_id == run.id))
        session.execute(
            delete(AccountSnapshot).where(AccountSnapshot.run_id == run.id)
        )
        session.execute(delete(BotState).where(BotState.run_id == run.id))
        session.delete(run)


def write_zero_state(config) -> None:
    state = {
        "version": 6,
        "bot": {
            "run_id": "test2",
            "environment": config.deriv.environment,
            "symbol": "1HZ100V",
            "status": "STOPPED",
            "cooldown_ticks_remaining": 0,
            "pending_contract_count": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "clients": {},
        "unresolved_contracts": [],
    }
    (ROOT / "bot_state.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=["test2"])
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != "RESET_TEST2":
        raise SystemExit("Reset confirmation must be RESET_TEST2")

    legacy_state = ROOT / "bot_state.json"
    if legacy_state.exists():
        data = json.loads(legacy_state.read_text(encoding="utf-8"))
        unresolved = data.get("unresolved_contracts", [])
        if unresolved and os.getenv("TEST1_CONTRACTS_RECONCILED") != "true":
            raise SystemExit(
                "Legacy state lists unresolved contracts. Reconcile them with Deriv, then "
                "set TEST1_CONTRACTS_RECONCILED=true for this one reset."
            )

    config = load_test2_config(os.getenv("DERIV_BOT_CONFIG", ROOT / "config.yaml"))
    archive = archive_test1()
    database = Database(config.database_url)
    reset_database(database, config.model.run_id)
    clear_active_files()
    database.create_schema()
    Test2Repository(database, config)
    write_zero_state(config)
    export_test2(database, config.model.run_id, config.storage.export_directory)
    print(f"RESET_COMPLETED archive={archive.name} run=test2 trades=0 profit=0.00")


if __name__ == "__main__":
    main()
