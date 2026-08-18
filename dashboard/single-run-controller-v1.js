(() => {
  "use strict";

  if (window.__DERIVADMIN_SINGLE_RUN_CONTROLLER_V1__) return;
  window.__DERIVADMIN_SINGLE_RUN_CONTROLLER_V1__ = true;

  const ACTIVE_STATES = new Set(["STARTING", "WAITING_FOR_CONDITION", "EXECUTING", "RUNNING"]);
  const TIMEOUT_NOISE = /backend request timed out|backend did not answer|backend timeout|timed out after\s+\d/i;
  const CONTROL_ROOT = ".global-run-panel";

  const state = {
    lifecycle: "",
    runtimeState: "",
    running: false,
    desired: null,
    requestInFlight: false,
    stopRetryAt: 0,
    startRetryAt: 0,
    syncTimer: null,
    mutationQueued: false,
  };

  function apiPath(path) {
    const value = String(path || "");
    return value.startsWith("/api/") ? value : `/api${value}`;
  }

  function xhr(method, path, payload, timeoutMs = 8000) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open(method, apiPath(path), true);
      request.withCredentials = true;
      request.timeout = timeoutMs;
      request.setRequestHeader("Accept", "application/json");
      if (payload !== undefined) request.setRequestHeader("Content-Type", "application/json");
      request.onload = () => {
        let body = {};
        try { body = JSON.parse(request.responseText || "{}"); } catch (_) {}
        if (request.status >= 200 && request.status < 300) resolve(body);
        else reject(Object.assign(new Error(String(body?.detail || `Request failed (${request.status})`)), { status: request.status, body }));
      };
      request.onerror = () => reject(new Error("connection unavailable"));
      request.ontimeout = () => reject(new Error("connection unavailable"));
      try { request.send(payload === undefined ? null : JSON.stringify(payload)); } catch (error) { reject(error); }
    });
  }

  function isActivePayload(payload) {
    const lifecycle = String(payload?.lifecycle || "").toLowerCase();
    const runtime = String(payload?.runtime_state || payload?.execution_status || "").toUpperCase();
    return payload?.enabled === true || lifecycle === "running" || ACTIVE_STATES.has(runtime);
  }

  function removeTimeoutMessages() {
    document.querySelectorAll(".global-message,.premium-message,[role='alert']").forEach((node) => {
      if (TIMEOUT_NOISE.test(String(node.textContent || ""))) node.remove();
    });
  }

  function removeInnerRunPanel() {
    document.querySelectorAll(".app-main .run-panel").forEach((page) => {
      if (!page.querySelector(".run-ledger") && !page.querySelector(".run-controls")) return;
      page.querySelector(".run-controls")?.remove();
      page.querySelector(".run-account-bar")?.remove();
      page.classList.remove("run-panel");
      page.classList.add("transactions-only-page");
    });

    // There is one execution authority: the fixed global Run panel. Any page-level
    // Start/Stop control is retired so two buttons can never issue opposing calls.
    document.querySelectorAll("[data-start-trading],[data-stop-trading]").forEach((button) => {
      if (!button.closest(CONTROL_ROOT)) button.remove();
    });
  }

  function setMainState(running) {
    state.running = Boolean(running);
    const panel = document.querySelector(CONTROL_ROOT);
    if (!panel) return;

    panel.dataset.executionState = state.running ? "running" : "stopped";
    panel.querySelectorAll("[data-run-start]").forEach((button) => {
      button.dataset.singleRunState = state.running ? "stop" : "start";
      button.setAttribute("aria-label", state.running ? "Stop trading" : "Start trading");
      const span = button.querySelector("span");
      if (span) span.textContent = state.running ? "Stop" : "Run";
      else {
        const textNode = Array.from(button.childNodes).find((node) => node.nodeType === Node.TEXT_NODE && String(node.textContent || "").trim());
        if (textNode) textNode.textContent = state.running ? " Stop" : " Run";
      }
    });
    panel.querySelectorAll("[data-run-execution-toggle]").forEach((toggle) => {
      toggle.classList.toggle("on", state.running);
      toggle.dataset.singleRunState = state.running ? "stop" : "start";
      toggle.setAttribute("aria-pressed", state.running ? "true" : "false");
      toggle.setAttribute("aria-label", state.running ? "Stop trading" : "Start trading");
    });
  }

  function openTransactions() {
    const panel = document.querySelector(CONTROL_ROOT);
    if (!panel) return;
    if (panel.classList.contains("collapsed")) panel.querySelector("[data-run-panel-toggle]")?.click();
    const tab = panel.querySelector('[data-run-tab="transactions"]');
    if (tab && !tab.classList.contains("active")) tab.click();
  }

  function renderCleanup() {
    removeTimeoutMessages();
    removeInnerRunPanel();
    setMainState(state.desired === "running" ? true : state.desired === "stopped" ? false : state.running);
  }

  async function syncLifecycle() {
    try {
      const payload = await xhr("GET", "/me/trading-lifecycle", undefined, 6500);
      state.lifecycle = String(payload?.lifecycle || "").toLowerCase();
      state.runtimeState = String(payload?.runtime_state || payload?.execution_status || "").toUpperCase();
      const serverRunning = isActivePayload(payload);

      if (state.desired === "stopped") {
        setMainState(false);
        if (!serverRunning) {
          state.desired = null;
          state.running = false;
        } else if (!state.requestInFlight && Date.now() >= state.stopRetryAt) {
          state.stopRetryAt = Date.now() + 1600;
          void stopExecution({ retry: true });
        }
      } else if (state.desired === "running") {
        setMainState(true);
        if (serverRunning) {
          state.desired = null;
          state.running = true;
        } else if (!state.requestInFlight && Date.now() >= state.startRetryAt) {
          state.startRetryAt = Date.now() + 2200;
          void startExecution({ retry: true });
        }
      } else {
        state.running = serverRunning;
        setMainState(serverRunning);
      }
    } catch (_) {
      // Runtime polling is best effort. A transient backend/network delay must not
      // replace the dashboard with a red timeout banner or create a second state.
    } finally {
      renderCleanup();
    }
  }

  async function stopExecution({ retry = false } = {}) {
    if (state.requestInFlight && retry) return;
    state.desired = "stopped";
    state.running = false;
    setMainState(false);
    state.requestInFlight = true;
    try {
      await xhr("POST", "/me/stop-trading", {}, 8500);
    } catch (_) {
      // Stop is idempotent. Keep the UI in Stop-requested state and retry from the
      // lifecycle loop instead of surfacing transport timing details to the user.
      state.stopRetryAt = Date.now() + 1200;
    } finally {
      state.requestInFlight = false;
      window.setTimeout(syncLifecycle, 120);
    }
  }

  async function startExecution({ retry = false } = {}) {
    if (state.requestInFlight && retry) return;
    state.desired = "running";
    state.running = true;
    setMainState(true);
    openTransactions();
    state.requestInFlight = true;
    try {
      const freshStart = state.lifecycle === "stopped" || state.lifecycle === "";
      await xhr("POST", "/me/resume-trading", { mode: freshStart ? "start_again" : "continue" }, 12000);
    } catch (_) {
      state.startRetryAt = Date.now() + 1800;
    } finally {
      state.requestInFlight = false;
      window.setTimeout(syncLifecycle, 150);
    }
  }

  async function resetMainPanel() {
    const panel = document.querySelector(CONTROL_ROOT);
    const reset = panel?.querySelector("[data-run-reset]");
    if (reset) {
      reset.disabled = true;
      reset.setAttribute("aria-busy", "true");
    }
    try {
      await xhr("POST", "/me/clear-trades", { scope: "today" }, 12000);
      // Clear visible counters immediately, then let the canonical shell refresh
      // repopulate from the now-empty account-scoped ledger.
      panel?.querySelectorAll(".run-panel-stats b,.run-stat b").forEach((value) => { value.textContent = "0"; });
      try {
        const refresh = window.FOA_FINAL_UI?.refresh;
        if (typeof refresh === "function") await refresh({ quiet: true });
      } catch (_) {}
    } catch (error) {
      // 409 means a contract is still settling; leave the current ledger intact.
      // Transport delays remain silent and can be retried by pressing Reset again.
      if (Number(error?.status || 0) === 409) {
        const body = panel?.querySelector(".run-panel-body");
        if (body && !body.querySelector(".single-run-reset-note")) {
          const note = document.createElement("div");
          note.className = "single-run-reset-note";
          note.textContent = "Reset will be available after the open contract settles.";
          body.prepend(note);
          window.setTimeout(() => note.remove(), 4500);
        }
      }
    } finally {
      if (reset) {
        reset.disabled = false;
        reset.removeAttribute("aria-busy");
      }
      removeTimeoutMessages();
    }
  }

  document.addEventListener("click", (event) => {
    const target = event.target?.closest?.(`${CONTROL_ROOT} [data-run-start],${CONTROL_ROOT} [data-run-execution-toggle]`);
    if (target) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const currentlyRunning = state.desired === "running" || (state.desired !== "stopped" && state.running);
      if (currentlyRunning) void stopExecution();
      else void startExecution();
      return;
    }

    const reset = event.target?.closest?.(`${CONTROL_ROOT} [data-run-reset]`);
    if (reset) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void resetMainPanel();
    }
  }, true);

  const observer = new MutationObserver(() => {
    if (state.mutationQueued) return;
    state.mutationQueued = true;
    requestAnimationFrame(() => {
      state.mutationQueued = false;
      renderCleanup();
    });
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  const style = document.createElement("style");
  style.id = "single-run-controller-v1-style";
  style.textContent = `
    .transactions-only-page{display:flex;flex-direction:column;gap:14px}
    .transactions-only-page>.run-controls,.transactions-only-page>.run-account-bar{display:none!important}
    .single-run-reset-note{margin:6px 0 10px;padding:9px 11px;border:1px solid rgba(77,174,255,.2);border-radius:10px;background:rgba(15,72,123,.18);font-size:12px;color:#a9c7e8}
    ${CONTROL_ROOT}[data-execution-state="running"] [data-run-start]{cursor:pointer}
    ${CONTROL_ROOT} [data-run-reset][aria-busy="true"]{opacity:.55;pointer-events:none}
  `;
  document.head.appendChild(style);

  renderCleanup();
  void syncLifecycle();
  state.syncTimer = window.setInterval(syncLifecycle, 2500);
  window.addEventListener("focus", syncLifecycle);
  window.addEventListener("pageshow", syncLifecycle);

  window.DERIVADMIN_SINGLE_RUN_CONTROLLER_V1 = Object.freeze({
    version: "20260818-single-run-v1",
    sync: syncLifecycle,
    stop: stopExecution,
    start: startExecution,
  });
})();
