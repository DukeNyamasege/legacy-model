from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

import app.api as base_api

_INSTALLED = False
UI_VERSION = "20260802-1"


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


def _html() -> str:
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
    #foa-bootstrap i{{display:block;width:34px;height:34px;margin:0 auto 16px;border:3px solid rgba(255,255,255,.15);border-top-color:#2f73ff;border-radius:50%;animation:foa-spin .8s linear infinite}}
    @keyframes foa-spin{{to{{transform:rotate(360deg)}}}}
  </style>
</head>
<body>
  <div id="foa-bootstrap"><div><i aria-hidden="true"></i><strong>Father of Automation</strong><span>Opening dashboard…</span></div></div>
  <noscript>This dashboard requires JavaScript.</noscript>
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


def _patched_dashboard_js() -> str:
    source = (base_api.ROOT / "dashboard" / "dashboard-v2.js").read_text(encoding="utf-8")
    for old in ("20260801-5", "20260801-6", "20260801-7", "20260801-8"):
        source = source.replace(f'const VERSION = "{old}";', f'const VERSION = "{UI_VERSION}";')

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
      if (!silent && !editing) render();
    }
  }
'''
    if old_refresh in source:
        source = source.replace(old_refresh, new_refresh)

    source += f'''

/* FOA_FINAL_STABLE_REFRESH {UI_VERSION}: logged-in users never see the wizard;
   background refreshes never rebuild or cover the route. */
(() => {{
  const style = document.createElement("style");
  style.id = "foa-final-mobile-polish";
  style.textContent = `
    body.foa-authenticated .foa-wizard-card{{display:none!important}}
    .foa-route-loader.foa-silent-refresh{{display:none!important;pointer-events:none!important}}
    @media(max-width:760px){{
      #foa-simple-app{{font-size:13px!important}}
      .foa-main{{padding:18px 12px 88px!important}}
      .foa-card{{border-radius:15px!important;padding:18px!important}}
      .foa-card h2{{font-size:20px!important}}
      .foa-kpi strong{{font-size:22px!important}}
      .foa-balance{{font-size:34px!important}}
      .foa-bottom-nav button{{font-size:12px!important;font-weight:750!important;gap:3px!important}}
      .foa-bottom-nav button span{{font-size:17px!important}}
    }}
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
  window.setInterval(mark, 1000);
}})();
'''
    return source


def _stable_actions_js() -> str:
    return f'''
(() => {{
  "use strict";
  const VERSION = "{UI_VERSION}";
  let lastPayloadKey = "";
  let inFlight = false;

  function css() {{
    if (document.getElementById("foa-stable-actions-css")) return;
    const style = document.createElement("style");
    style.id = "foa-stable-actions-css";
    style.textContent = `
      .foa-trades-card,.foa-all-trades{{overflow:hidden}}
      .foa-stable-table{{margin-top:12px;min-height:360px}}
      .foa-stable-head,.foa-stable-row{{display:grid;grid-template-columns:1fr 1.55fr .8fr 1.05fr;gap:12px;align-items:center;border-bottom:1px solid var(--line,rgba(148,163,184,.18));padding:12px 0}}
      .foa-stable-head{{color:var(--muted,#aab6c8);font-weight:750;font-size:13px}}
      .foa-stable-row{{font-size:14px}}
      .foa-stable-trade b{{font-weight:850}}.foa-stable-trade em{{font-style:normal;font-size:10px;padding:4px 7px;border-radius:7px;color:#83adff;background:rgba(47,115,255,.16);white-space:nowrap;margin-left:5px}}
      .foa-stable-result{{font-weight:900;text-align:right;white-space:nowrap}}.foa-stable-result.win{{color:var(--green,#41d75d)}}.foa-stable-result.loss{{color:var(--red,#ef4444)}}
      .foa-exit-mini{{display:inline-flex;align-items:center;gap:5px;margin-left:6px;color:var(--muted,#aab6c8);font-size:11px;font-weight:700}}.foa-exit-mini i{{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:999px;background:rgba(47,115,255,.16);color:#83adff;font-style:normal;font-size:12px;font-weight:900}}
      .foa-stable-empty{{padding:24px 0;color:var(--muted,#aab6c8)}}
      .foa-reset-actions{{display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap}}.foa-reset-actions button{{border:1px solid var(--line,rgba(148,163,184,.22));border-radius:9px;padding:8px 10px;background:rgba(255,255,255,.04);color:var(--text,#f8fafc);font-weight:750;cursor:pointer;font-size:12px}}.foa-reset-actions button.danger{{background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.35);color:#ff9a9a}}
      @media(max-width:760px){{
        .foa-stable-table{{min-height:410px}}
        .foa-stable-head,.foa-stable-row{{grid-template-columns:.9fr 1.5fr .75fr 1.05fr;gap:8px;padding:13px 0}}
        .foa-stable-head{{font-size:12px}}.foa-stable-row{{font-size:13px}}.foa-stable-trade em{{font-size:9px;padding:3px 6px;margin-left:3px}}.foa-stable-result{{font-size:13px}}.foa-exit-mini{{display:flex;margin-left:0;margin-top:4px;font-size:10px}}.foa-exit-mini i{{width:19px;height:19px;font-size:10px}}
      }}
    `;
    document.head.appendChild(style);
  }}

  const money = value => {{
    const amount = Number(value || 0);
    return `${{amount < 0 ? "-" : ""}}${{Math.abs(amount).toLocaleString(undefined, {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }})}}`;
  }};
  const timeOf = row => {{
    const raw = row.purchase_time || row.provider_purchase_time || row.settlement_time || row.provider_settlement_time;
    const date = raw ? new Date(raw) : null;
    return date && Number.isFinite(date.getTime()) ? date.toLocaleTimeString([], {{ hour: "2-digit", minute: "2-digit", second: "2-digit" }}) : "—";
  }};
  const exitDigit = row => {{
    if (row.exit_digit !== null && row.exit_digit !== undefined && row.exit_digit !== "") return String(row.exit_digit);
    const spot = row.exit_spot ?? row.exit_tick;
    const text = spot === null || spot === undefined ? "" : String(spot).replace(/[^0-9]/g, "");
    return text ? text[text.length - 1] : "—";
  }};
  const resultHtml = row => {{
    const outcome = String(row.outcome || "OPEN").toUpperCase();
    const cls = outcome === "WIN" ? "win" : outcome === "LOSS" ? "loss" : "neutral";
    const profit = Number(row.profit || 0);
    return `<span class="foa-stable-result ${{cls}}">${{outcome}}${{outcome === "OPEN" ? "" : ` · ${{profit < 0 ? "-" : ""}}$${{money(profit)}}`}}<small class="foa-exit-mini">Exit <i>${{exitDigit(row)}}</i></small></span>`;
  }};
  const rowHtml = row => `<div class="foa-stable-row"><span>${{timeOf(row)}}</span><span class="foa-stable-trade"><b>${{row.symbol || row.market || "—"}}</b><em>${{String(row.contract_type || row.type || "TRADE").toUpperCase()}}</em></span><span>$${{money(row.buy_price ?? row.stake ?? row.amount ?? 0)}}</span>${{resultHtml(row)}}</div>`;

  async function getTrades() {{
    if (inFlight || !document.querySelector(".foa-trades-card,.foa-all-trades")) return;
    inFlight = true;
    try {{
      const response = await fetch(`/me/trades/today?stable=${{Date.now()}}`, {{ credentials: "same-origin", cache: "no-store" }});
      if (!response.ok) return;
      const payload = await response.json();
      const rows = Array.isArray(payload.trades) ? payload.trades : [];
      const key = JSON.stringify(rows.slice(0, 30).map(row => [row.id, row.trade_id, row.outcome, row.profit, row.exit_digit, row.exit_spot, row.purchase_time]));
      if (key === lastPayloadKey) return;
      lastPayloadKey = key;
      render(rows);
    }} catch (_err) {{
    }} finally {{
      inFlight = false;
    }}
  }}

  function resetButtons() {{
    return `<div class="foa-reset-actions" data-stable-reset><button type="button" data-clear-scope="today">Clear Today</button><button type="button" class="danger" data-clear-scope="all">Clear All</button></div>`;
  }}

  function render(rows) {{
    css();
    document.querySelectorAll(".foa-trades-card,.foa-all-trades").forEach(card => {{
      const head = card.querySelector(".foa-card-head");
      if (head && !head.querySelector("[data-stable-reset]")) head.insertAdjacentHTML("beforeend", resetButtons());
      card.querySelectorAll(".foa-trade-head,.foa-trade-row,.foa-trade-head-wide,.foa-trade-row-wide,.foa-final-trade-table,.foa-stable-table").forEach(el => el.remove());
      const limit = card.classList.contains("foa-all-trades") ? 5000 : 6;
      const visible = rows.slice(0, limit);
      card.insertAdjacentHTML("beforeend", `<div class="foa-stable-table"><div class="foa-stable-head"><span>Time</span><span>Trade</span><span>Stake</span><span>Result</span></div>${{visible.length ? visible.map(rowHtml).join("") : `<div class="foa-stable-empty">No trades have been taken on this account today.</div>`}}</div>`);
    }});
  }}

  async function clearTrades(scope) {{
    const label = scope === "all" ? "all personal trades for this selected account" : "today's personal trades for this selected account";
    if (!confirm(`Clear ${{label}}? This resets this account's recovery state, not login or credentials.`)) return;
    const response = await fetch("/me/clear-trades", {{ method: "POST", credentials: "same-origin", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify({{ scope }}) }});
    const data = await response.json().catch(() => ({{}}));
    if (!response.ok) return alert(data.detail || data.message || "Could not clear trades.");
    lastPayloadKey = "";
    await getTrades();
  }}

  document.addEventListener("click", event => {{
    const clear = event.target.closest("[data-clear-scope]");
    if (clear) {{ event.preventDefault(); clearTrades(clear.dataset.clearScope || "today"); }}
    if (event.target.closest("[data-view],[data-mode],[data-control],#logout")) setTimeout(getTrades, 700);
  }}, true);

  document.addEventListener("DOMContentLoaded", () => {{ css(); setTimeout(getTrades, 500); setInterval(getTrades, 8000); }}, {{ once: true }});
  if (document.readyState !== "loading") {{ css(); setTimeout(getTrades, 500); setInterval(getTrades, 8000); }}
}})();
'''


def install_dashboard_stability_fix(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/", "/ui/dashboard-v2.js", "/ui/dashboard-actions-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/", include_in_schema=False)
    def stable_dashboard_root(
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
    def stable_dashboard_js() -> Response:
        return Response(
            _patched_dashboard_js(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    @app.get("/ui/dashboard-actions-v2.js", include_in_schema=False)
    def stable_dashboard_actions_js() -> Response:
        return Response(
            _stable_actions_js(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def stable_simplified_compat() -> Response:
        return Response(
            "/* compatibility: stable dashboard v2 */\n" + _patched_dashboard_js(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    app.state.dashboard_stability_fix_installed = True
    app.state.dashboard_ui_version = UI_VERSION
    _INSTALLED = True
