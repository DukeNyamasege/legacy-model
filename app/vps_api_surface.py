from __future__ import annotations

"""VPS-only API surface behind the Caddy reverse proxy."""

from typing import Any

from app.dashboard_stability_fix import _remove_route


_INSTALLED = False


def install_vps_api_surface(app: Any) -> None:
    """Keep static frontend delivery in the VPS frontend container, not FastAPI."""

    global _INSTALLED
    if _INSTALLED:
        return

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

    # The historical all-account summary is not used by the current account-scoped
    # Custom Strategy frontend and can monopolize the API process. Keep it retired.
    _remove_route(app, "/metrics/summary", "GET")

    @app.get("/", include_in_schema=False)
    def vps_api_root() -> dict[str, Any]:
        return {
            "service": "legacy-model-vps-api",
            "role": "api-control-plane",
            "frontend": "vps-frontend",
            "realtime": "/ws/me/live",
            "health": "/health",
            "hosting": "vps-only",
        }

    @app.get("/metrics/summary", include_in_schema=False)
    def retired_global_summary() -> dict[str, Any]:
        return {
            "retired": True,
            "reason": "VPS frontend uses account-scoped realtime data",
            "performance_profile": "constant-time-retired-summary",
        }

    app.state.vps_api_surface_installed = True
    app.state.frontend_served_by_api = False
    app.state.legacy_global_metrics_summary_retired = True
    app.state.api_surface = "vps-api-behind-caddy"
    _INSTALLED = True
