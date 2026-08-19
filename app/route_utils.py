from __future__ import annotations

from typing import Any


def remove_route(app: Any, path: str, method: str) -> None:
    """Remove every matching method/path registration from a FastAPI router."""
    expected = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        )
    ]
