from __future__ import annotations

from typing import Any

from fastapi.responses import Response

import app.final_execution_alert_api as final_alert_api
import app.strategy_v2_final_ui as original_ui
from app.dashboard_request_coalescing import (
    _headers as broker_headers,
    _script as broker_script,
)
from app.dashboard_stability_fix import _remove_route
from app.models import CandidateSignalRecord, ModelDecisionRecord
from app.strategy_v2_ui import _STRATEGY_V2_JS


_INSTALLED = False
UI_VERSION = "20260805-signal-alerts-2"

# These are scanning or ranking outcomes. They mean the market did not finish
# qualifying and must never be presented to a trader as a killed entry.
_NON_ACTIONABLE_STATUSES = {
    "CREATED",
    "SKIP_AIDR_DIGIT_EDGE",
    "SKIP_UNPROFITABLE_QUOTE",
    "SKIP_MARKET_ARBITRATION",
    "SKIP_MARKET_ARBITRATION_WITHIN_ACCOUNT_GROUP",
    "SKIP_NEWER_SAME_ACCOUNT_GROUP_SIGNAL",
    "SKIP_NEWER_STRATEGY_SIGNAL",
    "SKIP_MULTI_STRATEGY_EDGE",
    "SKIP_AIDR_ROLE_FAIRNESS",
    "SKIP_AIDR_TRADE_SPACING",
    "SKIP_NEW_TICK",
    "SKIP_STALE_SIGNAL",
    "SKIP_STALE",
    "SKIP_STANDARDIZED_SIGNAL_EXPIRED",
    "MODEL_REJECTED",
}

_ACTIONABLE_STATUSES = {
    "SKIP_PROVIDER_PROPOSAL_EXCEPTION",
    "SKIP_INVALID_PROVIDER_PROPOSAL",
    "SKIP_SHARED_CLOCK_PROPOSAL_EXCEPTION",
    "SKIP_SHARED_CLOCK_INVALID_PROPOSAL",
    "SKIP_NO_SCOPE_ACCOUNTS",
    "SKIP_NO_ENABLED_ACCOUNTS",
    "SKIP_NO_ELIGIBLE_ACCOUNTS",
    "SKIP_NO_RISK_ELIGIBLE_ACCTS",
    "SKIP_TRADING_LOCK",
    "SKIP_CONTRACT_NOT_VERIFIED",
    "SKIP_CONTRACT_UNSUPPORTED",
    "SKIP_INSUFFICIENT_BALANCE",
    "PROPOSAL_RESPONSE_MISSING",
    "PROPOSAL_NOT_PURCHASED",
    "PURCHASE_CONFIRMATION_MISSING",
    "CONSUMED_WITHOUT_PURCHASE",
}

_REFINED_ALERT_JS = r'''
/* FOA_EXECUTION_ALERT_REFINED: quiet scanning, side-rail placement, permanent close. */
(() => {
  "use strict";
  if (window.FOA_EXECUTION_ALERT_REFINED) return;
  window.FOA_EXECUTION_ALERT_REFINED = "20260805-2";

  const POLL_MS = 3000;
  const DISMISS_KEY = "foa-dismissed-execution-alerts-v2";
  const DISMISS_TTL_MS = 7 * 24 * 60 * 60 * 1000;
  let inFlight = false;
  let hideTimer = null;
  let currentId = "";

  const text = value => String(value ?? "").trim();

  function dismissedMap() {
    try {
      const parsed = JSON.parse(localStorage.getItem(DISMISS_KEY) || "{}");
      const now = Date.now();
      const clean = {};
      for (const [id, timestamp] of Object.entries(parsed || {})) {
        if (Number(timestamp) > now - DISMISS_TTL_MS) clean[id] = Number(timestamp);
      }
      localStorage.setItem(DISMISS_KEY, JSON.stringify(clean));
      return clean;
    } catch (_err) {
      return {};
    }
  }

  function isDismissed(id) {
    return Boolean(dismissedMap()[id]);
  }

  function rememberDismissed(id) {
    if (!id) return;
    try {
      const values = dismissedMap();
      values[id] = Date.now();
      localStorage.setItem(DISMISS_KEY, JSON.stringify(values));
    } catch (_err) {}
  }

  function ensureStyles() {
    if (document.getElementById("foa-execution-alert-refined-styles")) return;
    const style = document.createElement("style");
    style.id = "foa-execution-alert-refined-styles";
    style.textContent = `
      #foa-execution-alert-host {
        z-index: 40;
        pointer-events: none;
        box-sizing: border-box;
      }
      #foa-execution-alert-host[data-placement="right"],
      #foa-execution-alert-host[data-placement="left"] {
        position: fixed;
        width: 300px;
        max-height: calc(100vh - 110px);
        overflow: auto;
      }
      #foa-execution-alert-host[data-placement="inline"] {
        position: relative;
        width: 100%;
        margin: 10px 0 14px;
      }
      .foa-execution-alert {
        pointer-events: auto;
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        gap: 10px;
        align-items: start;
        width: 100%;
        box-sizing: border-box;
        padding: 12px;
        border: 1px solid rgba(248, 113, 113, .58);
        border-radius: 12px;
        background: rgba(69, 10, 10, .97);
        color: #fee2e2;
        box-shadow: 0 10px 28px rgba(0, 0, 0, .34);
      }
      .foa-execution-alert[data-severity="warning"] {
        border-color: rgba(251, 191, 36, .58);
        background: rgba(69, 38, 5, .97);
        color: #fef3c7;
      }
      .foa-execution-alert-icon {
        display: grid;
        place-items: center;
        width: 27px;
        height: 27px;
        border-radius: 999px;
        background: rgba(248, 113, 113, .18);
        font-weight: 900;
      }
      .foa-execution-alert-title {
        margin: 0 0 4px;
        font-size: 13px;
        line-height: 1.35;
        font-weight: 800;
      }
      .foa-execution-alert-reason {
        margin: 0;
        font-size: 12px;
        line-height: 1.48;
        overflow-wrap: anywhere;
      }
      .foa-execution-alert-meta {
        margin-top: 7px;
        font-size: 10px;
        line-height: 1.4;
        opacity: .76;
        overflow-wrap: anywhere;
      }
      .foa-execution-alert-close {
        border: 0;
        background: transparent;
        color: inherit;
        cursor: pointer;
        font-size: 21px;
        line-height: 1;
        padding: 0 1px 5px 6px;
        opacity: .82;
      }
      .foa-execution-alert-close:hover { opacity: 1; }
      @media (max-width: 900px) {
        #foa-execution-alert-host[data-placement] {
          position: relative;
          width: 100%;
          max-height: none;
          overflow: visible;
          margin: 10px 0 14px;
          left: auto !important;
          right: auto !important;
          top: auto !important;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function appNode() {
    return document.querySelector("#foa-simple-app");
  }

  function contentNode() {
    const app = appNode();
    if (!app) return null;
    return app.querySelector("main, [role='main'], .foa-main, .dashboard-main") || app;
  }

  function host() {
    let node = document.getElementById("foa-execution-alert-host");
    if (!node) {
      ensureStyles();
      node = document.createElement("aside");
      node.id = "foa-execution-alert-host";
      node.setAttribute("aria-live", "assertive");
      node.setAttribute("aria-atomic", "true");
      document.body.appendChild(node);
    }
    return node;
  }

  function placeHost() {
    const node = host();
    const content = contentNode();
    if (!content) return;

    const rect = content.getBoundingClientRect();
    const width = 300;
    const gap = 14;
    const rightSpace = window.innerWidth - rect.right;
    const leftSpace = rect.left;

    if (window.innerWidth > 900 && rightSpace >= width + gap) {
      if (node.parentElement !== document.body) document.body.appendChild(node);
      node.dataset.placement = "right";
      node.style.right = `${Math.max(10, rightSpace - width - 8)}px`;
      node.style.left = "auto";
      node.style.top = `${Math.max(82, rect.top)}px`;
      return;
    }
    if (window.innerWidth > 900 && leftSpace >= width + gap) {
      if (node.parentElement !== document.body) document.body.appendChild(node);
      node.dataset.placement = "left";
      node.style.left = `${Math.max(10, leftSpace - width - 8)}px`;
      node.style.right = "auto";
      node.style.top = `${Math.max(82, rect.top)}px`;
      return;
    }

    node.dataset.placement = "inline";
    node.style.left = "auto";
    node.style.right = "auto";
    node.style.top = "auto";
    if (node.parentElement !== content || content.firstElementChild !== node) {
      content.prepend(node);
    }
  }

  function clearAlert() {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = null;
    const node = document.getElementById("foa-execution-alert-host");
    if (node) node.replaceChildren();
    currentId = "";
  }

  function dismiss(id) {
    rememberDismissed(id);
    clearAlert();
  }

  function showAlert(alert) {
    const id = text(alert?.id);
    if (!id || isDismissed(id)) {
      clearAlert();
      return;
    }
    if (id === currentId && document.querySelector(".foa-execution-alert")) return;
    currentId = id;

    const card = document.createElement("section");
    card.className = "foa-execution-alert";
    card.dataset.severity = text(alert.severity || "error");
    card.dataset.alertId = id;
    card.setAttribute("role", "alert");

    const icon = document.createElement("div");
    icon.className = "foa-execution-alert-icon";
    icon.textContent = "!";

    const body = document.createElement("div");
    const title = document.createElement("h3");
    title.className = "foa-execution-alert-title";
    title.textContent = text(alert.title || "Qualified signal not purchased");

    const reason = document.createElement("p");
    reason.className = "foa-execution-alert-reason";
    reason.textContent = text(alert.reason || "The qualified contract was not purchased.");

    const meta = document.createElement("div");
    meta.className = "foa-execution-alert-meta";
    meta.textContent = [
      alert.market ? `Market ${alert.market}` : "",
      alert.contract ? `Contract ${alert.contract}` : "",
      alert.signal_short ? `Signal ${alert.signal_short}` : "",
      alert.status ? `Status ${text(alert.status).replaceAll("_", " ")}` : "",
    ].filter(Boolean).join(" • ");

    const close = document.createElement("button");
    close.type = "button";
    close.className = "foa-execution-alert-close";
    close.setAttribute("aria-label", "Dismiss this execution alert permanently");
    close.textContent = "×";
    close.addEventListener("click", () => dismiss(id));

    body.append(title, reason);
    if (meta.textContent) body.appendChild(meta);
    card.append(icon, body, close);
    host().replaceChildren(card);
    placeHost();

    if (hideTimer) clearTimeout(hideTimer);
    const expiresAt = Date.parse(text(alert.expires_at));
    const remaining = Number.isFinite(expiresAt)
      ? Math.max(0, expiresAt - Date.now())
      : 180000;
    hideTimer = setTimeout(clearAlert, remaining);
  }

  async function sync() {
    if (inFlight || !appNode()) return;
    inFlight = true;
    try {
      const response = await fetch(`/me/execution-alert?ts=${Date.now()}`, {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (response.status === 401) {
        clearAlert();
        return;
      }
      if (!response.ok) return;
      const payload = await response.json();
      if (!payload?.alert) {
        clearAlert();
        return;
      }
      showAlert(payload.alert);
    } catch (_err) {
    } finally {
      inFlight = false;
    }
  }

  function start() {
    placeHost();
    sync();
    setInterval(sync, POLL_MS);
    window.addEventListener("resize", placeHost, { passive: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
'''


def _has_execution_progress(signal: CandidateSignalRecord) -> bool:
    return bool(
        signal.proposal_request_timestamp
        or signal.proposal_response_timestamp
        or signal.purchase_request_timestamp
        or signal.purchase_confirmation_timestamp
        or signal.consumed
    )


def _actionable_candidate_alert(
    signal: CandidateSignalRecord,
    *,
    now: Any,
    account_mask: str,
    decision: ModelDecisionRecord | None,
) -> dict[str, Any] | None:
    status = str(signal.final_status or "CREATED").strip().upper()
    expected = original_ui._account_was_expected(signal, account_mask)

    # Cohort rotation and strategy scanning are normal operation, not errors for
    # an account that was not selected for that financial cycle.
    if expected is False:
        return None
    if status in _NON_ACTIONABLE_STATUSES:
        return None

    provider_failure = status.startswith(
        (
            "SKIP_PROVIDER_",
            "SKIP_PURCHASE_",
            "PURCHASE_",
            "REST_BULK_",
        )
    )
    if not (
        status in _ACTIONABLE_STATUSES
        or provider_failure
        or _has_execution_progress(signal)
    ):
        return None

    alert = original_ui._candidate_alert(
        signal,
        now=now,
        account_mask=account_mask,
        decision=decision,
    )
    if alert is not None:
        alert["title"] = "Qualified signal not purchased"
    return alert


def _actionable_matches_strategy(signal: CandidateSignalRecord, selection: Any) -> bool:
    status = str(signal.final_status or "CREATED").strip().upper()
    trigger = str(signal.trigger_name or "").strip().upper()
    # A failure in the common System/AIDR proposal clock blocks every selected
    # contract family before routing, so every enabled strategy must see it.
    if trigger.startswith("AIDR-") and status.startswith("SKIP_PROVIDER_"):
        return True
    return original_ui._matches_strategy(signal, selection)


def _script(*, compatibility: bool = False) -> str:
    source = broker_script(compatibility=compatibility)
    if "FOA_STRATEGY_V2_UI_VERSION:20260804-2" not in source:
        source += _STRATEGY_V2_JS
    source += _REFINED_ALERT_JS
    return source


def _headers() -> dict[str, str]:
    return {
        **broker_headers(),
        "X-FOA-Strategy-V2": "1",
        "X-FOA-Signal-Alerts": "2",
        "X-FOA-UI-Version": UI_VERSION,
    }


def install_execution_alert_refinement(app: Any) -> None:
    """Install quiet actionable alerts after every other API/UI wrapper."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_ui._candidate_alert = _actionable_candidate_alert
    original_ui._matches_strategy = _actionable_matches_strategy
    final_alert_api._candidate_alert = _actionable_candidate_alert
    final_alert_api._matches_strategy = _actionable_matches_strategy

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")
        _remove_route(app, path, "HEAD")

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def refined_dashboard_script() -> Response:
        return Response(
            _script(),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.head("/ui/dashboard-v2.js", include_in_schema=False)
    def refined_dashboard_head() -> Response:
        return Response(content=b"", headers=_headers())

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def refined_compat_script() -> Response:
        return Response(
            _script(compatibility=True),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.head("/ui/simplified-dashboard.js", include_in_schema=False)
    def refined_compat_head() -> Response:
        return Response(content=b"", headers=_headers())

    app.state.execution_alert_refinement_installed = True
    app.state.execution_alert_refinement_version = UI_VERSION
    _INSTALLED = True
