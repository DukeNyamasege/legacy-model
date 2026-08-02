from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

import app.api as base_api
from app.dashboard_stability_fix import _remove_route
from app.public_trader_stats_ui import _compat_script as public_stats_compat_script
from app.public_trader_stats_ui import _dashboard_script as public_stats_dashboard_script
from app.public_trader_stats_ui import _html as public_stats_html

_INSTALLED = False
UI_VERSION = "20260802-4"

_RESET_TRADES_ALWAYS_JS = r'''

/* FOA_RESET_TRADES_ALWAYS: stable personal reset controls with debounced DOM sync. */
(() => {
  "use strict";
  const VERSION = "20260802-4";
  let authKnown = false;
  let authenticated = false;
  let authInFlight = false;
  let busy = false;
  let initialized = false;
  let syncQueued = false;

  function ensureStyle() {
    if (document.getElementById("foa-reset-trades-always-css")) return;
    const style = document.createElement("style");
    style.id = "foa-reset-trades-always-css";
    style.textContent = `
      .foa-reset-trades-always{display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;margin-left:auto}
      .foa-reset-trades-always button{border:1px solid var(--line,rgba(148,163,184,.22));border-radius:10px;padding:8px 11px;background:rgba(255,255,255,.045);color:var(--text,#f8fafc);font-size:12px;font-weight:850;cursor:pointer;white-space:nowrap}
      .foa-reset-trades-always button:hover{background:rgba(47,115,255,.13);border-color:rgba(47,115,255,.38)}
      .foa-reset-trades-always button.danger{background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.38);color:#ffb4b4}
      .foa-reset-trades-always button:disabled{opacity:.55;cursor:not-allowed}
      .foa-reset-status{width:100%;font-size:11px;color:var(--muted,#aab6c8);text-align:right;margin-top:5px}
      @media(max-width:760px){.foa-card-head{gap:10px}.foa-reset-trades-always{width:100%;justify-content:flex-start;margin-left:0}.foa-reset-trades-always button{font-size:11px;padding:7px 9px;border-radius:9px}.foa-reset-status{text-align:left}}
    `;
    document.head.appendChild(style);
  }

  function setText(node, value) {
    if (!node) return;
    const next = String(value ?? "");
    if (node.textContent !== next) node.textContent = next;
  }

  function scheduleSync() {
    if (syncQueued) return;
    syncQueued = true;
    window.requestAnimationFrame(() => {
      syncQueued = false;
      sync();
    });
  }

  async function refreshAuth() {
    if (authInFlight) return;
    authInFlight = true;
    try {
      const response = await fetch(`/me?reset_controls=${Date.now()}`, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      authenticated = !!payload.authenticated;
      authKnown = true;
      scheduleSync();
    } catch (_) {
    } finally {
      authInFlight = false;
    }
  }

  function cardTargets() {
    return Array.from(document.querySelectorAll(".foa-trades-card,.foa-all-trades"));
  }

  function statusLine(node, message, error = false) {
    let line = node.querySelector(".foa-reset-status");
    if (!line) {
      line = document.createElement("div");
      line.className = "foa-reset-status";
      node.appendChild(line);
    }
    const color = error ? "#ffb4b4" : "var(--muted,#aab6c8)";
    if (line.style.color !== color) line.style.color = color;
    setText(line, message || "");
    if (message) {
      setTimeout(() => {
        if (line.textContent === message) setText(line, "");
      }, 4500);
    }
  }

  async function clearTrades(scope, target) {
    if (busy) return;
    const label = scope === "all" ? "ALL personal trade history" : "today's personal trade history";
    if (!window.confirm(`Clear ${label} for the currently logged-in account? This will also reset recovery state for that account.`)) return;
    busy = true;
    scheduleSync();
    try {
      const response = await fetch("/me/clear-trades", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ scope }),
      });
      const raw = await response.text();
      let body = {};
      try { body = raw ? JSON.parse(raw) : {}; } catch (_) { body = { detail: raw }; }
      if (!response.ok) throw new Error(body.detail || body.message || `${response.status} ${response.statusText}`);
      statusLine(target || document.body, body.message || `Cleared ${scope} trades.`);
      document.dispatchEvent(new CustomEvent("foa:trades-cleared", { detail: { scope, body } }));
      setTimeout(() => {
        fetch(`/me/trades/today?reset_refresh=${Date.now()}`, { credentials: "same-origin", cache: "no-store" }).catch(() => {});
      }, 250);
    } catch (error) {
      statusLine(target || document.body, String(error?.message || error), true);
    } finally {
      busy = false;
      scheduleSync();
    }
  }

  function controlsFor(card) {
    let wrap = card.querySelector(".foa-reset-trades-always");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "foa-reset-trades-always";
      wrap.innerHTML = `
        <button type="button" data-reset-trades-scope="today">Reset Today</button>
        <button type="button" class="danger" data-reset-trades-scope="all">Reset All</button>
      `;
      wrap.addEventListener("click", event => {
        const button = event.target.closest("[data-reset-trades-scope]");
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        clearTrades(button.dataset.resetTradesScope || "today", card);
      });
    }

    wrap.querySelectorAll("button").forEach(button => {
      if (button.disabled !== busy) button.disabled = busy;
      const label = busy
        ? "Resetting…"
        : button.dataset.resetTradesScope === "all" ? "Reset All" : "Reset Today";
      setText(button, label);
    });
    return wrap;
  }

  function sync() {
    ensureStyle();
    if (!authKnown) refreshAuth();
    const targets = cardTargets();

    if (!authenticated) {
      document.querySelectorAll(".foa-reset-trades-always,.foa-reset-status").forEach(node => node.remove());
      return;
    }

    targets.forEach(card => {
      const head = card.querySelector(".foa-card-head") || card;
      const controls = controlsFor(card);
      if (!head.contains(controls)) head.appendChild(controls);
    });
    document.body.dataset.foaResetTradesAlwaysVersion = VERSION;
  }

  function start() {
    if (initialized) return;
    initialized = true;
    refreshAuth();
    scheduleSync();

    document.addEventListener("click", event => {
      if (event.target.closest("[data-view], [data-mode], [data-control], .foa-save-button")) {
        setTimeout(scheduleSync, 100);
        setTimeout(scheduleSync, 700);
      }
    }, true);

    document.addEventListener("foa:trades-cleared", () => {
      setTimeout(scheduleSync, 100);
      setTimeout(scheduleSync, 1000);
    });

    // Child-list changes are coalesced into one animation-frame sync. Text and
    // disabled state are updated only when values actually change, preventing the
    // previous observer -> textContent -> observer feedback loop.
    new MutationObserver(scheduleSync).observe(document.documentElement, { childList: true, subtree: true });
    setInterval(scheduleSync, 1500);
    setInterval(refreshAuth, 5000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }

  window.FOA_RESET_TRADES_ALWAYS = VERSION;
  window.FOA_RESET_TRADES_NO_MUTATION_LOOP = true;
})();
'''


def _versioned(source: str) -> str:
    return (
        source.replace("20260802-3", UI_VERSION)
        .replace("20260802-2", UI_VERSION)
        .replace("20260802-1", UI_VERSION)
    )


def _html() -> str:
    return _versioned(public_stats_html())


def _dashboard_script() -> str:
    source = _versioned(public_stats_dashboard_script())
    if "FOA_RESET_TRADES_ALWAYS" not in source:
        source += _RESET_TRADES_ALWAYS_JS
    return source


def _compat_script() -> str:
    source = _versioned(public_stats_compat_script())
    if "FOA_RESET_TRADES_ALWAYS" not in source:
        source += _RESET_TRADES_ALWAYS_JS
    return source


def install_reset_trades_always_ui(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/", "/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/", include_in_schema=False)
    def reset_trades_always_root(
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
    def dashboard_v2_with_reset_trades_always() -> Response:
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
    def simplified_dashboard_with_reset_trades_always() -> Response:
        return Response(
            _compat_script(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    app.state.reset_trades_always_ui_installed = True
    _INSTALLED = True
