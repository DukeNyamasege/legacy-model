(() => {
  "use strict";

  if (window.__FOA_PLATFORM_DEFAULT_STRATEGY__) return;
  window.__FOA_PLATFORM_DEFAULT_STRATEGY__ = true;

  const VERSION = "20260813-v1";
  const INIT_PREFIX = `foa-platform-default-initialized:${VERSION}`;
  let applying = false;
  let pending = false;
  let pendingPayload = null;

  const q = (selector, root = document) => root.querySelector(selector);

  function currentAccountId(payload = null) {
    const me = window.FOA_NETLIFY_LIVE_CACHE?.me || window.FOA_BOOT_SESSION || {};
    return String(
      payload?.managed_account_id
      || me?.managed_account_id
      || me?.id
      || me?.account_id_masked
      || me?.account_id
      || "session",
    );
  }

  function initKey(payload = null) {
    return `${INIT_PREFIX}:${currentAccountId(payload)}`;
  }

  function wasInitialized(payload = null) {
    try { return localStorage.getItem(initKey(payload)) === "1"; } catch (_) { return false; }
  }

  function markInitialized(payload = null) {
    try { localStorage.setItem(initKey(payload), "1"); } catch (_) {}
  }

  function builderReady() {
    return Boolean(
      q(".strategy-builder-card")
      && q('[data-market-mode="all"]')
      && q('[data-strategy-mode="last_digit"]')
      && q('[data-trade-group="matches_differs"]'),
    );
  }

  function fire(field, eventName = null) {
    if (!field) return;
    const type = eventName || (field.tagName === "SELECT" || field.type === "checkbox" ? "change" : "input");
    field.dispatchEvent(new Event(type, { bubbles: true }));
  }

  function setBuilder(path, value) {
    const field = q(`[data-builder="${path}"]`);
    if (!field) return false;
    if (field.type === "checkbox") {
      const next = Boolean(value);
      if (field.checked === next) return true;
      field.checked = next;
      fire(field, "change");
      return true;
    }
    if (String(field.value) === String(value)) return true;
    field.value = String(value);
    fire(field, "change");
    return true;
  }

  function clickChoice(selector) {
    const button = q(selector);
    if (!button) return false;
    if (!button.classList.contains("active")) button.click();
    return true;
  }

  function setResult(path, value) {
    const field = q(`[data-result-route="${path}"]`);
    if (!field) return false;
    if (field.type === "checkbox") {
      const next = Boolean(value);
      if (field.checked !== next) {
        field.checked = next;
        fire(field, "change");
      }
      return true;
    }
    if (String(field.value) !== String(value)) {
      field.value = String(value);
      fire(field, field.tagName === "SELECT" ? "change" : "input");
      if (field.tagName !== "SELECT") fire(field, "change");
    }
    return true;
  }

  function applyPrimary() {
    if (!builderReady()) return false;

    clickChoice('[data-market-mode="all"]');
    clickChoice('[data-strategy-mode="last_digit"]');
    clickChoice('[data-trade-group="matches_differs"]');

    setBuilder("lastRule.window", 2);
    setBuilder("lastRule.operator", "all_same");
    setBuilder("trade.side", "differs");
    // The hidden numeric field remains valid for the canonical builder. The
    // dynamic-prediction authority below changes the saved prediction to the
    // final qualifying trigger digit (prediction_mode=last_digit).
    setBuilder("trade.prediction", 4);
    setBuilder("reanalyze.mode", "after_every_trade");
    setBuilder("money.stake", 0.5);
    setBuilder("money.takeProfit", 10);
    setBuilder("money.stopLoss", 100);
    setBuilder("money.martingale", 2.1);
    setBuilder("money.ticks", 1);
    setBuilder("virtualHook.enabled", true);
    setBuilder("virtualHook.enterAfterLosses", 2);
    setBuilder("virtualHook.exitAfterConsecutiveWins", 1);
    return true;
  }

  function applyDynamicPrediction() {
    const select = q("[data-last-digit-prediction]");
    if (!select) return false;
    if (select.value !== "last_digit") {
      select.value = "last_digit";
      fire(select, "change");
    }
    return true;
  }

  function applyAfterLoss() {
    const toggle = q("#result-routing-enabled");
    if (!toggle) return false;
    if (!toggle.checked) {
      toggle.checked = true;
      fire(toggle, "change");
    }

    setResult("tradeType", "over");
    setResult("prediction", 4);
    setResult("durationTicks", 1);
    setResult("analysisMode", "last_digit");
    setResult("lastRule.window", 5);
    setResult("lastRule.operator", "<=");
    setResult("lastRule.value", 5);
    setResult("tickDirectionRule.enabled", false);

    const recovery = q("#recovery-style");
    if (recovery && recovery.value !== "multiplier") {
      recovery.value = "multiplier";
      fire(recovery, "change");
    }
    return true;
  }

  function applyPreset({ force = false, payload = null } = {}) {
    if (applying) return false;
    if (!force && wasInitialized(payload)) {
      pendingPayload = null;
      return true;
    }
    if (!builderReady()) {
      pendingPayload = payload || pendingPayload || {};
      return false;
    }

    applying = true;
    let primaryApplied = false;
    try {
      primaryApplied = applyPrimary();
      if (primaryApplied) {
        markInitialized(payload);
        pendingPayload = null;
      }
    } finally {
      applying = false;
    }
    if (!primaryApplied) return false;

    // Primary changes re-render the builder. Re-apply dependent controls after
    // those renders so prediction and after-loss settings enter their canonical
    // state managers rather than being painted only visually.
    [0, 60, 160, 360, 700].forEach((delay) => {
      window.setTimeout(() => {
        if (applying || !builderReady()) return;
        applying = true;
        try {
          applyPrimary();
          applyDynamicPrediction();
          applyAfterLoss();
        } finally {
          applying = false;
        }
      }, delay);
    });
    return true;
  }

  function schedulePreset(options = {}) {
    if (options?.payload) pendingPayload = options.payload;
    if (pending) return;
    pending = true;
    window.setTimeout(() => {
      pending = false;
      applyPreset(options);
    }, 0);
  }

  const previousFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const response = await previousFetch(input, init);
    const url = typeof input === "string" ? input : String(input?.url || "");
    const method = String(init?.method || input?.method || "GET").toUpperCase();
    if (response.ok && method === "GET" && url.includes("/me/custom-strategy")) {
      try {
        const payload = await response.clone().json();
        if (payload?.authenticated && payload?.config?.configured === false) {
          schedulePreset({ payload });
        } else if (payload?.config?.configured === true) {
          pendingPayload = null;
        }
      } catch (_) {}
    }
    return response;
  };

  // Reset is intentionally owned here: cancelling leaves the current draft
  // untouched; confirming restores the complete platform preset as an editable
  // draft without writing to the server or starting trading.
  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-reset-strategy]");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!window.confirm("Reset this strategy to the platform default? Your current unsaved builder configuration will be replaced.")) return;
    schedulePreset({ force: true });
  }, true);

  // If the account's first server response arrived while another dashboard view
  // was open, apply the default when the Builder is mounted later.
  new MutationObserver(() => {
    if (pendingPayload && builderReady()) schedulePreset({ payload: pendingPayload });
  }).observe(document.documentElement, { childList: true, subtree: true });

  window.FOA_PLATFORM_DEFAULT_STRATEGY_VERSION = VERSION;
})();
