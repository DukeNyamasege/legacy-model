from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.custom_strategy_final_ui import _headers as base_headers
from app.custom_strategy_final_ui import _script as base_script
from app.dashboard_stability_fix import _remove_route


_INSTALLED = False
UI_VERSION = "20260808-custom-strategy-card-visibility-v4"

_VISIBILITY_JS = r'''

/* FOA_CUSTOM_STRATEGY_CARD_VISIBILITY_V4 */
(() => {
  "use strict";
  const VERSION = "20260808-4";
  let probing = false;

  function settingsGrid() {
    return document.querySelector(".foa-settings-grid");
  }

  function ensureStrategyAnchor() {
    const grid = settingsGrid();
    if (!grid) return null;
    let selector = document.getElementById("foa-strategy-selector");
    if (selector) return selector;

    selector = document.createElement("article");
    selector.id = "foa-strategy-selector";
    selector.dataset.foaCustomStrategyAnchor = VERSION;
    selector.hidden = true;
    selector.setAttribute("aria-hidden", "true");
    grid.insertAdjacentElement("afterbegin", selector);
    return selector;
  }

  function ensureCardShell() {
    const anchor = ensureStrategyAnchor();
    if (!anchor) return null;
    let card = document.getElementById("foa-custom-strategy-builder");
    if (card) return card;

    card = document.createElement("section");
    card.id = "foa-custom-strategy-builder";
    card.className = "foa-card";
    card.dataset.foaVisibilityShell = VERSION;
    card.innerHTML = `
      <div style="padding:18px 20px">
        <div style="font-size:9px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:var(--blue,#2563eb)">Custom execution strategy</div>
        <h2 style="margin:5px 0 6px;font-size:18px">Build your own trading pattern</h2>
        <p id="foa-custom-visibility-status" style="margin:0;color:var(--muted,#64748b);font-size:11px;line-height:1.5">Loading Custom Strategy Builder…</p>
      </div>`;
    anchor.insertAdjacentElement("afterend", card);
    document.body.dataset.foaCustomStrategyCard = VERSION;
    return card;
  }

  function setShellStatus(message, error = false) {
    const card = ensureCardShell();
    if (!card || card.querySelector(".foa-cs-hero")) return;
    const status = card.querySelector("#foa-custom-visibility-status");
    if (!status) return;
    status.textContent = message;
    status.style.color = error ? "#c93745" : "var(--muted,#64748b)";
  }

  async function probe() {
    if (probing || !settingsGrid()) return;
    const card = ensureCardShell();
    if (!card) return;
    document.body.dataset.foaCustomStrategyCard = VERSION;
    if (card.querySelector(".foa-cs-hero")) return;

    probing = true;
    try {
      const response = await fetch(`/me/custom-strategy?visibility_probe=${Date.now()}`, {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (response.status === 401) {
        setShellStatus("Custom Strategy Builder is ready after you sign in to this account.", true);
        return;
      }
      if (!response.ok) {
        setShellStatus(`Custom Strategy Builder API returned HTTP ${response.status}. Retrying automatically.`, true);
        return;
      }
      setShellStatus("Loading saved Custom Strategy configuration…");
    } catch (_error) {
      setShellStatus("Custom Strategy Builder could not reach the API. Retrying automatically.", true);
    } finally {
      probing = false;
    }
  }

  function apply() {
    if (!settingsGrid()) return;
    ensureCardShell();
    window.setTimeout(probe, 50);
  }

  function boot() {
    apply();
    const observer = new MutationObserver(() => apply());
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.setInterval(probe, 2500);
    document.addEventListener("click", event => {
      if (event.target.closest("[data-view='strategy'], [data-view='settings'], #foa-save-strategy, [data-control]")) {
        window.setTimeout(apply, 100);
        window.setTimeout(probe, 350);
      }
    }, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }

  window.FOA_CUSTOM_STRATEGY_CARD_VISIBILITY_V4 = VERSION;
})();
'''


def _script(*, compatibility: bool = False) -> str:
    source = base_script(compatibility=compatibility)
    if "FOA_CUSTOM_STRATEGY_CARD_VISIBILITY_V4" not in source:
        source += _VISIBILITY_JS
    return source


def _headers() -> dict[str, str]:
    return {
        **base_headers(),
        "X-FOA-Custom-Strategy": "v4",
        "X-FOA-Custom-Strategy-Card": "complete-builder-v2",
        "X-FOA-Custom-Strategy-Visibility": "anchor-fallback-v1",
        "X-FOA-UI-Version": UI_VERSION,
    }


def install_custom_strategy_card_visibility_fix(app: Any) -> None:
    """Serve the card compositor last and tolerate a late strategy selector mount."""

    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")
        _remove_route(app, path, "HEAD")

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def custom_strategy_visibility_dashboard_js() -> Response:
        return Response(
            _script(compatibility=False),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def custom_strategy_visibility_simplified_js() -> Response:
        return Response(
            _script(compatibility=True),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.head("/ui/dashboard-v2.js", include_in_schema=False)
    def custom_strategy_visibility_dashboard_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    @app.head("/ui/simplified-dashboard.js", include_in_schema=False)
    def custom_strategy_visibility_simplified_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    _INSTALLED = True
