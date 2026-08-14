(() => {
  "use strict";

  const POLL_MS = 1200;
  const ACTIVE_RUNTIME_STATES = new Set([
    "STARTING",
    "WAITING_FOR_CONDITION",
    "EXECUTING",
    "RUNNING",
  ]);
  let polling = false;
  let currentStopKey = "";
  const dismissedStopKeys = new Set();
  const fallbackActiveSessions = new Map();

  function selectedMode() {
    const active = document.querySelector('[data-mode="demo"].active, [data-mode="real"].active');
    return String(active?.dataset?.mode || "").toLowerCase();
  }

  function normalizedMode(value) {
    return String(value || "demo").toLowerCase() === "real" ? "real" : "demo";
  }

  function accountMask(value) {
    return String(
      value?.account_id_masked
      || value?.account_id
      || value?.label
      || "",
    ).trim();
  }

  function managedId(value) {
    const raw = Number(value?.managed_account_id ?? value?.id ?? 0);
    return Number.isFinite(raw) && raw > 0 ? String(Math.trunc(raw)) : "";
  }

  function accountIdentity(me) {
    return `${normalizedMode(me?.account_type)}:${managedId(me) || accountMask(me) || "unknown"}`;
  }

  function lifecycleMatchesAccount(me, lifecycle) {
    if (!me?.authenticated || !lifecycle?.authenticated) return false;

    const currentMode = normalizedMode(me.account_type);
    const uiMode = selectedMode();
    if (uiMode && uiMode !== currentMode) return false;

    if (lifecycle.account_type && normalizedMode(lifecycle.account_type) !== currentMode) {
      return false;
    }

    const meId = managedId(me);
    const lifeId = managedId(lifecycle);
    if (meId && lifeId && meId !== lifeId) return false;

    const meMask = accountMask(me);
    const lifeMask = accountMask(lifecycle);
    if (meMask && lifeMask && meMask !== lifeMask) return false;

    return true;
  }

  function sessionKey(lifecycle) {
    return String(lifecycle?.session_limits_started_at || "").trim();
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
    currentStopKey = "";
  }

  function exactLimitMatchesCurrentAccount(me, lifecycle) {
    if (!lifecycleMatchesAccount(me, lifecycle)) return false;

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

  function fallbackConfirmedTransition(me, lifecycle) {
    if (!lifecycleMatchesAccount(me, lifecycle)) return false;

    const key = accountIdentity(me);
    const session = sessionKey(lifecycle);
    const runtime = String(lifecycle?.runtime_state || "").toUpperCase();
    const status = String(lifecycle?.execution_status || "").toLowerCase();

    if (lifecycle?.enabled === true && ACTIVE_RUNTIME_STATES.has(runtime) && session) {
      fallbackActiveSessions.set(key, session);
      return false;
    }

    return Boolean(
      ["take_profit", "stop_loss"].includes(status)
      && lifecycle?.enabled === false
      && session
      && fallbackActiveSessions.get(key) === session,
    );
  }

  function confirmedTransition(me, lifecycle) {
    const gate = window.FOA_RISK_STOP_SESSION_GATE;
    if (gate?.observe) {
      const result = gate.observe(me, lifecycle);
      return Boolean(result?.confirmedRiskStop);
    }
    return fallbackConfirmedTransition(me, lifecycle);
  }

  function stopEventKey(me, lifecycle) {
    const status = String(lifecycle.execution_status || "").toLowerCase();
    const achieved = Number(lifecycle.limit_achieved ?? lifecycle.session_profit ?? 0).toFixed(2);
    const updated = String(lifecycle.execution_status_updated_at || "").trim();
    return [
      accountIdentity(me),
      sessionKey(lifecycle),
      status,
      updated,
      achieved,
    ].join(":");
  }

  function renderNotice(me, lifecycle) {
    if (!exactLimitMatchesCurrentAccount(me, lifecycle) || !confirmedTransition(me, lifecycle)) {
      removeNotice();
      return;
    }

    const eventKey = stopEventKey(me, lifecycle);
    if (!eventKey || dismissedStopKeys.has(eventKey)) {
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
      document.body.appendChild(notice);
    }

    if (currentStopKey === eventKey && notice.dataset.stopEvent === eventKey) return;

    currentStopKey = eventKey;
    notice.dataset.stopEvent = eventKey;
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
      dismissedStopKeys.add(eventKey);
      notice.remove();
      currentStopKey = "";
    }, { once: true });
  }

  async function poll() {
    if (polling || document.hidden) return;
    polling = true;
    try {
      const me = await getJSON("/me");
      if (!me?.authenticated) {
        removeNotice();
        return;
      }
      const lifecycle = await getJSON("/me/trading-lifecycle");
      renderNotice(me, lifecycle);
    } catch (_) {
    } finally {
      polling = false;
    }
  }

  function invalidateForAccountSwitch() {
    removeNotice();
    fallbackActiveSessions.clear();
    window.FOA_RISK_STOP_SESSION_GATE?.resetForAccountSwitch?.();
    window.setTimeout(poll, 900);
    window.setTimeout(poll, 1800);
  }

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-mode]")) {
      invalidateForAccountSwitch();
      return;
    }
    if (event.target?.closest?.('[data-main-action="start"],[data-main-action="resume"]')) {
      removeNotice();
    }
  }, true);

  window.addEventListener("focus", poll);
  window.addEventListener("pageshow", poll);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });

  window.setInterval(poll, POLL_MS);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", () => window.setTimeout(poll, 350), { once: true })
    : window.setTimeout(poll, 350);

  window.FOA_ACCOUNT_BOUND_RISK_NOTICE_VERSION = "20260814-3";
})();
