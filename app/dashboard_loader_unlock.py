from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

import app.api as base_api
from app.dashboard_stability_fix import _remove_route
from app.reset_trades_always_ui import _compat_script as reset_compat_script
from app.reset_trades_always_ui import _dashboard_script as reset_dashboard_script
from app.reset_trades_always_ui import _html as reset_html

_INSTALLED = False
UI_VERSION = "20260802-5"

_LOADER_UNLOCK_JS = r'''

/* FOA_DASHBOARD_LOADER_UNLOCK: prevent the opening overlay from blocking rendered content. */
(() => {
  "use strict";
  const VERSION = "20260802-5";

  function readyContent() {
    const app = document.getElementById("foa-simple-app");
    if (!app) return false;
    return !!app.querySelector(".foa-shell,.foa-main,.foa-card,.foa-topbar,.foa-bottom-nav");
  }

  function ensureStyle() {
    if (document.getElementById("foa-loader-unlock-css")) return;
    const style = document.createElement("style");
    style.id = "foa-loader-unlock-css";
    style.textContent = `
      body.foa-dashboard-ready #foa-bootstrap{display:none!important;opacity:0!important;pointer-events:none!important}
      body.foa-dashboard-ready .foa-route-loader.foa-loader-unlocked{display:none!important;opacity:0!important;visibility:hidden!important;pointer-events:none!important;backdrop-filter:none!important}
      body.foa-dashboard-ready .foa-route-loader.foa-loader-unlocked.show{display:none!important;opacity:0!important;visibility:hidden!important;pointer-events:none!important;backdrop-filter:none!important}
    `;
    document.head.appendChild(style);
  }

  function unlock(reason = "content-ready") {
    ensureStyle();
    if (!readyContent()) return false;
    document.body.classList.add("foa-dashboard-ready");
    document.body.dataset.foaDashboardLoaderUnlock = VERSION;
    document.body.dataset.foaDashboardLoaderUnlockReason = reason;

    const bootstrap = document.getElementById("foa-bootstrap");
    if (bootstrap) bootstrap.remove();

    document.querySelectorAll(".foa-route-loader").forEach(loader => {
      loader.classList.remove("show");
      loader.classList.add("foa-loader-unlocked");
      loader.setAttribute("aria-hidden", "true");
      loader.style.setProperty("display", "none", "important");
      loader.style.setProperty("opacity", "0", "important");
      loader.style.setProperty("visibility", "hidden", "important");
      loader.style.setProperty("pointer-events", "none", "important");
      loader.style.setProperty("backdrop-filter", "none", "important");
    });
    return true;
  }

  function sweep() {
    ensureStyle();
    if (readyContent()) unlock("sweep");
  }

  function boot() {
    ensureStyle();
    sweep();
    [250, 600, 1000, 1800, 3000, 5000, 8000].forEach(delay => {
      setTimeout(sweep, delay);
    });
    setInterval(sweep, 1200);
    new MutationObserver(sweep).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "style"] });
    window.addEventListener("pageshow", sweep);
    window.addEventListener("focus", sweep);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
  window.FOA_DASHBOARD_LOADER_UNLOCK = VERSION;
})();
'''


def _versioned(source: str) -> str:
    return (
        source.replace("20260802-4", UI_VERSION)
        .replace("20260802-3", UI_VERSION)
        .replace("20260802-2", UI_VERSION)
        .replace("20260802-1", UI_VERSION)
    )


def _html() -> str:
    return _versioned(reset_html())


def _dashboard_script() -> str:
    source = _versioned(reset_dashboard_script())
    if "FOA_DASHBOARD_LOADER_UNLOCK" not in source:
        source += _LOADER_UNLOCK_JS
    return source


def _compat_script() -> str:
    source = _versioned(reset_compat_script())
    if "FOA_DASHBOARD_LOADER_UNLOCK" not in source:
        source += _LOADER_UNLOCK_JS
    return source


def install_dashboard_loader_unlock(app: Any) -> None:
    """Install a final dashboard loader guard.

    When the dashboard content exists, the opening route-loader must never stay
    above the page forever. This does not hide a real blank-page failure; it only
    removes the overlay after rendered content is already present.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/", "/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/", include_in_schema=False)
    def dashboard_loader_unlock_root(
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
            _html(),
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def dashboard_v2_with_loader_unlock() -> Response:
        return Response(
            _dashboard_script(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def simplified_dashboard_with_loader_unlock() -> Response:
        return Response(
            _compat_script(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    app.state.dashboard_loader_unlock_installed = True
    _INSTALLED = True
