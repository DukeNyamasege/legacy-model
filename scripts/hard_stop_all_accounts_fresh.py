from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.api import DATABASE
from app.models import AccountRiskState, ManagedAccount, RuntimePreference, Trade, VirtualTrade, utc_now

CONFIRMATION = "STOP_ALL_AND_RESET_AIDR"
RESET_MARKER_PREFIX = "aidr_hard_reset_at:"
AIDR_PREFIXES = (
    "aidr_split_remaining:",
    "aidr_over1_over3_v1:account_epoch:",
    "hybrid_over2_put_v4:account_epoch:",
    "hybrid_o2u7_put_v1:account_epoch:",
)


def _reset_state(state: AccountRiskState) -> None:
    state.trading_day = ""
    state.daily_start_balance = 0.0
    state.session_profit = 0.0
    state.consecutive_losses = 0
    state.recovery_loss_debt = 0.0
    state.recovery_pending = False
    state.recovery_attempt_active = False
    state.protection_mode = "NORMAL_MODE"
    state.virtual_observation_count = 0
    state.virtual_win_count = 0
    state.virtual_loss_count = 0
    state.current_virtual_loss_streak = 0
    state.entered_virtual_mode_at = None
    state.recovery_pending_since = None
    state.equity_high_water = 0.0
    state.updated_at = utc_now()


def _write_reset_marker(session: Any, managed_account_id: int, value: str) -> None:
    key = f"{RESET_MARKER_PREFIX}{int(managed_account_id)}"
    row = session.get(RuntimePreference, key)
    if row is None:
        session.add(
            RuntimePreference(
                preference_key=key,
                preference_value=value,
                updated_at=utc_now(),
            )
        )
    else:
        row.preference_value = value
        row.updated_at = utc_now()


def run(*, apply: bool) -> dict[str, Any]:
    with DATABASE.session() as session:
        open_rows = session.execute(
            select(
                Trade.id,
                Trade.managed_account_id,
                Trade.account_id_masked,
                Trade.contract_id,
                Trade.purchase_time,
            )
            .where(Trade.settlement_time.is_(None))
            .order_by(Trade.purchase_time.desc())
            .limit(100)
        ).all()
        if open_rows:
            return {
                "ok": False,
                "applied": False,
                "reason": "Open provider contracts exist. Wait for settlement before the global fresh reset.",
                "open_contracts": [
                    {
                        "trade_id": row.id,
                        "managed_account_id": row.managed_account_id,
                        "account": row.account_id_masked,
                        "contract_id": row.contract_id,
                        "purchase_time": row.purchase_time,
                    }
                    for row in open_rows
                ],
            }

        accounts = session.scalars(select(ManagedAccount).order_by(ManagedAccount.id)).all()
        states = session.scalars(select(AccountRiskState).order_by(AccountRiskState.managed_account_id)).all()
        open_virtual = session.scalars(
            select(VirtualTrade).where(VirtualTrade.result == "OPEN")
        ).all()
        preference_rows = session.scalars(select(RuntimePreference)).all()
        matching_preferences = [
            row
            for row in preference_rows
            if any(str(row.preference_key or "").startswith(prefix) for prefix in AIDR_PREFIXES)
        ]

        preview = {
            "accounts_to_stop": len(accounts),
            "risk_states_to_reset": len(states),
            "open_virtual_to_cancel": len(open_virtual),
            "runtime_preferences_to_clear": len(matching_preferences),
            "session_markers_to_write": len(accounts),
            "actual_trade_history_deleted": 0,
            "virtual_trade_history_deleted": 0,
            "credentials_deleted": 0,
            "settings_deleted": 0,
        }
        if not apply:
            return {
                "ok": True,
                "applied": False,
                "mode": "dry-run",
                "preview": preview,
            }

        now = utc_now()
        marker_value = datetime.now(timezone.utc).isoformat()
        for row in accounts:
            row.enabled = False
            row.execution_status = "stopped"
            row.execution_status_reason = (
                "Platform recovery state was reset safely. Press Start to begin a fresh base-stake session."
            )[:160]
            row.execution_status_updated_at = now
            row.updated_at = now
            _write_reset_marker(session, int(row.id), marker_value)

        for state in states:
            _reset_state(state)

        for trade in open_virtual:
            trade.result = "VIRTUAL_CANCELLED_STOP"
            trade.reason = "Cancelled by platform-wide fresh recovery reset"
            trade.amount_charged = 0.0
            trade.actual_profit_loss = 0.0
            trade.actual_payout = 0.0
            trade.recovery_debt_change = 0.0
            trade.settled_at = now

        for preference in matching_preferences:
            session.delete(preference)

        return {
            "ok": True,
            "applied": True,
            "mode": "apply",
            "completed_at": marker_value,
            "result": preview,
            "message": (
                "All account executions were stopped and all AIDR recovery/virtual progress was reset. "
                "Accounts, credentials, balances, settings and trade history were preserved. "
                "Historical virtual rows are separated from every new Start session."
            ),
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Stop every account and clear only AIDR recovery/session state. "
            "No account, credential, setting, balance or trade history is deleted."
        )
    )
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Use the exact confirmation phrase: {CONFIRMATION}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show affected counts without changing the database.",
    )
    args = parser.parse_args()

    apply = not args.dry_run
    if apply and args.confirm != CONFIRMATION:
        raise SystemExit(
            f"Refusing to apply. Pass --confirm {CONFIRMATION}, or use --dry-run."
        )
    print(json.dumps(run(apply=apply), indent=2, default=str))


if __name__ == "__main__":
    main()
