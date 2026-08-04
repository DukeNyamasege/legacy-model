from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_stability_fix import _remove_route
from app.final_virtual_history_ui import (
    _compat_script as base_compat_script,
    _dashboard_script as base_dashboard_script,
    _headers as base_headers,
)

_INSTALLED = False
UI_VERSION = "20260804-multi-strategy-1"

_MULTI_STRATEGY_JS = r'''

/* FOA_MULTI_STRATEGY_UI_VERSION:20260804-1 */
(() => {
  "use strict";
  const VERSION = "20260804-1";
  let payload = null;
  let loading = false;
  let lastLoad = 0;

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
    if (!response.ok) throw new Error(body.detail || body.message || `Request failed (${response.status})`);
    return body;
  }

  function ensureStyles() {
    if (document.getElementById("foa-multi-strategy-css")) return;
    const style = document.createElement("style");
    style.id = "foa-multi-strategy-css";
    style.textContent = `
      .foa-strategy-selector{grid-column:1/-1;overflow:hidden}
      .foa-strategy-selector .foa-card-head{align-items:flex-start}
      .foa-strategy-family-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:12px 0}
      .foa-strategy-family{appearance:none;text-align:left;min-height:112px;padding:14px;border:1px solid var(--line);border-radius:14px;background:rgba(148,163,184,.055);color:var(--text);cursor:pointer;transition:.18s ease}
      .foa-strategy-family:hover{transform:translateY(-1px);border-color:rgba(47,115,255,.45)}
      .foa-strategy-family.active{border-color:var(--blue);background:rgba(47,115,255,.12);box-shadow:0 0 0 2px rgba(47,115,255,.08) inset}
      .foa-strategy-family b{display:block;font-size:14px;margin-bottom:5px}
      .foa-strategy-family span{display:block;color:var(--muted);font-size:11px;line-height:1.45}
      .foa-strategy-family em{display:inline-block;margin-top:10px;padding:3px 7px;border-radius:999px;background:rgba(47,115,255,.12);color:var(--blue);font-size:9px;font-style:normal;font-weight:800;text-transform:uppercase}
      .foa-strategy-side-row{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 14px}
      .foa-strategy-side{appearance:none;min-width:112px;min-height:40px;padding:0 14px;border:1px solid var(--line);border-radius:10px;background:transparent;color:var(--text);font-weight:800;cursor:pointer}
      .foa-strategy-side.active{border-color:var(--green);background:rgba(34,197,94,.12);color:var(--green)}
      .foa-strategy-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;padding:11px;border:1px solid var(--line);border-radius:12px;background:rgba(148,163,184,.045);margin-bottom:13px}
      .foa-strategy-summary div{min-width:0}.foa-strategy-summary span{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.04em}.foa-strategy-summary strong{display:block;font-size:11px;margin-top:3px;overflow-wrap:anywhere}
      .foa-strategy-save-row{display:flex;align-items:center;justify-content:space-between;gap:12px}
      .foa-strategy-save-row p{margin:0;color:var(--muted);font-size:10px;line-height:1.45}
      .foa-strategy-save{min-height:40px;padding:0 16px;border:0;border-radius:10px;background:var(--blue);color:white;font-weight:900;cursor:pointer;white-space:nowrap}
      .foa-strategy-save:disabled,.foa-strategy-family:disabled,.foa-strategy-side:disabled{opacity:.48;cursor:not-allowed;transform:none}
      .foa-strategy-message{margin-top:10px;font-size:10px;font-weight:700}.foa-strategy-message.ok{color:var(--green)}.foa-strategy-message.error{color:#ef4444}
      .foa-active-strategy-badge{display:inline-flex;align-items:center;gap:6px;padding:7px 10px;border:1px solid rgba(47,115,255,.28);border-radius:999px;background:rgba(47,115,255,.10);color:var(--blue);font-size:10px;font-weight:900;white-space:nowrap}
      .foa-active-strategy-badge i{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 0 4px rgba(34,197,94,.10)}
      @media(max-width:760px){
        .foa-strategy-family-grid{grid-template-columns:1fr;gap:7px}.foa-strategy-family{min-height:76px;padding:10px;border-radius:11px}.foa-strategy-family b{font-size:11px}.foa-strategy-family span{font-size:9px}.foa-strategy-family em{font-size:7.5px;margin-top:6px}
        .foa-strategy-summary{grid-template-columns:1fr 1fr;padding:9px;gap:7px}.foa-strategy-summary span{font-size:7.5px}.foa-strategy-summary strong{font-size:9px}
        .foa-strategy-side{min-width:88px;min-height:34px;padding:0 10px;font-size:9px}.foa-strategy-save-row{align-items:stretch;flex-direction:column}.foa-strategy-save{width:100%;min-height:35px;font-size:9px}.foa-active-strategy-badge{font-size:8px;padding:5px 7px}
      }
    `;
    document.head.appendChild(style);
  }

  function currentSelection() {
    return payload?.selection || payload?.catalog?.default || { family: "digits", side: "over", contract_type: "DIGITOVER", label: "Over / Under · Over" };
  }

  function familyMeta(family) {
    return payload?.catalog?.families?.[family] || null;
  }

  function isStopped() {
    const text = `${document.body.innerText}`.toLowerCase();
    const startVisible = Boolean(document.querySelector('[data-control="start"]'));
    return startVisible || text.includes("fresh start required") || text.includes("auto trading stopped");
  }

  function selectorMarkup() {
    const selection = currentSelection();
    const families = payload?.catalog?.families || {};
    const stopped = isStopped();
    const familyButtons = Object.entries(families).map(([family, meta]) => `
      <button type="button" class="foa-strategy-family ${family === selection.family ? "active" : ""}" data-strategy-family="${esc(family)}" ${stopped ? "" : "disabled"}>
        <b>${esc(meta.label)}</b><span>${esc(meta.description)}</span><em>${esc(Object.keys(meta.sides || {}).join(" / "))}</em>
      </button>`).join("");
    const selectedFamily = familyMeta(selection.family) || {};
    const sideButtons = Object.entries(selectedFamily.sides || {}).map(([side, meta]) => `
      <button type="button" class="foa-strategy-side ${side === selection.side ? "active" : ""}" data-strategy-side="${esc(side)}" ${stopped ? "" : "disabled"}>${esc(meta.label)}</button>`).join("");
    return `
      <article class="foa-card foa-strategy-selector" id="foa-strategy-selector">
        <div class="foa-card-head"><div><span class="foa-eyebrow">ACCOUNT STRATEGY</span><h2>Choose what this account trades</h2><p>Each account follows only its selected contract family. Recovery state never crosses between strategies.</p></div><span class="foa-save-state">${stopped ? "Stopped · editable" : "Stop required"}</span></div>
        <div class="foa-strategy-family-grid">${familyButtons}</div>
        <div class="foa-strategy-side-row">${sideButtons}</div>
        <div class="foa-strategy-summary">
          <div><span>Selected</span><strong>${esc(selection.label)}</strong></div>
          <div><span>Contract</span><strong>${esc(selection.contract_type)}</strong></div>
          <div><span>Normal rule</span><strong>${esc(selection.normal_rule || "Validated qualifying signal")}</strong></div>
          <div><span>Recovery</span><strong>${esc(selection.recovery_rule || "Same strategy family")}</strong></div>
        </div>
        <div class="foa-strategy-save-row"><p>${stopped ? "Saving resets only active recovery state and keeps all historical trades." : "Press Stop AutoTrade first. Pause preserves recovery state and cannot be used for switching."}</p><button type="button" class="foa-strategy-save" id="foa-save-strategy" ${stopped ? "" : "disabled"}>Save Strategy</button></div>
        <div class="foa-strategy-message" id="foa-strategy-message"></div>
      </article>`;
  }

  function ensureSelector() {
    ensureStyles();
    const grid = document.querySelector(".foa-settings-grid");
    if (!grid || !payload?.selection) return;
    let card = document.getElementById("foa-strategy-selector");
    const markup = selectorMarkup();
    if (!card) {
      grid.insertAdjacentHTML("afterbegin", markup);
      card = document.getElementById("foa-strategy-selector");
    } else if (!card.contains(document.activeElement)) {
      card.outerHTML = markup;
      card = document.getElementById("foa-strategy-selector");
    }
    bindSelector(card);
  }

  function setDraft(family, side) {
    const normalizedFamily = family || currentSelection().family;
    const meta = familyMeta(normalizedFamily);
    if (!meta) return;
    const allowedSides = Object.keys(meta.sides || {});
    const normalizedSide = allowedSides.includes(side) ? side : allowedSides[0];
    const sideMeta = meta.sides[normalizedSide];
    payload.selection = {
      ...payload.selection,
      family: normalizedFamily,
      side: normalizedSide,
      family_label: meta.label,
      label: `${meta.label} · ${sideMeta.label}`,
      contract_type: sideMeta.contract_type,
      description: meta.description,
      normal_rule: sideMeta.normal_rule,
      recovery_rule: sideMeta.recovery_rule,
    };
    ensureSelector();
    applyStrategyPresentation();
  }

  function bindSelector(card) {
    if (!card || card.dataset.bound === VERSION) return;
    card.dataset.bound = VERSION;
    card.querySelectorAll("[data-strategy-family]").forEach(button => {
      button.onclick = () => setDraft(button.dataset.strategyFamily, null);
    });
    card.querySelectorAll("[data-strategy-side]").forEach(button => {
      button.onclick = () => setDraft(currentSelection().family, button.dataset.strategySide);
    });
    const save = card.querySelector("#foa-save-strategy");
    if (save) save.onclick = saveStrategy;
  }

  async function saveStrategy() {
    const message = document.getElementById("foa-strategy-message");
    const button = document.getElementById("foa-save-strategy");
    if (button) button.disabled = true;
    if (message) { message.className = "foa-strategy-message"; message.textContent = "Saving strategy…"; }
    try {
      const selection = currentSelection();
      const result = await jsonRequest("/me/strategy-settings", {
        method: "POST",
        body: JSON.stringify({ family: selection.family, side: selection.side }),
      });
      payload.selection = result.selection;
      if (message) { message.className = "foa-strategy-message ok"; message.textContent = result.message || "Strategy saved."; }
      window.setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      if (message) { message.className = "foa-strategy-message error"; message.textContent = String(error.message || error); }
      if (button) button.disabled = false;
    }
  }

  function ensureBadge() {
    if (!payload?.selection) return;
    const intro = document.querySelector(".foa-page-intro");
    if (!intro) return;
    let badge = intro.querySelector(".foa-active-strategy-badge");
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "foa-active-strategy-badge";
      intro.appendChild(badge);
    }
    badge.innerHTML = `<i></i>${esc(payload.selection.label)} · ${esc(payload.selection.contract_type)}`;
  }

  function strategyFlow(selection) {
    if (selection.family === "digits" && selection.side === "over") return [
      ["1", "Normal Mode", "DIGITOVER 1", "Current AIDR normal entry."],
      ["2", "First Recovery", "DIGITOVER 3", "Targets the first recorded loss."],
      ["3", "Virtual Protection", "Virtual OVER 4", "$0 confirmation after the loss trigger."],
      ["4", "Full Recovery", "Real DIGITOVER 4", "Uses live payout economics to target recorded debt."],
    ];
    if (selection.family === "digits" && selection.side === "under") return [
      ["1", "Normal Mode", "DIGITUNDER 8", "Mirrored high-probability Under entry."],
      ["2", "First Recovery", "DIGITUNDER 6", "Mirrored first-recovery condition."],
      ["3", "Virtual Protection", "Virtual UNDER 5", "$0 confirmation using the soft 52.5% gate."],
      ["4", "Full Recovery", "Real DIGITUNDER 5", "Uses the same account debt and live payout sizing."],
    ];
    if (selection.family === "parity") return [
      ["1", "Parity Scan", selection.contract_type, "20/50/100/500-window parity alignment."],
      ["2", "Proposal Check", "Live break-even", "Requires a positive edge above the current quote."],
      ["3", "Virtual Protection", `Virtual ${selection.side.toUpperCase()}`, "$0 observation while the account is protected."],
      ["4", "Recovery", `Real ${selection.side.toUpperCase()}`, "The same parity family uses live payout-based debt sizing."],
    ];
    return [
      ["1", "RF-DIR5 Scan", selection.side.toUpperCase(), "Five-move direction, impulse and exhaustion filters."],
      ["2", "Proposal Check", selection.contract_type, "One-tick CALL/PUT quote must retain a positive edge."],
      ["3", "Virtual Protection", `Virtual ${selection.side.toUpperCase()}`, "$0 directional observation after the loss trigger."],
      ["4", "Recovery", `Real ${selection.contract_type}`, "The same direction family recovers from live proposal economics."],
    ];
  }

  function applyStrategyPresentation() {
    if (!payload?.selection) return;
    ensureBadge();
    document.querySelectorAll('[data-control="start"]').forEach(button => {
      const label = payload.selection.side.charAt(0).toUpperCase() + payload.selection.side.slice(1);
      if (!button.querySelector("svg")) button.textContent = `Start ${label} AutoTrade`;
      button.setAttribute("aria-label", `Start ${label} AutoTrade`);
    });
    const flow = document.querySelector(".foa-strategy-flow");
    const intro = flow?.previousElementSibling;
    if (flow && intro?.classList?.contains("foa-page-intro")) {
      const h1 = intro.querySelector("h1");
      const p = intro.querySelector("p");
      if (h1) h1.textContent = payload.selection.label;
      if (p) p.textContent = payload.selection.description || "Account-selected strategy with isolated execution.";
      flow.innerHTML = strategyFlow(payload.selection).map(step => `<article class="foa-card"><span class="foa-step">${esc(step[0])}</span><h2>${esc(step[1])}</h2><strong>${esc(step[2])}</strong><p>${esc(step[3])}</p></article>`).join("");
    }
    document.body.dataset.foaStrategyFamily = payload.selection.family;
    document.body.dataset.foaStrategySide = payload.selection.side;
    document.body.dataset.foaMultiStrategyVersion = VERSION;
  }

  async function load(force = false) {
    if (loading) return;
    const now = Date.now();
    if (!force && now - lastLoad < 7000) return;
    loading = true;
    try {
      payload = await jsonRequest("/me/strategy-settings");
      lastLoad = Date.now();
      ensureSelector();
      applyStrategyPresentation();
    } catch (_) {
      // Logged-out public dashboard: the selector is intentionally personal only.
    } finally {
      loading = false;
    }
  }

  function apply() {
    ensureStyles();
    ensureSelector();
    applyStrategyPresentation();
    load(false);
  }

  const observer = new MutationObserver(() => apply());
  function start() {
    observer.observe(document.documentElement, { childList: true, subtree: true });
    load(true);
    apply();
    window.setInterval(apply, 2500);
  }
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", start, { once: true }) : start();
  window.FOA_MULTI_STRATEGY_UI_VERSION = VERSION;
})();
'''


def _append(source: str) -> str:
    if "FOA_MULTI_STRATEGY_UI_VERSION:20260804-1" not in source:
        source += _MULTI_STRATEGY_JS
    return source


def _headers() -> dict[str, str]:
    return {
        **base_headers(),
        "X-FOA-UI-Version": UI_VERSION,
        "X-FOA-Multi-Strategy": "1",
    }


def install_multi_strategy_ui(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def multi_strategy_dashboard() -> Response:
        return Response(
            _append(base_dashboard_script()),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def multi_strategy_compat() -> Response:
        return Response(
            _append(base_compat_script()),
            media_type="application/javascript",
            headers=_headers(),
        )

    app.state.multi_strategy_ui_installed = True
    app.state.multi_strategy_ui_version = UI_VERSION
    _INSTALLED = True
