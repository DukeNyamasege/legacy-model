from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

import app.api as base_api
from app.dashboard_stability_fix import _remove_route


_INSTALLED = False
UI_VERSION = "20260812-builder-first-authority-2"


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-FOA-UI-Version": UI_VERSION,
        "X-FOA-Builder-First-Dashboard": "1",
    }
    if extra:
        headers.update(extra)
    return headers


def _boot_session(request: Request) -> dict[str, Any] | None:
    try:
        account = base_api.get_current_account(request)
    except Exception:
        base_api.LOGGER.exception("BUILDER_FIRST_BOOT_SESSION_FAILED")
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


def _dashboard_html(request: Request) -> tuple[str, bool]:
    html = (base_api.ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        'href="/ui/dashboard-v2.css"',
        f'href="/ui/dashboard-v2.css?v={UI_VERSION}"',
    )
    html = html.replace(
        'src="/ui/dashboard-v2.js"',
        f'src="/ui/dashboard-v2.js?v={UI_VERSION}"',
    )
    html = html.replace(
        'src="/ui/dashboard-actions-v2.js"',
        f'src="/ui/dashboard-actions-v2.js?v={UI_VERSION}"',
    )

    session = _boot_session(request)
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


def _read_dashboard_asset(name: str) -> str:
    return (base_api.ROOT / "dashboard" / name).read_text(encoding="utf-8")


def install_builder_first_dashboard_authority(app: Any) -> None:
    """Make the simplified Custom Strategy Builder shell the final UI authority.

    Several legacy compatibility layers still register dashboard routes during
    startup. This installer must run last so production cannot fall back to the
    old Overview/AIDR interface after the builder-first migration.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    for path in (
        "/",
        "/ui/dashboard-v2.css",
        "/ui/dashboard-v2.js",
        "/ui/dashboard-actions-v2.js",
        "/ui/simplified-dashboard.js",
    ):
        _remove_route(app, path, "GET")
        _remove_route(app, path, "HEAD")

    @app.get("/", include_in_schema=False)
    def builder_first_root(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ) -> HTMLResponse:
        if code or error:
            return base_api.oauth_callback(
                request,
                code=code,
                state=state,
                error=error,
                error_description=error_description,
            )
        html, booted = _dashboard_html(request)
        return HTMLResponse(
            html,
            headers=_headers({"X-FOA-Session-Bootstrap": "1" if booted else "0"}),
        )

    @app.get("/ui/dashboard-v2.css", include_in_schema=False)
    def builder_first_css() -> Response:
        return Response(
            _read_dashboard_asset("dashboard-v2.css"),
            media_type="text/css",
            headers=_headers(),
        )

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def builder_first_dashboard_js() -> Response:
        return Response(
            _read_dashboard_asset("dashboard-v2.js"),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/dashboard-actions-v2.js", include_in_schema=False)
    def builder_first_actions_js() -> Response:
        return Response(
            _read_dashboard_asset("dashboard-actions-v2.js"),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def builder_first_compat_js() -> Response:
        return Response(
            '(() => { "use strict"; window.FOA_SIMPLIFIED_DASHBOARD_COMPAT = "builder-first"; })();\n',
            media_type="application/javascript",
            headers=_headers({"X-FOA-Simplified-Compatibility": "builder-first"}),
        )

    @app.head("/", include_in_schema=False)
    def builder_first_root_head() -> Response:
        return Response(content=b"", media_type="text/html", headers=_headers())

    @app.head("/ui/dashboard-v2.css", include_in_schema=False)
    def builder_first_css_head() -> Response:
        return Response(content=b"", media_type="text/css", headers=_headers())

    @app.head("/ui/dashboard-v2.js", include_in_schema=False)
    def builder_first_dashboard_js_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    @app.head("/ui/dashboard-actions-v2.js", include_in_schema=False)
    def builder_first_actions_js_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    @app.head("/ui/simplified-dashboard.js", include_in_schema=False)
    def builder_first_compat_js_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    app.state.builder_first_dashboard_authority_installed = True
    app.state.dashboard_ui_version = UI_VERSION
    _INSTALLED = True
