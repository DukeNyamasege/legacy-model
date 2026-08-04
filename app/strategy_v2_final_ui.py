from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_request_coalescing import (
    _headers as broker_headers,
    _script as broker_script,
)
from app.dashboard_stability_fix import _remove_route
from app.strategy_v2_ui import _STRATEGY_V2_JS

_INSTALLED = False
UI_VERSION = "20260804-strategy-v2-final-1"


def _script(*, compatibility: bool = False) -> str:
    source = broker_script(compatibility=compatibility)
    if "FOA_STRATEGY_V2_UI_VERSION:20260804-2" not in source:
        source += _STRATEGY_V2_JS
    return source


def _headers() -> dict[str, str]:
    return {
        **broker_headers(),
        "X-FOA-Strategy-V2": "1",
        "X-FOA-UI-Version": UI_VERSION,
    }


def install_strategy_v2_final_ui(app: Any) -> None:
    """Serve request coalescing and strategy v2 from one final route authority."""

    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def final_strategy_v2_dashboard() -> Response:
        return Response(
            _script(),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def final_strategy_v2_compat() -> Response:
        return Response(
            _script(compatibility=True),
            media_type="application/javascript",
            headers=_headers(),
        )

    app.state.strategy_v2_final_ui_installed = True
    app.state.strategy_v2_final_ui_version = UI_VERSION
    _INSTALLED = True
