from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse

import app.api as base_api

_INSTALLED = False


def _remove_route(app: Any, path: str, method: str) -> None:
    expected = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        )
    ]


def _inject_scripts(html: str) -> str:
    scripts = (
        '<script src="/ui/account-lifecycle.js?v=20260731"></script>',
        '<script src="/ui/data-consistency.js?v=20260731"></script>',
        '<script src="/ui/security-hardening.js?v=20260731"></script>',
        '<script src="/ui/realtime-mode-hardening.js?v=20260731"></script>',
        '<script src="/custom-martingale.js?v=20260801-1"></script>',
        '<script src="/ui/readability-boost.js?v=20260801-2"></script>',
    )
    missing = [f"  {script}" for script in scripts if script not in html]
    if missing:
        html = html.replace("</body>", "\n".join(missing) + "\n</body>")
    return html


def install_dashboard_readability(app: Any) -> None:
    """Serve the dashboard with high-contrast readability CSS/JS installed last."""

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/", "GET")
    _remove_route(app, "/ui/readability-boost.js", "GET")

    @app.get("/", include_in_schema=False)
    def readable_dashboard(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ):
        # Keep compatibility with legacy root callbacks, though production OAuth
        # uses the explicit /oauth/callback endpoint.
        if code or error:
            return base_api.oauth_callback(
                request,
                code=code,
                state=state,
                error=error,
                error_description=error_description,
            )
        html = (base_api.ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(
            _inject_scripts(html),
            headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
        )

    @app.get("/ui/readability-boost.js", include_in_schema=False)
    def readability_boost_script() -> FileResponse:
        return FileResponse(
            base_api.ROOT / "dashboard" / "readability-boost.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    app.state.dashboard_readability_installed = True
    _INSTALLED = True
