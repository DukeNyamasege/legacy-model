(() => {
  "use strict";

  let lastLifecycle = null;
  let scheduled = false;

  function money(value, currency = "USD") {
    const amount = Number(value || 0);
    const unit = String(currency || "USD").toUpperCase();
    const prefix = unit === "USD" ? "$" : `${unit} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function currency() {
    return window.FOA_NETLIFY_LIVE_CACHE?.me?.currency
      || window.FOA_BOOT_SESSION?.currency
      || "USD";
  }

  function syncNotice() {
    scheduled = false;
    const lifecycle = lastLifecycle;
    if (!lifecycle) return;
    const status = String(lifecycle.execution_status || "").toLowerCase();
    if (!new Set(["take_profit", "stop_loss"]).has(status)) return;

    const isTp = status === "take_profit";
    const rawTarget = Number(lifecycle.limit_target || 0);
    const target = isTp ? Math.abs(rawTarget) : -Math.abs(rawTarget);
    const achieved = Number(lifecycle.limit_achieved ?? lifecycle.session_profit ?? 0);
    const notice = document.querySelector(".foa-final-limit-notifier");
    if (!notice) return;

    const values = notice.querySelector(".foa-limit-values");
    if (values) {
      values.textContent = `Target ${money(target, currency())} · Session P/L ${money(achieved, currency())}`;
    }

    const detail = notice.querySelector("small");
    if (detail && target !== 0) {
      detail.textContent = `${isTp ? "Take profit" : "Stop loss"} target ${money(target, currency())} reached at session P/L ${money(achieved, currency())}. Auto trading stopped; next Start begins fresh.`;
    }
  }

  function scheduleSync() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(syncNotice);
  }

  function installFetchBridge() {
    if (window.__FOA_SIGNED_RISK_LIMIT_FETCH_BRIDGE__) return;
    window.__FOA_SIGNED_RISK_LIMIT_FETCH_BRIDGE__ = true;
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const response = await originalFetch(input, init);
      const rawUrl = typeof input === "string" ? input : String(input?.url || "");
      const method = String(init?.method || "GET").toUpperCase();
      if (method === "GET" && rawUrl.includes("/me/trading-lifecycle") && response.ok) {
        try {
          lastLifecycle = await response.clone().json();
          scheduleSync();
        } catch (_) {}
      }
      return response;
    };
  }

  installFetchBridge();

  const observer = new MutationObserver(scheduleSync);
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });

  window.FOA_SIGNED_RISK_LIMIT_DISPLAY_VERSION = "20260813-1";
})();
