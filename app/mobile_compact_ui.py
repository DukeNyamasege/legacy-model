from __future__ import annotations

from app.route_utils import remove_route as _remove_route

from typing import Any

from fastapi.responses import Response

import app.api as base_api

_INSTALLED = False
UI_COMPACT_VERSION = "20260803-compact-session-nav"




def _compact_mobile_css() -> str:
    """High-specificity mobile overrides for the final dashboard.

    Several older UI compatibility layers inject styles after the main stylesheet.
    The selectors below are intentionally more specific so this final compact
    mobile system remains authoritative without changing the desktop design.
    """

    return r"""

/* FOA compact mobile authority: desktop remains unchanged. */
@media (max-width: 760px) {
  html body #foa-simple-app {
    font-size: 10px !important;
    line-height: 1.35 !important;
    -webkit-text-size-adjust: 100% !important;
    text-size-adjust: 100% !important;
  }
  html body #foa-simple-app .foa-main {
    padding: 10px 8px 66px !important;
  }
  html body #foa-simple-app .foa-topbar {
    min-height: 48px !important;
    padding: 7px 8px !important;
    gap: 7px !important;
  }
  html body #foa-simple-app .foa-mobile-brand strong {
    font-size: 10px !important;
    line-height: 1.08 !important;
  }
  html body #foa-simple-app .foa-logo {
    width: 27px !important;
    height: 27px !important;
    font-size: 12px !important;
  }
  html body #foa-simple-app .foa-page-title small,
  html body #foa-simple-app .foa-eyebrow,
  html body #foa-simple-app small {
    font-size: 8.5px !important;
    line-height: 1.3 !important;
    letter-spacing: .02em !important;
  }
  html body #foa-simple-app .foa-page-title strong {
    font-size: 12px !important;
    line-height: 1.15 !important;
  }
  html body #foa-simple-app h1,
  html body #foa-simple-app .foa-welcome-card h1 {
    font-size: 17px !important;
    line-height: 1.15 !important;
    margin: 5px 0 7px !important;
  }
  html body #foa-simple-app h2,
  html body #foa-simple-app .foa-card h2 {
    font-size: 12px !important;
    line-height: 1.2 !important;
    margin: 0 !important;
  }
  html body #foa-simple-app h3 {
    font-size: 11px !important;
    line-height: 1.2 !important;
  }
  html body #foa-simple-app p,
  html body #foa-simple-app label,
  html body #foa-simple-app .foa-simple-list,
  html body #foa-simple-app .foa-status-copy,
  html body #foa-simple-app .foa-trade-row,
  html body #foa-simple-app .foa-trade-row-wide {
    font-size: 9.5px !important;
    line-height: 1.4 !important;
  }
  html body #foa-simple-app .foa-card {
    border-radius: 11px !important;
    padding: 11px !important;
  }
  html body #foa-simple-app .foa-card-head {
    gap: 8px !important;
    margin-bottom: 8px !important;
  }
  html body #foa-simple-app .foa-kpis,
  html body #foa-simple-app .foa-public-grid,
  html body #foa-simple-app .foa-dashboard-grid,
  html body #foa-simple-app .foa-settings-grid {
    gap: 7px !important;
  }
  html body #foa-simple-app .foa-kpi {
    min-height: 70px !important;
    padding: 10px !important;
    border-radius: 11px !important;
    gap: 8px !important;
  }
  html body #foa-simple-app .foa-kpi span,
  html body #foa-simple-app .foa-kpi small {
    font-size: 8px !important;
    line-height: 1.25 !important;
  }
  html body #foa-simple-app .foa-kpi strong {
    font-size: 14px !important;
    line-height: 1.05 !important;
  }
  html body #foa-simple-app .foa-kpi-icon {
    width: 29px !important;
    height: 29px !important;
    font-size: 13px !important;
  }
  html body #foa-simple-app .foa-balance {
    font-size: 18px !important;
    line-height: 1.05 !important;
  }
  html body #foa-simple-app button,
  html body #foa-simple-app .foa-primary-link,
  html body #foa-simple-app .foa-secondary-link,
  html body #foa-simple-app .foa-login {
    font-size: 9.5px !important;
  }
  html body #foa-simple-app input,
  html body #foa-simple-app select,
  html body #foa-simple-app textarea {
    font-size: 16px !important;
    line-height: 1.25 !important;
  }
  html body #foa-simple-app button,
  html body #foa-simple-app .foa-primary-link,
  html body #foa-simple-app .foa-secondary-link,
  html body #foa-simple-app .foa-login {
    min-height: 34px !important;
    padding: 0 10px !important;
    border-radius: 8px !important;
  }
  html body #foa-simple-app input,
  html body #foa-simple-app select,
  html body #foa-simple-app textarea {
    min-height: 44px !important;
    padding: 9px 10px !important;
    border-radius: 8px !important;
  }
  html body #foa-simple-app .foa-top-actions {
    min-width: 0 !important;
    max-width: 46vw !important;
    gap: 5px !important;
  }
  html body #foa-simple-app .foa-login,
  html body #foa-simple-app .foa-logout {
    max-width: 118px !important;
    white-space: normal !important;
    line-height: 1.1 !important;
    text-align: center !important;
  }
  html body #foa-simple-app .foa-actions-row,
  html body #foa-simple-app .foa-welcome-actions,
  html body #foa-simple-app .foa-reset-actions {
    gap: 6px !important;
  }
  html body #foa-simple-app .foa-account-pill {
    max-width: 108px !important;
    padding: 5px 7px !important;
    gap: 4px !important;
  }
  html body #foa-simple-app .foa-account-pill b,
  html body #foa-simple-app .foa-account-pill span,
  html body #foa-simple-app .foa-logout {
    font-size: 8px !important;
  }
  html body #foa-simple-app .foa-stable-table {
    min-height: 250px !important;
    margin-top: 7px !important;
  }
  html body #foa-simple-app .foa-stable-head,
  html body #foa-simple-app .foa-stable-row {
    grid-template-columns: .82fr 1.42fr .68fr 1.08fr !important;
    gap: 5px !important;
    padding: 8px 0 !important;
  }
  html body #foa-simple-app .foa-stable-head {
    font-size: 8px !important;
  }
  html body #foa-simple-app .foa-stable-row,
  html body #foa-simple-app .foa-stable-result {
    font-size: 9px !important;
  }
  html body #foa-simple-app .foa-stable-trade em {
    font-size: 7px !important;
    padding: 2px 4px !important;
    margin-left: 2px !important;
  }
  html body #foa-simple-app .foa-exit-mini {
    font-size: 7.5px !important;
    gap: 3px !important;
    margin-top: 2px !important;
  }
  html body #foa-simple-app .foa-exit-mini i {
    width: 16px !important;
    height: 16px !important;
    font-size: 8px !important;
  }
  html body #foa-simple-app .foa-bottom-nav {
    min-height: 52px !important;
    padding: 4px 5px calc(4px + env(safe-area-inset-bottom)) !important;
    grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
  }
  html body #foa-simple-app .foa-bottom-nav button {
    min-height: 43px !important;
    font-size: 8px !important;
    gap: 1px !important;
    padding: 3px 2px !important;
    min-width: 0 !important;
    overflow: hidden !important;
  }
  html body #foa-simple-app .foa-bottom-nav button span {
    font-size: 13px !important;
  }
  html body #foa-simple-app .foa-route-loader > div {
    min-width: 0 !important;
    width: calc(100vw - 34px) !important;
    padding: 15px !important;
    border-radius: 12px !important;
  }
  html body #foa-simple-app .foa-route-loader strong {
    font-size: 12px !important;
  }
  html body #foa-simple-app .foa-route-loader span {
    font-size: 9px !important;
  }
  html body #foa-simple-app * {
    overflow-wrap: anywhere;
  }
}

@media (max-width: 420px) {
  html body #foa-simple-app {
    font-size: 9.5px !important;
  }
  html body #foa-simple-app .foa-main {
    padding-left: 6px !important;
    padding-right: 6px !important;
  }
  html body #foa-simple-app h1,
  html body #foa-simple-app .foa-welcome-card h1 {
    font-size: 15px !important;
  }
  html body #foa-simple-app h2,
  html body #foa-simple-app .foa-card h2 {
    font-size: 11px !important;
  }
  html body #foa-simple-app .foa-balance {
    font-size: 16px !important;
  }
  html body #foa-simple-app .foa-kpi strong {
    font-size: 13px !important;
  }
  html body #foa-simple-app .foa-stable-head {
    font-size: 7.5px !important;
  }
  html body #foa-simple-app .foa-stable-row,
  html body #foa-simple-app .foa-stable-result {
    font-size: 8.5px !important;
  }
}
"""


def install_mobile_compact_ui(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/ui/dashboard-v2.css", "GET")

    @app.get("/ui/dashboard-v2.css", include_in_schema=False)
    def compact_dashboard_css() -> Response:
        source = (base_api.ROOT / "dashboard" / "dashboard-v2.css").read_text(
            encoding="utf-8"
        )
        return Response(
            source + _compact_mobile_css(),
            media_type="text/css",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-FOA-Mobile-UI-Version": UI_COMPACT_VERSION,
            },
        )

    app.state.mobile_compact_ui_installed = True
    app.state.mobile_compact_ui_version = UI_COMPACT_VERSION
    _INSTALLED = True
