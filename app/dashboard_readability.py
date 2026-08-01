from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, Response

import app.api as base_api

_INSTALLED = False
UI_VERSION = "20260801-7"


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


def _standalone_dashboard_html() -> str:
    """Return the standalone responsive dashboard shell."""

    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="color-scheme" content="dark light">
  <meta name="theme-color" content="#071120">
  <meta name="robots" content="noindex,nofollow,noarchive,nosnippet">
  <title>Father of Automation</title>
  <link rel="stylesheet" href="/ui/dashboard-v2.css?v={UI_VERSION}">
  <style>
    html,body{{margin:0;min-height:100%;background:#071120;color:#f8fafc;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
    #foa-bootstrap{{min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 25% 0%,rgba(47,115,255,.18),transparent 32rem),linear-gradient(135deg,#030a14,#081322 48%,#0d1b2e)}}
    #foa-bootstrap>div{{text-align:center;padding:24px}}#foa-bootstrap strong{{display:block;font-size:20px;margin-bottom:8px}}#foa-bootstrap span{{color:#aab6c8;font-size:14px}}
    #foa-bootstrap i{{display:block;width:36px;height:36px;margin:0 auto 16px;border:3px solid rgba(255,255,255,.15);border-top-color:#2f73ff;border-radius:50%;animation:foa-spin .8s linear infinite}}
    @keyframes foa-spin{{to{{transform:rotate(360deg)}}}}
  </style>
</head>
<body>
  <div id="foa-bootstrap"><div><i aria-hidden="true"></i><strong>Father of Automation</strong><span>Opening dashboard…</span></div></div>
  <noscript>This dashboard requires JavaScript.</noscript>
  <!-- compatibility marker: /ui/simplified-dashboard.js -->
  <script src="/ui/dashboard-v2.js?v={UI_VERSION}" defer></script>
  <script src="/ui/dashboard-actions-v2.js?v={UI_VERSION}" defer></script>
  <script>
    window.setTimeout(function(){{
      var bootstrap=document.getElementById("foa-bootstrap");
      var app=document.getElementById("foa-simple-app");
      if(bootstrap&&app)bootstrap.remove();
      if(bootstrap&&!app)bootstrap.innerHTML='<div><strong>Dashboard could not start</strong><span>Refresh the page once. If the issue continues, check the browser console.</span></div>';
    }},8000);
  </script>
</body>
</html>"""


def _patched_dashboard_v2_javascript() -> str:
    """Serve the dashboard with final UX behavior without editing the bundled file.

    The standalone dashboard is still the source of truth. These patches make
    two user-facing rules strict:
    1. The onboarding wizard is only visible before login.
    2. Background refreshes update silently without a blocking loader or scroll
       interruption. Manual navigation/actions still show loaders.
    """

    source = (base_api.ROOT / "dashboard" / "dashboard-v2.js").read_text(
        encoding="utf-8"
    )
    source = source.replace(
        'const VERSION = "20260801-5";',
        f'const VERSION = "{UI_VERSION}";',
    ).replace(
        'const VERSION = "20260801-6";',
        f'const VERSION = "{UI_VERSION}";',
    )

    source = source.replace(
        '  function wizard({ compact = false } = {}) {\n'
        '    const steps = wizardSteps();',
        '  function wizard({ compact = false } = {}) {\n'
        '    if (authenticated()) return "";\n'
        '    S.wizardOpen = true;\n'
        '    const steps = wizardSteps();',
    )

    old_refresh = '''  async function refresh(force = false, loadingText = "Refreshing dashboard…") {
    if (S.busy && !force) return;
    S.busy = true;
    S.loaderText = loadingText;
    render(false);
    try {
      S.me = await getJSON("/me");
      if (authenticated()) {
        S.mode = S.me.account_type || S.mode;
        localStorage.setItem(K.mode, S.mode);
      }
      S.summary = await getJSON(`/metrics/summary?mode=${encodeURIComponent(S.mode)}`);
      if (authenticated()) {
        const [life, today] = await Promise.all([
          getJSON("/me/trading-lifecycle"),
          getJSON("/me/trades/today"),
        ]);
        S.life = life;
        S.trades = Array.isArray(today.trades) ? today.trades : [];
        S.tradeSummary = { ...(today.summary || {}), date: today.date };
      } else {
        S.life = null;
        S.trades = [];
        S.tradeSummary = {};
      }
      S.error = "";
    } catch (error) {
      S.error = `Dashboard refresh failed: ${String(error?.message || error)}`;
    } finally {
      S.busy = false;
      S.booting = false;
      S.loaderText = "";
      render();
    }
  }
'''

    new_refresh = '''  async function refresh(force = false, loadingText = "Refreshing dashboard…") {
    if (S.busy && !force) return;
    const silent = !force && String(loadingText || "").toLowerCase().includes("refreshing");
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const activeElement = document.activeElement;
    const activeTag = String(activeElement?.tagName || "").toLowerCase();
    const editing = ["input", "select", "textarea"].includes(activeTag);

    S.busy = !silent;
    if (!silent) {
      S.loaderText = loadingText;
      render(false);
    }
    try {
      S.me = await getJSON("/me");
      if (authenticated()) {
        S.mode = S.me.account_type || S.mode;
        localStorage.setItem(K.mode, S.mode);
      }
      S.summary = await getJSON(`/metrics/summary?mode=${encodeURIComponent(S.mode)}`);
      if (authenticated()) {
        const [life, today] = await Promise.all([
          getJSON("/me/trading-lifecycle"),
          getJSON("/me/trades/today"),
        ]);
        S.life = life;
        S.trades = Array.isArray(today.trades) ? today.trades : [];
        S.tradeSummary = { ...(today.summary || {}), date: today.date };
      } else {
        S.life = null;
        S.trades = [];
        S.tradeSummary = {};
      }
      S.error = "";
    } catch (error) {
      if (!silent) S.error = `Dashboard refresh failed: ${String(error?.message || error)}`;
    } finally {
      S.busy = false;
      S.booting = false;
      S.loaderText = "";
      if (!silent && !editing) {
        render();
      } else if (!editing) {
        render();
        requestAnimationFrame(() => window.scrollTo(scrollX, scrollY));
      }
    }
  }
'''

    if old_refresh in source:
        source = source.replace(old_refresh, new_refresh)
    else:
        source = (
            'console.warn("FOA dashboard refresh patch did not find the expected block");\n'
            + source
        )

    source += f'''

/* Final UX guard {UI_VERSION}: logged-in users never see wizard cards, and
   accidental refresh loaders are hidden if a browser keeps an older script. */
(() => {{
  const style = document.createElement("style");
  style.id = "foa-final-ux-guard";
  style.textContent = `
    body.foa-authenticated .foa-wizard-card{{display:none!important}}
    .foa-route-loader.foa-silent-refresh{{display:none!important;pointer-events:none!important;backdrop-filter:none!important;background:transparent!important}}
  `;
  document.head.appendChild(style);
  function mark() {{
    const loggedIn = !!document.querySelector("#logout,.foa-logout,.foa-account-pill");
    document.body.classList.toggle("foa-authenticated", loggedIn);
    if (loggedIn) document.querySelectorAll(".foa-wizard-card").forEach(el => el.remove());
    document.querySelectorAll(".foa-route-loader").forEach(el => {{
      const text = (el.textContent || "").toLowerCase();
      if (text.includes("refreshing dashboard")) el.classList.add("foa-silent-refresh");
    }});
  }}
  new MutationObserver(mark).observe(document.documentElement, {{ childList: true, subtree: true }});
  document.addEventListener("DOMContentLoaded", mark, {{ once: true }});
  window.setInterval(mark, 750);
}})();
'''
    return source


def install_dashboard_readability(app: Any) -> None:
    """Serve only the enhanced standalone desktop/mobile dashboard at root."""

    global _INSTALLED
    if _INSTALLED:
        return

    for path in (
        "/",
        "/ui/dashboard-v2.css",
        "/ui/dashboard-v2.js",
        "/ui/dashboard-actions-v2.js",
        "/ui/readability-boost.js",
        "/ui/simplified-dashboard.js",
    ):
        _remove_route(app, path, "GET")

    @app.get("/", include_in_schema=False)
    def enhanced_dashboard_root(
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
            _standalone_dashboard_html(),
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    @app.get("/ui/dashboard-v2.css", include_in_schema=False)
    def enhanced_dashboard_styles() -> FileResponse:
        return FileResponse(
            base_api.ROOT / "dashboard" / "dashboard-v2.css",
            media_type="text/css",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def enhanced_dashboard_javascript() -> Response:
        return Response(
            _patched_dashboard_v2_javascript(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    @app.get("/ui/dashboard-actions-v2.js", include_in_schema=False)
    def enhanced_dashboard_actions() -> FileResponse:
        return FileResponse(
            base_api.ROOT / "dashboard" / "dashboard-actions-v2.js",
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    @app.get("/ui/readability-boost.js", include_in_schema=False)
    def readability_boost_compatibility() -> FileResponse:
        return FileResponse(
            base_api.ROOT / "dashboard" / "readability-boost.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def simplified_dashboard_compatibility() -> Response:
        compatibility = (
            "/* deployment compatibility: /metrics/recent-trades; "
            "the enhanced UI uses /me/trades/today */\n"
        )
        return Response(
            compatibility + _patched_dashboard_v2_javascript(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    app.state.dashboard_readability_installed = True
    app.state.simplified_dashboard_standalone = True
    app.state.dashboard_ui_version = UI_VERSION
    _INSTALLED = True
