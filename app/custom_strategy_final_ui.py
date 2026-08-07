from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_stability_fix import _remove_route
from app.trading_controls_final_ui import _headers as base_headers
from app.trading_controls_final_ui import _script as base_script


_INSTALLED = False
UI_VERSION = "20260807-custom-strategy-v1"

_CUSTOM_JS = r'''

/* FOA_CUSTOM_STRATEGY_BUILDER_V1 */
(() => {
  "use strict";
  const VERSION = "20260807-1";
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
      #foa-custom-strategy-builder{margin-top:14px;border:1px solid #d8e2f4;border-radius:14px;background:#fff;padding:16px;box-shadow:0 8px 22px rgba(15,23,42,.04)}
      .foa-custom-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px}
      .foa-custom-head h3{margin:0;color:#10213f;font-size:16px}.foa-custom-head p{margin:4px 0 0;color:#62718b;font-size:12px;line-height:1.45}
      .foa-custom-badge{font-size:11px;font-weight:800;padding:5px 9px;border-radius:999px;background:#eef4ff;color:#1d5bd8;white-space:nowrap}
      .foa-custom-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.foa-custom-field{display:flex;flex-direction:column;gap:6px}
      .foa-custom-field label,.foa-custom-label{font-size:11px;font-weight:800;color:#31415f;text-transform:uppercase;letter-spacing:.035em}
      #foa-custom-strategy-builder select,#foa-custom-strategy-builder input{width:100%;box-sizing:border-box;border:1px solid #cad6ea;border-radius:9px;background:#fff;padding:9px 10px;color:#14213d;font-size:13px}
      .foa-custom-markets{grid-column:1/-1;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin-top:1px}
      .foa-custom-market{display:flex;align-items:center;gap:6px;border:1px solid #dbe5f3;border-radius:8px;padding:7px 8px;font-size:11px;color:#34435e;background:#f9fbff}.foa-custom-market input{width:auto!important;margin:0}
      .foa-custom-conditions{grid-column:1/-1;display:flex;flex-direction:column;gap:8px}.foa-custom-condition{display:grid;grid-template-columns:1.5fr .65fr 1.2fr auto;gap:7px;align-items:end;border:1px solid #e0e7f1;border-radius:10px;padding:9px;background:#fbfcff}
      .foa-custom-and{text-align:center;color:#6d7b91;font-size:10px;font-weight:900;letter-spacing:.08em}.foa-custom-remove{border:1px solid #efb7bd;background:#fff4f5;color:#c52e3b;border-radius:8px;padding:9px 10px;font-weight:800;cursor:pointer}
      .foa-custom-actions{grid-column:1/-1;display:flex;gap:8px;align-items:center;flex-wrap:wrap}.foa-custom-add,.foa-custom-save{border:0;border-radius:9px;padding:9px 12px;font-size:12px;font-weight:800;cursor:pointer}.foa-custom-add{background:#eef4ff;color:#225ec8}.foa-custom-save{background:#1268f3;color:#fff}
      .foa-custom-preview{grid-column:1/-1;border:1px solid #d7e4f6;border-radius:10px;background:#f5f9ff;padding:10px 12px;color:#27415f;font-size:12px;line-height:1.5}.foa-custom-status{font-size:11px;color:#66758c}.foa-custom-status.error{color:#c62838}.foa-custom-status.ok{color:#18864b}
      #foa-custom-strategy-builder [disabled]{opacity:.55;cursor:not-allowed}.foa-custom-note{grid-column:1/-1;color:#69778d;font-size:11px;line-height:1.45}
      @media(max-width:760px){.foa-custom-grid{grid-template-columns:1fr}.foa-custom-markets{grid-template-columns:repeat(2,minmax(0,1fr))}.foa-custom-condition{grid-template-columns:1fr 90px}.foa-custom-condition .foa-custom-detail{grid-column:1/-1}.foa-custom-remove{grid-column:2}}
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

  function defaultDraft() {
    return {
      market_mode: "all",
      markets: [],
      trade_type: "even",
      prediction: null,
      conditions: [{ kind: "digit_parity", window: 3, parity: "odd" }],
      match: "all",
    };
  }

  function conditionText(item) {
    const n = Number(item.window || 1);
    if (item.kind === "digit_parity") return `last ${n} digit(s) are ${item.parity || "odd"}`;
    if (item.kind === "digit_compare") return `last ${n} digit(s) are ${item.operator || ">="} ${Number(item.value ?? 4)}`;
    return `last ${n} tick direction(s) are ${item.direction || "rise"}`;
  }

  function previewText() {
    if (!draft) return "";
    const target = `${String(draft.trade_type || "even").toUpperCase()}${["over","under"].includes(draft.trade_type) ? ` ${draft.prediction ?? 2}` : ""}`;
    const markets = draft.market_mode === "all" ? "ALL MARKETS" : (draft.markets || []).join(", ") || "NO MARKET SELECTED";
    return `IF ${(draft.conditions || []).map(conditionText).join(" AND ")} THEN BUY ${target} ON ${markets}`;
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

  function render() {
    ensureStyle();
    const node = host();
    if (!node || !payload) return;
    if (!draft) draft = payload.config?.configured ? structuredClone(payload.config) : defaultDraft();
    const editable = Boolean(payload.editable);
    const active = Boolean(payload.active);
    const markets = payload.supported?.markets || [];
    const types = payload.supported?.trade_types || [];
    const disabled = editable ? "" : "disabled";
    const needsPrediction = ["over", "under"].includes(draft.trade_type);
    const selected = new Set(draft.markets || []);
    const conditions = (draft.conditions || []).map((item, index) => `
      ${index ? '<div class="foa-custom-and">AND</div>' : ''}
      <div class="foa-custom-condition">
        <div class="foa-custom-field"><label>Condition</label><select data-field="kind" data-index="${index}" ${disabled}>
          <option value="digit_parity" ${item.kind === "digit_parity" ? "selected" : ""}>Last N digits are Even/Odd</option>
          <option value="digit_compare" ${item.kind === "digit_compare" ? "selected" : ""}>Last N digits compare to a digit</option>
          <option value="direction" ${item.kind === "direction" ? "selected" : ""}>Last N tick directions are Rise/Fall</option>
        </select></div>
        <div class="foa-custom-field"><label>Last N</label><input type="number" min="1" max="100" step="1" data-field="window" data-index="${index}" value="${Number(item.window || 1)}" ${disabled}></div>
        <div class="foa-custom-field foa-custom-detail"><label>Rule</label>${detailHtml(item, index, editable)}</div>
        <button type="button" class="foa-custom-remove" data-remove="${index}" ${disabled}>×</button>
      </div>
    `).join("");

    node.innerHTML = `
      <div class="foa-custom-head"><div><h3>Custom Strategy Builder</h3><p>Scan continuously and stay silent until every pattern condition matches. A System signal never triggers this strategy.</p></div><span class="foa-custom-badge">${active ? "ACTIVE CUSTOM" : "CUSTOM OPTION"}</span></div>
      <div class="foa-custom-grid">
        <div class="foa-custom-field"><label>Market scope</label><select id="foa-custom-market-mode" ${disabled}><option value="all" ${draft.market_mode === "all" ? "selected" : ""}>All Markets</option><option value="selected" ${draft.market_mode === "selected" ? "selected" : ""}>Select Markets</option></select></div>
        <div class="foa-custom-field"><label>Trade type</label><select id="foa-custom-trade-type" ${disabled}>${types.map(item => `<option value="${esc(item.value)}" ${draft.trade_type === item.value ? "selected" : ""}>${esc(item.label)}</option>`).join("")}</select></div>
        <div class="foa-custom-markets">${markets.map(symbol => `<label class="foa-custom-market"><input type="checkbox" data-market="${esc(symbol)}" ${selected.has(symbol) ? "checked" : ""} ${disabled || draft.market_mode === "all" ? "disabled" : ""}>${esc(symbol)}</label>`).join("")}</div>
        <div class="foa-custom-field" id="foa-custom-prediction-wrap" style="${needsPrediction ? "" : "display:none"}"><label>${draft.trade_type === "under" ? "Under" : "Over"} prediction</label><input id="foa-custom-prediction" type="number" min="${draft.trade_type === "under" ? 1 : 0}" max="${draft.trade_type === "under" ? 9 : 8}" step="1" value="${Number(draft.prediction ?? (draft.trade_type === "under" ? 7 : 2))}" ${disabled}></div>
        <div class="foa-custom-note">Conditions use AND. Example: last 6 digits are Odd <b>AND</b> last 3 digits are ≥ 4. Direction means every one of the last N movements is Rise or Fall.</div>
        <div class="foa-custom-conditions"><div class="foa-custom-label">Pattern conditions</div>${conditions}</div>
        <div class="foa-custom-actions"><button type="button" class="foa-custom-add" id="foa-custom-add-condition" ${disabled || (draft.conditions || []).length >= 12 ? "disabled" : ""}>+ Add condition</button><button type="button" class="foa-custom-save" id="foa-custom-save" ${disabled || saving ? "disabled" : ""}>${saving ? "Saving…" : "Save & Select Custom Strategy"}</button><span class="foa-custom-status" id="foa-custom-status">${editable ? "Stop state confirmed — configuration is editable." : `Stop AutoTrade and settle open contracts to edit. Open contracts: ${Number(payload.open_contracts || 0)}.`}</span></div>
        <div class="foa-custom-preview"><b>Rule preview:</b> ${esc(previewText())}</div>
      </div>
    `;
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
        draft = payload.config?.configured ? structuredClone(payload.config) : defaultDraft();
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

  document.addEventListener("change", event => {
    const root = event.target.closest("#foa-custom-strategy-builder");
    if (!root || !draft) return;
    const target = event.target;
    if (target.id === "foa-custom-market-mode") {
      draft.market_mode = target.value;
      markDirty(); render(); return;
    }
    if (target.id === "foa-custom-trade-type") {
      draft.trade_type = target.value;
      draft.prediction = target.value === "under" ? 7 : target.value === "over" ? 2 : null;
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
    saving = true; render();
    try {
      const result = await api("/me/custom-strategy", { method: "POST", body: JSON.stringify(draft) });
      dirty = false;
      payload = { ...payload, active: true, config: result.config, selection: result.selection };
      draft = structuredClone(result.config);
      render();
      const status = document.getElementById("foa-custom-status");
      if (status) { status.textContent = result.message || "Custom Strategy saved."; status.className = "foa-custom-status ok"; }
      window.setTimeout(() => load(true), 700);
    } catch (error) {
      const status = document.getElementById("foa-custom-status");
      if (status) { status.textContent = String(error.message || error); status.className = "foa-custom-status error"; }
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
  window.FOA_CUSTOM_STRATEGY_BUILDER_V1 = VERSION;
})();
'''


def _script(*, compatibility: bool = False) -> str:
    source = base_script(compatibility=compatibility)
    if "FOA_CUSTOM_STRATEGY_BUILDER_V1" not in source:
        source += _CUSTOM_JS
    return source


def _headers() -> dict[str, str]:
    return {
        **base_headers(),
        "X-FOA-Custom-Strategy": "v1",
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
