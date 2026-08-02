from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

import app.api as base_api
from app.dashboard_stability_fix import _remove_route
from app.live_metrics_ui import _compat_script as live_metrics_compat_script
from app.live_metrics_ui import _dashboard_script as live_metrics_dashboard_script
from app.live_metrics_ui import _html as live_metrics_html

_INSTALLED = False
UI_VERSION = "20260802-7"

_PUBLIC_TRADER_STATS_JS = r'''

/* FOA_PUBLIC_TRADER_STATS: public totals from the dedicated stable endpoint. */
(() => {
  "use strict";
  const VERSION = "20260802-7";
  let inFlight = false;
  let lastKey = "";
  let started = false;

  const num = value => Number(value || 0).toLocaleString();
  const text = value => String(value ?? "").trim().replace(/\s+/g, " ");

  function ensureStyle() {
    if (document.getElementById("foa-public-trader-stats-css")) return;
    const style = document.createElement("style");
    style.id = "foa-public-trader-stats-css";
    style.textContent = `
      .foa-public-trader-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:0 0 16px 0}
      .foa-public-trader-stat{border:1px solid var(--line,rgba(148,163,184,.18));border-radius:16px;padding:14px 16px;background:linear-gradient(135deg,rgba(47,115,255,.12),rgba(255,255,255,.035));box-shadow:0 12px 32px rgba(0,0,0,.16)}
      .foa-public-trader-stat span{display:block;color:var(--muted,#64748b);font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px}
      .foa-public-trader-stat strong{display:block;color:var(--text,#0f172a);font-size:24px;line-height:1;font-weight:950;letter-spacing:-.03em}
      .foa-public-trader-stat small{display:block;color:var(--muted,#64748b);font-size:11px;margin-top:6px}
      [data-theme="dark"] .foa-public-trader-stat strong{color:#f8fafc}
      @media(max-width:760px){.foa-public-trader-stats{gap:8px;margin-bottom:12px}.foa-public-trader-stat{padding:11px 12px;border-radius:14px}.foa-public-trader-stat span{font-size:9px}.foa-public-trader-stat strong{font-size:20px}.foa-public-trader-stat small{font-size:10px}}
    `;
    document.head.appendChild(style);
  }

  function ensureBlock() {
    ensureStyle();
    const main = document.querySelector(".foa-main");
    if (!main) return null;
    let block = document.getElementById("foa-public-trader-stats");
    if (!block) {
      block = document.createElement("section");
      block.id = "foa-public-trader-stats";
      block.className = "foa-public-trader-stats";
      block.innerHTML = `
        <article class="foa-public-trader-stat"><span>Total Registered Traders</span><strong data-public-registered>0</strong><small>Unique linked traders</small></article>
        <article class="foa-public-trader-stat"><span>Trading Now</span><strong data-public-active>0</strong><small>Currently enabled</small></article>
      `;
    }
    const topbar = main.querySelector(".foa-topbar");
    if (topbar && block.previousElementSibling !== topbar) topbar.insertAdjacentElement("afterend", block);
    else if (!topbar && block.parentElement !== main) main.prepend(block);
    return block;
  }

  function apply(summary) {
    const block = ensureBlock();
    if (!block) return;
    const registered = num(summary.registered_traders ?? summary.total_registered_traders ?? 0);
    const active = num(summary.trading_now ?? summary.active_traders ?? 0);
    const registeredNode = block.querySelector("[data-public-registered]");
    const activeNode = block.querySelector("[data-public-active]");
    if (registeredNode && text(registeredNode.textContent) !== registered) registeredNode.textContent = registered;
    if (activeNode && text(activeNode.textContent) !== active) activeNode.textContent = active;
    document.body.dataset.foaPublicTraderStatsVersion = VERSION;
  }

  async function sync() {
    if (inFlight || !document.querySelector("#foa-simple-app")) return;
    inFlight = true;
    try {
      const response = await fetch(`/metrics/public-traders?t=${Date.now()}`, {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) return;
      const summary = await response.json();
      const key = JSON.stringify([
        summary.registered_traders,
        summary.trading_now,
        summary.linked_accounts,
        summary.enabled_accounts,
      ]);
      ensureBlock();
      if (key !== lastKey) {
        lastKey = key;
        apply(summary);
      }
    } catch (_err) {
      ensureBlock();
    } finally {
      inFlight = false;
    }
  }

  function start() {
    if (started) return;
    started = true;
    sync();
    window.setInterval(sync, 3000);
  }

  document.addEventListener("DOMContentLoaded", start, { once: true });
  document.addEventListener("click", event => {
    if (event.target.closest("[data-view], [data-mode], [data-control], .foa-save-button")) {
      window.setTimeout(sync, 250);
      window.setTimeout(sync, 1200);
    }
  }, true);
  if (document.readyState !== "loading") start();
  window.FOA_PUBLIC_TRADER_STATS = VERSION;
})();
'''


def _versioned(source: str) -> str:
    return (
        source.replace("20260802-6", UI_VERSION)
        .replace("20260802-5", UI_VERSION)
        .replace("20260802-4", UI_VERSION)
        .replace("20260802-3", UI_VERSION)
        .replace("20260802-2", UI_VERSION)
        .replace("20260802-1", UI_VERSION)
    )


def _html() -> str:
    return _versioned(live_metrics_html())


def _dashboard_script() -> str:
    source = _versioned(live_metrics_dashboard_script())
    if "FOA_PUBLIC_TRADER_STATS" not in source:
        source += _PUBLIC_TRADER_STATS_JS
    return source


def _compat_script() -> str:
    source = _versioned(live_metrics_compat_script())
    if "FOA_PUBLIC_TRADER_STATS" not in source:
        source += _PUBLIC_TRADER_STATS_JS
    return source


def install_public_trader_stats_ui(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/", "/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/", include_in_schema=False)
    def public_trader_stats_root(
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
    def dashboard_v2_with_public_trader_stats() -> Response:
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
    def simplified_dashboard_with_public_trader_stats() -> Response:
        return Response(
            _compat_script(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    app.state.public_trader_stats_ui_installed = True
    _INSTALLED = True
