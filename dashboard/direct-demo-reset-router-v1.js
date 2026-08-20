(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1__) return;
  window.__DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1__ = true;

  function runtimeState() {
    try { return window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.state?.() || {}; }
    catch (_) { return {}; }
  }

  function selectedAccount() {
    const state = runtimeState();
    const selectedId = Number(state.selected_managed_id || 0);
    const accounts = Array.isArray(state.accounts) ? state.accounts : [];
    return accounts.find((item) => Number(item?.managed_account_id || 0) === selectedId)
      || accounts.find((item) => item?.selected)
      || null;
  }

  function marketingRotSelected(account = selectedAccount()) {
    return Boolean(
      account?.marketing_tutorial
      && account?.simulation_only
      && String(account?.tutorial_view || "").toLowerCase() === "rot"
    );
  }

  function presentationRatio(account = selectedAccount()) {
    if (!marketingRotSelected(account)) return 1;
    const ratio = Number(account?.tutorial_balance_ratio);
    return Number.isFinite(ratio) && ratio > 0 && ratio <= 1 ? ratio : 0.25;
  }

  function applyMarketingBalanceProjection(event) {
    const account = selectedAccount();
    if (!marketingRotSelected(account)) return;
    const detail = event?.detail;
    if (!detail || typeof detail !== "object" || detail.__marketing_projection_applied) return;
    const ratio = presentationRatio(account);
    const absolute = Number(detail.balance);
    const delta = Number(detail.delta);
    if (Number.isFinite(absolute)) detail.balance = Math.round(absolute * ratio * 100000000) / 100000000;
    if (Number.isFinite(delta)) detail.delta = Math.round(delta * ratio * 100000000) / 100000000;
    // The provider correctly reports the underlying DOT demo login. Never allow
    // that identity to replace the visible ROT tutorial identity in the selector.
    if ("loginid" in detail) delete detail.loginid;
    detail.__marketing_projection_applied = true;
  }

  // Capture listeners run before the normal runtime UX listeners at the Window
  // target, so only the PRESENTATION value is scaled. The execution engine has
  // already processed the genuine DOT demo provider balance and contract state.
  window.addEventListener("derivadmin:direct-balance", applyMarketingBalanceProjection, true);
  window.addEventListener("derivadmin:direct-balance-live", applyMarketingBalanceProjection, true);

  // Existing demo-reset protection: a reset for a non-selected demo row must not
  // overwrite the currently visible selected-account balance.
  window.addEventListener("derivadmin:demo-balance-reset", (event) => {
    const detail = event.detail || {};
    const targetId = Number(detail.managed_account_id || 0);
    const selectedId = Number(runtimeState().selected_managed_id || 0);
    if (!targetId || !selectedId || targetId === selectedId) return;

    event.stopImmediatePropagation();
    try { window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.refresh_accounts?.(); } catch (_) {}
  }, true);

  function renderMarketingPresentation() {
    const account = selectedAccount();
    const active = marketingRotSelected(account);
    const panel = document.querySelector(".global-run-panel");
    let badge = document.querySelector(".marketing-tutorial-runtime-badge");

    if (!active) {
      badge?.remove();
      return;
    }

    // Keep dynamically generated fallback rows visually identical to the normal
    // Real account row: real flag, ROT number, Real label. The API itself already
    // supplies these values for the canonical shell render.
    const id = Number(account?.managed_account_id || 0);
    if (id) {
      document.querySelectorAll(`[data-account-id="${CSS.escape(String(id))}"]`).forEach((row) => {
        const oldSymbol = row.querySelector(".direct-account-symbol");
        if (oldSymbol) {
          const flag = document.createElement("span");
          flag.className = "deriv-real-flag";
          flag.setAttribute("aria-hidden", "true");
          oldSymbol.replaceWith(flag);
        }
        const label = row.querySelector("span b");
        const accountId = row.querySelector("span small");
        const expectedLabel = String(account?.label || "");
        const expectedId = String(account?.account_id || "");
        if (label && expectedLabel && label.textContent !== expectedLabel) label.textContent = expectedLabel;
        if (accountId && expectedId && accountId.textContent !== expectedId) accountId.textContent = expectedId;
      });
    }

    if (!panel) return;
    if (!badge) {
      badge = document.createElement("div");
      badge.className = "marketing-tutorial-runtime-badge";
      badge.style.cssText = "margin:7px 12px 0;padding:6px 9px;border-radius:9px;border:1px solid rgba(84,200,255,.2);background:rgba(8,24,40,.72);display:flex;align-items:center;gap:7px;font-size:8px;line-height:1.2;letter-spacing:.02em";
      (panel.querySelector(".run-panel-sheet") || panel).prepend(badge);
    }
    const signature = `${account?.account_id || "ROT"}|${presentationRatio(account)}`;
    if (badge.dataset.signature !== signature) {
      badge.dataset.signature = signature;
      badge.innerHTML = '<span style="font-weight:900;text-transform:uppercase">Tutorial</span><b>Demo execution</b><small style="opacity:.7">ROT view · linked DOT demo</small>';
    }
  }

  let renderTimer = 0;
  function renderSoon() {
    clearTimeout(renderTimer);
    renderTimer = window.setTimeout(renderMarketingPresentation, 0);
  }

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-account-id]")) {
      [80, 250, 700].forEach((delay) => window.setTimeout(() => {
        try { window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.refresh_accounts?.(); } catch (_) {}
        renderMarketingPresentation();
      }, delay));
    }
  }, true);
  window.addEventListener("pageshow", renderSoon);
  window.addEventListener("derivadmin:direct-run-state", renderSoon);
  window.addEventListener("derivadmin:direct-balance", renderSoon);
  window.addEventListener("derivadmin:direct-balance-live", renderSoon);

  const observer = new MutationObserver(renderSoon);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  renderSoon();

  window.DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1 = Object.freeze({
    version: "20260821-marketing-dot-rot-v2",
    marketing_rot_selected: marketingRotSelected,
    presentation_ratio: presentationRatio,
  });
})();
