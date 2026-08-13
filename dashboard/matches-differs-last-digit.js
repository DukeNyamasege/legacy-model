(() => {
  "use strict";

  const MODE_PREFIX = "foa-match-prediction-mode-v1";
  const DYNAMIC = "last_digit";
  const DYNAMIC_SIDES = new Set(["matches", "differs"]);
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

  function isDynamic() {
    return storageGet(modeKey()) === DYNAMIC;
  }

  function setDynamic(enabled) {
    if (enabled) storageSet(modeKey(), DYNAMIC);
    else storageRemove(modeKey());
  }

  function currentSide() {
    return String(document.querySelector('select[data-builder="trade.side"]')?.value || "").toLowerCase();
  }

  function dynamicSide() {
    return DYNAMIC_SIDES.has(currentSide());
  }

  function optionMarkup(selected) {
    const values = [
      `<option value="${DYNAMIC}" ${selected === DYNAMIC ? "selected" : ""}>Last digit</option>`,
    ];
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
    if (!dynamicSide()) {
      sourceField.hidden = false;
      existing?.remove();
      return;
    }

    sourceField.hidden = true;
    let field = existing;
    if (!field) {
      field = document.createElement("label");
      field.className = "field foa-last-digit-prediction-field";
      field.dataset.lastDigitPredictionField = "true";
      sourceField.after(field);
    }

    const selected = isDynamic() ? DYNAMIC : String(Math.max(0, Math.min(9, Number(input.value || 0))));
    field.innerHTML = `<span>Prediction</span><select data-last-digit-prediction aria-label="Prediction">${optionMarkup(selected)}</select>`;
  }

  function syncSummary() {
    if (!dynamicSide() || !isDynamic()) return;
    const summary = document.querySelector(".live-summary p");
    if (!summary) return;
    const side = currentSide() === "matches" ? "Matches" : "Differs";
    const current = String(summary.textContent || "");
    const updated = current.replace(
      new RegExp(`\\b${side}\\s+(?:[0-9]|last[_ ]digit)\\b`, "i"),
      `${side} last digit`,
    );
    if (updated !== current) summary.textContent = updated;
  }

  function hydrateFromServer(payload) {
    const config = payload?.config;
    const side = String(config?.trade_type || "").toLowerCase();
    if (!DYNAMIC_SIDES.has(side)) return;
    setDynamic(config?.prediction === null || config?.prediction === undefined);
    scheduleEnhance();
  }

  function installFetchBridge() {
    if (window.__FOA_MATCH_DIFF_LAST_DIGIT_BRIDGE__) return;
    window.__FOA_MATCH_DIFF_LAST_DIGIT_BRIDGE__ = true;
    const originalFetch = window.fetch.bind(window);

    window.fetch = async (input, init = {}) => {
      const method = String(init?.method || "GET").toUpperCase();
      const rawUrl = typeof input === "string" ? input : String(input?.url || "");
      const isCustom = rawUrl.includes("/me/custom-strategy") || rawUrl.includes("/api/me/custom-strategy");

      let nextInit = init;
      if (isCustom && method === "POST" && typeof init.body === "string") {
        try {
          const payload = JSON.parse(init.body);
          const side = String(payload?.trade_type || "").toLowerCase();
          if (DYNAMIC_SIDES.has(side) && isDynamic()) {
            payload.prediction = null;
            nextInit = { ...init, body: JSON.stringify(payload) };
          }
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
      if (value === DYNAMIC) {
        setDynamic(true);
      } else {
        setDynamic(false);
        if (input) {
          input.value = value;
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      window.setTimeout(scheduleEnhance, 0);
      return;
    }

    if (event.target?.closest?.('select[data-builder="trade.side"]')) {
      window.setTimeout(scheduleEnhance, 0);
    }
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

  window.FOA_MATCH_DIFF_LAST_DIGIT_VERSION = "20260813-1";
})();
