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


def safe_rows(sql: str, **params: Any) -> dict[str, Any]:
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

    trade_select = """
        SELECT
          t.id,
          t.trade_id,
          t.contract_id,
          t.managed_account_id,
          t.account_id_masked,
          cs.symbol,
          cs.contract_type,
          cs.barrier,
          cs.trigger_name,
          t.buy_price,
          t.payout,
          t.profit,
          t.outcome,
          t.entry_tick,
          t.exit_tick,
          t.exit_digit,
          t.purchase_time,
          t.settlement_time,
          t.signal_id
        FROM trades t
        LEFT JOIN candidate_signals cs ON cs.signal_id = t.signal_id
    """

    report["latest_actual_trades"] = safe_rows(
        trade_select
        + """
        ORDER BY COALESCE(t.settlement_time, t.purchase_time) DESC
        LIMIT 80
        """,
    )

    report["recent_losses"] = safe_rows(
        trade_select
        + """
        WHERE upper(coalesce(t.outcome, '')) = 'LOSS'
        ORDER BY COALESCE(t.settlement_time, t.purchase_time) DESC
        LIMIT 40
        """,
    )

    report["recent_candidate_decisions"] = safe_rows(
        """
        SELECT
          cs.signal_id,
          cs.symbol,
          cs.contract_type,
          cs.barrier,
          cs.trigger_name,
          cs.final_status,
          cs.consumed,
          cs.stale,
          cs.purchase_request_timestamp,
          cs.purchase_confirmation_timestamp,
          ds.execution_decision,
          ds.execution_reason,
          ds.selected_for_execution,
          cs.generated_timestamp
        FROM candidate_signals cs
        LEFT JOIN directional_signals ds ON ds.signal_id = cs.signal_id
        WHERE cs.generated_timestamp >= :since
          AND (
            cs.trigger_name ILIKE 'AIDR%%'
            OR cs.contract_type = 'DIGITOVER'
            OR cs.final_status ILIKE '%%RECOVERY%%'
            OR cs.final_status ILIKE '%%VIRTUAL%%'
            OR cs.final_status ILIKE '%%RISK%%'
            OR cs.final_status ILIKE '%%SKIP%%'
            OR ds.execution_decision ILIKE '%%RECOVERY%%'
            OR ds.execution_decision ILIKE '%%VIRTUAL%%'
            OR ds.execution_decision ILIKE '%%SKIP%%'
          )
        ORDER BY cs.generated_timestamp DESC
        LIMIT 120
        """,
        since=since,
    )

    report["recent_virtual_trades"] = safe_rows(
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
        trade_select
        + """
        WHERE t.settlement_time IS NULL
           OR upper(coalesce(t.outcome, 'OPEN')) = 'OPEN'
        ORDER BY t.purchase_time DESC
        LIMIT 50
        """,
    )

    print(json.dumps(report, indent=2, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
