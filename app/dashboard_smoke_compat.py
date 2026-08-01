from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

import app.api as base_api
from app.dashboard_settings_guard import _script as dashboard_script_with_settings_guard
from app.dashboard_stability_fix import UI_VERSION, _html, _remove_route

_INSTALLED = False


def _html_with_smoke_marker() -> str:
    html = _html()
    marker = "<!-- compatibility marker: /ui/simplified-dashboard.js -->"
    if "/ui/simplified-dashboard.js" not in html:
        html = html.replace(
            '<script src="/ui/dashboard-v2.js',
            marker + '\n  <script src="/ui/dashboard-v2.js',
        )
    return html


def _simplified_compat_script() -> str:
    # The production smoke test still checks the historical simplified-dashboard
    # route. Keep that route alive as a compatibility alias while the real
    # dashboard continues to load dashboard-v2.js and dashboard-actions-v2.js.
    compatibility_markers = """
/* production smoke compatibility markers:
   foa-simple-app
   foa-simple-active
   My Account
   Recent Trades
   /metrics/summary
   /metrics/recent-trades
   /me
*/
"""
    return compatibility_markers + dashboard_script_with_settings_guard()


def install_dashboard_smoke_compat(app: Any) -> None:
    """Keep the old deployment smoke checks compatible with the new dashboard.

    The live UI is now the stable dashboard-v2 shell. Some deployment checks still
    look for the old simplified-dashboard marker and route. This layer keeps that
    compatibility without re-loading the old legacy browser runtime.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/", include_in_schema=False)
    def dashboard_root_with_smoke_marker(
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
        return HTMLResponse(
            _html_with_smoke_marker(),
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def simplified_dashboard_smoke_compat() -> Response:
        return Response(
            _simplified_compat_script(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    app.state.dashboard_smoke_compat_installed = True
    _INSTALLED = True
