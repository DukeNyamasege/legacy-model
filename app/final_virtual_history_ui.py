from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

import app.api as base_api
from app.dashboard_loader_unlock import (
    _compat_script as loader_compat_script,
    _dashboard_script as loader_dashboard_script,
    _html as loader_html,
)
from app.dashboard_stability_fix import _remove_route, _stable_actions_js

_INSTALLED = False
UI_VERSION = "20260802-9"

_RISK_DISCLAIMER_JS = r'''

/* FOA_RISK_DISCLAIMER + FOA_VIRTUAL_WIN_PROGRESS: final non-observer UI layer. */
(() => {
  "use strict";
  const VERSION = "20260802-9";
  let started = false;

  function ensureStyle() {
    if (document.getElementById("foa-risk-disclaimer-css")) return;
    const style = document.createElement("style");
    style.id = "foa-risk-disclaimer-css";
    style.textContent = `
      .foa-risk-disclaimer{margin:18px 0 6px;padding:13px 15px;border:1px solid rgba(245,158,11,.28);border-radius:13px;background:rgba(245,158,11,.08);color:var(--muted,#64748b);font-size:11px;line-height:1.5}
      .foa-risk-disclaimer strong{color:var(--text,#0f172a);font-weight:900;margin-right:4px}
      [data-theme="dark"] .foa-risk-disclaimer{background:rgba(245,158,11,.07);color:#aab6c8}
      [data-theme="dark"] .foa-risk-disclaimer strong{color:#f8fafc}
      .foa-stable-trade em{white-space:normal!important;line-height:1.2!important;display:inline-block!important;vertical-align:middle!important;max-width:100%!important}
      .foa-aidr-mode.stopped{background:rgba(239,68,68,.14)!important;color:#dc2626!important}
      .foa-aidr-mode.paused{background:rgba(245,158,11,.15)!important;color:#b45309!important}
      [data-theme="dark"] .foa-aidr-mode.stopped{color:#fca5a5!important}
      [data-theme="dark"] .foa-aidr-mode.paused{color:#fcd34d!important}
      @media(max-width:760px){.foa-risk-disclaimer{margin:14px 0 2px;padding:11px 12px;font-size:10px;border-radius:11px}.foa-stable-trade em{font-size:8px!important;padding:3px 5px!important}}
    `;
    document.head.appendChild(style);
  }

  function ensureDisclaimer() {
    ensureStyle();
    const main = document.querySelector(".foa-main");
    if (!main) return;
    let box = document.getElementById("foa-risk-disclaimer");
    if (!box) {
      box = document.createElement("aside");
      box.id = "foa-risk-disclaimer";
      box.className = "foa-risk-disclaimer";
      box.innerHTML = `<strong>Risk Disclaimer:</strong> Automated trading involves financial risk. Past results, virtual wins, recovery calculations, and displayed statistics do not guarantee future profit. Users remain responsible for account settings, stake size, and all losses. For educational and automation purposes only.`;
    }
    if (box.parentElement !== main || main.lastElementChild !== box) main.appendChild(box);
    document.body.dataset.foaRiskDisclaimerVersion = VERSION;
    document.body.dataset.foaVirtualWinProgressVersion = VERSION;
  }

  function start() {
    if (started) return;
    started = true;
    ensureDisclaimer();
    window.setInterval(ensureDisclaimer, 2500);
  }

  document.addEventListener("DOMContentLoaded", start, { once: true });
  document.addEventListener("click", event => {
    if (event.target.closest("[data-view],[data-mode],[data-control],#logout")) {
      window.setTimeout(ensureDisclaimer, 300);
      window.setTimeout(ensureDisclaimer, 900);
    }
  }, true);
  if (document.readyState !== "loading") start();
  window.FOA_RISK_DISCLAIMER = VERSION;
  window.FOA_VIRTUAL_TRADES_IN_RECENT_HISTORY = VERSION;
  window.FOA_VIRTUAL_WIN_PROGRESS = VERSION;
})();
'''


def _versioned(source: str) -> str:
    value = source
    for old in (
        "20260802-8",
        "20260802-7",
        "20260802-6",
        "20260802-5",
        "20260802-4",
        "20260802-3",
        "20260802-2",
        "20260802-1",
        "20260801-8",
        "20260801-7",
        "20260801-6",
        "20260801-5",
    ):
        value = value.replace(old, UI_VERSION)
    return value


def _patch_aidr_lifecycle_labels(source: str) -> str:
    old = '''  function modeLabel(mode) {
    if (mode === "virtual") return "Virtual Protection";
    if (mode === "full_recovery") return "Full Recovery";
    if (mode === "exact_recovery") return "Exact Recovery";
    return "Normal Trading";
  }
'''
    new = '''  function modeLabel(mode) {
    if (mode === "stopped") return "Stopped · Fresh Start Required";
    if (mode === "paused") return "Paused · State Preserved";
    if (mode === "virtual") return "Virtual Protection";
    if (mode === "full_recovery") return "Full Recovery";
    if (mode === "exact_recovery") return "Exact Recovery";
    return "Normal Trading";
  }
'''
    return source.replace(old, new)


def _html() -> str:
    return _versioned(loader_html())


def _dashboard_script() -> str:
    source = _patch_aidr_lifecycle_labels(_versioned(loader_dashboard_script()))
    # Virtual observations are returned directly by the final /me/trades/today
    # authority. Keep the AIDR status strip, but remove the obsolete duplicate
    # standalone virtual-history card.
    source = source.replace(
        "    ensureVirtualCard(payload);",
        '    document.getElementById("foa-personal-virtual-trades")?.remove();',
    )
    if "FOA_VIRTUAL_WIN_PROGRESS" not in source:
        source += _RISK_DISCLAIMER_JS
    return source


def _compat_script() -> str:
    source = _patch_aidr_lifecycle_labels(_versioned(loader_compat_script()))
    source = source.replace(
        "    ensureVirtualCard(payload);",
        '    document.getElementById("foa-personal-virtual-trades")?.remove();',
    )
    if "FOA_VIRTUAL_WIN_PROGRESS" not in source:
        source += _RISK_DISCLAIMER_JS
    return source


def _actions_script() -> str:
    return _versioned(_stable_actions_js())


def _headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-FOA-UI-Version": UI_VERSION,
    }


def install_final_virtual_history_ui(app: Any) -> None:
    """Install final unified actual/virtual trade UI and risk disclaimer."""

    global _INSTALLED
    if _INSTALLED:
        return

    for path in (
        "/",
        "/ui/dashboard-v2.js",
        "/ui/dashboard-actions-v2.js",
        "/ui/simplified-dashboard.js",
    ):
        _remove_route(app, path, "GET")

    @app.get("/", include_in_schema=False)
    def final_virtual_history_root(
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
        return HTMLResponse(_html(), headers=_headers())

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def final_virtual_history_dashboard() -> Response:
        return Response(
            _dashboard_script(),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/dashboard-actions-v2.js", include_in_schema=False)
    def final_virtual_history_actions() -> Response:
        return Response(
            _actions_script(),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def final_virtual_history_compat() -> Response:
        return Response(
            _compat_script(),
            media_type="application/javascript",
            headers=_headers(),
        )

    app.state.final_virtual_history_ui_installed = True
    _INSTALLED = True
