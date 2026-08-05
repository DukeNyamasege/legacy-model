from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

import app.api as base_api
from app.final_public_controls import (
    _clear_account_runtime_preferences,
    _current_account_payload,
    _load_managed_account,
    _remove_route,
    _reset_risk_state,
)
from app.models import ManagedAccount, RuntimePreference, Trade, VirtualTrade, utc_now
from app.seamless_personal_execution import (
    _capture_dashboard_route,
    _current_strategy_payload,
    _markets,
    _normalize_market,
    _read_market,
    _safe_audit,
    _usable_pat,
    _write_preference,
)
from app.strategy_v2_preferences import (
    normalize_strategy,
    read_strategy,
    strategy_catalog_payload,
    write_strategy,
)


_INSTALLED = False
VERSION = "seamless-personal-execution-v2"
PENDING_STRATEGY_PREFIX = "pending_strategy:v1:"


class FinalStrategyRequest(BaseModel):
    family: str
    side: str
    prediction: int | None = None
    market: str | None = None


def _pending_key(managed_id: int) -> str:
    return f"{PENDING_STRATEGY_PREFIX}{int(managed_id)}"


def _open_contract_counts(session: Any, managed_id: int) -> tuple[int, int]:
    actual = len(
        session.scalars(
            select(Trade.id).where(
                Trade.managed_account_id == int(managed_id),
                Trade.settlement_time.is_(None),
            )
        ).all()
    )
    virtual = len(
        session.scalars(
            select(VirtualTrade.id).where(
                VirtualTrade.managed_account_id == int(managed_id),
                VirtualTrade.result == "OPEN",
            )
        ).all()
    )
    return actual, virtual


def _pending_payload(session: Any, managed_id: int) -> dict[str, Any] | None:
    row = session.get(RuntimePreference, _pending_key(managed_id))
    if row is None:
        return None
    try:
        payload = json.loads(str(row.preference_value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, dict) else None


def _write_pending(session: Any, managed_id: int, selection: Any) -> None:
    row = session.get(RuntimePreference, _pending_key(managed_id))
    if row is None:
        row = RuntimePreference(preference_key=_pending_key(managed_id))
        session.add(row)
    row.preference_value = json.dumps(
        {
            "family": selection.family,
            "side": selection.side,
            "prediction": selection.prediction,
            "version": selection.version,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    row.updated_at = utc_now()


def _clear_pending(session: Any, managed_id: int) -> None:
    row = session.get(RuntimePreference, _pending_key(managed_id))
    if row is not None:
        session.delete(row)


def _pending_public(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    try:
        return normalize_strategy(
            payload.get("family"),
            payload.get("side"),
            payload.get("prediction"),
        ).to_dict()
    except ValueError:
        return None


def install_final_seamless_personal_execution(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    dashboard_endpoint = _capture_dashboard_route(app)

    for path, method in (
        ("/me/auto-trade", "POST"),
        ("/me/resume-trading", "POST"),
        ("/me/stop-trading", "POST"),
        ("/me/trading-lifecycle", "GET"),
        ("/me/strategy-settings", "GET"),
        ("/me/strategy-settings", "POST"),
    ):
        _remove_route(app, path, method)

    @app.post("/me/stop-trading")
    def final_seamless_stop(request: Request) -> dict[str, Any]:
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            managed_id = int(row.id)
            open_actual, open_virtual = _open_contract_counts(session, managed_id)

            # Hard Stop is a fresh-start boundary. It clears only financial
            # recovery/session progress; account credentials and user settings stay.
            _reset_risk_state(session, managed_id)
            _clear_account_runtime_preferences(session, managed_id)

            settlement_only = bool(open_actual or open_virtual)
            row.enabled = settlement_only
            row.execution_status = "settlement_only" if settlement_only else "stopped"
            row.execution_status_reason = (
                "New entries stopped and recovery reset to base stake. Existing "
                "contracts remain connected only for settlement."
                if settlement_only
                else "New entries stopped. Next Start begins from the configured base stake."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            market = _read_market(session, managed_id)
            strategy = _current_strategy_payload(base_api, managed_id)

        _safe_audit(
            base_api,
            "FINAL_SEAMLESS_PERSONAL_STOP",
            request,
            {
                "managed_account_id": managed_id,
                "recovery_reset_to_base": True,
                "credentials_preserved": True,
                "settings_preserved": True,
                "strategy_preserved": strategy,
                "market_preserved": market,
                "open_actual_contracts": open_actual,
                "open_virtual_contracts": open_virtual,
                "settlement_only": settlement_only,
            },
        )
        return {
            "success": True,
            "state": "stopped",
            "lifecycle": "stopped",
            "enabled": False,
            "settlement_connection_active": settlement_only,
            "recovery_reset": True,
            "next_start_uses_base_stake": True,
            "credentials_preserved": True,
            "settings_preserved": True,
            "strategy": strategy,
            "market": market,
            "message": (
                "Execution stopped. Existing contracts will finish settling; the "
                "next Start uses the base stake with the same saved configuration."
            ),
        }

    @app.post("/me/resume-trading")
    def final_seamless_resume(
        request: Request,
        body: base_api.ResumeTradeRequest,
    ) -> dict[str, Any]:
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            has_pat, shared_pat = _usable_pat(base_api, row)
            if not has_pat:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This account has no verified Deriv Personal Access Token "
                        "with trade scope. Link one in Settings > Credentials."
                    ),
                )
            if base_api.execution_token_was_rejected(
                row.execution_status,
                row.execution_status_reason,
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The stored Deriv API token was rejected or expired. "
                        "Replace it in Settings > Credentials."
                    ),
                )
            previous = str(row.execution_status or "inactive").strip().lower()
            row.enabled = True
            row.execution_status = "connecting"
            row.execution_status_reason = (
                "Rejoining with the saved strategy, market, stake and risk settings."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            managed_id = int(row.id)
            market = _read_market(session, managed_id)
            strategy = _current_strategy_payload(base_api, managed_id)
            pending = _pending_public(_pending_payload(session, managed_id))

        base_api.REPOSITORY.set_status("RUNNING", "")
        _safe_audit(
            base_api,
            "FINAL_SEAMLESS_PERSONAL_REJOIN",
            request,
            {
                "managed_account_id": managed_id,
                "previous_status": previous,
                "stored_verified_pat_reused": True,
                "shared_verified_pat_reused": shared_pat,
                "strategy": strategy,
                "pending_strategy": pending,
                "market": market,
            },
        )
        return {
            "success": True,
            "state": "running",
            "lifecycle": "running",
            "enabled": True,
            "mode": str(body.mode),
            "stored_pat_reused": True,
            "shared_pat_reused": shared_pat,
            "strategy": strategy,
            "pending_strategy": pending,
            "market": market,
            "message": "Execution rejoined with the saved account configuration.",
        }

    @app.post("/me/auto-trade")
    def final_seamless_auto_trade(
        request: Request,
        body: base_api.AutoTradeRequest,
    ) -> dict[str, Any]:
        if bool(body.enabled):
            return final_seamless_resume(
                request,
                base_api.ResumeTradeRequest(mode="resume"),
            )
        return final_seamless_stop(request)

    @app.get("/me/trading-lifecycle")
    def final_seamless_lifecycle(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            return {"authenticated": False, "lifecycle": "logged_out"}
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, int(account["id"]))
            if row is None:
                return {"authenticated": False, "lifecycle": "missing"}
            managed_id = int(row.id)
            status = str(row.execution_status or "inactive").strip().lower()
            lifecycle = (
                "stopped"
                if status in {"stopped", "settlement_only", "inactive", "disabled"}
                else "running"
                if bool(row.enabled)
                else "paused"
            )
            has_pat, shared_pat = _usable_pat(base_api, row)
            open_actual, open_virtual = _open_contract_counts(session, managed_id)
            return {
                "authenticated": True,
                "lifecycle": lifecycle,
                "execution_status": status,
                "reason": str(row.execution_status_reason or ""),
                "enabled": lifecycle == "running",
                "settlement_connection_active": status == "settlement_only",
                "open_actual_contracts": open_actual,
                "open_virtual_contracts": open_virtual,
                "has_bulk_trade_pat": has_pat,
                "shared_verified_pat_available": shared_pat,
                "strategy": _current_strategy_payload(base_api, managed_id),
                "pending_strategy": _pending_public(
                    _pending_payload(session, managed_id)
                ),
                "market": _read_market(session, managed_id),
                "stop_resets_recovery_to_base": True,
                "pause_preserves_recovery": True,
            }

    @app.get("/me/strategy-settings")
    def final_seamless_strategy_settings(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        selection = read_strategy(base_api.DATABASE, managed_id)
        with base_api.DATABASE.session() as session:
            pending = _pending_public(_pending_payload(session, managed_id))
            market = _read_market(session, managed_id)
            open_actual, open_virtual = _open_contract_counts(session, managed_id)
        return {
            "authenticated": True,
            "managed_account_id": managed_id,
            "selection": selection.to_dict(),
            "pending_selection": pending,
            "catalog": strategy_catalog_payload(),
            "market": market,
            "markets": _markets(base_api),
            "open_actual_contracts": open_actual,
            "open_virtual_contracts": open_virtual,
            "seamless_switching": True,
        }

    @app.post("/me/strategy-settings")
    def final_seamless_strategy_switch(
        request: Request,
        body: FinalStrategyRequest,
    ) -> dict[str, Any]:
        try:
            requested = normalize_strategy(body.family, body.side, body.prediction)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            managed_id = int(row.id)
            previous = read_strategy(base_api.DATABASE, managed_id)
            changed = (
                previous.family,
                previous.side,
                previous.prediction,
            ) != (
                requested.family,
                requested.side,
                requested.prediction,
            )
            open_actual, open_virtual = _open_contract_counts(session, managed_id)
            open_count = open_actual + open_virtual

            market = (
                _normalize_market(base_api, body.market)
                if body.market is not None
                else _read_market(session, managed_id)
            )
            _write_preference(session, f"personal_execution_market:{managed_id}", market)

            queued = bool(changed and open_count)
            if queued:
                _write_pending(session, managed_id, requested)
                active = previous
            else:
                _clear_pending(session, managed_id)
                if changed:
                    _reset_risk_state(session, managed_id)
                    _clear_account_runtime_preferences(session, managed_id)
                active = write_strategy(
                    session,
                    managed_id,
                    family=requested.family,
                    side=requested.side,
                    prediction=requested.prediction,
                )

            status = str(row.execution_status or "inactive").strip().lower()
            running = bool(row.enabled) and status != "settlement_only"
            if queued:
                row.execution_status_reason = (
                    f"Strategy change to {requested.to_dict()['label']} is queued until "
                    f"{open_count} open contract(s) settle."
                )[:160]
            else:
                row.execution_status = "connecting" if running else row.execution_status
                row.execution_status_reason = (
                    f"Strategy changed to {active.to_dict()['label']}; market={market}. "
                    "The next unstarted cycle uses this selection from base state."
                )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()

        _safe_audit(
            base_api,
            "FINAL_SEAMLESS_STRATEGY_SWITCH",
            request,
            {
                "managed_account_id": managed_id,
                "previous": previous.to_dict(),
                "requested": requested.to_dict(),
                "active": active.to_dict(),
                "queued_until_settlement": queued,
                "open_actual_contracts": open_actual,
                "open_virtual_contracts": open_virtual,
                "execution_remained_enabled": running,
                "market": market,
                "credentials_preserved": True,
                "stake_tp_sl_martingale_preserved": True,
                "history_preserved": True,
            },
        )
        return {
            "success": True,
            "selection": active.to_dict(),
            "pending_selection": requested.to_dict() if queued else None,
            "queued_until_settlement": queued,
            "market": market,
            "lifecycle": "running" if running else "stopped",
            "execution_remained_enabled": running,
            "recovery_reset": bool(changed and not queued),
            "credentials_preserved": True,
            "settings_preserved": True,
            "history_preserved": True,
            "message": (
                f"{requested.to_dict()['label']} is queued and will activate after "
                f"the current {open_count} contract(s) settle."
                if queued
                else f"{active.to_dict()['label']} is active for the next qualifying cycle."
            ),
        }

    # The preceding script composer appended the market selector. Replace only its
    # response headers so the browser never receives a stale Content-Length after
    # JavaScript was appended.
    if dashboard_endpoint is not None:
        _remove_route(app, "/ui/dashboard-v2.js", "GET")
        _remove_route(app, "/ui/dashboard-v2.js", "HEAD")

        @app.get("/ui/dashboard-v2.js")
        def final_seamless_dashboard_script() -> Response:
            original = dashboard_endpoint()
            body = getattr(original, "body", b"")
            text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
            headers = dict(getattr(original, "headers", {}) or {})
            for key in list(headers):
                if str(key).lower() == "content-length":
                    headers.pop(key, None)
            headers.update(
                {
                    "Cache-Control": "no-store, max-age=0",
                    "X-FOA-Seamless-Execution": "2",
                }
            )
            return Response(
                content=text,
                media_type="application/javascript",
                headers=headers,
            )

        @app.head("/ui/dashboard-v2.js")
        def final_seamless_dashboard_script_head() -> Response:
            return Response(
                content=b"",
                media_type="application/javascript",
                headers={
                    "Cache-Control": "no-store, max-age=0",
                    "X-FOA-Seamless-Execution": "2",
                },
            )

    app.state.final_seamless_personal_execution_installed = True
    _INSTALLED = True
