from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_stability_fix import _remove_route

_INSTALLED = False
UI_VERSION = "20260802-9"


def _headers(media_type: str) -> dict[str, str]:
    return {
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-FOA-UI-Version": UI_VERSION,
        "Content-Type": media_type,
    }


def install_head_request_compat(app: Any) -> None:
    """Allow curl -I / browser HEAD checks for the final dashboard routes."""

    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/", "/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "HEAD")

    @app.head("/", include_in_schema=False)
    def dashboard_root_head() -> Response:
        return Response(content=b"", headers=_headers("text/html; charset=utf-8"))

    @app.head("/ui/dashboard-v2.js", include_in_schema=False)
    def dashboard_v2_head() -> Response:
        return Response(content=b"", headers=_headers("application/javascript"))

    @app.head("/ui/simplified-dashboard.js", include_in_schema=False)
    def simplified_dashboard_head() -> Response:
        return Response(content=b"", headers=_headers("application/javascript"))

    app.state.head_request_compat_installed = True
    _INSTALLED = True
