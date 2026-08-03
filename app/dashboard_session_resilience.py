from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

import app.api as base_api
from app.dashboard_stability_fix import _remove_route
from app.final_virtual_history_ui import UI_VERSION, _headers, _html

_INSTALLED = False


def _safe_boot_session(request: Request) -> dict[str, Any] | None:
    try:
        account = base_api.get_current_account(request)
    except Exception:
        base_api.LOGGER.exception("DASHBOARD_BOOT_SESSION_FAILED")
        return None
    if not account:
        return None
    return {
        "authenticated": True,
        "account_type": str(account.get("account_type") or "demo"),
        "available_account_types": account.get("available_account_types") or ["demo"],
        "label": account.get("label") or "Account",
        "account_id_masked": account.get("account_id_masked") or "",
        "currency": "USD",
        "enabled": bool(account.get("enabled")),
        "has_trading_api_token": bool(account.get("has_trading_api_token")),
        "requires_api_token": bool(account.get("requires_api_token")),
        "trading_api_token_invalid": bool(account.get("trading_api_token_invalid")),
        "settings": {
            "stake_amount": float(account.get("stake_amount", 0.50) or 0.50),
            "take_profit": float(account.get("take_profit", 0.0) or 0.0),
            "stop_loss": float(account.get("stop_loss", 0.0) or 0.0),
            "martingale_enabled": bool(account.get("martingale_enabled", True)),
        },
    }


def _html_with_boot_session(request: Request) -> tuple[str, bool]:
    session = _safe_boot_session(request)
    html = _html()
    if not session:
        return html, False
    payload = json.dumps(session, separators=(",", ":")).replace("</", "<\\/")
    script = f"<script>window.FOA_BOOT_SESSION={payload};</script>"
    marker = '<script src="/ui/dashboard-v2.js'
    if marker in html:
        html = html.replace(marker, f"{script}\n  {marker}", 1)
    else:
        html = html.replace("</head>", f"  {script}\n</head>", 1)
    return html, True


def install_dashboard_session_resilience(app: Any) -> None:
    """Install the final dashboard root with authenticated-session bootstrap."""

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/", "GET")

    @app.get("/", include_in_schema=False)
    def dashboard_session_resilient_root(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ):
        if code or error:
            return base_api.oauth_callback(
                request,
                code=code,
                state=state,
                error=error,
                error_description=error_description,
            )
        html, booted = _html_with_boot_session(request)
        headers = {
            **_headers(),
            "X-FOA-UI-Version": UI_VERSION,
            "X-FOA-Session-Bootstrap": "1" if booted else "0",
        }
        return HTMLResponse(html, headers=headers)

    app.state.dashboard_session_resilience_installed = True
    _INSTALLED = True
