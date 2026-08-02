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
UI_VERSION = "20260802-6"

_LOADER_UNLOCK_JS = r'''

/* FOA_DASHBOARD_LOADER_UNLOCK: one-shot release with no mutation feedback loop. */
(() => {
  "use strict";
  const VERSION = "20260802-6";
  let observer = null;
  let released = false;

  function readyContent() {
    const app = document.getElementById("foa-simple-app");
    if (!app) return false;
    return !!app.querySelector(".foa-shell,.foa-main,.foa-card,.foa-topbar,.foa-bottom-nav");
  }

  function releaseOpeningLoader(reason = "content-ready") {
    if (released || !readyContent()) return false;
    released = true;

    // Disconnect before changing the DOM so our own cleanup cannot trigger us.
    if (observer) {
      observer.disconnect();
      observer = null;
    }

    const bootstrap = document.getElementById("foa-bootstrap");
    if (bootstrap) bootstrap.remove();

    document.querySelectorAll(".foa-route-loader").forEach(loader => {
      const message = String(loader.textContent || "").toLowerCase();
      if (message.includes("opening dashboard") || loader.classList.contains("show")) {
        loader.remove();
      }
    });

    document.body.dataset.foaDashboardLoaderUnlock = VERSION;
    document.body.dataset.foaDashboardLoaderUnlockReason = reason;
    document.body.classList.remove("foa-dashboard-ready");
    return true;
  }

  function attempt(reason) {
    if (released) return;
    releaseOpeningLoader(reason);
  }

  function boot() {
    attempt("boot");
    if (released) return;

    // Watch only for new child nodes while the dashboard is initially rendering.
    // Do not watch class/style attributes: changing those from this callback caused
    // the previous infinite MutationObserver feedback loop and Chrome freeze.
    observer = new MutationObserver(() => attempt("content-added"));
    observer.observe(document.documentElement, { childList: true, subtree: true });

    [100, 250, 500, 1000, 1800, 3000, 5000, 8000].forEach(delay => {
      window.setTimeout(() => attempt(`timer-${delay}`), delay);
    });

    // Safety stop: never leave a permanent observer running on the dashboard.
    window.setTimeout(() => {
      if (observer) {
        observer.disconnect();
        observer = null;
      }
      attempt("final-timeout");
    }, 12000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }

  window.addEventListener("pageshow", () => attempt("pageshow"), { once: true });
  window.FOA_DASHBOARD_LOADER_UNLOCK = VERSION;
  window.FOA_DASHBOARD_LOADER_NO_MUTATION_LOOP = true;
})();
'''


def _versioned(source: str) -> str:
    return (
        source.replace("20260802-5", UI_VERSION)
        .replace("20260802-4", UI_VERSION)
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
    """Install a one-shot dashboard loader guard without DOM observer loops."""

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
