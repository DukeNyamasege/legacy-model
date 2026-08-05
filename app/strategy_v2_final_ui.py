from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

import app.api as base_api
from app.dashboard_request_coalescing import (
    _headers as broker_headers,
    _script as broker_script,
)
from app.dashboard_stability_fix import _remove_route
from app.models import CandidateSignalRecord, ModelDecisionRecord, Trade
from app.strategy_v2_preferences import read_strategy
from app.strategy_v2_ui import _STRATEGY_V2_JS

_INSTALLED = False
UI_VERSION = "20260805-signal-alerts-1"
ALERT_LIFETIME_SECONDS = 180
CREATED_GRACE_SECONDS = 12

_SIGNAL_ALERT_JS = r'''
/* FOA_SIGNAL_EXECUTION_ALERTS: account-scoped killed-signal notices. */
(() => {
  "use strict";
  const VERSION = "20260805-1";
  const POLL_MS = 2500;
  const DISMISS_KEY = "foa-dismissed-execution-alert";
  let inFlight = false;
  let hideTimer = null;
  let lastId = "";

  const text = value => String(value ?? "").trim();

  function ensureStyles() {
    if (document.getElementById("foa-execution-alert-styles")) return;
    const style = document.createElement("style");
    style.id = "foa-execution-alert-styles";
    style.textContent = `
      #foa-execution-alert-host {
        position: fixed;
        top: 70px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 2147483000;
        width: min(760px, calc(100vw - 28px));
        pointer-events: none;
      }
      .foa-execution-alert {
        pointer-events: auto;
        display: grid;
        grid-template-columns: auto 1fr auto;
        gap: 12px;
        align-items: start;
        padding: 13px 14px;
        border: 1px solid rgba(248, 113, 113, .58);
        border-radius: 12px;
        background: rgba(69, 10, 10, .97);
        color: #fee2e2;
        box-shadow: 0 16px 45px rgba(0, 0, 0, .48);
        backdrop-filter: blur(12px);
        animation: foaAlertIn .22s ease-out;
      }
      .foa-execution-alert[data-severity="warning"] {
        border-color: rgba(251, 191, 36, .58);
        background: rgba(69, 38, 5, .97);
        color: #fef3c7;
      }
      .foa-execution-alert-icon {
        display: grid;
        place-items: center;
        width: 28px;
        height: 28px;
        border-radius: 999px;
        background: rgba(248, 113, 113, .18);
        font-weight: 900;
        font-size: 15px;
      }
      .foa-execution-alert[data-severity="warning"] .foa-execution-alert-icon {
        background: rgba(251, 191, 36, .18);
      }
      .foa-execution-alert-title {
        margin: 0 0 3px;
        font-size: 14px;
        line-height: 1.35;
        font-weight: 800;
        letter-spacing: .01em;
      }
      .foa-execution-alert-reason {
        margin: 0;
        font-size: 12.5px;
        line-height: 1.48;
        color: inherit;
        opacity: .96;
      }
      .foa-execution-alert-meta {
        margin-top: 7px;
        font-size: 10.5px;
        line-height: 1.35;
        opacity: .74;
      }
      .foa-execution-alert-close {
        border: 0;
        background: transparent;
        color: inherit;
        cursor: pointer;
        font-size: 21px;
        line-height: 1;
        padding: 0 2px 4px 8px;
        opacity: .78;
      }
      .foa-execution-alert-close:hover { opacity: 1; }
      @keyframes foaAlertIn {
        from { opacity: 0; transform: translateY(-10px) scale(.985); }
        to { opacity: 1; transform: translateY(0) scale(1); }
      }
      @media (max-width: 640px) {
        #foa-execution-alert-host { top: 58px; width: calc(100vw - 18px); }
        .foa-execution-alert { grid-template-columns: auto 1fr auto; padding: 11px; gap: 9px; }
        .foa-execution-alert-title { font-size: 13px; }
        .foa-execution-alert-reason { font-size: 12px; }
      }
    `;
    document.head.appendChild(style);
  }

  function host() {
    let node = document.getElementById("foa-execution-alert-host");
    if (node) return node;
    ensureStyles();
    node = document.createElement("div");
    node.id = "foa-execution-alert-host";
    node.setAttribute("aria-live", "assertive");
    node.setAttribute("aria-atomic", "true");
    document.body.appendChild(node);
    return node;
  }

  function clearAlert() {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
    const node = document.getElementById("foa-execution-alert-host");
    if (node) node.replaceChildren();
    lastId = "";
  }

  function dismiss(id) {
    try { sessionStorage.setItem(DISMISS_KEY, id); } catch (_err) {}
    clearAlert();
  }

  function dismissed(id) {
    try { return sessionStorage.getItem(DISMISS_KEY) === id; } catch (_err) { return false; }
  }

  function showAlert(alert) {
    const id = text(alert?.id);
    if (!id || dismissed(id)) {
      clearAlert();
      return;
    }
    if (id === lastId && document.querySelector(".foa-execution-alert")) return;
    lastId = id;

    const card = document.createElement("section");
    card.className = "foa-execution-alert";
    card.dataset.severity = text(alert.severity || "error");
    card.setAttribute("role", "alert");

    const icon = document.createElement("div");
    icon.className = "foa-execution-alert-icon";
    icon.textContent = "!";

    const body = document.createElement("div");
    const title = document.createElement("h3");
    title.className = "foa-execution-alert-title";
    title.textContent = text(alert.title || "Signal killed");

    const reason = document.createElement("p");
    reason.className = "foa-execution-alert-reason";
    reason.textContent = text(alert.reason || "The contract was not purchased.");

    const meta = document.createElement("div");
    meta.className = "foa-execution-alert-meta";
    const parts = [
      alert.market ? `Market ${alert.market}` : "",
      alert.contract ? `Contract ${alert.contract}` : "",
      alert.signal_short ? `Signal ${alert.signal_short}` : "",
      alert.status ? `Status ${text(alert.status).replaceAll("_", " ")}` : "",
    ].filter(Boolean);
    meta.textContent = parts.join(" • ");

    const close = document.createElement("button");
    close.type = "button";
    close.className = "foa-execution-alert-close";
    close.setAttribute("aria-label", "Dismiss execution alert");
    close.textContent = "×";
    close.addEventListener("click", () => dismiss(id));

    body.append(title, reason);
    if (parts.length) body.appendChild(meta);
    card.append(icon, body, close);
    host().replaceChildren(card);

    if (hideTimer) clearTimeout(hideTimer);
    const expiresAt = Date.parse(text(alert.expires_at));
    const remaining = Number.isFinite(expiresAt) ? Math.max(0, expiresAt - Date.now()) : 180000;
    hideTimer = setTimeout(clearAlert, remaining);
    document.body.dataset.foaSignalExecutionAlerts = VERSION;
  }

  async function sync() {
    if (inFlight) return;
    if (!document.querySelector("#foa-simple-app")) return;
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

  document.addEventListener("DOMContentLoaded", () => {
    sync();
    setInterval(sync, POLL_MS);
  }, { once: true });
  if (document.readyState !== "loading") {
    sync();
    setInterval(sync, POLL_MS);
  }
  window.FOA_SIGNAL_EXECUTION_ALERTS = VERSION;
})();
'''


_STATUS_REASONS: dict[str, str] = {
    "SKIP_NEWER_SAME_ACCOUNT_GROUP_SIGNAL": (
        "A newer signal for the same account group replaced this signal before "
        "the purchase stage. No contract was bought."
    ),
    "SKIP_TRADING_LOCK": (
        "The signal was killed because another contract cycle was still locked "
        "or settling."
    ),
    "SKIP_NO_SCOPE_ACCOUNTS": (
        "The signal was valid, but no eligible account was available in this "
        "strategy and recovery scope."
    ),
    "SKIP_UNPROFITABLE_QUOTE": (
        "The provider quote did not satisfy the strategy economics, so the "
        "contract was not purchased."
    ),
    "SKIP_AIDR_DIGIT_EDGE": (
        "The live proposal did not retain the minimum statistical edge required "
        "by the AIDR strategy."
    ),
    "SKIP_NEW_TICK": (
        "A newer market tick arrived before execution and invalidated the signal."
    ),
    "SKIP_STALE_SIGNAL": (
        "The signal expired before the purchase request could be completed."
    ),
    "SKIP_STALE": (
        "The signal expired before the purchase request could be completed."
    ),
    "SKIP_ACCOUNT_NOT_ELIGIBLE": (
        "This account was not eligible for the signal at the purchase boundary."
    ),
    "SKIP_CONTRACT_UNSUPPORTED": (
        "The selected market or contract was not verified for this account."
    ),
    "SKIP_INSUFFICIENT_BALANCE": (
        "The account balance could not safely cover the required stake."
    ),
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _matches_strategy(signal: CandidateSignalRecord, selection: Any) -> bool:
    contract_type = str(signal.contract_type or "").upper()
    trigger_name = str(signal.trigger_name or "").upper()
    barrier = str(signal.barrier or "").strip()

    if selection.family == "system":
        return contract_type == "DIGITOVER" and trigger_name.startswith("AIDR-")
    if selection.family == "digits":
        return (
            contract_type == str(selection.contract_type).upper()
            and barrier == str(selection.prediction)
            and not trigger_name.startswith("AIDR-")
        )
    return contract_type == str(selection.contract_type).upper()


def _contract_label(signal: CandidateSignalRecord) -> str:
    contract_type = str(signal.contract_type or "contract").upper()
    barrier = str(signal.barrier or "").strip()
    if barrier and contract_type in {"DIGITOVER", "DIGITUNDER", "DIGITMATCH", "DIGITDIFF"}:
        return f"{contract_type} {barrier}"
    return contract_type


def _friendly_status(status: str) -> str:
    normalized = str(status or "CREATED").strip().upper()
    return _STATUS_REASONS.get(
        normalized,
        f"The signal was stopped before purchase: {normalized.replace('_', ' ').lower()}.",
    )


def _account_was_expected(signal: CandidateSignalRecord, account_mask: str) -> bool | None:
    expected = [str(value) for value in list(signal.expected_account_masks or []) if value]
    if not expected:
        return None
    return account_mask in expected


def _candidate_alert(
    signal: CandidateSignalRecord,
    *,
    now: datetime,
    account_mask: str,
    decision: ModelDecisionRecord | None,
) -> dict[str, Any] | None:
    generated = _utc(signal.generated_timestamp) or now
    age_seconds = max(0.0, (now - generated).total_seconds())
    status = str(signal.final_status or "CREATED").strip().upper()
    expected = _account_was_expected(signal, account_mask)

    if status == "CREATED" and age_seconds < CREATED_GRACE_SECONDS:
        return None

    if expected is False:
        reason = (
            "A qualifying signal was found, but this account was not selected in "
            "the current rotating execution cohort. No contract was purchased."
        )
        severity = "warning"
    elif status.startswith(("SKIP_", "KILL", "REJECT")):
        reason = _friendly_status(status)
        severity = "error"
    elif bool(signal.stale):
        reason = "The signal became stale before this account reached the purchase stage."
        severity = "error"
        status = "SKIP_STALE_SIGNAL"
    else:
        rejection_reasons = list(decision.rejection_reasons or []) if decision else []
        if rejection_reasons:
            reason = "Model rejected the signal: " + "; ".join(
                str(value) for value in rejection_reasons[:3]
            )
            severity = "error"
            status = str(decision.final_decision or "MODEL_REJECTED").upper()
        elif signal.purchase_request_timestamp and not signal.purchase_confirmation_timestamp:
            reason = (
                "The purchase request started, but no contract confirmation was "
                "registered for this account."
            )
            severity = "error"
            status = "PURCHASE_CONFIRMATION_MISSING"
        elif signal.proposal_response_timestamp and not signal.purchase_request_timestamp:
            reason = (
                "The proposal was received, but the signal never reached the "
                "contract purchase request."
            )
            severity = "error"
            status = "PROPOSAL_NOT_PURCHASED"
        elif signal.proposal_request_timestamp and not signal.proposal_response_timestamp:
            reason = (
                "The signal requested a provider proposal, but no proposal response "
                "arrived before execution expired."
            )
            severity = "error"
            status = "PROPOSAL_RESPONSE_MISSING"
        elif bool(signal.consumed):
            reason = (
                "The execution engine consumed the signal, but no purchased contract "
                "was registered for this account."
            )
            severity = "error"
            status = "CONSUMED_WITHOUT_PURCHASE"
        else:
            reason = (
                "The signal was found but remained unconsumed and never entered the "
                "purchase pipeline."
            )
            severity = "error"
            status = "CREATED_NOT_CONSUMED"

    expires_at = generated + timedelta(seconds=ALERT_LIFETIME_SECONDS)
    if expires_at <= now:
        return None
    return {
        "id": f"{signal.signal_id}:{account_mask}:{status}",
        "severity": severity,
        "title": "Signal killed — contract not purchased",
        "reason": reason[:420],
        "signal_id": str(signal.signal_id),
        "signal_short": str(signal.signal_id)[:8],
        "market": str(signal.symbol or ""),
        "contract": _contract_label(signal),
        "status": status,
        "created_at": generated.isoformat(),
        "expires_at": expires_at.isoformat(),
        "display_seconds": ALERT_LIFETIME_SECONDS,
    }


def _script(*, compatibility: bool = False) -> str:
    source = broker_script(compatibility=compatibility)
    if "FOA_STRATEGY_V2_UI_VERSION:20260804-2" not in source:
        source += _STRATEGY_V2_JS
    if "FOA_SIGNAL_EXECUTION_ALERTS" not in source:
        source += _SIGNAL_ALERT_JS
    return source


def _headers() -> dict[str, str]:
    return {
        **broker_headers(),
        "X-FOA-Strategy-V2": "1",
        "X-FOA-Signal-Alerts": "1",
        "X-FOA-UI-Version": UI_VERSION,
    }


def install_strategy_v2_final_ui(app: Any) -> None:
    """Serve final strategy controls plus account-scoped execution alerts."""

    global _INSTALLED
    if _INSTALLED:
        return

    for path in (
        "/me/execution-alert",
        "/ui/dashboard-v2.js",
        "/ui/simplified-dashboard.js",
    ):
        _remove_route(app, path, "GET")

    @app.get("/me/execution-alert", include_in_schema=False)
    def personal_execution_alert(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")

        managed_id = int(account["id"])
        account_mask = str(account.get("account_id_masked") or "")
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=ALERT_LIFETIME_SECONDS)
        selection = read_strategy(base_api.DATABASE, managed_id)

        with base_api.DATABASE.session() as session:
            candidates = list(
                session.scalars(
                    select(CandidateSignalRecord)
                    .where(CandidateSignalRecord.generated_timestamp >= cutoff)
                    .order_by(CandidateSignalRecord.generated_timestamp.desc())
                    .limit(160)
                ).all()
            )
            candidates = [
                signal for signal in candidates if _matches_strategy(signal, selection)
            ]
            if not candidates:
                return {
                    "authenticated": True,
                    "account": account_mask,
                    "alert": None,
                    "window_seconds": ALERT_LIFETIME_SECONDS,
                }

            signal_ids = [str(signal.signal_id) for signal in candidates]
            purchased_ids = set(
                session.scalars(
                    select(Trade.signal_id)
                    .where(Trade.managed_account_id == managed_id)
                    .where(Trade.signal_id.in_(signal_ids))
                ).all()
            )
            decisions = {
                str(row.signal_id): row
                for row in session.scalars(
                    select(ModelDecisionRecord).where(
                        ModelDecisionRecord.signal_id.in_(signal_ids)
                    )
                ).all()
            }

        for signal in candidates:
            signal_id = str(signal.signal_id)
            if signal_id in purchased_ids:
                return {
                    "authenticated": True,
                    "account": account_mask,
                    "alert": None,
                    "latest_result": "PURCHASED",
                    "signal_id": signal_id,
                    "window_seconds": ALERT_LIFETIME_SECONDS,
                }
            alert = _candidate_alert(
                signal,
                now=now,
                account_mask=account_mask,
                decision=decisions.get(signal_id),
            )
            if alert is not None:
                return {
                    "authenticated": True,
                    "account": account_mask,
                    "alert": alert,
                    "window_seconds": ALERT_LIFETIME_SECONDS,
                }

        return {
            "authenticated": True,
            "account": account_mask,
            "alert": None,
            "window_seconds": ALERT_LIFETIME_SECONDS,
        }

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def final_strategy_v2_dashboard() -> Response:
        return Response(
            _script(),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def final_strategy_v2_compat() -> Response:
        return Response(
            _script(compatibility=True),
            media_type="application/javascript",
            headers=_headers(),
        )

    app.state.strategy_v2_final_ui_installed = True
    app.state.strategy_v2_final_ui_version = UI_VERSION
    app.state.signal_execution_alerts_installed = True
    _INSTALLED = True
