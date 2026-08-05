from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import select

import app.api as base_api
from app.dashboard_stability_fix import _remove_route
from app.models import CandidateSignalRecord, ModelDecisionRecord, Trade
from app.strategy_v2_final_ui import (
    ALERT_LIFETIME_SECONDS,
    _candidate_alert,
    _matches_strategy,
)
from app.strategy_v2_preferences import read_strategy


_INSTALLED = False
ROUTE_PATH = "/me/execution-alert"


def _route_count(app: Any) -> int:
    return sum(
        1
        for route in app.router.routes
        if getattr(route, "path", None) == ROUTE_PATH
        and "GET" in set(getattr(route, "methods", set()) or set())
    )


def install_final_execution_alert_api(app: Any) -> None:
    """Install one final account-scoped signal outcome route.

    This authority runs after all compatibility, performance and database wrappers,
    so the dashboard alert endpoint cannot be lost to later route replacement.
    """

    global _INSTALLED
    if _INSTALLED and _route_count(app) == 1:
        return

    _remove_route(app, ROUTE_PATH, "GET")

    def personal_execution_alert(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")

        managed_id = int(account["id"])
        account_mask = str(account.get("account_id_masked") or "")
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=ALERT_LIFETIME_SECONDS)
        selection = read_strategy(base_api.DATABASE, managed_id)

        with base_api.DATABASE.session() as session:
            candidates = list(
                session.scalars(
                    select(CandidateSignalRecord)
                    .where(CandidateSignalRecord.generated_timestamp >= cutoff)
                    .order_by(CandidateSignalRecord.generated_timestamp.desc())
                    .limit(160)
                ).all()
            )
            candidates = [
                signal for signal in candidates if _matches_strategy(signal, selection)
            ]

            if not candidates:
                return {
                    "authenticated": True,
                    "account": account_mask,
                    "alert": None,
                    "window_seconds": ALERT_LIFETIME_SECONDS,
                }

            signal_ids = [str(signal.signal_id) for signal in candidates]
            purchased_ids = set(
                session.scalars(
                    select(Trade.signal_id)
                    .where(Trade.managed_account_id == managed_id)
                    .where(Trade.signal_id.in_(signal_ids))
                ).all()
            )
            decisions = {
                str(row.signal_id): row
                for row in session.scalars(
                    select(ModelDecisionRecord).where(
                        ModelDecisionRecord.signal_id.in_(signal_ids)
                    )
                ).all()
            }

        for signal in candidates:
            signal_id = str(signal.signal_id)
            if signal_id in purchased_ids:
                return {
                    "authenticated": True,
                    "account": account_mask,
                    "alert": None,
                    "latest_result": "PURCHASED",
                    "signal_id": signal_id,
                    "window_seconds": ALERT_LIFETIME_SECONDS,
                }

            alert = _candidate_alert(
                signal,
                now=now,
                account_mask=account_mask,
                decision=decisions.get(signal_id),
            )
            if alert is not None:
                return {
                    "authenticated": True,
                    "account": account_mask,
                    "alert": alert,
                    "window_seconds": ALERT_LIFETIME_SECONDS,
                }

        return {
            "authenticated": True,
            "account": account_mask,
            "alert": None,
            "window_seconds": ALERT_LIFETIME_SECONDS,
        }

    app.add_api_route(
        ROUTE_PATH,
        personal_execution_alert,
        methods=["GET"],
        include_in_schema=False,
        name="final_personal_execution_alert",
    )

    count = _route_count(app)
    if count != 1:
        raise RuntimeError(
            f"FINAL_EXECUTION_ALERT_ROUTE_INVALID path={ROUTE_PATH} count={count}"
        )

    base_api.LOGGER.info(
        "FINAL_EXECUTION_ALERT_ROUTE_INSTALLED path=%s count=%s window_seconds=%s",
        ROUTE_PATH,
        count,
        ALERT_LIFETIME_SECONDS,
    )
    app.state.final_execution_alert_api_installed = True
    app.state.final_execution_alert_route_count = count
    _INSTALLED = True
