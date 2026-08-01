from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, Response

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
        '<script src="/ui/simplified-dashboard.js?v=20260801-2"></script>',
    )
    missing = [f"  {script}" for script in scripts if script not in html]
    if missing:
        html = html.replace("</body>", "\n".join(missing) + "\n</body>")
    return html


def _safe_simplified_dashboard_javascript() -> str:
    """Return browser-parseable simplified UI JavaScript.

    The initial simplified dashboard contained a JavaScript grammar error by
    mixing ``??`` and ``||`` without parentheses. Browsers rejected the entire
    external script before its boot function ran, leaving the legacy blocking
    ``Synchronizing`` loader visible forever. Keep the source file intact for a
    small release diff, but repair the exact expression at the serving boundary
    and make the new UI remove the legacy loader as soon as boot begins.
    """

    path = base_api.ROOT / "dashboard" / "simplified-dashboard.js"
    source = path.read_text(encoding="utf-8")

    broken_expression = (
        'const safe = (value, fallback = "—") => '
        'String(value ?? fallback || fallback);'
    )
    fixed_expression = (
        'const safe = (value, fallback = "—") => '
        'String(value ?? fallback);'
    )
    source = source.replace(broken_expression, fixed_expression)

    boot_marker = (
        '  function boot() {\n'
        '    if (document.getElementById("foa-simple-app")) return;\n'
    )
    boot_replacement = (
        '  function boot() {\n'
        '    const legacyLoader = document.getElementById("smart-loader");\n'
        '    if (legacyLoader) {\n'
        '      legacyLoader.classList.remove("active", "blocking");\n'
        '      legacyLoader.hidden = true;\n'
        '      legacyLoader.setAttribute("aria-hidden", "true");\n'
        '      legacyLoader.remove();\n'
        '    }\n'
        '    document.documentElement.classList.add("foa-simplified-ready");\n'
        '    if (document.getElementById("foa-simple-app")) return;\n'
    )
    source = source.replace(boot_marker, boot_replacement)

    # A runtime exception after parsing should never restore or preserve the old
    # blocking screen. The simplified application already renders API failures in
    # its own visible error card.
    source = (
        'window.__FOA_SIMPLIFIED_UI_VERSION__ = "20260801-2";\n'
        + source
        + '\nwindow.setTimeout(() => {\n'
        + '  const loader = document.getElementById("smart-loader");\n'
        + '  if (loader) loader.remove();\n'
        + '}, 1500);\n'
    )
    return source


def install_dashboard_readability(app: Any) -> None:
    """Serve the dashboard with the final simplified responsive UI installed last."""

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/", "GET")
    _remove_route(app, "/ui/readability-boost.js", "GET")
    _remove_route(app, "/ui/simplified-dashboard.js", "GET")

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

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def simplified_dashboard_script() -> Response:
        return Response(
            _safe_simplified_dashboard_javascript(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": "20260801-2",
            },
        )

    app.state.dashboard_readability_installed = True
    _INSTALLED = True
