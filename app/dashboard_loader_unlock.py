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
UI_VERSION = "20260802-7"

_LOADER_UNLOCK_JS = r'''

/* FOA_DASHBOARD_LOADER_UNLOCK: one-shot release with no mutation feedback loop. */
(() => {
  "use strict";
  const VERSION = "20260802-7";
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

    observer = new MutationObserver(() => attempt("content-added"));
    observer.observe(document.documentElement, { childList: true, subtree: true });

    [100, 250, 500, 1000, 1800, 3000, 5000, 8000].forEach(delay => {
      window.setTimeout(() => attempt(`timer-${delay}`), delay);
    });

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

_AIDR_STATUS_JS = r'''

/* FOA_AIDR_PERSONAL_STATUS: live recovery state and $0 virtual observations. */
(() => {
  "use strict";
  const VERSION = "20260802-7";
  let started = false;
  let inFlight = false;
  let lastPayload = null;

  const esc = value => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
  const money = value => {
    const amount = Number(value || 0);
    return `${amount < 0 ? "-" : ""}$${Math.abs(amount).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  };
  const timeText = value => {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };

  function ensureStyle() {
    if (document.getElementById("foa-aidr-status-css")) return;
    const style = document.createElement("style");
    style.id = "foa-aidr-status-css";
    style.textContent = `
      .foa-aidr-status{display:grid;grid-template-columns:minmax(180px,.8fr) repeat(3,minmax(120px,.55fr)) minmax(250px,1.4fr);gap:10px;align-items:stretch;margin:0 0 16px;padding:14px 16px;border:1px solid var(--line,rgba(148,163,184,.2));border-radius:16px;background:linear-gradient(135deg,rgba(47,115,255,.10),rgba(255,255,255,.03));box-shadow:0 12px 28px rgba(15,23,42,.10)}
      .foa-aidr-status>div{min-width:0;padding:3px 7px;border-right:1px solid var(--line,rgba(148,163,184,.16))}.foa-aidr-status>div:last-child{border-right:0}
      .foa-aidr-status span{display:block;color:var(--muted,#64748b);font-size:10px;font-weight:850;letter-spacing:.07em;text-transform:uppercase;margin-bottom:5px}
      .foa-aidr-status strong{display:block;color:var(--text,#0f172a);font-size:15px;line-height:1.25;font-weight:900;overflow-wrap:anywhere}
      .foa-aidr-status small{display:block;color:var(--muted,#64748b);font-size:11px;line-height:1.35;margin-top:4px}
      .foa-aidr-mode{display:inline-flex!important;width:max-content;max-width:100%;padding:5px 9px;border-radius:999px;background:rgba(47,115,255,.13);color:#1d4ed8!important}
      .foa-aidr-mode.virtual{background:rgba(245,158,11,.16);color:#b45309!important}.foa-aidr-mode.full_recovery,.foa-aidr-mode.exact_recovery{background:rgba(139,92,246,.15);color:#6d28d9!important}
      [data-theme="dark"] .foa-aidr-status strong{color:#f8fafc}[data-theme="dark"] .foa-aidr-mode{color:#93c5fd!important}[data-theme="dark"] .foa-aidr-mode.virtual{color:#fcd34d!important}[data-theme="dark"] .foa-aidr-mode.full_recovery,[data-theme="dark"] .foa-aidr-mode.exact_recovery{color:#c4b5fd!important}
      .foa-virtual-card{margin-top:16px}.foa-virtual-note{color:var(--muted,#64748b);font-size:12px;margin:4px 0 12px}
      .foa-virtual-head,.foa-virtual-row{display:grid;grid-template-columns:110px minmax(120px,1fr) 110px 110px 110px 120px;gap:10px;align-items:center;padding:9px 4px;border-bottom:1px solid var(--line,rgba(148,163,184,.18));font-size:12px}
      .foa-virtual-head{color:var(--muted,#64748b);font-size:10px;font-weight:850;text-transform:uppercase;letter-spacing:.05em}
      .foa-virtual-row strong{font-size:12px}.foa-virtual-result.win{color:#16a34a}.foa-virtual-result.loss{color:#dc2626}.foa-virtual-result.open{color:#d97706}
      .foa-virtual-empty{padding:18px 4px;color:var(--muted,#64748b);font-size:12px}
      @media(max-width:900px){.foa-aidr-status{grid-template-columns:repeat(2,minmax(0,1fr))}.foa-aidr-status>div{border-right:0;border-bottom:1px solid var(--line,rgba(148,163,184,.14));padding:7px}.foa-aidr-status>div:last-child{grid-column:1/-1;border-bottom:0}.foa-virtual-head{display:none}.foa-virtual-row{grid-template-columns:1fr 1fr 1fr;gap:7px}.foa-virtual-row>*:nth-child(4),.foa-virtual-row>*:nth-child(5),.foa-virtual-row>*:nth-child(6){text-align:left}}
      @media(max-width:540px){.foa-aidr-status{grid-template-columns:1fr 1fr;padding:10px;gap:5px}.foa-aidr-status strong{font-size:13px}.foa-aidr-status small{font-size:10px}.foa-virtual-row{grid-template-columns:1fr 1fr;font-size:11px}}
    `;
    document.head.appendChild(style);
  }

  function modeLabel(mode) {
    if (mode === "virtual") return "Virtual Protection";
    if (mode === "full_recovery") return "Full Recovery";
    if (mode === "exact_recovery") return "Exact Recovery";
    return "Normal Trading";
  }

  function statusHTML(payload) {
    const wins = Number(payload.virtual_wins || 0);
    const required = Number(payload.virtual_wins_required || 1);
    return `
      <div><span>AIDR Mode</span><strong class="foa-aidr-mode ${esc(payload.mode)}">${esc(modeLabel(payload.mode))}</strong><small>${esc(payload.account || "Current account")}</small></div>
      <div><span>Recovery Debt</span><strong>${money(payload.recovery_debt)}</strong><small>Actual monetary losses only</small></div>
      <div><span>Virtual Wins</span><strong>${wins} / ${required}</strong><small>Must be consecutive</small></div>
      <div><span>Recovery Targets</span><strong>${Number(payload.split_recovery_remaining || 0)}</strong><small>One full-debt target</small></div>
      <div><span>Next Action</span><strong>${esc(payload.next_action || "Normal OVER-1 execution.")}</strong><small>Virtual observations charge $0.00</small></div>
    `;
  }

  function ensureStatus(payload) {
    const main = document.querySelector(".foa-main");
    if (!main) return;
    let panel = document.getElementById("foa-aidr-personal-status");
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "foa-aidr-personal-status";
      panel.className = "foa-aidr-status";
    }
    const publicStats = document.getElementById("foa-public-trader-stats");
    const intro = main.querySelector(".foa-page-intro");
    const kpis = main.querySelector(".foa-kpis");
    const anchor = publicStats || intro || kpis;
    if (anchor && panel.previousElementSibling !== anchor) anchor.insertAdjacentElement("afterend", panel);
    else if (!anchor && panel.parentElement !== main) main.prepend(panel);
    const html = statusHTML(payload);
    if (panel.innerHTML !== html) panel.innerHTML = html;
  }

  function virtualRows(payload) {
    const rows = Array.isArray(payload.virtual_trades) ? payload.virtual_trades : [];
    if (!rows.length) return `<div class="foa-virtual-empty">No virtual observations have been recorded today for this account.</div>`;
    return rows.map(row => {
      const raw = String(row.result || "OPEN").toUpperCase();
      const outcome = raw.includes("WIN") ? "WIN" : raw.includes("LOSS") ? "LOSS" : raw;
      const css = outcome === "WIN" ? "win" : outcome === "LOSS" ? "loss" : "open";
      return `<div class="foa-virtual-row">
        <span>${esc(timeText(row.created_at))}</span>
        <strong>${esc(row.market || "—")} · OVER ${esc(row.barrier || "3")}</strong>
        <span>${money(row.simulated_stake)}</span>
        <span>${row.actual_last_digit ?? "—"}</span>
        <strong class="foa-virtual-result ${css}">${esc(outcome)}</strong>
        <span>$0.00 charged</span>
      </div>`;
    }).join("");
  }

  function ensureVirtualCard(payload) {
    const allTrades = document.querySelector(".foa-all-trades");
    let card = document.getElementById("foa-personal-virtual-trades");
    if (!allTrades) {
      if (card) card.remove();
      return;
    }
    if (!card) {
      card = document.createElement("section");
      card.id = "foa-personal-virtual-trades";
      card.className = "foa-card foa-virtual-card";
      allTrades.insertAdjacentElement("afterend", card);
    } else if (card.previousElementSibling !== allTrades) {
      allTrades.insertAdjacentElement("afterend", card);
    }
    const html = `
      <div class="foa-card-head"><div><h2>Virtual Protection Trades ($0)</h2><p class="foa-virtual-note">Hypothetical OVER-4 observations used after a failed real recovery. They never deduct account balance or add debt.</p></div><span class="foa-period">${Number(payload.virtual_wins || 0)}/${Number(payload.virtual_wins_required || 1)} wins</span></div>
      <div class="foa-virtual-head"><span>Time</span><span>Market / Contract</span><span>Simulated Stake</span><span>Exit Digit</span><span>Result</span><span>Financial Impact</span></div>
      ${virtualRows(payload)}
    `;
    if (card.innerHTML !== html) card.innerHTML = html;
  }

  function apply(payload) {
    ensureStyle();
    if (!payload || !payload.authenticated) {
      document.getElementById("foa-aidr-personal-status")?.remove();
      document.getElementById("foa-personal-virtual-trades")?.remove();
      return;
    }
    ensureStatus(payload);
    ensureVirtualCard(payload);
    document.body.dataset.foaAidrPersonalStatusVersion = VERSION;
  }

  async function sync() {
    if (inFlight || !document.querySelector("#foa-simple-app")) return;
    inFlight = true;
    try {
      const response = await fetch(`/me/aidr-status?t=${Date.now()}`, {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) return;
      lastPayload = await response.json();
      apply(lastPayload);
    } catch (_err) {
    } finally {
      inFlight = false;
    }
  }

  function start() {
    if (started) return;
    started = true;
    sync();
    window.setInterval(() => {
      if (lastPayload) apply(lastPayload);
      sync();
    }, 3000);
  }

  document.addEventListener("DOMContentLoaded", start, { once: true });
  document.addEventListener("click", event => {
    if (event.target.closest("[data-view], [data-mode], [data-control], .foa-save-button")) {
      window.setTimeout(() => { if (lastPayload) apply(lastPayload); sync(); }, 250);
      window.setTimeout(() => { if (lastPayload) apply(lastPayload); }, 1000);
    }
  }, true);
  if (document.readyState !== "loading") start();
  window.FOA_AIDR_PERSONAL_STATUS = VERSION;
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
    return _versioned(reset_html())


def _dashboard_script() -> str:
    source = _versioned(reset_dashboard_script())
    if "FOA_DASHBOARD_LOADER_UNLOCK" not in source:
        source += _LOADER_UNLOCK_JS
    if "FOA_AIDR_PERSONAL_STATUS" not in source:
        source += _AIDR_STATUS_JS
    return source


def _compat_script() -> str:
    source = _versioned(reset_compat_script())
    if "FOA_DASHBOARD_LOADER_UNLOCK" not in source:
        source += _LOADER_UNLOCK_JS
    if "FOA_AIDR_PERSONAL_STATUS" not in source:
        source += _AIDR_STATUS_JS
    return source


def install_dashboard_loader_unlock(app: Any) -> None:
    """Install one-shot loader release and account-level AIDR status UI."""

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
