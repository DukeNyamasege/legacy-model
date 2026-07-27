from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from app.account_lifecycle import (
    PAUSED_STATUSES,
    install_repository_account_lifecycle,
    pause_account,
    stop_account,
)
from app.api import (
    ROOT,
    REPOSITORY,
    app,
    get_current_account,
    oauth_callback,
)
from app.security_hardening import install_api_security_hardening

install_repository_account_lifecycle()
install_api_security_hardening(app)

# Replace only the public dashboard GET route so the approved dashboard can load
# lifecycle and inspection-deterrence enhancements without rewriting the large
# dashboard source file.
app.router.routes[:] = [
    route
    for route in app.router.routes
    if not (
        getattr(route, "path", None) == "/"
        and "GET" in set(getattr(route, "methods", set()) or set())
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


@app.get("/ui/security-hardening.js", include_in_schema=False)
def security_hardening_script() -> FileResponse:
    return FileResponse(
        ROOT / "dashboard" / "security-hardening.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.post("/me/pause-trading")
def pause_personal_trading(request: Request) -> dict:
    account = get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    pause_account(
        REPOSITORY,
        int(account["id"]),
        status="manual_pause",
        reason="Auto trading paused by user; recovery and session state preserved",
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
