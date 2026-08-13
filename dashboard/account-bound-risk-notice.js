(() => {
  "use strict";

  const POLL_MS = 1200;
  let polling = false;
  let suppressUntil = 0;

  function selectedMode() {
    const active = document.querySelector('[data-mode="demo"].active, [data-mode="real"].active');
    return String(active?.dataset?.mode || "").toLowerCase();
  }

  function normalizedMode(value) {
    return String(value || "demo").toLowerCase() === "real" ? "real" : "demo";
  }

  function accountMask(me) {
    return String(me?.account_id_masked || me?.account_id || me?.label || "").trim();
  }

  function money(value, currency = "USD") {
    const amount = Number(value || 0);
    const unit = String(currency || "USD").toUpperCase();
    const prefix = unit === "USD" ? "$" : `${unit} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  async function getJSON(path) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`${path} returned ${response.status}`);
    return response.json();
  }

  function removeNotice() {
    document.querySelectorAll(".foa-account-risk-notifier").forEach((node) => node.remove());
  }

  function exactLimitMatchesCurrentAccount(me, lifecycle) {
    if (!me?.authenticated || !lifecycle?.authenticated) return false;
    const currentMode = normalizedMode(me.account_type);
    const uiMode = selectedMode();
    if (uiMode && uiMode !== currentMode) return false;

    const status = String(lifecycle.execution_status || "").toLowerCase();
    const meStatus = String(me.execution_status || "").toLowerCase();
    if (!["take_profit", "stop_loss"].includes(status)) return false;
    if (meStatus !== status) return false;
    if (Boolean(me.enabled) || Boolean(lifecycle.enabled)) return false;
    if (lifecycle.risk_limit_is_hard_stop !== true) return false;

    const configured = Number(
      status === "take_profit"
        ? me?.settings?.take_profit
        : me?.settings?.stop_loss,
    );
    const rawTarget = Number(lifecycle.limit_target || 0);
    const targetMagnitude = Math.abs(rawTarget);
    if (!Number.isFinite(configured) || !Number.isFinite(targetMagnitude)) return false;
    if (Math.abs(Math.abs(configured) - targetMagnitude) > 0.005) return false;
    return targetMagnitude > 0;
  }

  function renderNotice(me, lifecycle) {
    if (!exactLimitMatchesCurrentAccount(me, lifecycle)) {
      removeNotice();
      return;
    }

    const status = String(lifecycle.execution_status || "").toLowerCase();
    const isTp = status === "take_profit";
    const rawTarget = Number(lifecycle.limit_target || 0);
    const target = isTp ? Math.abs(rawTarget) : -Math.abs(rawTarget);
    const achieved = Number(lifecycle.limit_achieved ?? lifecycle.session_profit ?? 0);
    const currency = me.currency || "USD";
    const mode = normalizedMode(me.account_type);
    const account = accountMask(me);

    let notice = document.querySelector(".foa-account-risk-notifier");
    if (!notice) {
      notice = document.createElement("aside");
      notice.className = "foa-account-risk-notifier";
      notice.setAttribute("role", "status");
      notice.setAttribute("aria-live", "polite");
      (document.querySelector("#foa-simple-app") || document.body).appendChild(notice);
    }

    notice.dataset.account = account;
    notice.dataset.accountType = mode;
    notice.dataset.limit = status;
    notice.innerHTML = `
      <div class="foa-account-risk-icon">${isTp ? "TP" : "SL"}</div>
      <div class="foa-account-risk-copy">
        <strong>${isTp ? "Take Profit hit — trading stopped" : "Stop Loss hit — trading stopped"}</strong>
        <span>Target ${money(target, currency)} · Session P/L ${money(achieved, currency)}</span>
        <small>${mode === "real" ? "Real" : "Demo"} · ${account || "current trading account"}</small>
      </div>
      <button type="button" class="foa-account-risk-ok">OK</button>`;

    notice.querySelector(".foa-account-risk-ok")?.addEventListener("click", () => {
      notice.remove();
      suppressUntil = Date.now() + 30000;
    }, { once: true });
  }

  async function poll() {
    if (polling || document.hidden || Date.now() < suppressUntil) return;
    polling = true;
    try {
      // Read /me first and lifecycle second. A Demo/Real switch between the two
      // reads cannot create a notice because both status and configured target
      // must still agree with the currently selected account.
      const me = await getJSON("/me");
      if (!me?.authenticated) {
        removeNotice();
        return;
      }
      const lifecycle = await getJSON("/me/trading-lifecycle");
      renderNotice(me, lifecycle);
    } catch (_) {
      // A delayed UI read never changes or stops backend execution.
    } finally {
      polling = false;
    }
  }

  function invalidateForAccountSwitch() {
    removeNotice();
    suppressUntil = Date.now() + 800;
    window.setTimeout(poll, 900);
    window.setTimeout(poll, 1800);
  }

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-mode]")) invalidateForAccountSwitch();
  }, true);

  window.addEventListener("focus", poll);
  window.addEventListener("pageshow", poll);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });

  // Legacy risk notices are intentionally hidden by the matching CSS asset. This
  // account-bound authority is the only layer allowed to render TP/SL notices.
  window.setInterval(poll, POLL_MS);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", () => window.setTimeout(poll, 350), { once: true })
    : window.setTimeout(poll, 350);

  window.FOA_ACCOUNT_BOUND_RISK_NOTICE_VERSION = "20260813-1";
})();
