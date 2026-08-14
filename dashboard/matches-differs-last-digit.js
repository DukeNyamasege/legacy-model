(() => {
  "use strict";

  const MODE_PREFIX = "foa-match-prediction-mode-v4";
  const WINDOW_PREFIX = "foa-match-prediction-window-v1";
  const LEGACY_PREFIX = "foa-match-prediction-mode-v1";
  const DYNAMIC_SIDES = new Set(["matches", "differs"]);
  const FREQUENCY_MODES = new Set([
    "most_appearing",
    "second_most_appearing",
    "least_appearing",
  ]);
  const DYNAMIC_MODES = [
    ["last_digit", "Last digit"],
    ["most_appearing", "Most appearing"],
    ["second_most_appearing", "Second most appearing"],
    ["least_appearing", "Least appearing"],
  ];
  const DYNAMIC_VALUES = new Set(DYNAMIC_MODES.map(([value]) => value));
  let scheduled = false;

  function storageGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }

  function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch (_) {}
  }

  function storageRemove(key) {
    try { localStorage.removeItem(key); } catch (_) {}
  }

  function currentMe() {
    return window.FOA_NETLIFY_LIVE_CACHE?.me || window.FOA_BOOT_SESSION || null;
  }

  function accountIdentity() {
    const me = currentMe();
    const mode = String(me?.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
    const account = String(me?.account_id_masked || me?.account_id || me?.label || "session").trim();
    return `${mode}:${account}`;
  }

  function modeKey() {
    return `${MODE_PREFIX}:${accountIdentity()}`;
  }

  function windowKey() {
    return `${WINDOW_PREFIX}:${accountIdentity()}`;
  }

  function currentMode() {
    const value = String(storageGet(modeKey()) || "").trim().toLowerCase();
    return DYNAMIC_VALUES.has(value) ? value : "";
  }

  function setMode(mode) {
    if (DYNAMIC_VALUES.has(mode)) storageSet(modeKey(), mode);
    else storageRemove(modeKey());
    storageRemove(`${LEGACY_PREFIX}:${accountIdentity()}`);
  }

  function predictionWindow() {
    const value = Math.round(Number(storageGet(windowKey()) || 100));
    return Math.max(1, Math.min(1000, Number.isFinite(value) ? value : 100));
  }

  function setPredictionWindow(value) {
    const next = Math.max(1, Math.min(1000, Math.round(Number(value || 100))));
    storageSet(windowKey(), String(next));
    return next;
  }

  function currentSide() {
    return String(document.querySelector('select[data-builder="trade.side"]')?.value || "").toLowerCase();
  }

  function dynamicSide() {
    return DYNAMIC_SIDES.has(currentSide());
  }

  function optionMarkup(selected) {
    const values = DYNAMIC_MODES.map(([value, label]) =>
      `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`,
    );
    for (let digit = 0; digit <= 9; digit += 1) {
      values.push(`<option value="${digit}" ${String(selected) === String(digit) ? "selected" : ""}>${digit}</option>`);
    }
    return values.join("");
  }

  function syncPredictionControl() {
    const input = document.querySelector('input[data-builder="trade.prediction"]');
    if (!input) return;
    const sourceField = input.closest("label.field");
    if (!sourceField) return;

    const existing = document.querySelector("[data-last-digit-prediction-field]");
    const existingWindow = document.querySelector("[data-dynamic-prediction-window-field]");
    if (!dynamicSide()) {
      sourceField.hidden = false;
      sourceField.style.removeProperty("display");
      existing?.remove();
      existingWindow?.remove();
      return;
    }

    sourceField.hidden = true;
    sourceField.style.setProperty("display", "none", "important");
    let field = existing;
    if (!field) {
      field = document.createElement("label");
      field.className = "field foa-last-digit-prediction-field";
      field.dataset.lastDigitPredictionField = "true";
      sourceField.after(field);
    }

    const mode = currentMode();
    const selected = mode || String(Math.max(0, Math.min(9, Number(input.value || 0))));
    field.innerHTML = `<span>Prediction</span><select data-last-digit-prediction aria-label="Prediction">${optionMarkup(selected)}</select>`;

    let windowField = existingWindow;
    if (!FREQUENCY_MODES.has(mode)) {
      windowField?.remove();
      return;
    }
    if (!windowField) {
      windowField = document.createElement("label");
      windowField.className = "field foa-dynamic-prediction-window-field";
      windowField.dataset.dynamicPredictionWindowField = "true";
      field.after(windowField);
    }
    windowField.innerHTML = `
      <span>Prediction analysis ticks</span>
      <input type="number" min="1" max="1000" step="1" inputmode="numeric"
        data-dynamic-prediction-window value="${predictionWindow()}"
        aria-label="Prediction analysis ticks">
    `;
  }

  function syncSummary() {
    if (!dynamicSide()) return;
    const mode = currentMode();
    if (!mode) return;
    const summary = document.querySelector(".live-summary p");
    if (!summary) return;
    const side = currentSide() === "matches" ? "Matches" : "Differs";
    const labels = {
      last_digit: "last qualifying trigger digit",
      most_appearing: `most appearing digit in the last ${predictionWindow()} ticks`,
      second_most_appearing: `second most appearing digit in the last ${predictionWindow()} ticks`,
      least_appearing: `least appearing digit in the last ${predictionWindow()} ticks`,
    };
    const current = String(summary.textContent || "");
    const replacement = `${side} ${labels[mode] || mode}`;
    const updated = current.replace(
      new RegExp(`\\b${side}\\s+(?:[0-9]|last[_ ]digit|last qualifying trigger digit|most appearing digit(?: in the last \\d+ ticks)?|second most appearing digit(?: in the last \\d+ ticks)?|least appearing digit(?: in the last \\d+ ticks)?)\\b`, "i"),
      replacement,
    );
    if (updated !== current) summary.textContent = updated;
  }

  function modeFromConfig(config) {
    const direct = String(config?.prediction_mode || "").trim().toLowerCase();
    if (DYNAMIC_VALUES.has(direct)) return direct;
    const nested = String(config?.reanalyze?.prediction_mode || "").trim().toLowerCase();
    if (DYNAMIC_VALUES.has(nested)) return nested;
    if (config?.prediction === null || config?.prediction === undefined) return "last_digit";
    return "";
  }

  function hydrateFromServer(payload) {
    const config = payload?.config;
    const side = String(config?.trade_type || "").toLowerCase();
    if (!DYNAMIC_SIDES.has(side)) return;
    const mode = modeFromConfig(config);
    setMode(mode);
    if (FREQUENCY_MODES.has(mode)) {
      setPredictionWindow(
        config?.reanalyze?.prediction_window || config?.prediction_window || 100,
      );
    }
    scheduleEnhance();
  }

  function installFetchBridge() {
    if (window.__FOA_MATCH_DIFF_DYNAMIC_BRIDGE__) return;
    window.__FOA_MATCH_DIFF_DYNAMIC_BRIDGE__ = true;
    const originalFetch = window.fetch.bind(window);

    window.fetch = async (input, init = {}) => {
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      const rawUrl = typeof input === "string" ? input : String(input?.url || "");
      const isCustom = rawUrl.includes("/me/custom-strategy") || rawUrl.includes("/api/me/custom-strategy");

      let nextInit = init;
      if (isCustom && method === "POST" && typeof init.body === "string") {
        try {
          const payload = JSON.parse(init.body);
          const side = String(payload?.trade_type || "").toLowerCase();
          if (DYNAMIC_SIDES.has(side)) {
            const mode = currentMode();
            const reanalyze = payload.reanalyze && typeof payload.reanalyze === "object"
              ? { ...payload.reanalyze }
              : {};
            if (mode) {
              payload.prediction = null;
              reanalyze.prediction_mode = mode;
              if (FREQUENCY_MODES.has(mode)) {
                reanalyze.prediction_window = predictionWindow();
              } else {
                delete reanalyze.prediction_window;
              }
            } else {
              delete reanalyze.prediction_mode;
              delete reanalyze.prediction_window;
            }
            payload.reanalyze = reanalyze;
          }
          nextInit = { ...init, body: JSON.stringify(payload) };
        } catch (_) {}
      }

      const response = await originalFetch(input, nextInit);
      if (isCustom && method === "GET" && response.ok) {
        try {
          const payload = await response.clone().json();
          hydrateFromServer(payload);
        } catch (_) {}
      }
      return response;
    };
  }

  function enhance() {
    scheduled = false;
    syncPredictionControl();
    syncSummary();
  }

  function scheduleEnhance() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(enhance);
  }

  installFetchBridge();

  document.addEventListener("change", (event) => {
    const dynamicSelect = event.target?.closest?.("[data-last-digit-prediction]");
    if (dynamicSelect) {
      const value = String(dynamicSelect.value || "");
      const input = document.querySelector('input[data-builder="trade.prediction"]');
      if (DYNAMIC_VALUES.has(value)) {
        setMode(value);
        if (FREQUENCY_MODES.has(value)) setPredictionWindow(predictionWindow());
      } else {
        setMode("");
        if (input) {
          input.value = value;
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      window.setTimeout(scheduleEnhance, 0);
      return;
    }

    const windowInput = event.target?.closest?.("[data-dynamic-prediction-window]");
    if (windowInput) {
      windowInput.value = String(setPredictionWindow(windowInput.value));
      syncSummary();
      return;
    }

    if (event.target?.closest?.('select[data-builder="trade.side"]')) {
      window.setTimeout(scheduleEnhance, 0);
    }
  }, true);

  document.addEventListener("input", (event) => {
    const windowInput = event.target?.closest?.("[data-dynamic-prediction-window]");
    if (!windowInput) return;
    setPredictionWindow(windowInput.value);
    syncSummary();
  }, true);

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.('[data-trade-group="matches_differs"]')) {
      window.setTimeout(scheduleEnhance, 0);
    }
  }, true);

  const observer = new MutationObserver(scheduleEnhance);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener("pageshow", scheduleEnhance);
  window.addEventListener("focus", scheduleEnhance);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleEnhance();
  });

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleEnhance, { once: true })
    : scheduleEnhance();

  window.FOA_MATCH_DIFF_LAST_DIGIT_VERSION = "20260814-6";
})();
