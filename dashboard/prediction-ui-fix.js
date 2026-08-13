(() => {
  "use strict";

  if (window.__FOA_FINAL_PREDICTION_UI__) return;
  window.__FOA_FINAL_PREDICTION_UI__ = true;

  const MODE_PREFIX = "foa-match-prediction-mode-v3";
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

  function key(prefix = MODE_PREFIX) {
    return `${prefix}:${accountIdentity()}`;
  }

  function getStoredMode() {
    try {
      const value = String(localStorage.getItem(key()) || "").trim().toLowerCase();
      return MODE_VALUES.has(value) ? value : "";
    } catch (_) {
      return "";
    }
  }

  function setStoredMode(mode) {
    try {
      if (MODE_VALUES.has(mode)) localStorage.setItem(key(), mode);
      else localStorage.removeItem(key());
      localStorage.removeItem(key(LEGACY_PREFIX));
    } catch (_) {}
  }

  function clearLegacyMode() {
    try { localStorage.removeItem(key(LEGACY_PREFIX)); } catch (_) {}
  }

  function sideValue() {
    return String(document.querySelector('select[data-builder="trade.side"]')?.value || "").toLowerCase();
  }

  function sourcePrediction() {
    const input = document.querySelector('input[data-builder="trade.prediction"]');
    return {
      input,
      field: input ? (input.closest("label.field") || input.parentElement) : null,
    };
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

  function hydrate(payload) {
    const config = payload?.config;
    const side = String(config?.trade_type || "").toLowerCase();
    if (!SIDES.has(side)) return;
    const mode = modeFromConfig(config);
    setStoredMode(mode);
    if (!mode && Number.isInteger(Number(config?.prediction))) {
      const { input } = sourcePrediction();
      if (input) input.value = String(config.prediction);
    }
    schedule();
  }

  function enhance() {
    scheduled = false;
    clearLegacyMode();

    const side = sideValue();
    const { input, field: source } = sourcePrediction();
    if (!input || !source) return;

    const active = SIDES.has(side);
    let finalField = document.querySelector("[data-final-prediction-field]");

    if (!active) {
      source.hidden = false;
      source.style.removeProperty("display");
      finalField?.remove();
      return;
    }

    source.hidden = true;
    source.style.setProperty("display", "none", "important");

    if (!finalField) {
      finalField = document.createElement("label");
      finalField.className = "field foa-final-prediction-field";
      finalField.dataset.finalPredictionField = "true";
      source.after(finalField);
    }

    const mode = getStoredMode();
    const fixed = String(Math.max(0, Math.min(9, Number(input.value || 0))));
    const selected = mode || fixed;
    let select = finalField.querySelector("[data-final-prediction]");
    if (!select) {
      finalField.innerHTML = `<span>Prediction</span><select data-final-prediction aria-label="Prediction">${options(selected)}</select>`;
      select = finalField.querySelector("[data-final-prediction]");
    }
    if (select && select.value !== selected) select.value = selected;
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(enhance);
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
            const mode = getStoredMode();
            const reanalyze = payload.reanalyze && typeof payload.reanalyze === "object" ? { ...payload.reanalyze } : {};
            if (mode) {
              payload.prediction = null;
              reanalyze.prediction_mode = mode;
            } else {
              delete reanalyze.prediction_mode;
              const { input: predictionInput } = sourcePrediction();
              payload.prediction = Math.max(0, Math.min(9, Number(predictionInput?.value || payload.prediction || 0)));
            }
            payload.reanalyze = reanalyze;
            nextInit = { ...init, body: JSON.stringify(payload) };
          }
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
    const select = event.target?.closest?.("[data-final-prediction]");
    if (select) {
      const value = String(select.value || "");
      const { input } = sourcePrediction();
      if (MODE_VALUES.has(value)) {
        setStoredMode(value);
      } else {
        setStoredMode("");
        if (input) {
          input.value = String(Math.max(0, Math.min(9, Number(value || 0))));
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      window.setTimeout(schedule, 0);
      return;
    }
    if (event.target?.matches?.('select[data-builder="trade.side"],[data-mode],[data-mobile-mode]')) {
      window.setTimeout(schedule, 0);
    }
  }, true);

  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("pageshow", schedule);
  window.addEventListener("focus", schedule);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", schedule, { once: true })
    : schedule();

  window.FOA_FINAL_PREDICTION_VERSION = "20260813-3";
})();
