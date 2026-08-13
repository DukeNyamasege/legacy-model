(() => {
  "use strict";

  if (window.__FOA_FINAL_PREDICTION_UI__) return;
  window.__FOA_FINAL_PREDICTION_UI__ = true;

  const PRIMARY_PREFIX = "foa-match-prediction-mode-v3";
  const RECOVERY_PREFIX = "foa-after-loss-prediction-mode-v1";
  const LEGACY_PREFIX = "foa-match-prediction-mode-v1";
  const MODES = [
    ["last_digit", "Last digit"],
    ["most_appearing", "Most appearing"],
    ["second_most_appearing", "Second most appearing"],
  ];
  const MODE_VALUES = new Set(MODES.map(([value]) => value));
  const SIDES = new Set(["matches", "differs"]);
  let scheduled = false;

  function currentMe() {
    return window.FOA_NETLIFY_LIVE_CACHE?.me || window.FOA_BOOT_SESSION || {};
  }

  function accountIdentity() {
    const me = currentMe();
    const mode = String(me.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
    const account = String(me.account_id_masked || me.account_id || me.label || "session").trim();
    return `${mode}:${account}`;
  }

  function key(prefix) {
    return `${prefix}:${accountIdentity()}`;
  }

  function getMode(prefix) {
    try {
      const value = String(localStorage.getItem(key(prefix)) || "").trim().toLowerCase();
      return MODE_VALUES.has(value) ? value : "";
    } catch (_) {
      return "";
    }
  }

  function setMode(prefix, mode) {
    try {
      if (MODE_VALUES.has(mode)) localStorage.setItem(key(prefix), mode);
      else localStorage.removeItem(key(prefix));
      localStorage.removeItem(key(LEGACY_PREFIX));
    } catch (_) {}
  }

  function clearLegacyMode() {
    try { localStorage.removeItem(key(LEGACY_PREFIX)); } catch (_) {}
  }

  function options(selected) {
    return [...MODES, ...Array.from({ length: 10 }, (_, digit) => [String(digit), String(digit)])]
      .map(([value, label]) => `<option value="${value}" ${String(selected) === value ? "selected" : ""}>${label}</option>`)
      .join("");
  }

  function modeFromConfig(config) {
    const direct = String(config?.prediction_mode || "").trim().toLowerCase();
    if (MODE_VALUES.has(direct)) return direct;
    const nested = String(config?.reanalyze?.prediction_mode || "").trim().toLowerCase();
    if (MODE_VALUES.has(nested)) return nested;
    if (config?.prediction === null || config?.prediction === undefined) return "last_digit";
    return "";
  }

  function primarySource() {
    const input = document.querySelector('input[data-builder="trade.prediction"]');
    return { input, field: input ? (input.closest("label.field") || input.parentElement) : null };
  }

  function recoverySource() {
    const input = document.querySelector('#result-routing-section input[data-result-route="prediction"]');
    return { input, field: input ? (input.closest("label.result-routing-field") || input.parentElement) : null };
  }

  function primarySide() {
    return String(document.querySelector('select[data-builder="trade.side"]')?.value || "").toLowerCase();
  }

  function recoverySide() {
    return String(document.querySelector('#result-routing-section select[data-result-route="tradeType"]')?.value || "").toLowerCase();
  }

  function ensureSelector({ input, source, active, selectorAttr, fieldAttr, modePrefix }) {
    if (!input || !source) return;
    let field = document.querySelector(`[${fieldAttr}]`);
    if (!active) {
      if (field) field.remove();
      return;
    }

    source.hidden = true;
    source.style.setProperty("display", "none", "important");
    if (!field) {
      field = document.createElement("label");
      field.className = source.classList.contains("result-routing-field")
        ? "result-routing-field foa-final-prediction-field"
        : "field foa-final-prediction-field";
      field.setAttribute(fieldAttr.replace(/^data-/, "data-"), "true");
      source.after(field);
    }

    const mode = getMode(modePrefix);
    const fixed = String(Math.max(0, Math.min(9, Number(input.value || 0))));
    const selected = mode || fixed;
    let select = field.querySelector(`[${selectorAttr}]`);
    if (!select) {
      field.innerHTML = `<span>Prediction</span><select ${selectorAttr} aria-label="Prediction">${options(selected)}</select>`;
      select = field.querySelector(`[${selectorAttr}]`);
    }
    if (select && select.value !== selected) select.value = selected;
  }

  function enhancePrimary() {
    clearLegacyMode();
    const { input, field } = primarySource();
    if (!input || !field) return;
    const active = SIDES.has(primarySide());
    if (!active) {
      field.hidden = false;
      field.style.removeProperty("display");
      document.querySelector("[data-final-prediction-field]")?.remove();
      return;
    }
    ensureSelector({
      input,
      source: field,
      active,
      selectorAttr: "data-final-prediction",
      fieldAttr: "data-final-prediction-field",
      modePrefix: PRIMARY_PREFIX,
    });
  }

  function enhanceRecovery() {
    const { input, field } = recoverySource();
    if (!input || !field) return;
    const side = recoverySide();
    const active = SIDES.has(side);
    const needsNumeric = ["over", "under", "matches", "differs"].includes(side);
    if (!active) {
      document.querySelector("[data-after-loss-prediction-field]")?.remove();
      field.hidden = !needsNumeric;
      if (needsNumeric) field.style.removeProperty("display");
      return;
    }
    ensureSelector({
      input,
      source: field,
      active,
      selectorAttr: "data-after-loss-prediction",
      fieldAttr: "data-after-loss-prediction-field",
      modePrefix: RECOVERY_PREFIX,
    });
  }

  function enhance() {
    scheduled = false;
    enhancePrimary();
    enhanceRecovery();
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(enhance);
  }

  function hydrate(payload) {
    const config = payload?.config;
    const side = String(config?.trade_type || "").toLowerCase();
    if (SIDES.has(side)) {
      const mode = modeFromConfig(config);
      setMode(PRIMARY_PREFIX, mode);
      if (!mode && Number.isInteger(Number(config?.prediction))) {
        const { input } = primarySource();
        if (input) input.value = String(config.prediction);
      }
    }

    const route = payload?.result_routing?.after_loss;
    const routeSide = String(route?.trade_type || "").toLowerCase();
    if (SIDES.has(routeSide)) {
      const raw = String(route?.prediction || "").trim().toLowerCase();
      const mode = MODE_VALUES.has(raw) ? raw : "";
      setMode(RECOVERY_PREFIX, mode);
      if (!mode && Number.isInteger(Number(route?.prediction))) {
        const { input } = recoverySource();
        if (input) input.value = String(route.prediction);
      }
    }
    schedule();
  }

  function installFetchBridge() {
    if (window.__FOA_FINAL_PREDICTION_FETCH_BRIDGE__) return;
    window.__FOA_FINAL_PREDICTION_FETCH_BRIDGE__ = true;
    const previousFetch = window.fetch.bind(window);

    window.fetch = async (input, init = {}) => {
      const rawUrl = typeof input === "string" ? input : String(input?.url || "");
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      const custom = rawUrl.includes("/me/custom-strategy") || rawUrl.includes("/api/me/custom-strategy");
      let nextInit = init;

      if (custom && method === "POST" && typeof init.body === "string") {
        try {
          clearLegacyMode();
          const payload = JSON.parse(init.body);
          const side = String(payload?.trade_type || "").toLowerCase();
          if (SIDES.has(side)) {
            const mode = getMode(PRIMARY_PREFIX);
            const reanalyze = payload.reanalyze && typeof payload.reanalyze === "object" ? { ...payload.reanalyze } : {};
            if (mode) {
              payload.prediction = null;
              reanalyze.prediction_mode = mode;
            } else {
              delete reanalyze.prediction_mode;
              const { input: predictionInput } = primarySource();
              payload.prediction = Math.max(0, Math.min(9, Number(predictionInput?.value || payload.prediction || 0)));
            }
            payload.reanalyze = reanalyze;
          }

          const route = payload?.result_routing?.after_loss;
          const routeSide = String(route?.trade_type || "").toLowerCase();
          if (route && SIDES.has(routeSide)) {
            const mode = getMode(RECOVERY_PREFIX);
            if (mode) route.prediction = mode;
            else {
              const { input: recoveryInput } = recoverySource();
              route.prediction = Math.max(0, Math.min(9, Number(recoveryInput?.value || route.prediction || 0)));
            }
          }
          nextInit = { ...init, body: JSON.stringify(payload) };
        } catch (_) {}
      }

      const response = await previousFetch(input, nextInit);
      if (custom && method === "GET" && response.ok) {
        try { hydrate(await response.clone().json()); } catch (_) {}
      }
      return response;
    };
  }

  installFetchBridge();

  document.addEventListener("change", (event) => {
    const primary = event.target?.closest?.("[data-final-prediction]");
    if (primary) {
      const value = String(primary.value || "");
      const { input } = primarySource();
      if (MODE_VALUES.has(value)) setMode(PRIMARY_PREFIX, value);
      else {
        setMode(PRIMARY_PREFIX, "");
        if (input) {
          input.value = String(Math.max(0, Math.min(9, Number(value || 0))));
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      window.setTimeout(schedule, 0);
      return;
    }

    const recovery = event.target?.closest?.("[data-after-loss-prediction]");
    if (recovery) {
      const value = String(recovery.value || "");
      const { input } = recoverySource();
      if (MODE_VALUES.has(value)) setMode(RECOVERY_PREFIX, value);
      else {
        setMode(RECOVERY_PREFIX, "");
        if (input) {
          input.value = String(Math.max(0, Math.min(9, Number(value || 0))));
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      window.setTimeout(schedule, 0);
      return;
    }

    if (event.target?.matches?.('select[data-builder="trade.side"],select[data-result-route="tradeType"],[data-mode],[data-mobile-mode]')) {
      window.setTimeout(schedule, 0);
    }
  }, true);

  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("pageshow", schedule);
  window.addEventListener("focus", schedule);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", schedule, { once: true })
    : schedule();

  window.FOA_FINAL_PREDICTION_VERSION = "20260813-4";
})();
