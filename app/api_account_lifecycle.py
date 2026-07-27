from __future__ import annotations

import math

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

import app.api as base_api
from app.account_lifecycle import (
    PAUSED_STATUSES,
    install_repository_account_lifecycle,
    pause_account,
    stop_account,
)
from app.api import (
    AutoTradeRequest,
    CONFIG,
    LOGGER,
    ROOT,
    REPOSITORY,
    ResumeTradeRequest,
    app,
    get_current_account,
    get_me as base_get_me,
    oauth_callback,
)
from app.dashboard_consistency import (
    consistent_period_response,
    install_dashboard_consistency,
)
from app.security_hardening import install_api_security_hardening
from app.services.telegram_api_alerts import queue_real_api_lifecycle_alert

install_repository_account_lifecycle()
install_dashboard_consistency(base_api)
install_api_security_hardening(app)

# Replace routes where this production wrapper adds lifecycle, private Telegram,
# or dashboard-accounting guarantees. Everything else remains in app.api.
_replaced_routes = {
    ("/", "GET"),
    ("/me", "GET"),
    ("/me/auto-trade", "POST"),
    ("/me/resume-trading", "POST"),
    ("/metrics/system-performance", "GET"),
}
app.router.routes[:] = [
    route
    for route in app.router.routes
    if not any(
        getattr(route, "path", None) == path
        and method in set(getattr(route, "methods", set()) or set())
        for path, method in _replaced_routes
    )
]


@app.get("/", include_in_schema=False)
def lifecycle_dashboard(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    if code or error:
        return oauth_callback(
            request,
            code=code,
            state=state,
            error=error,
            error_description=error_description,
        )
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    scripts = (
        '<script src="/ui/account-lifecycle.js?v=20260727"></script>',
        '<script src="/ui/data-consistency.js?v=20260727"></script>',
        '<script src="/ui/security-hardening.js?v=20260727"></script>',
    )
    injection = []
    for marker in scripts:
        if marker not in html:
            injection.append(f"  {marker}")
    if injection:
        html = html.replace("</body>", "\n".join(injection) + "\n</body>")
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/ui/account-lifecycle.js", include_in_schema=False)
def lifecycle_script() -> FileResponse:
    return FileResponse(
        ROOT / "dashboard" / "account-lifecycle.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/ui/data-consistency.js", include_in_schema=False)
def data_consistency_script() -> FileResponse:
    return FileResponse(
        ROOT / "dashboard" / "data-consistency.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/ui/security-hardening.js", include_in_schema=False)
def security_hardening_script() -> FileResponse:
    return FileResponse(
        ROOT / "dashboard" / "security-hardening.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/me")
def get_me_consistent(request: Request) -> dict:
    """Keep the personal card mathematically settled-trade consistent.

    An account can briefly have one purchased-but-unsettled contract. Displaying
    that as a completed Trade while Wins + Losses is still one lower makes the
    card look corrupt. Completed Trades therefore equals Wins + Losses; any open
    amount is reported separately for consumers that need it.
    """
    payload = base_get_me(request)
    if not payload.get("authenticated"):
        return payload
    stats = dict(payload.get("stats") or {})
    reported_trades = int(stats.get("trades") or 0)
    wins = int(stats.get("wins") or 0)
    losses = int(stats.get("losses") or 0)
    settled_trades = wins + losses
    stats.update(
        {
            "trades": settled_trades,
            "settled_trades": settled_trades,
            "open_trades": max(0, reported_trades - settled_trades),
        }
    )
    payload["stats"] = stats
    payload["data_consistency"] = {
        "invariant_ok": settled_trades == wins + losses,
        "rule": "completed_trades_equal_wins_plus_losses",
    }
    return payload


@app.get("/metrics/system-performance")
def system_performance_consistent(
    request: Request,
    period: str = "today",
    simulated_base_stake: float = 0.50,
) -> dict:
    if not math.isfinite(float(simulated_base_stake)):
        raise HTTPException(status_code=400, detail="Simulation stake must be finite")
    try:
        return consistent_period_response(
            base_api,
            request=request,
            period=period,
            simulated_base_stake=float(simulated_base_stake),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/me/auto-trade")
def toggle_auto_trade_with_private_alert(request: Request, body: AutoTradeRequest) -> dict:
    account = get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if body.enabled and not account.get("has_trading_api_token", False):
        raise HTTPException(
            status_code=409,
            detail="Save a Deriv API token for this account before joining auto trading.",
        )

    before_status = str(account.get("execution_status") or "inactive").strip().lower()
    REPOSITORY.set_managed_account_enabled(int(account["id"]), bool(body.enabled))
    REPOSITORY.set_status("RUNNING", "")

    if body.enabled:
        event = "start" if before_status in {"inactive", "disabled", "stopped"} else "resume"
        queue_real_api_lifecycle_alert(
            REPOSITORY,
            CONFIG,
            LOGGER,
            managed_account_id=int(account["id"]),
            event=event,
        )
    else:
        queue_real_api_lifecycle_alert(
            REPOSITORY,
            CONFIG,
            LOGGER,
            managed_account_id=int(account["id"]),
            event="pause",
            reason="Auto trading paused by trader",
        )

    REPOSITORY.audit(
        "PERSONAL_AUTO_TRADE_UPDATED",
        str(account.get("account_id_masked", "account")),
        request.client.host if request.client else "unknown",
        {
            "managed_account_id": int(account["id"]),
            "enabled": bool(body.enabled),
        },
    )
    return {"success": True, "enabled": bool(body.enabled)}


@app.post("/me/resume-trading")
def resume_trading_with_private_alert(request: Request, body: ResumeTradeRequest) -> dict:
    account = get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not account.get("has_trading_api_token", False):
        raise HTTPException(
            status_code=409,
            detail="Save a Deriv API token before resuming auto trading.",
        )

    before_status = str(account.get("execution_status") or "inactive").strip().lower()
    reset_stake = body.mode == "start_again"
    REPOSITORY.resume_managed_account(int(account["id"]), reset_recovery=reset_stake)
    REPOSITORY.set_managed_account_enabled(int(account["id"]), True)
    REPOSITORY.set_status("RUNNING", "")

    event = "start" if reset_stake or before_status in {"inactive", "disabled", "stopped"} else "resume"
    queue_real_api_lifecycle_alert(
        REPOSITORY,
        CONFIG,
        LOGGER,
        managed_account_id=int(account["id"]),
        event=event,
    )

    REPOSITORY.audit(
        "PERSONAL_RESUME_TRADING",
        str(account.get("account_id_masked", "account")),
        request.client.host if request.client else "unknown",
        {
            "managed_account_id": int(account["id"]),
            "mode": body.mode,
            "recovery_reset": reset_stake,
        },
    )
    return {"success": True, "mode": body.mode, "recovery_reset": reset_stake}


@app.post("/me/pause-trading")
def pause_personal_trading(request: Request) -> dict:
    account = get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    reason = "Auto trading paused by user; recovery and session state preserved"
    pause_account(
        REPOSITORY,
        int(account["id"]),
        status="manual_pause",
        reason=reason,
    )
    queue_real_api_lifecycle_alert(
        REPOSITORY,
        CONFIG,
        LOGGER,
        managed_account_id=int(account["id"]),
        event="pause",
        reason=reason,
    )
    REPOSITORY.audit(
        "PERSONAL_TRADING_PAUSED",
        str(account.get("account_id_masked", "account")),
        request.client.host if request.client else "unknown",
        {
            "managed_account_id": int(account["id"]),
            "recovery_state_preserved": True,
        },
    )
    return {
        "success": True,
        "state": "paused",
        "message": "Trading paused. Recovery and session state were preserved.",
    }


@app.post("/me/stop-trading")
def stop_personal_trading(request: Request) -> dict:
    account = get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Snapshot opening/closing balance and session P/L before Stop intentionally
    # clears the account session state.
    queue_real_api_lifecycle_alert(
        REPOSITORY,
        CONFIG,
        LOGGER,
        managed_account_id=int(account["id"]),
        event="stop",
    )
    stop_account(REPOSITORY, int(account["id"]))
    REPOSITORY.audit(
        "PERSONAL_TRADING_STOPPED",
        str(account.get("account_id_masked", "account")),
        request.client.host if request.client else "unknown",
        {
            "managed_account_id": int(account["id"]),
            "recovery_state_reset": True,
            "next_start_uses_base_stake": True,
        },
    )
    return {
        "success": True,
        "state": "stopped",
        "message": "Trading stopped. Recovery was cleared; Start Again will use the configured base stake.",
    }


@app.get("/me/trading-lifecycle")
def personal_trading_lifecycle(request: Request) -> dict:
    account = get_current_account(request)
    if not account:
        return {"authenticated": False}
    status = str(account.get("execution_status") or "inactive").strip().lower()
    if status == "stopped":
        lifecycle = "stopped"
    elif not bool(account.get("enabled")) or status in PAUSED_STATUSES:
        lifecycle = "paused"
    else:
        lifecycle = "running"
    return {
        "authenticated": True,
        "lifecycle": lifecycle,
        "execution_status": status,
        "reason": str(account.get("execution_status_reason") or ""),
        "enabled": bool(account.get("enabled")),
    }
