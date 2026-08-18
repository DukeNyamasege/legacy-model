(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_RESET_AUTHORITY_V1__) return;
  window.__DERIVADMIN_DIRECT_RESET_AUTHORITY_V1__ = true;

  // Capture the pre-engine fetch chain. The direct execution engine later gives
  // /clear-trades a local-first compatibility response; Reset needs the opposite:
  // wait for the real bounded API result, then clear browser counters on success.
  const controlFetch = window.fetch.bind(window);
  let busy = false;

  function detailFrom(response) {
    return response.clone().json()
      .then((payload) => String(payload?.detail || payload?.message || "Reset could not be completed."))
      .catch(() => "Reset could not be completed.");
  }

  async function resetAll() {
    if (busy) return;
    if (!window.confirm("Do you want to reset all trades?")) return;
    busy = true;
    try {
      const response = await controlFetch("/api/me/clear-trades", {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: "all" }),
      });
      if (!response.ok) {
        window.alert(await detailFrom(response));
        return;
      }
      try { window.DERIVADMIN_DIRECT_EXECUTION_V1?.clear?.(); } catch (_) {}
      window.dispatchEvent(new CustomEvent("derivadmin:direct-reset-all"));
    } catch (_) {
      window.alert("Reset could not be completed. Please try again after the current contract settles.");
    } finally {
      busy = false;
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.(".global-run-panel [data-run-reset]");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    resetAll();
  }, true);

  window.DERIVADMIN_DIRECT_RESET_AUTHORITY_V1 = Object.freeze({
    version: "20260818-reset-authority-v1",
    reset: resetAll,
  });
})();
