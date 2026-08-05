from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import or_, select

from app.final_personal_trade_stream import (
    _aidr_summary,
    _sort_time,
    _virtual_rows_with_progress,
)
from app.final_public_controls import (
    STOPPED_STATUSES,
    ClearTradesRequest,
    _clear_account_runtime_preferences,
    _current_account_payload,
    _load_managed_account,
    _remove_route,
    _reporting_timezone,
    _reset_risk_state,
    _today_bounds_utc,
    _trade_to_payload,
)
from app.models import (
    AccountRiskState,
    CandidateSignalRecord,
    DirectionalSignal,
    ManagedAccount,
    RuntimePreference,
    Trade,
    VirtualTrade,
    utc_now,
)
from app.strategy_v2_preferences import (
    normalize_strategy,
    read_strategy,
    strategy_catalog_payload,
    write_strategy,
)


_INSTALLED = False
VERSION = "seamless-personal-execution-v1"
MARKET_PREFERENCE_PREFIX = "personal_execution_market:"
HISTORY_ALL_PREFIX = "personal_history_cutoff_all:"
HISTORY_DAY_PREFIX = "personal_history_cutoff_day:"


class SeamlessStrategyRequest(BaseModel):
    family: str
    side: str
    prediction: int | None = None
    market: str | None = None


class ExecutionMarketRequest(BaseModel):
    market: str


def _market_key(managed_id: int) -> str:
    return f"{MARKET_PREFERENCE_PREFIX}{int(managed_id)}"


def _history_all_key(managed_id: int) -> str:
    return f"{HISTORY_ALL_PREFIX}{int(managed_id)}"


def _history_day_key(managed_id: int, day: str) -> str:
    return f"{HISTORY_DAY_PREFIX}{int(managed_id)}:{day}"


def _preference(session: Any, key: str, default: str = "") -> str:
    row = session.get(RuntimePreference, key)
    return str(row.preference_value or default) if row is not None else default


def _write_preference(session: Any, key: str, value: str) -> None:
    row = session.get(RuntimePreference, key)
    if row is None:
        row = RuntimePreference(preference_key=key)
        session.add(row)
    row.preference_value = str(value)
    row.updated_at = utc_now()


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _markets(base_api: Any) -> list[str]:
    values = list(getattr(getattr(base_api.CONFIG, "strategy", None), "symbols", ()) or ())
    normalized = [str(value).strip().upper() for value in values if str(value).strip()]
    return list(dict.fromkeys(normalized))


def _normalize_market(base_api: Any, value: Any) -> str:
    market = str(value or "ALL").strip().upper() or "ALL"
    allowed = {"ALL", "AUTO", *_markets(base_api)}
    if market not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported market. Choose ALL or one of: {', '.join(_markets(base_api))}",
        )
    return "ALL" if market == "AUTO" else market


def _read_market(session: Any, managed_id: int) -> str:
    return (_preference(session, _market_key(managed_id), "ALL") or "ALL").upper()


def _usable_pat(base_api: Any, row: ManagedAccount) -> tuple[bool, bool]:
    try:
        payload = base_api.managed_account_payload(row)
    except Exception:
        return False, False
    direct = bool(base_api.has_trading_api_token(payload))
    shared = bool(base_api.shared_trading_api_token(payload)) if not direct else False
    return direct or shared, shared


def _safe_audit(base_api: Any, action: str, request: Request, details: dict[str, Any]) -> None:
    try:
        base_api.REPOSITORY.audit(
            action,
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            details,
        )
    except Exception as exc:
        base_api.LOGGER.warning(
            "PERSONAL_EXECUTION_AUDIT_DEGRADED action=%s error_type=%s "
            "user_action_committed=true",
            action,
            type(exc).__name__,
        )


def _history_cutoff(session: Any, managed_id: int, day: str) -> datetime | None:
    values = (
        _preference(session, _history_all_key(managed_id)),
        _preference(session, _history_day_key(managed_id, day)),
    )
    parsed = [value for value in (_timestamp(item) for item in values) if value is not None]
    return max(parsed) if parsed else None


def _current_strategy_payload(base_api: Any, managed_id: int) -> dict[str, Any]:
    selection = read_strategy(base_api.DATABASE, managed_id)
    return selection.to_dict()


def _capture_dashboard_route(app: Any) -> Any | None:
    for route in app.router.routes:
        if (
            getattr(route, "path", None) == "/ui/dashboard-v2.js"
            and "GET" in set(getattr(route, "methods", set()) or set())
        ):
            return getattr(route, "endpoint", None)
    return None


_MARKET_UI_JS = r'''
/* FOA_SEAMLESS_EXECUTION_MARKET_V1 */
(() => {
  "use strict";
  if (window.FOA_SEAMLESS_EXECUTION_MARKET) return;
  window.FOA_SEAMLESS_EXECUTION_MARKET = "1";

  async function request(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: {"Content-Type": "application/json", ...(options.headers || {})},
    });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(body.detail || body.message || `Request failed (${response.status})`);
    return body;
  }

  async function install() {
    const selector = document.getElementById("foa-strategy-selector");
    if (!selector || selector.querySelector(".foa-execution-market-row")) return;
    let payload;
    try { payload = await request("/me/execution-market"); } catch (_) { return; }

    const row = document.createElement("div");
    row.className = "foa-execution-market-row";
    row.style.cssText = "display:grid;grid-template-columns:minmax(0,1fr) minmax(180px,260px);gap:12px;align-items:center;margin:12px 0;padding:12px;border:1px solid var(--line);border-radius:12px;background:rgba(47,115,255,.045)";
    const copy = document.createElement("div");
    copy.innerHTML = "<strong style='font-size:12px'>Execution market</strong><p style='margin:4px 0 0;color:var(--muted);font-size:10px;line-height:1.45'>ALL lets the model rotate markets. Choosing one market keeps this account on that market through stop, rejoin and strategy changes.</p>";
    const select = document.createElement("select");
    select.id = "foa-execution-market";
    select.style.cssText = "height:42px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--text);padding:0 10px;font-weight:800";
    const markets = ["ALL", ...(payload.markets || [])];
    for (const market of markets) {
      const option = document.createElement("option");
      option.value = market;
      option.textContent = market === "ALL" ? "ALL · automatic rotation" : market;
      option.selected = market === payload.market;
      select.appendChild(option);
    }
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        await request("/me/execution-market", {
          method: "POST",
          body: JSON.stringify({market: select.value}),
        });
      } catch (error) {
        window.alert(String(error.message || error));
      } finally {
        select.disabled = false;
      }
    });
    row.append(copy, select);
    const summary = selector.querySelector(".foa-strategy-summary");
    if (summary) summary.before(row); else selector.appendChild(row);
  }

  const observer = new MutationObserver(() => install());
  observer.observe(document.documentElement, {subtree: true, childList: true});
  document.addEventListener("DOMContentLoaded", install, {once: true});
  if (document.readyState !== "loading") install();
})();
'''


def install_seamless_personal_execution(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import app.api as base_api

    dashboard_endpoint = _capture_dashboard_route(app)

    for path, method in (
        ("/me/auto-trade", "POST"),
        ("/me/resume-trading", "POST"),
        ("/me/pause-trading", "POST"),
        ("/me/stop-trading", "POST"),
        ("/me/trading-lifecycle", "GET"),
        ("/me/trades/today", "GET"),
        ("/me/clear-trades", "POST"),
        ("/me/strategy-settings", "GET"),
        ("/me/strategy-settings", "POST"),
        ("/me/execution-market", "GET"),
        ("/me/execution-market", "POST"),
    ):
        _remove_route(app, path, method)

    @app.post("/me/stop-trading")
    def seamless_stop(request: Request) -> dict[str, Any]:
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            managed_id = int(row.id)
            row.enabled = False
            row.execution_status = "stopped"
            row.execution_status_reason = (
                "New entries stopped. Credentials, strategy, market, stake and "
                "recovery state are preserved for seamless rejoin."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            market = _read_market(session, managed_id)
            strategy = _current_strategy_payload(base_api, managed_id)
        _safe_audit(
            base_api,
            "SEAMLESS_PERSONAL_TRADING_STOPPED",
            request,
            {
                "managed_account_id": managed_id,
                "recovery_state_preserved": True,
                "credentials_preserved": True,
                "strategy_preserved": strategy,
                "market_preserved": market,
                "open_contract_settlement_preserved": True,
            },
        )
        return {
            "success": True,
            "state": "stopped",
            "lifecycle": "stopped",
            "enabled": False,
            "recovery_preserved": True,
            "strategy": strategy,
            "market": market,
            "message": "New entries stopped. Rejoin continues the preserved account configuration.",
        }

    @app.post("/me/pause-trading")
    def seamless_pause(request: Request) -> dict[str, Any]:
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            row.enabled = False
            row.execution_status = "manual_pause"
            row.execution_status_reason = (
                "Execution paused; account configuration and recovery state preserved."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            managed_id = int(row.id)
        _safe_audit(
            base_api,
            "SEAMLESS_PERSONAL_TRADING_PAUSED",
            request,
            {"managed_account_id": managed_id, "state_preserved": True},
        )
        return {
            "success": True,
            "state": "paused",
            "lifecycle": "paused",
            "enabled": False,
            "recovery_preserved": True,
        }

    @app.post("/me/resume-trading")
    def seamless_resume(
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
                        "with trade scope. Link it in Settings > Credentials."
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
                "Rejoining execution with preserved strategy, market, stake and "
                "account recovery state."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            managed_id = int(row.id)
            market = _read_market(session, managed_id)
            strategy = _current_strategy_payload(base_api, managed_id)

        base_api.REPOSITORY.set_status("RUNNING", "")
        _safe_audit(
            base_api,
            "SEAMLESS_PERSONAL_TRADING_REJOINED",
            request,
            {
                "managed_account_id": managed_id,
                "requested_mode": str(body.mode),
                "previous_status": previous,
                "recovery_state_preserved": True,
                "shared_verified_pat_reused": shared_pat,
                "strategy": strategy,
                "market": market,
            },
        )
        return {
            "success": True,
            "state": "running",
            "lifecycle": "running",
            "enabled": True,
            "mode": str(body.mode),
            "recovery_reset": False,
            "recovery_preserved": True,
            "stored_pat_reused": True,
            "shared_pat_reused": shared_pat,
            "strategy": strategy,
            "market": market,
            "message": "Execution rejoined with the preserved account configuration.",
        }

    @app.post("/me/auto-trade")
    def seamless_auto_trade(
        request: Request,
        body: base_api.AutoTradeRequest,
    ) -> dict[str, Any]:
        if bool(body.enabled):
            return seamless_resume(
                request,
                base_api.ResumeTradeRequest(mode="resume"),
            )
        return seamless_stop(request)

    @app.get("/me/trading-lifecycle")
    def seamless_lifecycle(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            return {"authenticated": False, "lifecycle": "logged_out"}
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, int(account["id"]))
            if row is None:
                return {"authenticated": False, "lifecycle": "missing"}
            status = str(row.execution_status or "inactive").strip().lower()
            lifecycle = (
                "running"
                if bool(row.enabled)
                else "stopped"
                if status in STOPPED_STATUSES
                else "paused"
            )
            has_pat, shared_pat = _usable_pat(base_api, row)
            return {
                "authenticated": True,
                "lifecycle": lifecycle,
                "execution_status": status,
                "reason": str(row.execution_status_reason or ""),
                "enabled": bool(row.enabled),
                "has_bulk_trade_pat": has_pat,
                "shared_verified_pat_available": shared_pat,
                "strategy": _current_strategy_payload(base_api, int(row.id)),
                "market": _read_market(session, int(row.id)),
                "recovery_preserved_across_stop": True,
            }

    @app.get("/me/execution-market")
    def get_execution_market(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        with base_api.DATABASE.session() as session:
            market = _read_market(session, int(account["id"]))
        return {
            "authenticated": True,
            "market": market,
            "markets": _markets(base_api),
            "automatic_rotation": market == "ALL",
        }

    @app.post("/me/execution-market")
    def set_execution_market(
        request: Request,
        body: ExecutionMarketRequest,
    ) -> dict[str, Any]:
        account = _current_account_payload(request)
        market = _normalize_market(base_api, body.market)
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, int(account["id"]), with_for_update=True)
            if row is None:
                raise HTTPException(status_code=401, detail="Managed account was not found")
            previous = _read_market(session, int(row.id))
            _write_preference(session, _market_key(int(row.id)), market)
            row.execution_status_reason = (
                f"Execution market changed from {previous} to {market}; "
                "the next unstarted signal uses the new market."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            enabled = bool(row.enabled)
        _safe_audit(
            base_api,
            "PERSONAL_EXECUTION_MARKET_CHANGED",
            request,
            {
                "managed_account_id": int(account["id"]),
                "previous_market": previous,
                "new_market": market,
                "execution_remained_enabled": enabled,
            },
        )
        return {
            "success": True,
            "market": market,
            "automatic_rotation": market == "ALL",
            "lifecycle": "running" if enabled else "stopped",
            "message": (
                "Market preference saved. It remains active through stop, rejoin "
                "and strategy switching."
            ),
        }

    @app.get("/me/strategy-settings")
    def seamless_strategy_settings(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        selection = read_strategy(base_api.DATABASE, managed_id)
        with base_api.DATABASE.session() as session:
            market = _read_market(session, managed_id)
        return {
            "authenticated": True,
            "managed_account_id": managed_id,
            "selection": selection.to_dict(),
            "catalog": strategy_catalog_payload(),
            "market": market,
            "markets": _markets(base_api),
            "seamless_switching": True,
        }

    @app.post("/me/strategy-settings")
    def seamless_strategy_switch(
        request: Request,
        body: SeamlessStrategyRequest,
    ) -> dict[str, Any]:
        try:
            requested = normalize_strategy(body.family, body.side, body.prediction)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            managed_id = int(row.id)
            previous = read_strategy(base_api.DATABASE, managed_id)
            strategy_changed = (
                previous.family,
                previous.side,
                previous.prediction,
            ) != (
                requested.family,
                requested.side,
                requested.prediction,
            )
            open_actual = int(
                session.scalar(
                    select(Trade.id)
                    .where(
                        Trade.managed_account_id == managed_id,
                        Trade.settlement_time.is_(None),
                    )
                    .limit(1)
                )
                is not None
            )
            open_virtual = int(
                session.scalar(
                    select(VirtualTrade.id)
                    .where(
                        VirtualTrade.managed_account_id == managed_id,
                        VirtualTrade.result == "OPEN",
                    )
                    .limit(1)
                )
                is not None
            )

            if strategy_changed:
                _reset_risk_state(session, managed_id)
                _clear_account_runtime_preferences(session, managed_id)

            selection = write_strategy(
                session,
                managed_id,
                family=requested.family,
                side=requested.side,
                prediction=requested.prediction,
            )
            market = (
                _normalize_market(base_api, body.market)
                if body.market is not None
                else _read_market(session, managed_id)
            )
            _write_preference(session, _market_key(managed_id), market)
            enabled = bool(row.enabled)
            row.execution_status = "connecting" if enabled else "stopped"
            row.execution_status_reason = (
                f"Strategy changed to {selection.to_dict()['label']}; "
                f"market={market}. Existing contracts settle under their original "
                "metadata; the next unstarted cycle uses this selection."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()

        _safe_audit(
            base_api,
            "SEAMLESS_PERSONAL_STRATEGY_CHANGED",
            request,
            {
                "managed_account_id": managed_id,
                "previous_family": previous.family,
                "previous_side": previous.side,
                "previous_prediction": previous.prediction,
                "new_family": selection.family,
                "new_side": selection.side,
                "new_prediction": selection.prediction,
                "market": market,
                "execution_remained_enabled": enabled,
                "open_actual_contracts_preserved": open_actual,
                "open_virtual_contracts_preserved": open_virtual,
                "strategy_recovery_reset": strategy_changed,
                "credentials_preserved": True,
                "personal_settings_preserved": True,
                "history_preserved": True,
            },
        )
        return {
            "success": True,
            "selection": selection.to_dict(),
            "market": market,
            "lifecycle": "running" if enabled else "stopped",
            "execution_remained_enabled": enabled,
            "recovery_reset": strategy_changed,
            "credentials_preserved": True,
            "settings_preserved": True,
            "history_preserved": True,
            "open_contracts_settle_under_previous_strategy": bool(open_actual or open_virtual),
            "message": (
                f"{selection.to_dict()['label']} saved. Existing open contracts "
                "continue settlement; the next unstarted cycle uses the new strategy."
            ),
        }

    @app.post("/me/clear-trades")
    def seamless_clear_history(
        request: Request,
        body: ClearTradesRequest,
    ) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        scope = str(body.scope or "today").strip().lower()
        if scope not in {"today", "all"}:
            raise HTTPException(status_code=400, detail="scope must be today or all")
        start, end = _today_bounds_utc()
        now = utc_now()
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id, with_for_update=True)
            if row is None:
                raise HTTPException(status_code=401, detail="Managed account was not found")
            trade_query = select(Trade.id).where(
                Trade.managed_account_id == managed_id,
                Trade.settlement_time.is_not(None),
            )
            virtual_query = select(VirtualTrade.id).where(
                VirtualTrade.managed_account_id == managed_id,
                VirtualTrade.result != "OPEN",
            )
            if scope == "today":
                trade_query = trade_query.where(
                    or_(
                        Trade.purchase_time.between(start, end),
                        Trade.settlement_time.between(start, end),
                        Trade.provider_purchase_time.between(start, end),
                    )
                )
                virtual_query = virtual_query.where(
                    or_(
                        VirtualTrade.created_at.between(start, end),
                        VirtualTrade.settled_at.between(start, end),
                    )
                )
                key = _history_day_key(
                    managed_id,
                    start.astimezone(_reporting_timezone()).date().isoformat(),
                )
            else:
                key = _history_all_key(managed_id)
            hidden_actual = len(session.scalars(trade_query).all())
            hidden_virtual = len(session.scalars(virtual_query).all())
            _write_preference(session, key, now.isoformat())
            row.execution_status_reason = (
                f"Personal {scope} history hidden from the dashboard. "
                "Financial and settlement records remain preserved."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()

        _safe_audit(
            base_api,
            "PERSONAL_TRADE_HISTORY_HIDDEN",
            request,
            {
                "managed_account_id": managed_id,
                "scope": scope,
                "hidden_actual_rows": hidden_actual,
                "hidden_virtual_rows": hidden_virtual,
                "database_rows_deleted": 0,
                "recovery_state_preserved": True,
            },
        )
        return {
            "success": True,
            "scope": scope,
            "deleted_trades": 0,
            "deleted_virtual_trades": 0,
            "hidden_trades": hidden_actual,
            "hidden_virtual_trades": hidden_virtual,
            "database_records_preserved": True,
            "recovery_preserved": True,
            "message": (
                f"Reset {scope} dashboard history without deleting financial records "
                "or changing the account recovery state."
            ),
        }

    @app.get("/me/trades/today")
    def seamless_trade_stream(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        start, end = _today_bounds_utc()
        day = start.astimezone(_reporting_timezone()).date().isoformat()

        with base_api.DATABASE.session() as session:
            cutoff = _history_cutoff(session, managed_id, day)
            actual_time_filter = or_(
                Trade.purchase_time.between(start, end),
                Trade.settlement_time.between(start, end),
                Trade.provider_purchase_time.between(start, end),
            )
            virtual_time_filter = or_(
                VirtualTrade.created_at.between(start, end),
                VirtualTrade.settled_at.between(start, end),
            )
            if cutoff is not None:
                actual_time_filter = actual_time_filter & or_(
                    Trade.settlement_time.is_(None),
                    Trade.purchase_time >= cutoff,
                    Trade.provider_purchase_time >= cutoff,
                )
                virtual_time_filter = virtual_time_filter & or_(
                    VirtualTrade.result == "OPEN",
                    VirtualTrade.created_at >= cutoff,
                )

            actual_rows = session.execute(
                select(Trade, CandidateSignalRecord, DirectionalSignal)
                .outerjoin(
                    CandidateSignalRecord,
                    CandidateSignalRecord.signal_id == Trade.signal_id,
                )
                .outerjoin(
                    DirectionalSignal,
                    DirectionalSignal.signal_id == Trade.signal_id,
                )
                .where(
                    Trade.managed_account_id == managed_id,
                    actual_time_filter,
                )
                .order_by(Trade.purchase_time.desc())
                .limit(5000)
            ).all()
            virtual_rows = session.scalars(
                select(VirtualTrade)
                .where(
                    VirtualTrade.managed_account_id == managed_id,
                    virtual_time_filter,
                )
                .order_by(VirtualTrade.created_at.asc())
                .limit(5000)
            ).all()
            state = session.get(AccountRiskState, managed_id)

        actual_trades = [
            {
                **_trade_to_payload(trade, candidate, directional),
                "is_virtual": False,
                "trade_kind": "actual",
                "history_retained": True,
            }
            for trade, candidate, directional in actual_rows
        ]
        virtual_trades = _virtual_rows_with_progress(list(virtual_rows))
        trades = sorted(
            [*actual_trades, *virtual_trades],
            key=_sort_time,
            reverse=True,
        )

        wins = sum(str(row.get("outcome") or "").upper() == "WIN" for row in actual_trades)
        losses = sum(str(row.get("outcome") or "").upper() == "LOSS" for row in actual_trades)
        open_trades = sum(
            str(row.get("outcome") or "OPEN").upper() not in {"WIN", "LOSS"}
            for row in actual_trades
        )
        profit = sum(float(row.get("profit") or 0.0) for row in actual_trades)
        aidr = _aidr_summary(state, managed_id)

        return {
            "authenticated": True,
            "account": str(account.get("account_id_masked") or ""),
            "account_type": str(account.get("account_type") or "demo"),
            "timezone": str(_reporting_timezone()),
            "date": day,
            "history_cutoff": cutoff.isoformat() if cutoff else None,
            "history_preserved_across_stop": True,
            "database_records_preserved": True,
            "trades": trades,
            "aidr": aidr,
            "summary": {
                "total": len(actual_trades),
                "settled": wins + losses,
                "wins": wins,
                "losses": losses,
                "open": open_trades,
                "profit": round(profit, 8),
                "win_rate": wins / (wins + losses) if wins + losses else 0.0,
                "virtual_observations": len(virtual_trades),
                "virtual_wins": int(aidr["virtual_wins"]),
                "virtual_wins_required": int(aidr["virtual_wins_required"]),
                "virtual_losses": int(aidr["virtual_losses"]),
                "virtual_open": sum(row.get("outcome") == "OPEN" for row in virtual_trades),
                "history_rows": len(trades),
            },
        }

    if dashboard_endpoint is not None:
        _remove_route(app, "/ui/dashboard-v2.js", "GET")
        _remove_route(app, "/ui/dashboard-v2.js", "HEAD")

        @app.get("/ui/dashboard-v2.js")
        def seamless_dashboard_script() -> Response:
            original = dashboard_endpoint()
            body = getattr(original, "body", b"")
            text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
            headers = dict(getattr(original, "headers", {}) or {})
            headers.update(
                {
                    "Cache-Control": "no-store, max-age=0",
                    "X-FOA-Seamless-Execution": "1",
                }
            )
            return Response(
                content=text + "\n" + _MARKET_UI_JS,
                media_type="application/javascript",
                headers=headers,
            )

        @app.head("/ui/dashboard-v2.js")
        def seamless_dashboard_script_head() -> Response:
            return Response(
                content=b"",
                media_type="application/javascript",
                headers={
                    "Cache-Control": "no-store, max-age=0",
                    "X-FOA-Seamless-Execution": "1",
                },
            )

    app.state.seamless_personal_execution_installed = True
    _INSTALLED = True
