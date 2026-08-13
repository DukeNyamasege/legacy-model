(() => {
  "use strict";

  if (window.__FOA_STOPPED_UI_AUTHORITY__) return;
  window.__FOA_STOPPED_UI_AUTHORITY__ = true;

  const READY_TEXT = "Ready - Auto trading is stopped. Press Start Auto Trading to begin a fresh session.";
  let scheduled = false;

  function currentSnapshot() {
    const cache = window.FOA_NETLIFY_LIVE_CACHE || {};
    return {
      me: cache.me || window.FOA_BOOT_SESSION || null,
      lifecycle: cache.lifecycle || null,
    };
  }

  function genuineCurrentRiskStop(me, lifecycle) {
    const gate = window.FOA_RISK_STOP_SESSION_GATE;
    if (!gate?.observe || !me || !lifecycle) return false;
    try {
      return Boolean(gate.observe(me, lifecycle)?.confirmedRiskStop);
    } catch (_) {
      return false;
    }
  }

  function stopped(me, lifecycle) {
    if (!me?.authenticated) return false;
    if (me.enabled === false) return true;
    if (lifecycle?.enabled === false) return true;
    return String(lifecycle?.runtime_state || "").toUpperCase() === "STOPPED";
  }

  function enforce() {
    scheduled = false;
    const { me, lifecycle } = currentSnapshot();
    if (!stopped(me, lifecycle)) return;
    if (genuineCurrentRiskStop(me, lifecycle)) return;

    document.querySelectorAll(".foa-account-risk-notifier,.foa-final-limit-notifier,.limit-notifier")
      .forEach((node) => node.remove());

    document.querySelectorAll(".builder-status-line").forEach((line) => {
      line.dataset.runtimeState = "STOPPED";
      line.dataset.stoppedAuthority = "true";
      line.classList.remove(
        "tp", "sl", "take-profit", "stop-loss", "risk-hit", "danger", "warning",
      );
      const copy = line.querySelector("span");
      if (copy && copy.textContent !== READY_TEXT) copy.textContent = READY_TEXT;
    });

    document.querySelectorAll("[data-main-action]").forEach((button) => {
      if (button.dataset.mainAction !== "start") button.dataset.mainAction = "start";
      if (button.textContent !== "Start Auto Trading") button.textContent = "Start Auto Trading";
      button.classList.remove("danger");
      button.disabled = false;
    });

    document.querySelectorAll(".trades-control-panel").forEach((panel) => {
      const title = panel.querySelector("h2");
      const paragraphs = panel.querySelectorAll("p");
      if (title) title.textContent = "Ready";
      if (paragraphs.length > 1) paragraphs[1].textContent = "Auto trading is stopped.";
    });
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(enforce);
  }

  const previousFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const response = await previousFetch(input, init);
    const url = typeof input === "string" ? input : String(input?.url || "");
    const method = String(init?.method || input?.method || "GET").toUpperCase();
    if (response.ok && (
      (method === "GET" && (url.includes("/me/trading-lifecycle") || url.includes("/me/live-snapshot")))
      || (method === "POST" && (url.includes("/me/stop-trading") || url.includes("/me/auto-trade")))
    )) {
      window.setTimeout(schedule, 0);
      window.setTimeout(schedule, 120);
    }
    return response;
  };

  new MutationObserver(schedule).observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  window.addEventListener("pageshow", schedule);
  window.addEventListener("focus", schedule);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) schedule();
  });
  window.setInterval(schedule, 500);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", schedule, { once: true })
    : schedule();

  window.FOA_STOPPED_UI_AUTHORITY_VERSION = "20260813-1";
})();
