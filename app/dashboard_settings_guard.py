from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_stability_fix import UI_VERSION, _patched_dashboard_js, _remove_route

_INSTALLED = False

_SETTINGS_GUARD_JS = r'''

/* FOA_SETTINGS_PERSISTENCE_VERSION:20260802-1
   Keep account settings fields stable while typing and while the save request is
   in flight. The trading token is deliberately not stored in browser storage. */
(() => {
  const VERSION = "20260802-1";
  const PREFIX = "foa-settings-draft:";
  const FIELD_NAMES = new Set([
    "stake_amount",
    "take_profit",
    "stop_loss",
    "martingale_mode",
    "martingale_trigger_losses",
    "martingale_multiplier",
    "martingale_max_levels",
    "martingale_max_stake",
  ]);

  function accountKey() {
    const account = (document.querySelector(".foa-account-pill span")?.textContent || "account").trim();
    const mode = (document.querySelector(".foa-account-pill b")?.textContent || localStorage.getItem("foa-mode-v2") || "demo").trim().toLowerCase();
    return `${PREFIX}${mode}:${account}`;
  }

  function readDraft() {
    try { return JSON.parse(sessionStorage.getItem(accountKey()) || "{}"); }
    catch (_) { return {}; }
  }

  function writeDraft(values) {
    try { sessionStorage.setItem(accountKey(), JSON.stringify(values)); }
    catch (_) {}
  }

  function capture(form) {
    if (!form) return;
    const values = readDraft();
    for (const element of Array.from(form.elements || [])) {
      if (!element.name || !FIELD_NAMES.has(element.name)) continue;
      values[element.name] = element.value;
    }
    writeDraft(values);
  }

  function hydrate() {
    const form = document.querySelector("#settings-form");
    if (!form) return;
    const active = document.activeElement;
    const editing = form.contains(active) && ["INPUT", "SELECT", "TEXTAREA"].includes(active?.tagName || "");
    const values = readDraft();
    for (const element of Array.from(form.elements || [])) {
      if (!element.name || !FIELD_NAMES.has(element.name)) continue;
      if (editing && element === active) continue;
      if (Object.prototype.hasOwnProperty.call(values, element.name)) {
        element.value = values[element.name];
      }
    }
  }

  document.addEventListener("input", event => {
    const form = event.target?.closest?.("#settings-form");
    if (form) capture(form);
  }, true);

  document.addEventListener("change", event => {
    const form = event.target?.closest?.("#settings-form");
    if (form) capture(form);
  }, true);

  document.addEventListener("submit", event => {
    const form = event.target?.closest?.("#settings-form");
    if (form) capture(form);
  }, true);

  new MutationObserver(() => hydrate()).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", hydrate, { once: true });
  window.setInterval(hydrate, 1000);
  window.FOA_SETTINGS_PERSISTENCE_VERSION = VERSION;
})();
'''


def _script() -> str:
    source = _patched_dashboard_js()
    if "FOA_SETTINGS_PERSISTENCE_VERSION:20260802-1" not in source:
        source += _SETTINGS_GUARD_JS
    return source


def install_dashboard_settings_guard(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def dashboard_v2_with_settings_guard() -> Response:
        return Response(
            _script(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def simplified_dashboard_with_settings_guard() -> Response:
        return Response(
            "/* compatibility: stable dashboard v2 */\n" + _script(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    app.state.dashboard_settings_guard_installed = True
    _INSTALLED = True
