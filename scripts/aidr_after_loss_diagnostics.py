#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api import DATABASE  # noqa: E402


def rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    with DATABASE.session() as session:
        result = session.execute(text(sql), params)
        return [dict(row._mapping) for row in result]


def scalar(sql: str, **params: Any) -> Any:
    with DATABASE.session() as session:
        return session.execute(text(sql), params).scalar()


def safe_rows(label: str, sql: str, **params: Any) -> dict[str, Any]:
    try:
        return {"ok": True, "rows": rows(sql, **params)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=18)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "AIDR after-loss continuation diagnostics",
    }

    report["enabled_accounts"] = safe_rows(
        "enabled_accounts",
        """
        SELECT
          ma.id AS managed_account_id,
          ma.label,
          ma.enabled,
          ma.execution_status,
          ma.execution_status_reason,
          ma.stake_amount,
          ma.take_profit,
          ma.stop_loss,
          ars.protection_mode,
          ars.consecutive_losses,
          ars.recovery_loss_debt,
          ars.recovery_pending,
          ars.recovery_attempt_active,
          ars.virtual_observation_count,
          ars.virtual_win_count,
          ars.virtual_loss_count,
          ars.current_virtual_loss_streak,
          ars.updated_at AS risk_updated_at
        FROM managed_accounts ma
        LEFT JOIN account_risk_states ars ON ars.managed_account_id = ma.id
        WHERE ma.enabled = true
           OR lower(coalesce(ma.execution_status, '')) IN (
             'active','connecting','validating','reconnecting',
             'recovery_pending','virtual_protection','base_stake_protection'
           )
        ORDER BY ma.id
        LIMIT 200
        """,
    )

    report["latest_actual_trades"] = safe_rows(
        "latest_actual_trades",
        """
        SELECT
          t.id,
          t.managed_account_id,
          t.account_id_masked,
          t.market,
          t.contract_type,
          t.barrier,
          t.buy_price,
          t.payout,
          t.profit,
          t.outcome,
          t.exit_spot,
          t.exit_digit,
          t.purchase_time,
          t.settlement_time,
          t.signal_id
        FROM trades t
        ORDER BY COALESCE(t.settlement_time, t.purchase_time, t.created_at) DESC
        LIMIT 80
        """,
    )

    report["recent_losses"] = safe_rows(
        "recent_losses",
        """
        SELECT
          t.id,
          t.managed_account_id,
          t.account_id_masked,
          t.market,
          t.contract_type,
          t.barrier,
          t.buy_price,
          t.profit,
          t.outcome,
          t.settlement_time,
          t.signal_id
        FROM trades t
        WHERE upper(coalesce(t.outcome, '')) = 'LOSS'
        ORDER BY COALESCE(t.settlement_time, t.purchase_time, t.created_at) DESC
        LIMIT 40
        """,
    )

    report["recent_candidate_decisions"] = safe_rows(
        "recent_candidate_decisions",
        """
        SELECT
          signal_id,
          symbol,
          contract_type,
          barrier,
          trigger_name,
          status,
          purchase_requested,
          purchase_confirmed,
          stale,
          created_at,
          updated_at
        FROM candidate_signals
        WHERE created_at >= :since
          AND (
            trigger_name ILIKE 'AIDR%%'
            OR contract_type = 'DIGITOVER'
            OR status ILIKE '%%RECOVERY%%'
            OR status ILIKE '%%VIRTUAL%%'
            OR status ILIKE '%%RISK%%'
            OR status ILIKE '%%SKIP%%'
          )
        ORDER BY created_at DESC
        LIMIT 120
        """,
        since=since,
    )

    report["recent_virtual_trades"] = safe_rows(
        "recent_virtual_trades",
        """
        SELECT
          id,
          virtual_trade_id,
          managed_account_id,
          account_id_masked,
          market,
          contract_type,
          barrier,
          result,
          entry_spot,
          exit_spot,
          actual_last_digit,
          created_at,
          settled_at,
          signal_id
        FROM virtual_trades
        ORDER BY COALESCE(settled_at, created_at) DESC
        LIMIT 80
        """,
    )

    report["open_contracts"] = safe_rows(
        "open_contracts",
        """
        SELECT
          id,
          managed_account_id,
          account_id_masked,
          market,
          contract_type,
          barrier,
          buy_price,
          outcome,
          purchase_time,
          signal_id
        FROM trades
        WHERE settlement_time IS NULL
           OR upper(coalesce(outcome, 'OPEN')) = 'OPEN'
        ORDER BY purchase_time DESC
        LIMIT 50
        """,
    )

    print(json.dumps(report, indent=2, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
