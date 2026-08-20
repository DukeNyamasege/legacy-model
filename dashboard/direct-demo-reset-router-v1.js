(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1__) return;
  window.__DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1__ = true;

  const EPSILON = 0.000001;

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

  function marketingPartitionSelected(account = selectedAccount()) {
    return Boolean(
      account?.marketing_tutorial
      && account?.simulation_only
      && account?.demo_partition
      && ["dot", "rot"].includes(String(account?.tutorial_view || "").toLowerCase())
    );
  }

  function marketingRotSelected(account = selectedAccount()) {
    return marketingPartitionSelected(account)
      && String(account?.tutorial_view || "").toLowerCase() === "rot";
  }

  function partitionView(account = selectedAccount()) {
    return marketingRotSelected(account) ? "rot" : "dot";
  }

  function partitionLedgerBalance(account = selectedAccount()) {
    if (!marketingPartitionSelected(account)) return NaN;
    const value = partitionView(account) === "rot"
      ? Number(account?.partition_rot_balance)
      : Number(account?.partition_dot_balance);
    return Number.isFinite(value) ? value : Number(account?.balance);
  }

  function providerLedgerBalance(account = selectedAccount()) {
    const value = Number(account?.partition_provider_balance);
    return Number.isFinite(value) ? value : NaN;
  }

  function partitionShare(account = selectedAccount()) {
    const share = Number(account?.demo_partition_share ?? account?.tutorial_balance_ratio);
    if (Number.isFinite(share) && share > 0 && share < 1) return share;
    return partitionView(account) === "rot" ? 0.25 : 0.75;
  }

  function availablePartitionBalance(account = selectedAccount()) {
    const visible = Number(account?.balance);
    if (Number.isFinite(visible)) return Math.max(0, visible);
    const ledger = partitionLedgerBalance(account);
    return Number.isFinite(ledger) ? Math.max(0, ledger) : NaN;
  }

  function canSpend(amount, account = selectedAccount()) {
    if (!marketingPartitionSelected(account)) return true;
    const required = Number(amount);
    const available = availablePartitionBalance(account);
    if (!Number.isFinite(required) || required <= 0 || !Number.isFinite(available)) return false;
    return required <= available + EPSILON;
  }

  let refreshTimer = 0;
  function refreshPartitionsSoon(delay = 450) {
    clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => {
      try { window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.refresh_accounts?.(); } catch (_) {}
    }, delay);
  }

  function applyPartitionBalanceProjection(event) {
    const account = selectedAccount();
    if (!marketingPartitionSelected(account)) return;
    const detail = event?.detail;
    if (!detail || typeof detail !== "object" || detail.__marketing_projection_applied) return;

    const providerBaseline = providerLedgerBalance(account);
    const partitionBaseline = partitionLedgerBalance(account);
    const absolute = Number(detail.balance);

    // The partition ledger is the stable server checkpoint. A live absolute Deriv
    // balance represents the one underlying DOT demo account, so apply the entire
    // provider movement since that checkpoint only to the currently selected
    // partition. Do NOT multiply every balance event by 25%/75%.
    if (
      Number.isFinite(absolute)
      && Number.isFinite(providerBaseline)
      && Number.isFinite(partitionBaseline)
    ) {
      detail.balance = Math.max(
        0,
        Math.round((partitionBaseline + (absolute - providerBaseline)) * 100000000) / 100000000,
      );
    }

    // Delta-only events already represent the exact money movement (purchase debit
    // or settlement credit), so they remain unscaled and are applied to this one
    // visible partition by the normal runtime listener.
    if (marketingRotSelected(account) && "loginid" in detail) delete detail.loginid;
    detail.__marketing_projection_applied = true;

    if (String(detail.reason || "").toLowerCase() === "settlement") {
      // The SETTLED receipt persists the same P/L server-side. Refresh shortly
      // afterward so both DOT and ROT rows receive the new durable ledger totals.
      refreshPartitionsSoon(650);
    }
  }

  window.addEventListener("derivadmin:direct-balance", applyPartitionBalanceProjection, true);
  window.addEventListener("derivadmin:direct-balance-live", applyPartitionBalanceProjection, true);

  // Enforce true sub-balance behavior at the last browser boundary before Deriv.
  // Only BUY messages in this explicitly marked simulation workspace are checked.
  // Ordinary Deriv accounts and all non-BUY WebSocket messages are untouched.
  const nativeWebSocketSend = WebSocket.prototype.send;
  if (!WebSocket.prototype.__derivadminDemoPartitionGuard) {
    Object.defineProperty(WebSocket.prototype, "__derivadminDemoPartitionGuard", {
      value: true,
      configurable: false,
      enumerable: false,
      writable: false,
    });
    WebSocket.prototype.send = function guardedDemoPartitionSend(data) {
      let payload = null;
      try { payload = typeof data === "string" ? JSON.parse(data) : null; } catch (_) {}
      if (payload && Object.prototype.hasOwnProperty.call(payload, "buy")) {
        const account = selectedAccount();
        if (marketingPartitionSelected(account)) {
          const price = Number(payload.price);
          const available = availablePartitionBalance(account);
          if (!canSpend(price, account)) {
            const view = partitionView(account).toUpperCase();
            const shown = Number.isFinite(available) ? available.toFixed(2) : "0.00";
            const required = Number.isFinite(price) ? price.toFixed(2) : "unknown";
            const text = `Direct • ${view} demo partition insufficient • available $${shown} • required $${required}`;
            window.dispatchEvent(new CustomEvent("derivadmin:direct-status", {
              detail: { running: true, ownerLost: false, text, partition_guard: true },
            }));
            throw new Error(`${view} demo partition balance is insufficient`);
          }
        }
      }
      return nativeWebSocketSend.call(this, data);
    };
  }

  window.addEventListener("derivadmin:demo-balance-reset", (event) => {
    const account = selectedAccount();
    if (marketingPartitionSelected(account)) {
      // Both visible balances are children of the same underlying demo balance.
      // A successful reset therefore refreshes BOTH partitions from the server's
      // newly rebased 75%/25% ledger.
      refreshPartitionsSoon(150);
      return;
    }

    // Existing ordinary-account protection: a reset for a non-selected demo row
    // must not overwrite another selected account's visible balance.
    const detail = event.detail || {};
    const targetId = Number(detail.managed_account_id || 0);
    const selectedId = Number(runtimeState().selected_managed_id || 0);
    if (!targetId || !selectedId || targetId === selectedId) return;

    event.stopImmediatePropagation();
    try { window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.refresh_accounts?.(); } catch (_) {}
  }, true);

  function renderMarketingPresentation() {
    const account = selectedAccount();
    const active = marketingPartitionSelected(account);
    const panel = document.querySelector(".global-run-panel");
    let badge = document.querySelector(".marketing-tutorial-runtime-badge");

    if (!active) {
      badge?.remove();
      return;
    }

    if (marketingRotSelected(account)) {
      // ROT is a demo partition with Real-style presentation only. Keep the
      // familiar US/Real visual treatment without changing its financial nature.
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
    }

    if (!panel) return;
    if (!badge) {
      badge = document.createElement("div");
      badge.className = "marketing-tutorial-runtime-badge";
      badge.style.cssText = "margin:7px 12px 0;padding:6px 9px;border-radius:9px;border:1px solid rgba(84,200,255,.2);background:rgba(8,24,40,.72);display:flex;align-items:center;gap:7px;font-size:8px;line-height:1.2;letter-spacing:.02em";
      (panel.querySelector(".run-panel-sheet") || panel).prepend(badge);
    }
    const signature = `${partitionView(account)}|${partitionShare(account)}|${account?.account_id || ""}`;
    if (badge.dataset.signature !== signature) {
      badge.dataset.signature = signature;
      badge.innerHTML = '<span style="font-weight:900;text-transform:uppercase">Tutorial</span><b>Shared demo execution</b><small style="opacity:.7">DOT 75% · ROT 25%</small>';
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
    version: "20260821-marketing-dot-rot-v3-partitions",
    marketing_partition_selected: marketingPartitionSelected,
    marketing_rot_selected: marketingRotSelected,
    partition_share: partitionShare,
    available_balance: availablePartitionBalance,
    can_spend: canSpend,
  });
})();
