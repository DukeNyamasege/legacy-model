(() => {
  "use strict";

  if (window.__DERIVADMIN_PUBLIC_TESTING_RUNTIME_V1__) return;
  window.__DERIVADMIN_PUBLIC_TESTING_RUNTIME_V1__ = true;

  /*
   * Public-testing mode is ACCESS/UI ONLY.
   *
   * This file deliberately owns NO Run/Stop state, NO execution lifecycle polling,
   * NO Deriv tick mirror, NO fetch transport override, and NO trading endpoints.
   * Manual live execution belongs exclusively to deriv-direct-execution-v2.
   * Scheduled/offline execution belongs to the VPS worker.
   *
   * Keeping this separation prevents the historical start/stop loop where this
   * testing helper and the direct engine both changed the same Run button and
   * alternated /me/resume-trading with /me/stop-trading.
   */

  const state = { testingFree: false, renderQueued: false };

  function isPremiumNoise(node) {
    const text = String(node?.textContent || "").toLowerCase();
    return text.includes("premium use only")
      || text.includes("pay kes 250")
      || text.includes("weekly access will soon")
      || text.includes("premium renewal reminder");
  }

  function apply() {
    state.renderQueued = false;
    document.documentElement.dataset.publicTestingAccess = state.testingFree ? "free" : "paid";
    if (!state.testingFree) return;
    document.querySelectorAll(".global-message,.premium-message,.paid-soon-banner,.premium-reminder").forEach((node) => {
      if (isPremiumNoise(node) || node.matches?.(".paid-soon-banner,.premium-reminder")) node.remove();
    });
  }

  function queueApply() {
    if (state.renderQueued) return;
    state.renderQueued = true;
    requestAnimationFrame(apply);
  }

  async function loadAccessMode() {
    try {
      const response = await window.fetch("/api/me/public-testing-access", {
        credentials: "include",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const payload = response.ok ? await response.json() : {};
      state.testingFree = payload?.public_testing_free_access === true;
    } catch (_) {
      // The production frontend is currently built in free-testing mode too; a
      // temporary access-status read failure must not install any trading logic.
      state.testingFree = document.documentElement.dataset.publicTestingAccess === "free";
    }
    queueApply();
  }

  const observer = new MutationObserver(queueApply);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("pagehide", () => observer.disconnect(), { once: true });
  window.addEventListener("focus", loadAccessMode);

  loadAccessMode();
  queueApply();

  window.DERIVADMIN_PUBLIC_TESTING_RUNTIME_V1 = Object.freeze({
    version: "20260818-public-testing-access-only-v7",
    refresh: loadAccessMode,
    execution_authority: "none",
  });
})();
