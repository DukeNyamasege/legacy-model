from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

import app.api as base_api
from app.account_identity_ui import _compat_script as account_identity_compat_script
from app.account_identity_ui import _dashboard_script as account_identity_dashboard_script
from app.dashboard_smoke_compat import _html_with_smoke_marker
from app.dashboard_stability_fix import _remove_route

_INSTALLED = False
UI_VERSION = "20260802-2"

_LIVE_METRICS_JS = r'''

/* FOA_LIVE_METRICS_SYNC: update overview/trades KPIs without full route reload. */
(() => {
  "use strict";
  const VERSION = "20260802-2";
  let inFlight = false;
  let lastKey = "";

  const text = value => String(value ?? "").trim().replace(/\s+/g, " ");
  const money = (value, currency = "USD") => {
    const amount = Number(value || 0);
    const prefix = String(currency || "USD").toUpperCase() === "USD" ? "$" : `${currency} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };
  const pct = value => `${(Number(value || 0) * (Math.abs(Number(value || 0)) <= 1 ? 100 : 1)).toFixed(1)}%`;
  const num = value => Number(value || 0).toLocaleString();

  function labelMatches(value, wanted) {
    const normalized = text(value).toLowerCase().replace(/[’']/g, "'");
    return wanted.some(item => normalized === item || normalized.includes(item));
  }

  function setKpi(labels, value, caption) {
    document.querySelectorAll(".foa-kpi").forEach(card => {
      const label = text(card.querySelector("span")?.textContent || "");
      if (!labelMatches(label, labels)) return;
      const strong = card.querySelector("strong");
      const small = card.querySelector("small");
      if (strong && text(strong.textContent) !== text(value)) strong.textContent = value;
      if (small && caption !== undefined && text(small.textContent) !== text(caption)) small.textContent = caption;
    });
  }

  function setSimpleStat(containerSelector, labels, value) {
    document.querySelectorAll(`${containerSelector} div`).forEach(row => {
      const label = text(row.querySelector("span")?.textContent || "");
      if (!labelMatches(label, labels)) return;
      const strong = row.querySelector("strong");
      if (strong && text(strong.textContent) !== text(value)) strong.textContent = value;
    });
  }

  function setBalance(value) {
    document.querySelectorAll(".foa-balance").forEach(node => {
      if (text(node.textContent) !== text(value)) node.textContent = value;
    });
  }

  function setAccountPill(me) {
    const account = me.account_id_full || me.login_id || me.display_account_id || me.account_id || me.account_id_masked || "";
    if (!account) return;
    document.querySelectorAll(".foa-account-pill span").forEach(node => {
      if (text(node.textContent) !== account) node.textContent = account;
    });
  }

  function setBotStatus(life) {
    const lifecycle = String(life?.lifecycle || "").toLowerCase();
    if (!lifecycle) return;
    const status = lifecycle === "running" ? "Running" : lifecycle === "paused" ? "Paused" : "Stopped";
    const caption = lifecycle === "running" ? "Live and trading" : lifecycle === "paused" ? "State preserved" : "Ready to start";
    document.querySelectorAll(".foa-kpi").forEach(card => {
      const label = text(card.querySelector("span")?.textContent || "");
      if (!labelMatches(label, ["bot status"])) return;
      const strong = card.querySelector("strong");
      const small = card.querySelector("small");
      if (strong) strong.innerHTML = `${status}<span class="dot ${lifecycle}"></span>`;
      if (small) small.textContent = caption;
    });
  }

  function apply(payload) {
    const { me, today, life } = payload;
    if (!me || !me.authenticated) return;
    const currency = me.currency || today.currency || "USD";
    const balance = money(me.balance ?? today.balance ?? 0, currency);
    const summary = today.summary || {};
    const rows = Array.isArray(today.trades) ? today.trades : [];
    const wins = Number(summary.wins ?? rows.filter(row => String(row.outcome).toUpperCase() === "WIN").length);
    const losses = Number(summary.losses ?? rows.filter(row => String(row.outcome).toUpperCase() === "LOSS").length);
    const open = Number(summary.open ?? Math.max(0, rows.length - wins - losses));
    const total = Number(summary.total ?? rows.length);
    const profit = Number(summary.profit ?? rows.reduce((sum, row) => sum + Number(row.profit || 0), 0));
    const settled = wins + losses;
    const rate = settled ? wins / settled : 0;
    const avg = settled ? profit / settled : 0;

    setAccountPill(me);
    setBalance(balance);
    setBotStatus(life);

    setKpi(["balance"], balance, `${String(me.account_type || "demo").toLowerCase()} account balance`);
    setKpi(["today's profit", "today’s profit", "profit / loss"], money(profit, currency), `${settled} settled trades`);
    setKpi(["win rate"], pct(rate), `${wins} wins / ${losses} losses`);
    setKpi(["total"], num(total), "All trades today");
    setKpi(["open trades"], num(open), "Awaiting settlement");

    setSimpleStat(".foa-account-stats", ["today's trades", "today’s trades"], num(total));
    setSimpleStat(".foa-account-stats", ["wins"], num(wins));
    setSimpleStat(".foa-account-stats", ["losses"], num(losses));
    setSimpleStat(".foa-perf-stats", ["p/l"], money(profit, currency));
    setSimpleStat(".foa-perf-stats", ["open"], num(open));
    setSimpleStat(".foa-perf-stats", ["avg trade"], money(avg, currency));

    document.body.dataset.foaLiveMetricsVersion = VERSION;
  }

  async function sync() {
    if (inFlight) return;
    if (!document.querySelector("#foa-simple-app")) return;
    const active = document.activeElement;
    if (active && ["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName || "")) return;
    inFlight = true;
    try {
      const [meResponse, tradesResponse, lifeResponse] = await Promise.all([
        fetch(`/me?live_metrics=${Date.now()}`, { credentials: "same-origin", cache: "no-store" }),
        fetch(`/me/trades/today?live_metrics=${Date.now()}`, { credentials: "same-origin", cache: "no-store" }),
        fetch(`/me/trading-lifecycle?live_metrics=${Date.now()}`, { credentials: "same-origin", cache: "no-store" }),
      ]);
      if (!meResponse.ok || !tradesResponse.ok) return;
      const me = await meResponse.json();
      const today = await tradesResponse.json();
      const life = lifeResponse.ok ? await lifeResponse.json() : null;
      const key = JSON.stringify([
        me.account_id_full || me.login_id || me.display_account_id || me.account_id || me.account_id_masked,
        me.account_type,
        me.balance,
        today.summary,
        (today.trades || []).slice(0, 3).map(row => [row.id, row.outcome, row.profit, row.settlement_time]),
        life?.lifecycle,
      ]);
      if (key !== lastKey) {
        lastKey = key;
        apply({ me, today, life });
      }
    } catch (_err) {
    } finally {
      inFlight = false;
    }
  }

  document.addEventListener("DOMContentLoaded", () => { sync(); setInterval(sync, 2500); }, { once: true });
  document.addEventListener("click", event => {
    if (event.target.closest("[data-control], [data-view], [data-mode], .foa-save-button")) {
      setTimeout(sync, 500);
      setTimeout(sync, 1800);
    }
  }, true);
  if (document.readyState !== "loading") { sync(); setInterval(sync, 2500); }
  window.FOA_LIVE_METRICS_SYNC = VERSION;
})();
'''


def _html() -> str:
    html = _html_with_smoke_marker().replace("20260802-1", UI_VERSION)
    if "/ui/simplified-dashboard.js" not in html:
        html = html.replace('<script src="/ui/dashboard-v2.js', '<!-- compatibility marker: /ui/simplified-dashboard.js -->\n  <script src="/ui/dashboard-v2.js')
    return html


def _dashboard_script() -> str:
    source = account_identity_dashboard_script()
    if "FOA_LIVE_METRICS_SYNC" not in source:
        source += _LIVE_METRICS_JS
    return source.replace("20260802-1", UI_VERSION)


def _compat_script() -> str:
    source = account_identity_compat_script()
    if "FOA_LIVE_METRICS_SYNC" not in source:
        source += _LIVE_METRICS_JS
    return source.replace("20260802-1", UI_VERSION)


def install_live_metrics_ui(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/", "/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/", include_in_schema=False)
    def live_metrics_root(
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
    def dashboard_v2_with_live_metrics() -> Response:
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
    def simplified_dashboard_with_live_metrics() -> Response:
        return Response(
            _compat_script(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    app.state.live_metrics_ui_installed = True
    app.state.dashboard_ui_version = UI_VERSION
    _INSTALLED = True
