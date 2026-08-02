from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_settings_guard import _script as dashboard_script_with_settings_guard
from app.dashboard_smoke_compat import _simplified_compat_script
from app.dashboard_stability_fix import UI_VERSION, _remove_route

_INSTALLED = False

_ACCOUNT_ID_UI_JS = r'''

/* FOA_ACCOUNT_ID_BADGE: show exact logged-in BOT/ROT account on every page. */
(() => {
  const VERSION = "20260802-account-id";
  function ensureStyle() {
    if (document.getElementById("foa-account-id-badge-css")) return;
    const style = document.createElement("style");
    style.id = "foa-account-id-badge-css";
    style.textContent = `
      .foa-account-id-badge{position:fixed;top:12px;right:14px;z-index:80;display:flex;align-items:center;gap:7px;padding:8px 10px;border-radius:999px;border:1px solid rgba(148,163,184,.26);background:rgba(7,17,32,.82);box-shadow:0 12px 30px rgba(0,0,0,.25);backdrop-filter:blur(14px);color:#f8fafc;font-size:12px;font-weight:850;letter-spacing:.01em;max-width:calc(100vw - 28px)}
      .foa-account-id-badge b{color:#83adff;font-weight:900}.foa-account-id-badge span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:170px}.foa-account-id-badge small{color:#aab6c8;font-size:10px;text-transform:uppercase;letter-spacing:.08em}
      @media(max-width:760px){.foa-account-id-badge{top:8px;right:10px;padding:7px 9px;font-size:11px}.foa-account-id-badge span{max-width:135px}.foa-account-id-badge small{font-size:9px}}
    `;
    document.head.appendChild(style);
  }
  function upsert(data) {
    ensureStyle();
    const authenticated = !!data && data.authenticated;
    let badge = document.getElementById("foa-account-id-badge");
    if (!authenticated) { if (badge) badge.remove(); return; }
    const account = data.account_id_full || data.login_id || data.display_account_id || data.account_id || "";
    const type = (data.account_type_label || data.account_type || "Account").toString();
    if (!account) return;
    if (!badge) {
      badge = document.createElement("div");
      badge.id = "foa-account-id-badge";
      badge.className = "foa-account-id-badge";
      document.body.appendChild(badge);
    }
    badge.innerHTML = `<small>${type}</small><b>●</b><span>${account}</span>`;
  }
  async function refreshIdentity() {
    try {
      const response = await fetch(`/me?identity=${Date.now()}`, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      upsert(await response.json());
    } catch (_) {}
  }
  document.addEventListener("DOMContentLoaded", () => { refreshIdentity(); setInterval(refreshIdentity, 3000); }, { once: true });
  if (document.readyState !== "loading") { refreshIdentity(); setInterval(refreshIdentity, 3000); }
  window.FOA_ACCOUNT_ID_BADGE = VERSION;
})();
'''


def _dashboard_script() -> str:
    source = dashboard_script_with_settings_guard()
    if "FOA_ACCOUNT_ID_BADGE" not in source:
        source += _ACCOUNT_ID_UI_JS
    return source


def _compat_script() -> str:
    source = _simplified_compat_script()
    if "FOA_ACCOUNT_ID_BADGE" not in source:
        source += _ACCOUNT_ID_UI_JS
    return source


def install_account_identity_ui(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def dashboard_v2_with_account_identity() -> Response:
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
    def simplified_dashboard_with_account_identity() -> Response:
        return Response(
            _compat_script(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-UI-Version": UI_VERSION,
            },
        )

    app.state.account_identity_ui_installed = True
    _INSTALLED = True
