from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_stability_fix import _remove_route
from app.trading_controls_final_ui import _headers as base_headers
from app.trading_controls_final_ui import _script as base_script


_INSTALLED = False
UI_VERSION = "20260808-custom-strategy-builder-card-v3"

_CUSTOM_JS = r'''

/* FOA_CUSTOM_STRATEGY_BUILDER_V3 */
(() => {
  "use strict";
  const VERSION = "20260808-3";
  let payload = null;
  let draft = null;
  let loading = false;
  let saving = false;
  let dirty = false;

  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, Number(value)));

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(body.detail || body.message || `HTTP ${response.status}`);
    return body;
  }

  function ensureStyle() {
    if (document.getElementById("foa-custom-strategy-style")) return;
    const style = document.createElement("style");
    style.id = "foa-custom-strategy-style";
    style.textContent = `
      #foa-custom-strategy-builder{margin-top:16px;border:1px solid var(--line,#dce5f1);border-radius:18px;background:var(--card,#fff);overflow:hidden;box-shadow:0 18px 48px rgba(15,23,42,.07);color:var(--ink,#172033)}
      .foa-cs-hero{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding:19px 20px;background:linear-gradient(135deg,rgba(37,99,235,.10),rgba(124,58,237,.05) 52%,transparent);border-bottom:1px solid var(--line,#dce5f1)}
      .foa-cs-kicker{display:flex;align-items:center;gap:7px;margin-bottom:5px;font-size:9px;font-weight:900;letter-spacing:.13em;text-transform:uppercase;color:var(--blue,#2563eb)}
      .foa-cs-dot{width:7px;height:7px;border-radius:50%;background:var(--blue,#2563eb);box-shadow:0 0 0 4px rgba(37,99,235,.10)}
      .foa-cs-hero h2{margin:0;font-size:18px;line-height:1.25;color:var(--ink,#172033)}.foa-cs-hero p{max-width:700px;margin:6px 0 0;font-size:10.5px;line-height:1.55;color:var(--muted,#64748b)}
      .foa-cs-badge{flex:none;padding:7px 10px;border:1px solid var(--line,#dce5f1);border-radius:999px;background:var(--card,#fff);font-size:9px;font-weight:900;color:var(--muted,#64748b);white-space:nowrap}.foa-cs-badge.active{border-color:rgba(34,197,94,.35);background:rgba(34,197,94,.08);color:#16834a}
      .foa-cs-body{padding:16px;display:grid;gap:12px}
      .foa-cs-section{border:1px solid var(--line,#dce5f1);border-radius:15px;background:var(--card,#fff);padding:14px}
      .foa-cs-section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}.foa-cs-section-title{display:flex;align-items:flex-start;gap:10px}.foa-cs-step{display:grid;place-items:center;width:26px;height:26px;border-radius:9px;background:rgba(37,99,235,.09);color:var(--blue,#2563eb);font-size:10px;font-weight:900;flex:none}.foa-cs-section h3{margin:1px 0 2px;font-size:12px}.foa-cs-section p{margin:0;color:var(--muted,#64748b);font-size:9.5px;line-height:1.45}
      .foa-cs-mini{font-size:9px;font-weight:800;color:var(--muted,#64748b);white-space:nowrap}
      .foa-cs-segment{display:inline-grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);gap:4px;padding:4px;border:1px solid var(--line,#dce5f1);border-radius:11px;background:rgba(148,163,184,.07)}
      .foa-cs-segment button{min-height:34px;padding:0 13px;border:0;border-radius:8px;background:transparent;color:var(--muted,#64748b);font-size:9.5px;font-weight:850;cursor:pointer}.foa-cs-segment button.active{background:var(--card,#fff);color:var(--blue,#2563eb);box-shadow:0 2px 8px rgba(15,23,42,.08)}
      .foa-cs-market-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin-top:10px}.foa-cs-market{position:relative;display:flex;align-items:center;gap:8px;min-height:42px;padding:7px 9px;border:1px solid var(--line,#dce5f1);border-radius:10px;background:rgba(148,163,184,.035);cursor:pointer}.foa-cs-market.selected{border-color:rgba(37,99,235,.45);background:rgba(37,99,235,.06)}.foa-cs-market input{position:absolute;opacity:0;pointer-events:none}.foa-cs-check{display:grid;place-items:center;width:16px;height:16px;border:1px solid #bac8dc;border-radius:5px;font-size:10px;color:transparent;flex:none}.foa-cs-market.selected .foa-cs-check{border-color:var(--blue,#2563eb);background:var(--blue,#2563eb);color:#fff}.foa-cs-market b{display:block;font-size:9.5px}.foa-cs-market small{display:block;margin-top:2px;font-size:8px;color:var(--muted,#64748b)}
      .foa-cs-contract-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:7px}.foa-cs-trade{min-height:64px;border:1px solid var(--line,#dce5f1);border-radius:11px;background:rgba(148,163,184,.035);color:var(--ink,#172033);cursor:pointer;padding:9px 7px;text-align:left}.foa-cs-trade strong{display:block;font-size:10px}.foa-cs-trade small{display:block;margin-top:4px;font-size:8px;color:var(--muted,#64748b);line-height:1.35}.foa-cs-trade.active{border-color:rgba(37,99,235,.55);background:rgba(37,99,235,.07);box-shadow:0 0 0 2px rgba(37,99,235,.06)}
      .foa-cs-contract-settings{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}.foa-cs-control{display:flex;flex-direction:column;gap:6px}.foa-cs-control label,.foa-cs-label{font-size:8.5px;font-weight:900;letter-spacing:.06em;text-transform:uppercase;color:var(--muted,#64748b)}
      #foa-custom-strategy-builder input[type=number],#foa-custom-strategy-builder select{width:100%;height:38px;box-sizing:border-box;border:1px solid var(--line,#dce5f1);border-radius:9px;background:var(--card,#fff);color:var(--ink,#172033);padding:0 10px;font-size:10.5px;font-weight:750;outline:none}.foa-cs-control input:focus,.foa-cs-control select:focus{border-color:var(--blue,#2563eb);box-shadow:0 0 0 3px rgba(37,99,235,.08)}
      .foa-cs-help{font-size:8.5px;color:var(--muted,#64748b);line-height:1.4}.foa-cs-quick{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}.foa-cs-quick button{min-width:35px;min-height:28px;border:1px solid var(--line,#dce5f1);border-radius:8px;background:var(--card,#fff);color:var(--muted,#64748b);font-size:8.5px;font-weight:850;cursor:pointer}.foa-cs-quick button.active{border-color:var(--blue,#2563eb);background:rgba(37,99,235,.07);color:var(--blue,#2563eb)}
      .foa-cs-prediction-grid{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}.foa-cs-prediction-grid button{width:31px;height:31px;border:1px solid var(--line,#dce5f1);border-radius:50%;background:var(--card,#fff);color:var(--ink,#172033);font-size:9px;font-weight:900;cursor:pointer}.foa-cs-prediction-grid button.active{border-color:var(--blue,#2563eb);background:var(--blue,#2563eb);color:#fff}
      .foa-cs-condition-list{display:grid;gap:8px}.foa-cs-condition{position:relative;display:grid;grid-template-columns:31px 1.35fr .55fr 1.2fr 32px;gap:8px;align-items:end;padding:10px;border:1px solid var(--line,#dce5f1);border-radius:12px;background:rgba(148,163,184,.025)}.foa-cs-condition-no{display:grid;place-items:center;width:27px;height:27px;margin-bottom:5px;border-radius:8px;background:rgba(37,99,235,.08);color:var(--blue,#2563eb);font-size:9px;font-weight:900}.foa-cs-remove{height:34px;border:1px solid rgba(239,68,68,.24);border-radius:8px;background:rgba(239,68,68,.05);color:#c93745;font-weight:900;cursor:pointer}.foa-cs-and{display:flex;align-items:center;justify-content:center;gap:8px;height:15px;color:var(--muted,#64748b);font-size:8px;font-weight:900;letter-spacing:.12em}.foa-cs-and:before,.foa-cs-and:after{content:"";height:1px;flex:1;background:var(--line,#dce5f1)}
      .foa-cs-add{margin-top:9px;min-height:34px;padding:0 12px;border:1px dashed rgba(37,99,235,.45);border-radius:9px;background:rgba(37,99,235,.04);color:var(--blue,#2563eb);font-size:9px;font-weight:900;cursor:pointer}
      .foa-cs-recovery-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.foa-cs-recovery{position:relative;min-height:100px;padding:12px;border:1px solid var(--line,#dce5f1);border-radius:12px;background:rgba(148,163,184,.025);cursor:pointer}.foa-cs-recovery input{position:absolute;opacity:0;pointer-events:none}.foa-cs-recovery strong{display:block;font-size:10px}.foa-cs-recovery small{display:block;margin-top:5px;color:var(--muted,#64748b);font-size:8.5px;line-height:1.45}.foa-cs-recovery.active{border-color:rgba(37,99,235,.5);background:rgba(37,99,235,.065);box-shadow:0 0 0 2px rgba(37,99,235,.05)}.foa-cs-recovery-tag{display:inline-block;margin-bottom:8px;padding:4px 6px;border-radius:6px;background:rgba(37,99,235,.09);color:var(--blue,#2563eb);font-size:7.5px;font-weight:900;letter-spacing:.05em}
      .foa-cs-recovery-detail{display:grid;grid-template-columns:minmax(0,1fr) 240px;gap:12px;align-items:center;margin-top:9px;padding:10px 11px;border:1px solid var(--line,#dce5f1);border-radius:10px;background:rgba(37,99,235,.035)}.foa-cs-recovery-detail[hidden]{display:none!important}.foa-cs-recovery-detail b{font-size:9.5px}.foa-cs-recovery-detail p{margin:3px 0 0;font-size:8.5px;color:var(--muted,#64748b);line-height:1.45}
      .foa-cs-preview{padding:12px 13px;border:1px solid rgba(37,99,235,.22);border-radius:12px;background:linear-gradient(135deg,rgba(37,99,235,.065),rgba(124,58,237,.035));font-size:9.5px;line-height:1.55;color:var(--ink,#172033)}.foa-cs-preview b{display:block;margin-bottom:4px;font-size:8px;letter-spacing:.08em;text-transform:uppercase;color:var(--blue,#2563eb)}
      .foa-cs-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px;border-top:1px solid var(--line,#dce5f1);background:rgba(148,163,184,.025)}.foa-cs-status{font-size:8.8px;color:var(--muted,#64748b);line-height:1.45}.foa-cs-status.ok{color:#16834a}.foa-cs-status.error{color:#c93745}.foa-cs-save{min-width:142px;min-height:40px;padding:0 16px;border:0;border-radius:10px;background:linear-gradient(135deg,var(--blue,#2563eb),#4f46e5);color:#fff;font-size:10px;font-weight:900;cursor:pointer;box-shadow:0 8px 18px rgba(37,99,235,.20)}
      #foa-custom-strategy-builder button:disabled,#foa-custom-strategy-builder input:disabled,#foa-custom-strategy-builder select:disabled{opacity:.48;cursor:not-allowed}
      @media(max-width:1100px){.foa-cs-market-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.foa-cs-contract-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
      @media(max-width:780px){.foa-cs-hero{padding:15px}.foa-cs-body{padding:10px}.foa-cs-market-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.foa-cs-contract-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.foa-cs-contract-settings,.foa-cs-recovery-grid,.foa-cs-recovery-detail{grid-template-columns:1fr}.foa-cs-condition{grid-template-columns:28px 1fr 82px 32px}.foa-cs-condition .foa-cs-detail{grid-column:2/5}.foa-cs-footer{align-items:stretch;flex-direction:column}.foa-cs-save{width:100%}}
      @media(max-width:480px){.foa-cs-market-grid{grid-template-columns:1fr}.foa-cs-condition{grid-template-columns:26px 1fr 32px}.foa-cs-condition .foa-cs-window{grid-column:2}.foa-cs-condition .foa-cs-detail{grid-column:2/4}.foa-cs-remove{grid-column:3;grid-row:1}.foa-cs-section{padding:11px}.foa-cs-segment{display:grid;grid-auto-flow:row;grid-template-columns:1fr 1fr;width:100%}}
    `;
    document.head.appendChild(style);
  }

  function host() {
    const selector = document.getElementById("foa-strategy-selector");
    if (!selector) return null;
    let node = document.getElementById("foa-custom-strategy-builder");
    if (!node) {
      node = document.createElement("section");
      node.id = "foa-custom-strategy-builder";
      selector.insertAdjacentElement("afterend", node);
    }
    return node;
  }

  function marketLabel(symbol) {
    const value = String(symbol || "");
    const match1s = value.match(/^1HZ(\d+)V$/);
    if (match1s) return [`Volatility ${match1s[1]} (1s)`, value];
    const matchNormal = value.match(/^R_(\d+)$/);
    if (matchNormal) return [`Volatility ${matchNormal[1]}`, value];
    return [value, value];
  }

  function defaultDuration() {
    return Number(payload?.supported?.duration?.default || 1);
  }

  function defaultMartingale() {
    return {
      mode: "system",
      multiplier: Number(payload?.supported?.martingale?.default_multiplier || 2),
      split_count: Number(payload?.supported?.martingale?.default_split_count || 2),
    };
  }

  function defaultDraft() {
    return {
      market_mode: "all",
      markets: [],
      trade_type: "even",
      prediction: null,
      duration_ticks: defaultDuration(),
      conditions: [{ kind: "digit_parity", window: 3, parity: "odd" }],
      match: "all",
      martingale: defaultMartingale(),
    };
  }

  function hydrateDraft() {
    const value = payload?.config?.configured ? structuredClone(payload.config) : defaultDraft();
    value.duration_ticks = Number.isFinite(Number(value.duration_ticks)) ? Number(value.duration_ticks) : defaultDuration();
    value.martingale = {
      ...defaultMartingale(),
      ...(payload?.martingale || {}),
      ...(value.martingale || {}),
    };
    return value;
  }

  function conditionText(item) {
    const n = Number(item.window || 1);
    if (item.kind === "digit_parity") return `last ${n} digit${n === 1 ? "" : "s"} ${n === 1 ? "is" : "are"} ${String(item.parity || "odd").toUpperCase()}`;
    if (item.kind === "digit_compare") return `last ${n} digit${n === 1 ? "" : "s"} ${n === 1 ? "is" : "are"} ${item.operator || ">="} ${Number(item.value ?? 4)}`;
    return `last ${n} tick direction${n === 1 ? "" : "s"} ${n === 1 ? "is" : "are"} ${String(item.direction || "rise").toUpperCase()}`;
  }

  function recoveryText() {
    const settings = draft?.martingale || defaultMartingale();
    if (settings.mode === "multiplier") return `CUSTOM MULTIPLIER ×${Number(settings.multiplier || 2).toFixed(2)}`;
    if (settings.mode === "split") return `SPLIT RECOVERY · ${Number(settings.split_count || 2)} PART${Number(settings.split_count || 2) === 1 ? "" : "S"}`;
    return "SYSTEM MARTINGALE · FULL RECOVERY";
  }

  function previewText() {
    if (!draft) return "";
    const target = `${String(draft.trade_type || "even").toUpperCase()}${["over","under"].includes(draft.trade_type) ? ` ${draft.prediction ?? (draft.trade_type === "under" ? 7 : 2)}` : ""}`;
    const markets = draft.market_mode === "all" ? "ALL 10 MARKETS" : (draft.markets || []).join(", ") || "NO MARKET SELECTED";
    const duration = Math.max(1, Number(draft.duration_ticks || 1));
    return `IF ${(draft.conditions || []).map(conditionText).join(" AND ")} → BUY ${target} ON ${markets} FOR ${duration} ${duration === 1 ? "TICK" : "TICKS"} · RECOVERY: ${recoveryText()}`;
  }

  function detailHtml(item, index, editable) {
    const disabled = editable ? "" : "disabled";
    if (item.kind === "digit_parity") {
      return `<select data-field="parity" data-index="${index}" ${disabled}><option value="even" ${item.parity === "even" ? "selected" : ""}>Even</option><option value="odd" ${item.parity !== "even" ? "selected" : ""}>Odd</option></select>`;
    }
    if (item.kind === "digit_compare") {
      const ops = ["<","<=","==","!=",">=",">"];
      return `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px"><select data-field="operator" data-index="${index}" ${disabled}>${ops.map(op => `<option value="${esc(op)}" ${item.operator === op ? "selected" : ""}>${esc(op)}</option>`).join("")}</select><input type="number" min="0" max="9" step="1" data-field="value" data-index="${index}" value="${Number(item.value ?? 4)}" ${disabled}></div>`;
    }
    return `<select data-field="direction" data-index="${index}" ${disabled}><option value="rise" ${item.direction !== "fall" ? "selected" : ""}>Rise</option><option value="fall" ${item.direction === "fall" ? "selected" : ""}>Fall</option></select>`;
  }

  function predictionHtml(editable) {
    if (!["over", "under"].includes(draft.trade_type)) return "";
    const isUnder = draft.trade_type === "under";
    const minimum = isUnder ? 1 : 0;
    const maximum = isUnder ? 9 : 8;
    const current = clamp(draft.prediction ?? (isUnder ? 7 : 2), minimum, maximum);
    const disabled = editable ? "" : "disabled";
    const digits = Array.from({ length: maximum - minimum + 1 }, (_, index) => minimum + index);
    return `
      <div class="foa-cs-control">
        <label>${isUnder ? "Under" : "Over"} prediction / barrier</label>
        <input id="foa-custom-prediction" type="number" min="${minimum}" max="${maximum}" step="1" value="${current}" ${disabled}>
        <div class="foa-cs-prediction-grid">${digits.map(value => `<button type="button" data-prediction="${value}" class="${Number(current) === value ? "active" : ""}" ${disabled}>${value}</button>`).join("")}</div>
      </div>
    `;
  }

  function recoveryDetailHtml(editable) {
    const settings = draft.martingale || defaultMartingale();
    const disabled = editable ? "" : "disabled";
    const multiplierMeta = payload?.supported?.martingale || {};
    const minMultiplier = Number(multiplierMeta.minimum_multiplier || 1.10);
    const maxMultiplier = Number(multiplierMeta.maximum_multiplier || 10);
    return `
      <div class="foa-cs-recovery-detail" id="foa-cs-multiplier-detail" ${settings.mode === "multiplier" ? "" : "hidden"}>
        <div><b>Custom multiplier</b><p>After a real loss, size the next qualifying custom-pattern recovery from the base stake using your multiplier. A win returns this multiplier cycle to base stake.</p></div>
        <div class="foa-cs-control"><input id="foa-cs-multiplier" type="number" min="${minMultiplier}" max="${maxMultiplier}" step="0.10" value="${Number(settings.multiplier || 2).toFixed(2)}" ${disabled}><div class="foa-cs-quick">${[1.5,2,2.5,3].map(value => `<button type="button" data-multiplier="${value}" class="${Number(settings.multiplier || 2) === value ? "active" : ""}" ${disabled}>×${value}</button>`).join("")}</div></div>
      </div>
      <div class="foa-cs-recovery-detail" id="foa-cs-split-detail" ${settings.mode === "split" ? "" : "hidden"}>
        <div><b>Split exact-debt recovery</b><p>Divide the real remaining debt across the selected number of successful recovery parts. Failed parts still enter virtual protection and do not consume a successful part.</p></div>
        <div><div class="foa-cs-label">Successful recovery parts</div><div class="foa-cs-quick">${[1,2,3].map(value => `<button type="button" data-split-count="${value}" class="${Number(settings.split_count || 2) === value ? "active" : ""}" ${disabled}>${value} part${value === 1 ? "" : "s"}</button>`).join("")}</div></div>
      </div>
    `;
  }

  function syncExternalMartingalePanel() {
    const panel = document.getElementById("foa-manual-martingale-v2");
    if (!panel) return;
    panel.style.display = (Boolean(payload?.active) || dirty) ? "none" : "";
  }

  function render() {
    ensureStyle();
    const node = host();
    if (!node || !payload) return;
    if (!draft) draft = hydrateDraft();

    const editable = Boolean(payload.editable);
    const active = Boolean(payload.active);
    const markets = payload.supported?.markets || [];
    const types = payload.supported?.trade_types || [];
    const durationMeta = payload.supported?.duration || { minimum: 1, maximum: 100, default: 1 };
    const disabled = editable ? "" : "disabled";
    const selected = new Set(draft.markets || []);
    const martingale = draft.martingale || defaultMartingale();
    const conditions = (draft.conditions || []).map((item, index) => `
      ${index ? '<div class="foa-cs-and">AND</div>' : ''}
      <div class="foa-cs-condition">
        <div class="foa-cs-condition-no">${index + 1}</div>
        <div class="foa-cs-control"><label>Pattern</label><select data-field="kind" data-index="${index}" ${disabled}>
          <option value="digit_parity" ${item.kind === "digit_parity" ? "selected" : ""}>Digits are Even / Odd</option>
          <option value="digit_compare" ${item.kind === "digit_compare" ? "selected" : ""}>Digits compare to value</option>
          <option value="direction" ${item.kind === "direction" ? "selected" : ""}>Tick direction Rise / Fall</option>
        </select></div>
        <div class="foa-cs-control foa-cs-window"><label>Last N</label><input type="number" min="1" max="100" step="1" data-field="window" data-index="${index}" value="${Number(item.window || 1)}" ${disabled}></div>
        <div class="foa-cs-control foa-cs-detail"><label>Must be</label>${detailHtml(item, index, editable)}</div>
        <button type="button" class="foa-cs-remove" data-remove="${index}" title="Remove condition" ${disabled || (draft.conditions || []).length <= 1 ? "disabled" : ""}>×</button>
      </div>
    `).join("");

    node.innerHTML = `
      <div class="foa-cs-hero">
        <div><div class="foa-cs-kicker"><span class="foa-cs-dot"></span>Custom execution strategy</div><h2>Build your own trading pattern</h2><p>Choose where to trade, the exact contract to purchase, what the market must do before entry, how many ticks the contract runs, and how losses are recovered. The bot executes only this saved rule for a Custom Strategy account.</p></div>
        <span class="foa-cs-badge ${active ? "active" : ""}">${active ? "ACTIVE STRATEGY" : "STRATEGY BUILDER"}</span>
      </div>
      <div class="foa-cs-body">
        <section class="foa-cs-section">
          <div class="foa-cs-section-head"><div class="foa-cs-section-title"><span class="foa-cs-step">1</span><div><h3>Choose markets</h3><p>Trade all supported markets or select one, two, three, or any combination.</p></div></div><span class="foa-cs-mini">${draft.market_mode === "all" ? "10 markets" : `${selected.size} selected`}</span></div>
          <div class="foa-cs-segment"><button type="button" data-market-mode="all" class="${draft.market_mode === "all" ? "active" : ""}" ${disabled}>All Markets</button><button type="button" data-market-mode="selected" class="${draft.market_mode === "selected" ? "active" : ""}" ${disabled}>Choose Markets</button></div>
          <div class="foa-cs-market-grid">${markets.map(symbol => { const [label, code] = marketLabel(symbol); const checked = selected.has(symbol); return `<label class="foa-cs-market ${checked ? "selected" : ""}"><input type="checkbox" data-market="${esc(symbol)}" ${checked ? "checked" : ""} ${disabled || draft.market_mode === "all" ? "disabled" : ""}><span class="foa-cs-check">✓</span><span><b>${esc(label)}</b><small>${esc(code)}</small></span></label>`; }).join("")}</div>
        </section>

        <section class="foa-cs-section">
          <div class="foa-cs-section-head"><div class="foa-cs-section-title"><span class="foa-cs-step">2</span><div><h3>Choose contract</h3><p>Select one exact trade type. Over and Under also expose the prediction/barrier.</p></div></div><span class="foa-cs-mini">${esc(String(draft.trade_type || "even").toUpperCase())}</span></div>
          <div class="foa-cs-contract-grid">${types.map(item => {
            const descriptions = { even:"Last digit is even", odd:"Last digit is odd", over:"Last digit is over barrier", under:"Last digit is under barrier", rise:"Exit above entry", fall:"Exit below entry" };
            return `<button type="button" class="foa-cs-trade ${draft.trade_type === item.value ? "active" : ""}" data-trade-type="${esc(item.value)}" ${disabled}><strong>${esc(item.label)}</strong><small>${esc(descriptions[item.value] || item.contract_type || "")}</small></button>`;
          }).join("")}</div>
          <div class="foa-cs-contract-settings">
            ${predictionHtml(editable)}
            <div class="foa-cs-control"><label>Contract duration (ticks)</label><input id="foa-custom-duration" type="number" inputmode="numeric" min="${Number(durationMeta.minimum || 1)}" max="${Number(durationMeta.maximum || 100)}" step="1" value="${Number(draft.duration_ticks || durationMeta.default || 1)}" ${disabled}><span class="foa-cs-help">This is how long the purchased/virtual contract runs after entry. It is separate from Last N pattern lookback.</span><div class="foa-cs-quick">${[1,2,3,5,10].map(value => `<button type="button" data-duration="${value}" class="${Number(draft.duration_ticks) === value ? "active" : ""}" ${disabled}>${value}t</button>`).join("")}</div></div>
          </div>
        </section>

        <section class="foa-cs-section">
          <div class="foa-cs-section-head"><div class="foa-cs-section-title"><span class="foa-cs-step">3</span><div><h3>Define entry pattern</h3><p>Every condition is joined with AND. Scanning remains silent until all conditions are true on a selected market.</p></div></div><span class="foa-cs-mini">${(draft.conditions || []).length}/12 conditions</span></div>
          <div class="foa-cs-condition-list">${conditions}</div>
          <button type="button" class="foa-cs-add" id="foa-custom-add-condition" ${disabled || (draft.conditions || []).length >= 12 ? "disabled" : ""}>＋ Add another AND condition</button>
        </section>

        <section class="foa-cs-section">
          <div class="foa-cs-section-head"><div class="foa-cs-section-title"><span class="foa-cs-step">4</span><div><h3>Choose recovery / Martingale</h3><p>This recovery policy belongs to this Custom Strategy. Recovery still waits for your custom entry pattern to qualify again.</p></div></div><span class="foa-cs-mini">${esc(recoveryText())}</span></div>
          <div class="foa-cs-recovery-grid">
            <label class="foa-cs-recovery ${martingale.mode === "system" ? "active" : ""}"><input type="radio" name="foa-custom-mm" value="system" ${martingale.mode === "system" ? "checked" : ""} ${disabled}><span class="foa-cs-recovery-tag">FULL</span><strong>System Martingale</strong><small>Target the full remaining real debt on the next qualifying real recovery entry using the existing exact-debt calculation. Virtual protection remains active after failed recovery.</small></label>
            <label class="foa-cs-recovery ${martingale.mode === "multiplier" ? "active" : ""}"><input type="radio" name="foa-custom-mm" value="multiplier" ${martingale.mode === "multiplier" ? "checked" : ""} ${disabled}><span class="foa-cs-recovery-tag">CUSTOM</span><strong>Custom Multiplier</strong><small>Choose your own stake multiplier such as ×1.5, ×2 or ×3 for the next qualifying recovery trade.</small></label>
            <label class="foa-cs-recovery ${martingale.mode === "split" ? "active" : ""}"><input type="radio" name="foa-custom-mm" value="split" ${martingale.mode === "split" ? "checked" : ""} ${disabled}><span class="foa-cs-recovery-tag">SPLIT</span><strong>Split Recovery</strong><small>Spread the actual remaining debt across 1, 2 or 3 successful recovery parts instead of one larger recovery trade.</small></label>
          </div>
          ${recoveryDetailHtml(editable)}
        </section>

        <div class="foa-cs-preview"><b>Bot execution rule</b>${esc(previewText())}</div>
      </div>
      <div class="foa-cs-footer"><span class="foa-cs-status" id="foa-custom-status">${editable ? "Configuration is editable. Saving selects Custom Strategy; press Start afterward to begin scanning." : `Stop AutoTrade and settle all open contracts before editing. Open contracts: ${Number(payload.open_contracts || 0)}.`}</span><button type="button" class="foa-cs-save" id="foa-custom-save" data-legacy-label="Save & Select Custom Strategy" ${disabled || saving ? "disabled" : ""}>${saving ? "Saving…" : "Save Strategy"}</button></div>
    `;
    syncExternalMartingalePanel();
  }

  function markDirty() { dirty = true; }

  async function load(force = false) {
    if (loading || saving) return;
    if (!force && dirty) return;
    if (!document.getElementById("foa-strategy-selector")) return;
    loading = true;
    try {
      payload = await api(`/me/custom-strategy?ts=${Date.now()}`);
      if (!dirty || force) {
        draft = hydrateDraft();
        dirty = false;
      }
      render();
    } catch (_) {
    } finally {
      loading = false;
    }
  }

  function resetDetail(item, kind) {
    item.kind = kind;
    delete item.parity; delete item.operator; delete item.value; delete item.direction;
    if (kind === "digit_parity") item.parity = "odd";
    else if (kind === "digit_compare") { item.operator = ">="; item.value = 4; }
    else item.direction = "rise";
  }

  function chooseTradeType(value) {
    draft.trade_type = value;
    if (value === "under") draft.prediction = 7;
    else if (value === "over") draft.prediction = 2;
    else draft.prediction = null;
    markDirty(); render();
  }

  document.addEventListener("change", event => {
    const root = event.target.closest("#foa-custom-strategy-builder");
    if (!root || !draft) return;
    const target = event.target;
    if (target.id === "foa-custom-duration") {
      draft.duration_ticks = Number(target.value);
      markDirty(); render(); return;
    }
    if (target.id === "foa-custom-prediction") {
      draft.prediction = Number(target.value);
      markDirty(); render(); return;
    }
    if (target.dataset.market) {
      const values = new Set(draft.markets || []);
      if (target.checked) values.add(target.dataset.market); else values.delete(target.dataset.market);
      draft.markets = [...values]; markDirty(); render(); return;
    }
    if (target.name === "foa-custom-mm") {
      draft.martingale.mode = target.value;
      markDirty(); render(); return;
    }
    if (target.id === "foa-cs-multiplier") {
      draft.martingale.multiplier = Number(target.value);
      markDirty(); render(); return;
    }
    const index = Number(target.dataset.index);
    const field = target.dataset.field;
    if (Number.isInteger(index) && field && draft.conditions?.[index]) {
      const item = draft.conditions[index];
      if (field === "kind") resetDetail(item, target.value);
      else if (["window", "value"].includes(field)) item[field] = Number(target.value);
      else item[field] = target.value;
      markDirty(); render();
    }
  }, true);

  document.addEventListener("click", async event => {
    const root = event.target.closest("#foa-custom-strategy-builder");
    if (!root || !draft) return;

    const marketMode = event.target.closest("[data-market-mode]");
    if (marketMode) {
      draft.market_mode = marketMode.dataset.marketMode;
      markDirty(); render(); return;
    }
    const tradeType = event.target.closest("[data-trade-type]");
    if (tradeType) { chooseTradeType(tradeType.dataset.tradeType); return; }
    const duration = event.target.closest("[data-duration]");
    if (duration) {
      draft.duration_ticks = Number(duration.dataset.duration);
      markDirty(); render(); return;
    }
    const prediction = event.target.closest("[data-prediction]");
    if (prediction) {
      draft.prediction = Number(prediction.dataset.prediction);
      markDirty(); render(); return;
    }
    const multiplier = event.target.closest("[data-multiplier]");
    if (multiplier) {
      draft.martingale.mode = "multiplier";
      draft.martingale.multiplier = Number(multiplier.dataset.multiplier);
      markDirty(); render(); return;
    }
    const split = event.target.closest("[data-split-count]");
    if (split) {
      draft.martingale.mode = "split";
      draft.martingale.split_count = Number(split.dataset.splitCount);
      markDirty(); render(); return;
    }
    const recovery = event.target.closest(".foa-cs-recovery");
    if (recovery) {
      const input = recovery.querySelector('input[name="foa-custom-mm"]');
      if (input && !input.disabled) {
        draft.martingale.mode = input.value;
        markDirty(); render();
      }
      return;
    }
    const remove = event.target.closest("[data-remove]");
    if (remove) {
      const index = Number(remove.dataset.remove);
      if (draft.conditions.length > 1) draft.conditions.splice(index, 1);
      markDirty(); render(); return;
    }
    if (event.target.closest("#foa-custom-add-condition")) {
      if (draft.conditions.length < 12) draft.conditions.push({ kind: "digit_parity", window: 1, parity: "odd" });
      markDirty(); render(); return;
    }
    if (!event.target.closest("#foa-custom-save") || saving) return;

    if (draft.market_mode === "selected" && !(draft.markets || []).length) {
      const status = document.getElementById("foa-custom-status");
      if (status) { status.textContent = "Select at least one market or choose All Markets."; status.className = "foa-cs-status error"; }
      return;
    }

    saving = true; render();
    try {
      const result = await api("/me/custom-strategy", {
        method: "POST",
        body: JSON.stringify({ ...draft, martingale: draft.martingale }),
      });
      dirty = false;
      payload = {
        ...payload,
        active: true,
        config: result.config,
        martingale: result.martingale,
        selection: result.selection,
      };
      draft = hydrateDraft();
      render();
      const status = document.getElementById("foa-custom-status");
      if (status) { status.textContent = result.message || "Custom Strategy saved."; status.className = "foa-cs-status ok"; }
      window.setTimeout(() => load(true), 700);
    } catch (error) {
      const status = document.getElementById("foa-custom-status");
      if (status) { status.textContent = String(error.message || error); status.className = "foa-cs-status error"; }
    } finally {
      saving = false;
      render();
    }
  }, true);

  function boot() {
    ensureStyle();
    load(true);
    window.setInterval(() => load(false), 2500);
    document.addEventListener("click", event => {
      if (event.target.closest("[data-view='strategy'], #foa-save-strategy, [data-control]")) {
        window.setTimeout(() => load(true), 350);
      }
    }, true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
  window.FOA_CUSTOM_STRATEGY_BUILDER_V3 = VERSION;
  window.FOA_CUSTOM_STRATEGY_BUILDER_V2 = VERSION;
  window.FOA_CUSTOM_STRATEGY_BUILDER_V1 = VERSION;
})();
'''


def _script(*, compatibility: bool = False) -> str:
    source = base_script(compatibility=compatibility)
    if "FOA_CUSTOM_STRATEGY_BUILDER_V3" not in source:
        source += _CUSTOM_JS
    return source


def _headers() -> dict[str, str]:
    return {
        **base_headers(),
        "X-FOA-Custom-Strategy": "v3",
        "X-FOA-Custom-Strategy-Card": "complete-builder-v1",
        "X-FOA-UI-Version": UI_VERSION,
    }


def install_custom_strategy_final_ui(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")
        _remove_route(app, path, "HEAD")

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def custom_strategy_dashboard_js() -> Response:
        return Response(
            _script(compatibility=False),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def custom_strategy_simplified_js() -> Response:
        return Response(
            _script(compatibility=True),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.head("/ui/dashboard-v2.js", include_in_schema=False)
    def custom_strategy_dashboard_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    @app.head("/ui/simplified-dashboard.js", include_in_schema=False)
    def custom_strategy_simplified_head() -> Response:
        return Response(content=b"", media_type="application/javascript", headers=_headers())

    app.state.custom_strategy_final_ui_installed = True
    app.state.dashboard_ui_version = UI_VERSION
    _INSTALLED = True
