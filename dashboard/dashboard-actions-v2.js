(() => {
  "use strict";

  /*
   * Builder-first compatibility layer.
   *
   * The old dashboard actions script used MutationObserver + interval polling to
   * rewrite legacy trade tables. That fought the new builder UI by repeatedly
   * touching the DOM during navigation. Keep only the optional clear-trades
   * handler for any compatibility buttons that may still be injected elsewhere.
   */
  const VERSION = "20260812-builder-actions-quiet";
  window.FOA_BUILDER_ACTIONS_QUIET = VERSION;

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await response.text();
    let payload = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch (_) {
      payload = null;
    }
    if (!response.ok) {
      throw new Error(payload?.detail || payload?.message || text || `HTTP ${response.status}`);
    }
    return payload;
  }

  function emitTradesCleared() {
    document.dispatchEvent(new CustomEvent("foa:trades-cleared"));
  }

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-clear-scope]");
    if (!button) return;
    event.preventDefault();

    const scope = button.dataset.clearScope === "all" ? "all" : "today";
    const label = scope === "all" ? "all personal trade history" : "today's personal trades";
    if (!window.confirm(`Clear ${label}? This keeps registered traders and credentials.`)) return;

    button.disabled = true;
    try {
      const payload = await api("/me/clear-trades", {
        method: "POST",
        body: JSON.stringify({ scope }),
      });
      window.alert(payload?.message || "Trades cleared.");
      emitTradesCleared();
    } catch (error) {
      window.alert(String(error?.message || error));
    } finally {
      button.disabled = false;
    }
  }, true);
})();
