(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1__) return;
  window.__DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1__ = true;

  // Capture runs before the normal v3 balance listener at the Window target. If a
  // demo row that is not currently selected was reset, refresh the account list
  // but do not let that event overwrite the visible selected real-account balance.
  window.addEventListener("derivadmin:demo-balance-reset", (event) => {
    const detail = event.detail || {};
    const targetId = Number(detail.managed_account_id || 0);
    let selectedId = 0;
    try {
      selectedId = Number(window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.state?.().selected_managed_id || 0);
    } catch (_) {}
    if (!targetId || !selectedId || targetId === selectedId) return;

    event.stopImmediatePropagation();
    try { window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.refresh_accounts?.(); } catch (_) {}
  }, true);

  window.DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1 = Object.freeze({
    version: "20260818-demo-reset-router-v1",
  });
})();
