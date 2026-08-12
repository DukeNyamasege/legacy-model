from __future__ import annotations

from typing import Any

from app.dashboard_stability_fix import _remove_route


_INSTALLED = False


def install_backend_only_surface(app: Any) -> None:
    """Expose the dedicated VPS as API/realtime only in split mode."""

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

    # Production logs showed the historical all-account summary consuming tens of
    # seconds even though the current Custom Strategy frontend does not render it.
    # Remove the expensive implementation entirely from the backend-only entrypoint
    # so an old browser/client cannot accidentally monopolize the API process.
    _remove_route(app, "/metrics/summary", "GET")

    @app.get("/", include_in_schema=False)
    def backend_root() -> dict[str, Any]:
        return {
            "service": "legacy-model-backend",
            "role": "api-worker-database-backend",
            "frontend": "netlify",
            "realtime": "/ws/me/live",
            "health": "/health",
        }

    @app.get("/metrics/summary", include_in_schema=False)
    def retired_global_summary() -> dict[str, Any]:
        return {
            "retired": True,
            "reason": "Netlify Custom Strategy frontend uses account-scoped realtime data",
            "performance_profile": "constant-time-retired-summary",
        }

    app.state.backend_only_surface_installed = True
    app.state.legacy_global_metrics_summary_retired = True
    _INSTALLED = True
