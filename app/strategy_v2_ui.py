from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_stability_fix import _remove_route
from app.final_virtual_history_ui import (
    _compat_script as base_compat_script,
    _dashboard_script as base_dashboard_script,
)
from app.multi_strategy_ui import _append as append_v1
from app.multi_strategy_ui import _headers as headers_v1

_INSTALLED = False
UI_VERSION = "20260804-strategy-v2-1"

_STRATEGY_V2_JS = r'''

/* FOA_STRATEGY_V2_UI_VERSION:20260804-1 */
(() => {
  "use strict";
  const VERSION = "20260804-1";
  let catalog = null;
  let serverSelection = null;
  let loading = false;
  const draftPredictions = new Map();

  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) {
      const detail = typeof body.detail === "string" ? body.detail : body.message;
      throw new Error(detail || `Request failed (${response.status})`);
    }
    return body;
  }

  function ensureStyles() {
    if (document.getElementById("foa-strategy-v2-css")) return;
    const style = document.createElement("style");
    style.id = "foa-strategy-v2-css";
    style.textContent = `
      .foa-strategy-family-grid{grid-template-columns:repeat(4,minmax(0,1fr))!important}
      .foa-prediction-box{display:grid;grid-template-columns:minmax(0,1fr) 150px;align-items:center;gap:14px;margin:4px 0 14px;padding:13px;border:1px solid var(--line);border-radius:12px;background:rgba(47,115,255,.055)}
      .foa-prediction-box b{display:block;font-size:12px}.foa-prediction-box p{margin:4px 0 0;color:var(--muted);font-size:10px;line-height:1.45}
      .foa-prediction-input{width:100%;height:42px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--text);font-size:15px;font-weight:900;text-align:center;outline:none}
      .foa-prediction-input:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(47,115,255,.1)}
      .foa-prediction-input:disabled{opacity:.48}
      .foa-system-note{margin:4px 0 14px;padding:11px 13px;border:1px solid rgba(34,197,94,.25);border-radius:12px;background:rgba(34,197,94,.07);font-size:10px;line-height:1.5;color:var(--muted)}
      .foa-system-note strong{color:var(--green)}
      @media(max-width:1050px){.foa-strategy-family-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}}
      @media(max-width:760px){.foa-strategy-family-grid{grid-template-columns:1fr!important}.foa-prediction-box{grid-template-columns:1fr;gap:8px;padding:10px}.foa-prediction-input{height:38px;font-size:13px}}
    `;
    document.head.appendChild(style);
  }

  function activeFamily() {
    return document.querySelector(".foa-strategy-family.active")?.dataset.strategyFamily
      || serverSelection?.family || "system";
  }

  function activeSide() {
    return document.querySelector(".foa-strategy-side.active")?.dataset.strategySide
      || serverSelection?.side || "system";
  }

  function sideMeta(family, side) {
    return catalog?.families?.[family]?.sides?.[side] || null;
  }

  function predictionFor(family, side) {
    if (family !== "digits") return null;
    const key = `${family}:${side}`;
    const input = document.getElementById("foa-strategy-prediction");
    if (input && input.value !== "") return Number(input.value);
    if (draftPredictions.has(key)) return Number(draftPredictions.get(key));
    if (serverSelection?.family === family && serverSelection?.side === side && serverSelection?.prediction !== null && serverSelection?.prediction !== undefined) {
      return Number(serverSelection.prediction);
    }
    return Number(sideMeta(family, side)?.prediction_default ?? (side === "over" ? 2 : 7));
  }

  function selectionLabel(family, side, prediction) {
    const familyMeta = catalog?.families?.[family];
    const meta = sideMeta(family, side);
    if (!familyMeta || !meta) return "Strategy";
    return `${familyMeta.label} · ${meta.label}${family === "digits" ? ` ${prediction}` : ""}`;
  }

  function contractLabel(family, side, prediction) {
    const contract = sideMeta(family, side)?.contract_type || "";
    return family === "digits" ? `${contract} ${prediction}` : contract;
  }

  function updateSummary(family, side, prediction) {
    const summary = document.querySelector("#foa-strategy-selector .foa-strategy-summary");
    if (!summary) return;
    const values = summary.querySelectorAll("strong");
    const meta = sideMeta(family, side) || {};
    if (values[0]) values[0].textContent = selectionLabel(family, side, prediction);
    if (values[1]) values[1].textContent = contractLabel(family, side, prediction);
    if (values[2]) values[2].textContent = family === "digits" ? contractLabel(family, side, prediction) : (meta.normal_rule || "Validated qualifying signal");
    if (values[3]) values[3].textContent = meta.recovery_rule || "Same selected contract";
  }

  function flowSteps(family, side, prediction) {
    if (family === "system") return [
      ["1", "Normal Mode", "DIGITOVER 1", "The default system analyses and selects the normal OVER 1 entry."],
      ["2", "First Recovery", "DIGITOVER 3", "The first recorded loss moves the account to the system OVER 3 recovery."],
      ["3", "Virtual Protection", "Virtual DIGITOVER 4", "After the recovery loss, qualifying OVER 4 entries are observed at $0."],
      ["4", "Real Recovery", "DIGITOVER 4", "A confirmed virtual win returns the account to one payout-sized real recovery."],
    ];
    if (family === "digits") {
      const contract = contractLabel(family, side, prediction);
      return [
        ["1", "Selected Contract", contract, "The user-selected prediction is used for every qualifying real entry."],
        ["2", "First Recovery", contract, "After the first loss, the same contract and prediction perform recovery."],
        ["3", "Virtual Protection", `Virtual ${contract}`, "After the second loss, the same contract is observed with zero financial impact."],
        ["4", "Real Recovery", contract, "After virtual confirmation, the same selected contract resumes using live payout sizing."],
      ];
    }
    if (family === "parity") {
      const contract = sideMeta(family, side)?.contract_type || side.toUpperCase();
      return [
        ["1", "Parity Scan", contract, "The selected Even or Odd contract is analysed across rolling windows."],
        ["2", "First Recovery", contract, "After the first loss, recovery keeps the exact same parity contract."],
        ["3", "Virtual Protection", `Virtual ${contract}`, "After the second loss, the same parity contract is observed at $0."],
        ["4", "Real Recovery", contract, "Virtual confirmation returns the account to the same selected parity."],
      ];
    }
    const contract = sideMeta(family, side)?.contract_type || side.toUpperCase();
    return [
      ["1", "RF-DIR5 Scan", contract, "Direction, impulse, efficiency and exhaustion filters qualify the selected side."],
      ["2", "First Recovery", contract, "After the first loss, recovery retains the exact CALL or PUT choice."],
      ["3", "Virtual Protection", `Virtual ${contract}`, "After the second loss, the same direction is observed at $0."],
      ["4", "Real Recovery", contract, "Virtual confirmation returns the account to the same selected direction."],
    ];
  }

  function updatePresentation(family, side, prediction) {
    const flow = document.querySelector(".foa-strategy-flow");
    const intro = flow?.previousElementSibling;
    if (flow && intro?.classList?.contains("foa-page-intro")) {
      const key = `v2:${family}:${side}:${prediction ?? ""}`;
      if (flow.dataset.strategyV2Key !== key) {
        flow.dataset.strategyV2Key = key;
        const h1 = intro.querySelector("h1");
        const p = intro.querySelector("p");
        if (h1) h1.textContent = selectionLabel(family, side, prediction);
        if (p) p.textContent = catalog?.families?.[family]?.description || "Account-selected strategy with isolated execution.";
        flow.innerHTML = flowSteps(family, side, prediction).map(step => `<article class="foa-card"><span class="foa-step">${esc(step[0])}</span><h2>${esc(step[1])}</h2><strong>${esc(step[2])}</strong><p>${esc(step[3])}</p></article>`).join("");
      }
    }
    const badge = document.querySelector(".foa-active-strategy-badge");
    if (badge) badge.innerHTML = `<i></i>${esc(selectionLabel(family, side, prediction))} · ${esc(contractLabel(family, side, prediction))}`;
    const buttonLabel = family === "system" ? "Start System AutoTrade" : `Start ${side.charAt(0).toUpperCase() + side.slice(1)} AutoTrade`;
    document.querySelectorAll('[data-control="start"]').forEach(button => {
      if (!button.querySelector("svg")) button.textContent = buttonLabel;
      button.setAttribute("aria-label", buttonLabel);
    });
    document.body.dataset.foaStrategyV2Family = family;
    document.body.dataset.foaStrategyV2Side = side;
    document.body.dataset.foaStrategyV2Prediction = prediction ?? "";
  }

  function injectChoiceControls(card, family, side, prediction) {
    card.querySelector(".foa-prediction-box,.foa-system-note")?.remove();
    const summary = card.querySelector(".foa-strategy-summary");
    if (!summary) return;
    if (family === "digits") {
      const meta = sideMeta(family, side) || {};
      const stopped = !card.querySelector(".foa-strategy-family:not(:disabled)") ? false : true;
      summary.insertAdjacentHTML("beforebegin", `
        <div class="foa-prediction-box">
          <div><b>Prediction digit</b><p>Choose the permanent barrier for this account. ${esc(side.toUpperCase())} ${esc(prediction)} remains unchanged during normal, recovery and virtual execution.</p></div>
          <input id="foa-strategy-prediction" class="foa-prediction-input" type="number" inputmode="numeric" step="1" min="${esc(meta.prediction_min)}" max="${esc(meta.prediction_max)}" value="${esc(prediction)}" ${stopped ? "" : "disabled"} aria-label="Prediction digit">
        </div>`);
      const input = document.getElementById("foa-strategy-prediction");
      if (input) input.oninput = () => {
        const value = Number(input.value);
        draftPredictions.set(`${family}:${side}`, value);
        const text = card.querySelector(".foa-prediction-box p");
        if (text) text.textContent = `Choose the permanent barrier for this account. ${side.toUpperCase()} ${value} remains unchanged during normal, recovery and virtual execution.`;
        updateSummary(family, side, value);
        updatePresentation(family, side, value);
      };
    } else if (family === "system") {
      summary.insertAdjacentHTML("beforebegin", `<div class="foa-system-note"><strong>Default system sequence:</strong> OVER 1 normal → OVER 3 first recovery → virtual OVER 4 after the second loss → real OVER 4 recovery.</div>`);
    }
  }

  async function saveV2(event) {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    const family = activeFamily();
    const side = activeSide();
    const prediction = predictionFor(family, side);
    const button = document.getElementById("foa-save-strategy");
    const message = document.getElementById("foa-strategy-message");
    if (button) button.disabled = true;
    if (message) { message.className = "foa-strategy-message"; message.textContent = "Saving strategy…"; }
    try {
      const result = await jsonRequest("/me/strategy-settings", {
        method: "POST",
        body: JSON.stringify({ family, side, prediction }),
      });
      serverSelection = result.selection;
      if (message) { message.className = "foa-strategy-message ok"; message.textContent = result.message || "Strategy saved."; }
      window.setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      if (message) { message.className = "foa-strategy-message error"; message.textContent = String(error.message || error); }
      if (button) button.disabled = false;
    }
  }

  function apply() {
    ensureStyles();
    const card = document.getElementById("foa-strategy-selector");
    if (!card || !catalog) return;
    const family = activeFamily();
    const side = activeSide();
    const prediction = predictionFor(family, side);
    injectChoiceControls(card, family, side, prediction);
    updateSummary(family, side, prediction);
    updatePresentation(family, side, prediction);
    const save = document.getElementById("foa-save-strategy");
    if (save && save.dataset.strategyV2Bound !== VERSION) {
      save.dataset.strategyV2Bound = VERSION;
      save.onclick = saveV2;
    }
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const payload = await jsonRequest("/me/strategy-settings");
      catalog = payload.catalog;
      serverSelection = payload.selection;
      apply();
    } catch (_) {
      // Strategy configuration is personal and unavailable on logged-out pages.
    } finally {
      loading = false;
    }
  }

  const observer = new MutationObserver(() => apply());
  function start() {
    observer.observe(document.documentElement, { childList: true, subtree: true });
    load();
    window.setInterval(apply, 700);
    window.setInterval(load, 10000);
  }
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", start, { once: true }) : start();
  window.FOA_STRATEGY_V2_UI_VERSION = VERSION;
})();
'''


def _append_v2(source: str) -> str:
    source = append_v1(source)
    if "FOA_STRATEGY_V2_UI_VERSION:20260804-1" not in source:
        source += _STRATEGY_V2_JS
    return source


def _headers() -> dict[str, str]:
    return {
        **headers_v1(),
        "X-FOA-Strategy-V2": "1",
        "X-FOA-UI-Version": UI_VERSION,
    }


def install_strategy_v2_ui(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def strategy_v2_dashboard() -> Response:
        return Response(
            _append_v2(base_dashboard_script()),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def strategy_v2_compat() -> Response:
        return Response(
            _append_v2(base_compat_script()),
            media_type="application/javascript",
            headers=_headers(),
        )

    app.state.strategy_v2_ui_installed = True
    app.state.strategy_v2_ui_version = UI_VERSION
    _INSTALLED = True
