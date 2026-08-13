from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

import app.api as base_api
from app.custom_strategy_last_digit_prediction import (
    install_custom_strategy_last_digit_prediction,
)

# Dynamic Matches/Differs prediction must extend the canonical strategy module
# before the API/runtime modules bind normalization helpers by import.
install_custom_strategy_last_digit_prediction()

from app.custom_strategy_api import install_custom_strategy_api  # noqa: E402
from app.custom_strategy_runtime_api import install_custom_strategy_runtime_api  # noqa: E402
from app.dashboard_live_events import install_dashboard_live_events  # noqa: E402
from app.dashboard_stability_fix import _remove_route  # noqa: E402
from app.global_trade_history_cutoff import install_global_trade_history_cutoff  # noqa: E402
from app.session_risk_api_authority import install_session_risk_api_authority  # noqa: E402


_INSTALLED = False
UI_VERSION = "20260813-final-readiness-1"


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-FOA-UI-Version": UI_VERSION,
        "X-FOA-Builder-First-Dashboard": "1",
        "X-FOA-Custom-Direct-Runtime": "1",
        "X-FOA-Live-Dashboard": "sse-primary",
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

    direct_script = f'<script src="/ui/custom-runtime-client.js?v={UI_VERSION}"></script>'
    marker = '<script src="/ui/dashboard-v2.js'
    if marker in html:
        html = html.replace(marker, f"{direct_script}\n  {marker}", 1)
    else:
        html = html.replace("</head>", f"  {direct_script}\n</head>", 1)

    session = _boot_session(request)
    if not session:
        return html, False
    payload = json.dumps(session, separators=(",", ":")).replace("</", "<\\/")
    script = f"<script>window.FOA_BOOT_SESSION={payload};</script>"
    marker = '<script src="/ui/custom-runtime-client.js'
    if marker in html:
        html = html.replace(marker, f"{script}\n  {marker}", 1)
    else:
        html = html.replace("</head>", f"  {script}\n</head>", 1)
    return html, True


def _read_dashboard_asset(name: str) -> str:
    return (base_api.ROOT / "dashboard" / name).read_text(encoding="utf-8")


def install_builder_first_dashboard_authority(app: Any) -> None:
    """Make Custom Strategy Builder + direct account execution the final authority."""

    global _INSTALLED
    if _INSTALLED:
        return

    # This is the final API installer in api_v3. Re-assert the account-wide trade
    # visibility boundary here so no older reset route can make cleared history
    # reappear after logout/login or on a second device.
    install_global_trade_history_cutoff(app)

    # Re-register the direct Custom Strategy routes, then replace their generic
    # lifecycle endpoints with the signed session-risk authority. Every browser and
    # the worker therefore read the same frozen TP/SL thresholds for this Start.
    install_custom_strategy_api(app)
    install_custom_strategy_runtime_api(app)
    install_session_risk_api_authority(app)
    install_dashboard_live_events(app)

    for path in (
        "/",
        "/ui/dashboard-v2.css",
        "/ui/dashboard-v2.js",
        "/ui/dashboard-actions-v2.js",
        "/ui/custom-runtime-client.js",
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
        css = (
            _read_dashboard_asset("dashboard-v2.css")
            + "\n\n"
            + _read_dashboard_asset("mobile-first-compact.css")
        )
        return Response(css, media_type="text/css", headers=_headers())

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def builder_first_dashboard_js() -> Response:
        source = (
            _read_dashboard_asset("dashboard-v2.js")
            + "\n\n"
            + _read_dashboard_asset("oauth-direct-runtime.js")
        )
        return Response(source, media_type="application/javascript", headers=_headers())

    @app.get("/ui/dashboard-actions-v2.js", include_in_schema=False)
    def builder_first_actions_js() -> Response:
        return Response(
            _read_dashboard_asset("dashboard-actions-v2.js"),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/custom-runtime-client.js", include_in_schema=False)
    def custom_runtime_client_js() -> Response:
        return Response(
            _read_dashboard_asset("custom-runtime-client.js"),
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

    @app.head("/ui/custom-runtime-client.js", include_in_schema=False)
    def custom_runtime_client_js_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    @app.head("/ui/simplified-dashboard.js", include_in_schema=False)
    def builder_first_compat_js_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    app.state.builder_first_dashboard_authority_installed = True
    app.state.custom_direct_runtime_ui_installed = True
    app.state.dashboard_ui_version = UI_VERSION
    _INSTALLED = True
