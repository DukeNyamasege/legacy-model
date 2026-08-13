(() => {
  "use strict";

  const RESET_PREFIX = "foa-trade-session-reset-v1";
  let clearing = false;

  function currentMe() {
    return window.FOA_NETLIFY_LIVE_CACHE?.me || window.FOA_BOOT_SESSION || null;
  }

  function identity(payload = null) {
    const me = currentMe();
    const mode = String(payload?.account_type || me?.account_type || "demo").toLowerCase() === "real"
      ? "real"
      : "demo";
    const account = String(
      payload?.account || me?.account_id_masked || me?.account_id || "public"
    );
    return `${mode}:${account}`;
  }

  function resetKey(payload = null) {
    return `${RESET_PREFIX}:${identity(payload)}`;
  }

  function rememberCutoff(value, payload = null) {
    const parsed = Date.parse(String(value || ""));
    if (!Number.isFinite(parsed)) return;
    try {
      localStorage.setItem(resetKey(payload), new Date(parsed).toISOString());
    } catch (_) {}
  }

  function applyImmediateClear() {
    document.querySelectorAll(".foa-trades-table .trade-row").forEach((row) => row.remove());
    document.querySelectorAll(".builder-stats.compact .builder-stat").forEach((card) => {
      const label = String(card.querySelector("span")?.textContent || "").trim().toLowerCase();
      const value = card.querySelector("strong");
      if (!value || label === "balance") return;
      if (label === "p/l") value.textContent = "$0.00";
      else if (["runs", "wins", "losses"].includes(label)) value.textContent = "0";
    });
  }

  async function clearGlobally(button) {
    if (clearing) return;
    if (!window.confirm("Clear the current trade view everywhere? Previous trades will stay hidden after logout, login, or another device.")) return;
    clearing = true;
    button.disabled = true;
    try {
      const response = await fetch("/me/clear-trades", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ scope: "all" }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload?.detail || payload?.message || `Clear Trades failed (${response.status})`);
      }
      rememberCutoff(payload?.history_cleared_at, payload);
      applyImmediateClear();
      window.dispatchEvent(new CustomEvent("foa:global-trades-cleared", { detail: payload }));
    } catch (error) {
      window.alert(String(error?.message || error));
    } finally {
      clearing = false;
      button.disabled = false;
    }
  }

  // Persist the server-side cutoff into the existing client filter. This makes a
  // second device discard stale realtime/cache rows even before its next render.
  if (!window.__FOA_GLOBAL_TRADE_CLEAR_FETCH__) {
    window.__FOA_GLOBAL_TRADE_CLEAR_FETCH__ = true;
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const response = await originalFetch(input, init);
      const url = typeof input === "string" ? input : String(input?.url || "");
      const method = String(init?.method || "GET").toUpperCase();
      if (method === "GET" && /\/me\/trades\/today(?:\?|$)/.test(url) && response.ok) {
        response.clone().json().then((payload) => {
          rememberCutoff(payload?.history_cleared_at, payload);
        }).catch(() => {});
      }
      return response;
    };
  }

  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-clear-local-trades]");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    clearGlobally(button);
  }, true);

  window.FOA_GLOBAL_TRADE_CLEAR_VERSION = "20260813-1";
})();
