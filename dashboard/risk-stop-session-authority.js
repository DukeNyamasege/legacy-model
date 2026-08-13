(() => {
  "use strict";

  if (window.__FOA_RISK_STOP_SESSION_AUTHORITY__) return;
  window.__FOA_RISK_STOP_SESSION_AUTHORITY__ = true;

  const ACTIVE_RUNTIME_STATES = new Set([
    "STARTING",
    "WAITING_FOR_CONDITION",
    "EXECUTING",
    "RUNNING",
  ]);
  const RISK_STATUSES = new Set(["take_profit", "stop_loss"]);
  const activeSessionByAccount = new Map();
  const confirmedStopEvents = new Set();

  let lastMe = null;
  let lastLifecycle = null;
  let scheduled = false;

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

  function accountKey(me) {
    const id = managedId(me);
    const mask = accountMask(me);
    return `${normalizedMode(me?.account_type)}:${id || mask || "unknown"}`;
  }

  function lifecycleMatchesAccount(me, lifecycle) {
    if (!me?.authenticated || !lifecycle?.authenticated) return false;

    const meMode = normalizedMode(me.account_type);
    const lifeMode = lifecycle.account_type
      ? normalizedMode(lifecycle.account_type)
      : meMode;
    if (lifeMode !== meMode) return false;

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

  function status(lifecycle) {
    return String(lifecycle?.execution_status || "").toLowerCase();
  }

  function isActiveLifecycle(lifecycle) {
    const runtime = String(lifecycle?.runtime_state || "").toUpperCase();
    return lifecycle?.enabled === true && ACTIVE_RUNTIME_STATES.has(runtime);
  }

  function stopEventKey(me, lifecycle) {
    const achieved = Number(
      lifecycle?.limit_achieved ?? lifecycle?.session_profit ?? 0,
    ).toFixed(2);
    const updated = String(lifecycle?.execution_status_updated_at || "").trim();
    return [
      accountKey(me),
      sessionKey(lifecycle),
      status(lifecycle),
      updated,
      achieved,
    ].join(":");
  }

  function observe(me, lifecycle) {
    if (!lifecycleMatchesAccount(me, lifecycle)) {
      return {
        matches: false,
        riskStop: false,
        confirmedRiskStop: false,
        accountKey: "",
        sessionKey: "",
        eventKey: "",
      };
    }

    lastMe = me;
    lastLifecycle = lifecycle;

    const key = accountKey(me);
    const session = sessionKey(lifecycle);
    const currentStatus = status(lifecycle);

    if (isActiveLifecycle(lifecycle) && session) {
      activeSessionByAccount.set(key, session);
    }

    const riskStop = RISK_STATUSES.has(currentStatus)
      && lifecycle?.enabled === false
      && lifecycle?.risk_limit_is_hard_stop === true;

    const confirmedRiskStop = Boolean(
      riskStop
      && session
      && activeSessionByAccount.get(key) === session,
    );

    const eventKey = riskStop ? stopEventKey(me, lifecycle) : "";
    if (confirmedRiskStop && eventKey) confirmedStopEvents.add(eventKey);

    return {
      matches: true,
      riskStop,
      confirmedRiskStop,
      accountKey: key,
      sessionKey: session,
      eventKey,
    };
  }

  function isConfirmedStop(me, lifecycle) {
    const result = observe(me, lifecycle);
    return result.confirmedRiskStop;
  }

  function resetForAccountSwitch() {
    activeSessionByAccount.clear();
    confirmedStopEvents.clear();
    lastLifecycle = null;
    document.querySelectorAll(
      ".foa-account-risk-notifier,.foa-final-limit-notifier,.limit-notifier",
    ).forEach((node) => node.remove());
    schedule();
  }

  function genericStoppedText() {
    return "Ready - Auto trading is stopped. Press Start Auto Trading to begin a fresh session.";
  }

  function sanitizeDom() {
    scheduled = false;

    const cache = window.FOA_NETLIFY_LIVE_CACHE || {};
    const me = cache.me || lastMe || window.FOA_BOOT_SESSION || null;
    const lifecycle = cache.lifecycle || lastLifecycle || null;
    if (!me || !lifecycle) return;

    const observation = observe(me, lifecycle);

    // All old risk-notice layers stay retired. The only visible TP/SL notice is
    // .foa-account-risk-notifier, and it is allowed only for a confirmed
    // running -> TP/SL transition in the same account and same Start session.
    document.querySelectorAll(".foa-final-limit-notifier,.limit-notifier")
      .forEach((node) => node.remove());

    if (observation.riskStop && !observation.confirmedRiskStop) {
      document.querySelectorAll(".foa-account-risk-notifier")
        .forEach((node) => node.remove());

      document.querySelectorAll(".builder-status-line").forEach((line) => {
        line.dataset.runtimeState = "STOPPED";
        line.dataset.riskStopSuppressed = "true";
        line.classList.remove(
          "tp",
          "sl",
          "take-profit",
          "stop-loss",
          "risk-hit",
          "danger",
          "warning",
        );
        const copy = line.querySelector("span");
        const text = genericStoppedText();
        if (copy && copy.textContent !== text) copy.textContent = text;
      });
      return;
    }

    document.querySelectorAll(".builder-status-line").forEach((line) => {
      delete line.dataset.riskStopSuppressed;
    });
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(sanitizeDom);
  }

  function installFetchBridge() {
    if (window.__FOA_RISK_STOP_SESSION_FETCH_BRIDGE__) return;
    window.__FOA_RISK_STOP_SESSION_FETCH_BRIDGE__ = true;

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const response = await originalFetch(input, init);
      const rawUrl = typeof input === "string" ? input : String(input?.url || "");
      const method = String(init?.method || input?.method || "GET").toUpperCase();

      if (method === "GET" && response.ok) {
        try {
          if (/\/me(?:\?|$)/.test(rawUrl) && !rawUrl.includes("/me/")) {
            const me = await response.clone().json();
            if (me?.authenticated) {
              const previous = lastMe ? accountKey(lastMe) : "";
              const next = accountKey(me);
              if (previous && next && previous !== next) resetForAccountSwitch();
              lastMe = me;
            }
          } else if (rawUrl.includes("/me/trading-lifecycle")) {
            const lifecycle = await response.clone().json();
            lastLifecycle = lifecycle;
            const me = window.FOA_NETLIFY_LIVE_CACHE?.me || lastMe || window.FOA_BOOT_SESSION;
            if (me) observe(me, lifecycle);
            schedule();
          } else if (rawUrl.includes("/me/live-snapshot")) {
            const snapshot = await response.clone().json();
            if (snapshot?.me && snapshot?.lifecycle) {
              lastMe = snapshot.me;
              lastLifecycle = snapshot.lifecycle;
              observe(snapshot.me, snapshot.lifecycle);
              schedule();
            }
          }
        } catch (_) {}
      }

      return response;
    };
  }

  installFetchBridge();

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-mode]")) {
      resetForAccountSwitch();
      return;
    }
    if (event.target?.closest?.('[data-main-action="start"],[data-main-action="resume"]')) {
      const me = window.FOA_NETLIFY_LIVE_CACHE?.me || lastMe || window.FOA_BOOT_SESSION;
      if (me) activeSessionByAccount.delete(accountKey(me));
      document.querySelectorAll(".foa-account-risk-notifier")
        .forEach((node) => node.remove());
      schedule();
    }
  }, true);

  const observer = new MutationObserver(schedule);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });

  window.addEventListener("pageshow", schedule);
  window.addEventListener("focus", schedule);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) schedule();
  });

  window.setInterval(() => {
    const cache = window.FOA_NETLIFY_LIVE_CACHE || {};
    if (cache.me && cache.lifecycle) observe(cache.me, cache.lifecycle);
    schedule();
  }, 500);

  window.FOA_RISK_STOP_SESSION_GATE = {
    observe,
    isConfirmedStop,
    resetForAccountSwitch,
    confirmedStopEvents,
  };
  window.FOA_RISK_STOP_SESSION_AUTHORITY_VERSION = "20260813-1";
})();