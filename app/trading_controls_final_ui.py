from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_stability_fix import _remove_route
from app.strategy_v2_final_ui import _headers as strategy_headers
from app.strategy_v2_final_ui import _script as strategy_script


_INSTALLED = False
UI_VERSION = "20260807-trade-kpis-manual-martingale-v2"

_EXTRA_JS = r'''

/* FOA_TRADE_OUTCOME_KPIS_AND_MANUAL_MARTINGALE_V2 */
(() => {
  "use strict";
  const VERSION = "20260807-2";
  let policyPayload = null;
  let policyLoading = false;
  let policySaving = false;

  const text = value => String(value ?? "").trim().replace(/\s+/g, " ");
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
    if (document.getElementById("foa-trade-manual-martingale-v2-css")) return;
    const style = document.createElement("style");
    style.id = "foa-trade-manual-martingale-v2-css";
    style.textContent = `
      #custom-martingale-card{display:none!important}
      .foa-kpis.foa-kpis-compact.foa-six-trade-kpis{grid-template-columns:repeat(6,minmax(0,1fr))!important}
      .foa-outcome-kpi strong{font-variant-numeric:tabular-nums}
      .foa-manual-martingale-v2{margin-top:14px}
      .foa-mm-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px}
      .foa-mm-head h2{margin:3px 0 4px;font-size:16px}.foa-mm-head p{margin:0;color:var(--muted,#64748b);font-size:11px;line-height:1.5}
      .foa-mm-badge{white-space:nowrap;padding:6px 9px;border:1px solid var(--line,#dbe3ef);border-radius:999px;font-size:9px;font-weight:800;color:var(--muted,#64748b)}
      .foa-mm-options{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
      .foa-mm-option{position:relative;display:block;padding:13px;border:1px solid var(--line,#dbe3ef);border-radius:13px;background:var(--card,#fff);cursor:pointer;min-height:105px}
      .foa-mm-option.active{border-color:var(--blue,#2563eb);box-shadow:0 0 0 3px rgba(47,115,255,.09)}
      .foa-mm-option input{position:absolute;opacity:0;pointer-events:none}.foa-mm-option strong{display:block;font-size:12px}.foa-mm-option small{display:block;margin-top:5px;color:var(--muted,#64748b);font-size:9.5px;line-height:1.45}
      .foa-mm-detail{display:grid;grid-template-columns:minmax(0,1fr) 210px;gap:14px;align-items:center;margin-top:12px;padding:12px;border:1px solid var(--line,#dbe3ef);border-radius:12px;background:rgba(47,115,255,.045)}
      .foa-mm-detail[hidden]{display:none!important}.foa-mm-detail b{display:block;font-size:11px}.foa-mm-detail p{margin:4px 0 0;color:var(--muted,#64748b);font-size:9.5px;line-height:1.45}
      .foa-mm-detail input,.foa-mm-detail select{width:100%;height:40px;border:1px solid var(--line,#dbe3ef);border-radius:10px;background:var(--card,#fff);color:var(--ink,#111827);font-weight:800;padding:0 10px;outline:none}
      .foa-mm-quick{display:flex;gap:6px;margin-top:7px}.foa-mm-quick button{flex:1;min-height:28px;border:1px solid var(--line,#dbe3ef);border-radius:8px;background:var(--card,#fff);color:var(--ink,#111827);font-size:9px;font-weight:800;cursor:pointer}
      .foa-mm-status{margin-top:12px;padding:10px 12px;border-radius:10px;background:rgba(47,115,255,.06);color:var(--muted,#64748b);font-size:9.5px;line-height:1.5}.foa-mm-status.ok{background:rgba(34,197,94,.07)}.foa-mm-status.error{background:rgba(239,68,68,.07)}
      .foa-mm-actions{display:flex;justify-content:flex-end;margin-top:12px}.foa-mm-save{min-height:38px;padding:0 16px;border:0;border-radius:10px;background:var(--blue,#2563eb);color:#fff;font-size:10px;font-weight:900;cursor:pointer}
      .foa-mm-save:disabled,.foa-mm-option.disabled{opacity:.48;cursor:not-allowed}
      .foa-mm-system-lock{padding:13px;border:1px solid rgba(34,197,94,.25);border-radius:12px;background:rgba(34,197,94,.06);color:var(--muted,#64748b);font-size:10px;line-height:1.55}
      @media(max-width:1200px){.foa-kpis.foa-kpis-compact.foa-six-trade-kpis{grid-template-columns:repeat(3,minmax(0,1fr))!important}}
      @media(max-width:820px){.foa-mm-options{grid-template-columns:1fr}.foa-mm-detail{grid-template-columns:1fr}.foa-mm-head{display:block}.foa-mm-badge{display:inline-block;margin-top:8px}}
      @media(max-width:700px){.foa-kpis.foa-kpis-compact.foa-six-trade-kpis{grid-template-columns:repeat(2,minmax(0,1fr))!important}}
    `;
    document.head.appendChild(style);
  }

  function tradeKpiContainer() {
    if (!document.querySelector(".foa-all-trades")) return null;
    return document.querySelector(".foa-kpis.foa-kpis-compact");
  }

  function cardByLabel(container, wanted) {
    return [...container.querySelectorAll(".foa-kpi")].find(card =>
      text(card.querySelector("span")?.textContent).toLowerCase() === wanted.toLowerCase()
    ) || null;
  }

  function outcomeCounts(container) {
    const rate = cardByLabel(container, "Win Rate");
    const caption = text(rate?.querySelector("small")?.textContent || "");
    const match = caption.match(/([\d,]+)\s+wins?\s*\/\s*([\d,]+)\s+loss(?:es)?/i);
    if (!match) return null;
    return {
      wins: Number(match[1].replaceAll(",", "")) || 0,
      losses: Number(match[2].replaceAll(",", "")) || 0,
    };
  }

  function makeOutcomeCard(kind, value) {
    const wins = kind === "wins";
    const article = document.createElement("article");
    article.className = `foa-kpi target foa-outcome-kpi foa-${kind}-kpi`;
    article.dataset.foaOutcomeKpi = kind;
    article.innerHTML = `<div class="foa-kpi-icon">${wins ? "✓" : "×"}</div><div><span>${wins ? "Wins" : "Losses"}</span><strong>${value.toLocaleString()}</strong><small>${wins ? "Profiting trades" : "Losing trades"}</small></div>`;
    return article;
  }

  function syncTradeOutcomeKpis() {
    const container = tradeKpiContainer();
    if (!container) return;
    const counts = outcomeCounts(container);
    if (!counts) return;
    container.classList.add("foa-six-trade-kpis");
    const total = cardByLabel(container, "Total");
    let wins = container.querySelector('[data-foa-outcome-kpi="wins"]') || makeOutcomeCard("wins", counts.wins);
    let losses = container.querySelector('[data-foa-outcome-kpi="losses"]') || makeOutcomeCard("losses", counts.losses);
    if (text(wins.querySelector("strong")?.textContent) !== counts.wins.toLocaleString()) wins.querySelector("strong").textContent = counts.wins.toLocaleString();
    if (text(losses.querySelector("strong")?.textContent) !== counts.losses.toLocaleString()) losses.querySelector("strong").textContent = counts.losses.toLocaleString();
    if (total) {
      total.insertAdjacentElement("afterend", wins);
      wins.insertAdjacentElement("afterend", losses);
    }
    document.body.dataset.foaTradeOutcomeKpis = VERSION;
  }

  function panelSignature(payload) {
    const s = payload?.settings || {};
    const q = payload?.selection || {};
    return JSON.stringify([
      q.family, q.side, q.prediction, q.label,
      s.mode, Number(s.multiplier || 2), Number(s.split_count || 2),
      Boolean(payload?.editable), Boolean(payload?.recovery_active), Number(payload?.split_remaining || 0),
    ]);
  }

  function selectedMode() {
    return document.querySelector('input[name="foa-mm-mode"]:checked')?.value || "system";
  }

  function setPolicyStatus(message, kind = "") {
    const node = document.getElementById("foa-mm-status");
    if (!node) return;
    node.className = `foa-mm-status${kind ? ` ${kind}` : ""}`;
    if (text(node.textContent) !== text(message)) node.textContent = message;
  }

  function renderPolicyDetails() {
    const mode = selectedMode();
    document.querySelectorAll(".foa-mm-option").forEach(node => {
      node.classList.toggle("active", Boolean(node.querySelector('input[name="foa-mm-mode"]')?.checked));
    });
    const multiplierBox = document.getElementById("foa-mm-multiplier-detail");
    const splitBox = document.getElementById("foa-mm-split-detail");
    if (multiplierBox) multiplierBox.hidden = mode !== "multiplier";
    if (splitBox) splitBox.hidden = mode !== "split";
    if (mode === "system") {
      setPolicyStatus("System Martingale uses the existing exact-debt calculation with the same selected manual contract. A failed real recovery still enters virtual protection.");
    } else if (mode === "multiplier") {
      const value = Number(document.getElementById("foa-mm-multiplier")?.value || 2);
      setPolicyStatus(`Custom Multiplier is currently x${value.toFixed(2)}. A winning multiplier recovery ends that Martingale cycle and returns to base stake. It does not guarantee exact debt recovery.`);
    } else {
      const parts = Math.max(1, Math.min(3, Number(document.getElementById("foa-mm-split-count")?.value || 2)));
      setPolicyStatus(`Split System Martingale targets the actual loss debt across up to ${parts} successful recovery part${parts === 1 ? "" : "s"}. Failed parts enter virtual protection and do not consume a part. Recovery ends early if the real debt is already fully repaid.`);
    }
  }

  function bindPolicyPanel(panel) {
    panel.querySelectorAll('input[name="foa-mm-mode"]').forEach(input => input.addEventListener("change", renderPolicyDetails));
    document.getElementById("foa-mm-multiplier")?.addEventListener("input", renderPolicyDetails);
    document.getElementById("foa-mm-split-count")?.addEventListener("change", renderPolicyDetails);
    panel.querySelectorAll("[data-mm-multiplier]").forEach(button => button.addEventListener("click", () => {
      const input = document.getElementById("foa-mm-multiplier");
      if (input) input.value = button.dataset.mmMultiplier || "2";
      renderPolicyDetails();
    }));
    document.getElementById("foa-mm-save")?.addEventListener("click", savePolicy);
  }

  function renderManualPolicyPanel() {
    const selector = document.getElementById("foa-strategy-selector");
    if (!selector || !policyPayload) return;
    const signature = panelSignature(policyPayload);
    let panel = document.getElementById("foa-manual-martingale-v2");
    if (panel && panel.dataset.signature === signature) return;
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "foa-manual-martingale-v2";
      panel.className = "foa-card foa-manual-martingale-v2";
      selector.insertAdjacentElement("afterend", panel);
    }

    const selection = policyPayload.selection || {};
    const settings = policyPayload.settings || {};
    const family = String(selection.family || "system");
    const applicable = Boolean(policyPayload.applicable) && family !== "system";
    if (!applicable) {
      panel.innerHTML = `<div class="foa-mm-head"><div><span class="foa-eyebrow">RECOVERY CONTROL</span><h2>Martingale</h2><p>Recovery policy for the selected strategy.</p></div><span class="foa-mm-badge">System Strategy</span></div><div class="foa-mm-system-lock"><strong>System Martingale is locked for System Strategy.</strong><br>The existing OVER 1 normal → OVER 3 first recovery → virtual OVER 4 → real OVER 4 recovery sequence stays unchanged. Manual overrides apply only to Over/Under, Even/Odd and Rise/Fall.</div>`;
      panel.dataset.signature = signature;
      return;
    }

    const editable = Boolean(policyPayload.editable);
    const disabled = editable ? "" : "disabled";
    const currentMode = ["system", "multiplier", "split"].includes(settings.mode) ? settings.mode : "system";
    const remaining = Number(policyPayload.split_remaining || 0);
    const label = selection.label || `${family} ${selection.side || ""}`.trim();
    panel.innerHTML = `
      <div class="foa-mm-head"><div><span class="foa-eyebrow">MANUAL STRATEGY RECOVERY</span><h2>Choose Martingale</h2><p>${esc(label)} keeps the same selected contract through normal, recovery and virtual modes. Only recovery stake sizing changes.</p></div><span class="foa-mm-badge">${editable ? "Stopped · editable" : "Stop AutoTrade to edit"}</span></div>
      <div class="foa-mm-options">
        <label class="foa-mm-option ${currentMode === "system" ? "active" : ""} ${editable ? "" : "disabled"}"><input type="radio" name="foa-mm-mode" value="system" ${currentMode === "system" ? "checked" : ""} ${disabled}><strong>1. System Martingale</strong><small>Existing exact-debt recovery using the selected manual contract.</small></label>
        <label class="foa-mm-option ${currentMode === "multiplier" ? "active" : ""} ${editable ? "" : "disabled"}"><input type="radio" name="foa-mm-mode" value="multiplier" ${currentMode === "multiplier" ? "checked" : ""} ${disabled}><strong>2. Custom Multiplier</strong><small>Choose your own multiplier, for example ×1.5, ×2 or ×3.</small></label>
        <label class="foa-mm-option ${currentMode === "split" ? "active" : ""} ${editable ? "" : "disabled"}"><input type="radio" name="foa-mm-mode" value="split" ${currentMode === "split" ? "checked" : ""} ${disabled}><strong>3. Split System Martingale</strong><small>Spread the actual debt target across 1, 2 or 3 successful parts.</small></label>
      </div>
      <div id="foa-mm-multiplier-detail" class="foa-mm-detail" ${currentMode === "multiplier" ? "" : "hidden"}><div><b>Multiplier</b><p>Level 1 starts after the first monetary loss. A second monetary loss uses virtual protection before the next real recovery.</p></div><div><input id="foa-mm-multiplier" type="number" min="1.10" max="10" step="0.10" value="${Number(settings.multiplier || 2).toFixed(2)}" ${disabled}><div class="foa-mm-quick"><button type="button" data-mm-multiplier="1.5" ${disabled}>×1.5</button><button type="button" data-mm-multiplier="2" ${disabled}>×2</button><button type="button" data-mm-multiplier="3" ${disabled}>×3</button></div></div></div>
      <div id="foa-mm-split-detail" class="foa-mm-detail" ${currentMode === "split" ? "" : "hidden"}><div><b>Recovery split</b><p>The split can use a recovery stake below the normal base stake, down to the provider minimum, so splitting genuinely reduces one-shot exposure.${remaining ? ` Current cycle: ${remaining} part(s) remaining.` : ""}</p></div><select id="foa-mm-split-count" ${disabled}><option value="1" ${Number(settings.split_count) === 1 ? "selected" : ""}>1 recovery part</option><option value="2" ${Number(settings.split_count || 2) === 2 ? "selected" : ""}>2 recovery parts</option><option value="3" ${Number(settings.split_count) === 3 ? "selected" : ""}>3 recovery parts</option></select></div>
      <div id="foa-mm-status" class="foa-mm-status"></div>
      <div class="foa-mm-actions"><button id="foa-mm-save" type="button" class="foa-mm-save" ${disabled}>Save Martingale</button></div>`;
    panel.dataset.signature = signature;
    bindPolicyPanel(panel);
    renderPolicyDetails();
  }

  async function loadPolicy(force = false) {
    if (policyLoading) return;
    if (!force && !document.getElementById("foa-strategy-selector")) return;
    policyLoading = true;
    try {
      policyPayload = await jsonRequest(`/me/manual-martingale?ui=${Date.now()}`);
      renderManualPolicyPanel();
    } catch (_) {
    } finally {
      policyLoading = false;
    }
  }

  async function savePolicy() {
    if (policySaving || !policyPayload?.applicable || !policyPayload?.editable) return;
    policySaving = true;
    const button = document.getElementById("foa-mm-save");
    if (button) button.disabled = true;
    setPolicyStatus("Saving Martingale…");
    try {
      const mode = selectedMode();
      const multiplier = Math.max(1.10, Math.min(10, Number(document.getElementById("foa-mm-multiplier")?.value || 2)));
      const splitCount = Math.max(1, Math.min(3, Math.trunc(Number(document.getElementById("foa-mm-split-count")?.value || 2))));
      const result = await jsonRequest("/me/manual-martingale", {
        method: "POST",
        body: JSON.stringify({ mode, multiplier, split_count: splitCount }),
      });
      policyPayload = { ...policyPayload, settings: result.settings || policyPayload.settings };
      setPolicyStatus(result.message || "Martingale saved.", "ok");
      window.setTimeout(() => loadPolicy(true), 450);
    } catch (error) {
      setPolicyStatus(String(error.message || error), "error");
      if (button) button.disabled = false;
    } finally {
      policySaving = false;
    }
  }

  function apply() {
    ensureStyles();
    syncTradeOutcomeKpis();
    if (document.getElementById("foa-strategy-selector")) {
      if (policyPayload) renderManualPolicyPanel();
      else loadPolicy(true);
    }
  }

  function boot() {
    ensureStyles();
    apply();
    const observer = new MutationObserver(() => apply());
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.setInterval(() => {
      syncTradeOutcomeKpis();
      if (document.getElementById("foa-strategy-selector")) loadPolicy(false);
    }, 2500);
  }

  document.addEventListener("click", event => {
    if (event.target.closest("[data-view='trades'], [data-view='strategy'], #foa-save-strategy, [data-control]")) {
      window.setTimeout(apply, 200);
      window.setTimeout(() => loadPolicy(true), 800);
    }
  }, true);

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
  window.FOA_TRADE_OUTCOME_KPIS_AND_MANUAL_MARTINGALE_V2 = VERSION;
})();
'''


def _script(*, compatibility: bool = False) -> str:
    source = strategy_script(compatibility=compatibility)
    if "FOA_TRADE_OUTCOME_KPIS_AND_MANUAL_MARTINGALE_V2" not in source:
        source += _EXTRA_JS
    return source


def _headers() -> dict[str, str]:
    return {
        **strategy_headers(),
        "X-FOA-Trade-Outcome-KPIs": "wins-losses-v1",
        "X-FOA-Manual-Martingale": "v2",
        "X-FOA-UI-Version": UI_VERSION,
    }


def install_trading_controls_final_ui(app: Any) -> None:
    """Install final Trades KPIs and manual-strategy Martingale controls."""

    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")
        _remove_route(app, path, "HEAD")

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def dashboard_v2_with_final_trading_controls() -> Response:
        return Response(_script(compatibility=False), media_type="application/javascript", headers=_headers())

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def simplified_dashboard_with_final_trading_controls() -> Response:
        return Response(_script(compatibility=True), media_type="application/javascript", headers=_headers())

    @app.head("/ui/dashboard-v2.js", include_in_schema=False)
    def dashboard_v2_with_final_trading_controls_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    @app.head("/ui/simplified-dashboard.js", include_in_schema=False)
    def simplified_dashboard_with_final_trading_controls_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    app.state.trading_controls_final_ui_installed = True
    app.state.dashboard_ui_version = UI_VERSION
    _INSTALLED = True
