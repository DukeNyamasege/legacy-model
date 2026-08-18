(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_INTERACTION_GUARD_V2__) return;
  window.__DERIVADMIN_DIRECT_INTERACTION_GUARD_V2__ = true;

  const approvedOnce = new WeakSet();
  const START_SELECTOR = [
    ".global-run-panel [data-run-start]",
    "[data-builder-trade]",
    "[data-ready-trade]",
    "[data-trade-now-selected]",
    "[data-start-trading]",
  ].join(",");

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function engineState() {
    try { return window.DERIVADMIN_DIRECT_EXECUTION_V1?.state?.() || {}; }
    catch (_) { return {}; }
  }

  function humanTarget(strategy) {
    const side = String(strategy?.trade_type || strategy?.side || "strategy").toUpperCase();
    const prediction = strategy?.prediction;
    return prediction === null || prediction === undefined || ["EVEN", "ODD", "RISE", "FALL"].includes(side)
      ? side
      : `${side} ${prediction}`;
  }

  function conditionText(condition) {
    const c = condition || {};
    const windowSize = Number(c.window || 1);
    if (c.kind === "digit_parity") return `Last ${windowSize} digit${windowSize === 1 ? "" : "s"} ${String(c.parity || "even").toUpperCase()}`;
    if (c.kind === "digit_compare") {
      if (c.operator === "all_same") return `Last ${windowSize} digits all the same`;
      if (c.operator === "all_even") return `Last ${windowSize} digits all even`;
      if (c.operator === "all_odd") return `Last ${windowSize} digits all odd`;
      return `Last ${windowSize} digit${windowSize === 1 ? "" : "s"} ${c.operator || "=="} ${c.value ?? 0}`;
    }
    if (c.kind === "direction") return `Last ${windowSize} move${windowSize === 1 ? "" : "s"} ${String(c.direction || "").replaceAll("_", " ")}`;
    if (c.kind === "percentage") {
      const target = String(c.target || "").replaceAll("_", " ");
      const value = c.value === null || c.value === undefined ? "" : ` ${c.value}`;
      return `${target}${value} ${c.operator || ">="} ${c.threshold ?? 0}% in ${windowSize} ticks`;
    }
    return "Saved strategy condition";
  }

  function strategySummary() {
    const strategy = engineState().strategy || {};
    const name = String(strategy.name || strategy.strategy_name || "Current strategy");
    const markets = Array.isArray(strategy.markets) ? strategy.markets : [];
    const conditions = Array.isArray(strategy.conditions) ? strategy.conditions : [];
    const stake = Number(strategy?.execution_settings?.stake_amount);
    return {
      name,
      target: humanTarget(strategy),
      markets: markets.length ? (markets.length === 10 ? "All 10 markets" : `${markets.length} selected market${markets.length === 1 ? "" : "s"}`) : "Saved market selection",
      conditions: conditions.slice(0, 6).map(conditionText),
      stake: Number.isFinite(stake) ? stake : null,
    };
  }

  function modal({ title, intro = "", body = "", confirmText = "Proceed", cancelText = "Cancel", danger = false }) {
    return new Promise((resolve) => {
      const existing = document.getElementById("direct-confirm-modal-v2");
      if (existing) existing.remove();
      const host = document.createElement("div");
      host.id = "direct-confirm-modal-v2";
      host.className = "direct-confirm-modal-v2";
      host.innerHTML = `
        <div class="direct-confirm-backdrop" data-direct-modal-cancel></div>
        <section class="direct-confirm-card" role="dialog" aria-modal="true" aria-labelledby="direct-confirm-title-v2">
          <div class="direct-confirm-icon ${danger ? "danger" : "trade"}">${danger ? "!" : "▶"}</div>
          <h3 id="direct-confirm-title-v2">${esc(title)}</h3>
          ${intro ? `<p>${esc(intro)}</p>` : ""}
          <div class="direct-confirm-body">${body}</div>
          <div class="direct-confirm-actions">
            <button type="button" class="direct-confirm-cancel" data-direct-modal-cancel>${esc(cancelText)}</button>
            <button type="button" class="direct-confirm-go ${danger ? "danger" : ""}" data-direct-modal-confirm>${esc(confirmText)}</button>
          </div>
        </section>`;
      document.body.appendChild(host);
      const close = (value) => {
        host.classList.add("closing");
        setTimeout(() => host.remove(), 130);
        resolve(value);
      };
      host.querySelectorAll("[data-direct-modal-cancel]").forEach((button) => button.addEventListener("click", () => close(false), { once: true }));
      host.querySelector("[data-direct-modal-confirm]")?.addEventListener("click", () => close(true), { once: true });
    });
  }

  async function confirmStart(target) {
    const summary = strategySummary();
    const conditionRows = summary.conditions.length
      ? summary.conditions.map((item) => `<li>${esc(item)}</li>`).join("")
      : `<li>The strategy currently loaded in Builder/Library will be saved before execution.</li>`;
    const ok = await modal({
      title: "Start this trading strategy?",
      intro: "You are about to start live analysis and automatic execution when the saved entry condition is met.",
      confirmText: "Proceed",
      body: `
        <div class="direct-strategy-confirm-summary">
          <b>${esc(summary.name)}</b>
          <span>${esc(summary.target)} · ${esc(summary.markets)}${summary.stake !== null ? ` · Stake ${esc(summary.stake)}` : ""}</span>
          <ul>${conditionRows}</ul>
        </div>`,
    });
    if (!ok || !target.isConnected) return;
    approvedOnce.add(target);
    target.click();
  }

  async function confirmReset() {
    const ok = await modal({
      title: "Reset all trades?",
      intro: "This clears the current account's full settled run history and resets its local recovery/session counters. An open Deriv contract must finish first.",
      confirmText: "OK, reset all",
      cancelText: "Cancel",
      danger: true,
      body: `<div class="direct-reset-note">Real and virtual settled trades will be cleared for this account. The currently loaded strategy is kept.</div>`,
    });
    if (!ok) return;
    try {
      const response = await window.fetch("/api/me/clear-trades", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: "all" }),
      });
      if (!response.ok) {
        let message = "Reset could not be completed.";
        try { message = String((await response.json())?.detail || message); } catch (_) {}
        await modal({ title: "Reset not completed", intro: message, confirmText: "OK", cancelText: "Close", danger: true });
        return;
      }
      window.dispatchEvent(new CustomEvent("derivadmin:direct-reset-all"));
    } catch (_) {
      await modal({ title: "Reset not completed", intro: "The reset request could not be sent. Your trading state was not changed by this dialog.", confirmText: "OK", cancelText: "Close", danger: true });
    }
  }

  async function confirmDemoReset(resetTarget) {
    const row = resetTarget.closest(".account-dropdown-row.demo,.account-row.demo");
    const managedId = Number(row?.getAttribute("data-account-id") || 0);
    const accountId = String(row?.querySelector("small")?.textContent || "this demo account").split("·")[0].trim();
    const ok = await modal({
      title: "Reset demo balance?",
      intro: `Reset ${accountId} to Deriv's default demo balance of $10,000 USD?`,
      confirmText: "Reset balance",
      cancelText: "Cancel",
      danger: true,
      body: `<div class="direct-reset-note">This calls Deriv's official demo-balance reset API for the exact linked demo account you selected. Real accounts are never reset.</div>`,
    });
    if (!ok) return;
    try {
      const response = await window.fetch("/api/me/reset-demo-balance", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ managed_account_id: managedId || null }),
      });
      let payload = {};
      try { payload = await response.json(); } catch (_) {}
      if (!response.ok) {
        await modal({ title: "Demo balance not reset", intro: String(payload?.detail || "Deriv did not confirm the reset."), confirmText: "OK", cancelText: "Close", danger: true });
        return;
      }
      window.dispatchEvent(new CustomEvent("derivadmin:demo-balance-reset", { detail: payload }));
    } catch (_) {
      await modal({ title: "Demo balance not reset", intro: "The Deriv reset request could not be completed.", confirmText: "OK", cancelText: "Close", danger: true });
    }
  }

  document.addEventListener("click", (event) => {
    const reset = event.target?.closest?.(".global-run-panel [data-run-reset]");
    if (reset) {
      event.preventDefault();
      event.stopImmediatePropagation();
      confirmReset();
      return;
    }

    const demoReset = event.target?.closest?.(".account-dropdown-row.demo [data-demo-reset],.account-dropdown-row.demo em,.account-row.demo [data-demo-reset]");
    if (demoReset) {
      event.preventDefault();
      event.stopImmediatePropagation();
      confirmDemoReset(demoReset);
      return;
    }

    const target = event.target?.closest?.(START_SELECTOR);
    if (!target) return;
    if (approvedOnce.has(target)) {
      approvedOnce.delete(target);
      return;
    }

    const current = engineState();
    const globalRun = Boolean(target.closest(".global-run-panel"));
    if (current.running && globalRun) {
      // A running bot's main button means Stop. Never put a Start confirmation in
      // front of a Stop action.
      return;
    }
    if (current.running && !globalRun) {
      event.preventDefault();
      event.stopImmediatePropagation();
      modal({
        title: "A strategy is already running",
        intro: "Stop the current bot before starting a different strategy. This keeps one execution strategy authoritative at a time.",
        confirmText: "OK",
        cancelText: "Close",
      });
      return;
    }

    event.preventDefault();
    event.stopImmediatePropagation();
    confirmStart(target);
  }, true);

  const style = document.createElement("style");
  style.id = "direct-interaction-guard-v2-style";
  style.textContent = `
    .direct-confirm-modal-v2{position:fixed;inset:0;z-index:2147483000;display:grid;place-items:center;padding:18px;animation:directModalIn .14s ease-out}
    .direct-confirm-modal-v2.closing{opacity:0;transition:opacity .13s ease}
    .direct-confirm-backdrop{position:absolute;inset:0;background:rgba(0,5,14,.76);backdrop-filter:blur(9px)}
    .direct-confirm-card{position:relative;width:min(520px,100%);max-height:min(78vh,700px);overflow:auto;border:1px solid rgba(83,196,255,.24);border-radius:22px;background:linear-gradient(155deg,#0a1d34,#061224);box-shadow:0 34px 100px rgba(0,0,0,.58);padding:22px;color:#eef8ff}
    .direct-confirm-icon{width:44px;height:44px;border-radius:14px;display:grid;place-items:center;margin-bottom:14px;background:rgba(48,197,255,.12);border:1px solid rgba(72,216,255,.25);color:#58dcff;font-weight:900}.direct-confirm-icon.danger{background:rgba(255,90,110,.11);border-color:rgba(255,90,110,.27);color:#ff7a8c}
    .direct-confirm-card h3{font-size:20px;margin:0 0 8px}.direct-confirm-card>p{font-size:12px;line-height:1.6;color:#91a7bd;margin:0 0 14px}.direct-confirm-body{font-size:11px;color:#c8d8e7}.direct-strategy-confirm-summary{padding:14px;border-radius:16px;background:#07182b;border:1px solid rgba(116,190,255,.13)}.direct-strategy-confirm-summary>b{display:block;font-size:13px;color:#fff}.direct-strategy-confirm-summary>span{display:block;color:#5fdcff;margin-top:5px;font-size:10px}.direct-strategy-confirm-summary ul{margin:12px 0 0;padding-left:18px;color:#9bb0c5;line-height:1.65}.direct-reset-note{padding:12px 13px;border-radius:14px;background:rgba(255,255,255,.035);color:#9db0c4;line-height:1.55}
    .direct-confirm-actions{display:grid;grid-template-columns:1fr 1.25fr;gap:9px;margin-top:18px}.direct-confirm-actions button{min-height:45px;border-radius:13px;font-weight:800;font-size:11px;cursor:pointer}.direct-confirm-cancel{border:1px solid rgba(138,180,215,.17);background:#09182a;color:#9eb4c8}.direct-confirm-go{border:0;background:linear-gradient(120deg,#246cff,#38cfff);color:white}.direct-confirm-go.danger{background:linear-gradient(120deg,#d53b56,#ff6b72)}
    @keyframes directModalIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  `;
  document.head.appendChild(style);

  window.DERIVADMIN_DIRECT_INTERACTION_GUARD_V2 = Object.freeze({ version: "20260818-interaction-v2.1" });
})();
