from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_stability_fix import _remove_route

_INSTALLED = False
UI_VERSION = "20260805-signal-alerts-1"


def _headers(media_type: str, *, signal_alerts: bool = False) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-FOA-UI-Version": UI_VERSION,
        "Content-Type": media_type,
    }
    if signal_alerts:
        headers["X-FOA-Signal-Alerts"] = "1"
    return headers


def install_head_request_compat(app: Any) -> None:
    """Allow curl -I / browser HEAD checks for the final dashboard routes."""

    global _INSTALLED
    if _INSTALLED:
        return

    for path in (
        "/",
        "/ui/dashboard-v2.js",
        "/ui/dashboard-actions-v2.js",
        "/ui/dashboard-v2.css",
        "/ui/simplified-dashboard.js",
        "/health",
        "/health/live",
        "/health/database",
        "/runtime",
    ):
        _remove_route(app, path, "HEAD")

    @app.head("/", include_in_schema=False)
    def dashboard_root_head() -> Response:
        return Response(content=b"", headers=_headers("text/html; charset=utf-8"))

    @app.head("/ui/dashboard-v2.js", include_in_schema=False)
    def dashboard_v2_head() -> Response:
        return Response(
            content=b"",
            headers=_headers("application/javascript", signal_alerts=True),
        )

    @app.head("/ui/dashboard-actions-v2.js", include_in_schema=False)
    def dashboard_actions_v2_head() -> Response:
        return Response(content=b"", headers=_headers("application/javascript"))

    @app.head("/ui/dashboard-v2.css", include_in_schema=False)
    def dashboard_v2_css_head() -> Response:
        return Response(content=b"", headers=_headers("text/css; charset=utf-8"))

    @app.head("/ui/simplified-dashboard.js", include_in_schema=False)
    def simplified_dashboard_head() -> Response:
        return Response(
            content=b"",
            headers=_headers("application/javascript", signal_alerts=True),
        )

    @app.head("/health", include_in_schema=False)
    def health_head() -> Response:
        return Response(content=b"", headers=_headers("application/json"))

    @app.head("/health/live", include_in_schema=False)
    def health_live_head() -> Response:
        return Response(content=b"", headers=_headers("application/json"))

    @app.head("/health/database", include_in_schema=False)
    def health_database_head() -> Response:
        return Response(content=b"", headers=_headers("application/json"))

    @app.head("/runtime", include_in_schema=False)
    def runtime_head() -> Response:
        return Response(content=b"", headers=_headers("application/json"))

    app.state.head_request_compat_installed = True
    _INSTALLED = True
