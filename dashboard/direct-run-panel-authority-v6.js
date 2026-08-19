(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_RUN_PANEL_AUTHORITY_V6__) return;
  window.__DERIVADMIN_DIRECT_RUN_PANEL_AUTHORITY_V6__ = true;

  /*
   * FINAL RUN-PANEL PRESENTATION / STOP AUTHORITY
   *
   * - START is still owned by the single browser-direct execution engine.
   * - STOP first closes the synchronous browser BUY fence, then persists the
   *   server hard-stop sentinel.  No status acknowledgement is needed before the
   *   visible button becomes Start.
   * - Reset clears history only. It never changes execution state.
   * - No status banners, no auto-expand, no 400ms DOM repaint loop.
   */

  const STATUS_URL = "/api/me/direct-execution/status";
  const STOP_URL = "/api/me/direct-execution/stop";

  const state = {
    serverActive: false,
    serverOwner: "stopped",
    userStopLatch: false,
    stopRetry: null,
    statusTimer: null,
    renderQueued: false,
    resetUntil: 0,
  };

  function engine() {
    return window.DERIVADMIN_DIRECT_EXECUTION_V1 || null;
  }

  function engineState() {
    try { return engine()?.state?.() || {}; }
    catch (_) { return {}; }
  }

  function browserRunning() {
    return Boolean(engineState().running);
  }

  function effectiveRunning() {
    if (browserRunning()) {
      state.userStopLatch = false;
      return true;
    }
    if (state.userStopLatch) return false;
    return Boolean(state.serverActive);
  }

  function queueRender() {
    if (state.renderQueued) return;
    state.renderQueued = true;
    requestAnimationFrame(() => {
      state.renderQueued = false;
      render();
    });
  }

  function render() {
    const running = effectiveRunning();
    document.documentElement.dataset.finalRunState = running ? "running" : "stopped";
    document.documentElement.dataset.finalRunOwner = browserRunning() ? "browser" : state.serverOwner;

    const panel = document.querySelector(".global-run-panel");
    if (!panel) return;
    panel.dataset.finalRunState = running ? "running" : "stopped";

    // There is exactly one execution action and one expand/collapse chevron.
    panel.querySelectorAll("[data-run-execution-toggle],.run-panel-execution").forEach((node) => node.remove());
    panel.querySelectorAll("[data-run-start]").forEach((button) => {
      button.dataset.finalRunState = running ? "stop" : "start";
      button.setAttribute("aria-label", running ? "Stop trading" : "Start trading");
      button.setAttribute("title", running ? "Stop trading" : "Start trading");
    });

    // Old runtime helpers are deliberately not allowed any visible layout space.
    panel.querySelectorAll(
      ".direct-final-run-state-v5,.direct-final-run-state-v6,.direct-bot-state-pill,.direct-execution-state,.direct-loaded-strategy-badge"
    ).forEach((node) => node.remove());

    // Reset is local-first. While the backend clear is settling, do not allow an
    // old server response to visually repopulate the ledger for a few seconds.
    if (Date.now() < state.resetUntil) {
      panel.querySelectorAll(".transaction-rows").forEach((rows) => { rows.innerHTML = ""; });
      panel.querySelectorAll(".run-panel-stats b,.run-stat b").forEach((node) => {
        if (!node.closest("[data-run-start]")) node.textContent = "0";
      });
    }
  }

  async function readServerStatus() {
    if (browserRunning()) {
      state.serverActive = false;
      state.serverOwner = "browser";
      queueRender();
      return;
    }
    try {
      const response = await window.fetch(STATUS_URL, {
        credentials: "include",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json();
      const owner = String(payload?.owner || "stopped").toLowerCase();
      const stopped = payload?.hard_stop === true || payload?.enabled === false || owner === "stopped";
      state.serverOwner = stopped ? "stopped" : owner;
      state.serverActive = !stopped;
      if (stopped) state.userStopLatch = true;
      queueRender();
    } catch (_) {
      // Status is advisory. Never show a raw backend timeout to the user and never
      // flip a locally stopped button back to Stop merely because a read failed.
    }
  }

  function xhrStop() {
    clearTimeout(state.stopRetry);
    let attempt = 0;

    const send = () => {
      attempt += 1;
      const xhr = new XMLHttpRequest();
      xhr.open("POST", STOP_URL, true);
      xhr.withCredentials = true;
      xhr.timeout = 2500;
      xhr.setRequestHeader("Content-Type", "application/json");
      xhr.setRequestHeader("Accept", "application/json");
      const retry = () => {
        if (!state.userStopLatch || browserRunning()) return;
        state.stopRetry = setTimeout(send, Math.min(5000, 400 + attempt * 300));
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          state.serverActive = false;
          state.serverOwner = "stopped";
          queueRender();
          return;
        }
        retry();
      };
      xhr.onerror = retry;
      xhr.ontimeout = retry;
      try { xhr.send("{}"); } catch (_) { retry(); }
    };

    send();
  }

  function hardStopEverything() {
    state.userStopLatch = true;
    state.serverActive = false;
    state.serverOwner = "stopped";

    // Financial fence FIRST. Any BUY that has not already been sent to Deriv is
    // rejected synchronously from this line onward.
    try { window.DERIVADMIN_DIRECT_HARD_STOP_FENCE_V1?.hard_stop?.(); } catch (_) {}
    try { if (browserRunning()) engine()?.stop?.("Trading stopped by user"); } catch (_) {}

    queueRender();
    xhrStop();
  }

  function xhrClearAll() {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/me/clear-trades", true);
    xhr.withCredentials = true;
    xhr.timeout = 5000;
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        state.resetUntil = 0;
        try { window.FOA_FINAL_UI?.refresh?.({ quiet: true }); } catch (_) {}
        queueRender();
      }
    };
    xhr.onerror = () => {};
    xhr.ontimeout = () => {};
    try { xhr.send(JSON.stringify({ scope: "all" })); } catch (_) {}
  }

  function resetTrades() {
    if (!window.confirm("Do you want to reset all trades?")) return;
    state.resetUntil = Date.now() + 6000;
    try { engine()?.clear?.(); } catch (_) {}
    window.dispatchEvent(new CustomEvent("derivadmin:direct-reset-all"));
    queueRender();
    xhrClearAll();
  }

  // Window capture executes before document-level shell/legacy listeners.
  window.addEventListener("click", (event) => {
    const run = event.target?.closest?.(".global-run-panel [data-run-start]");
    if (run) {
      if (effectiveRunning()) {
        event.preventDefault();
        event.stopImmediatePropagation();
        hardStopEverything();
        return;
      }
      // A stopped Start falls through to the existing strategy confirmation and
      // the one browser-direct engine. Do not create another start controller.
      state.userStopLatch = false;
      setTimeout(queueRender, 0);
      setTimeout(queueRender, 80);
      return;
    }

    const reset = event.target?.closest?.(".global-run-panel [data-run-reset]");
    if (reset) {
      event.preventDefault();
      event.stopImmediatePropagation();
      resetTrades();
      return;
    }
  }, true);

  window.addEventListener("derivadmin:hard-stop", queueRender);
  window.addEventListener("derivadmin:hard-stop-cleared", () => {
    state.userStopLatch = false;
    state.serverActive = false;
    state.serverOwner = "browser";
    queueRender();
  });
  window.addEventListener("derivadmin:direct-trade", queueRender);
  window.addEventListener("derivadmin:direct-clear", queueRender);
  window.addEventListener("derivadmin:direct-reset-all", queueRender);
  window.addEventListener("focus", readServerStatus);
  window.addEventListener("pageshow", readServerStatus);

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.(".global-run-panel [data-run-panel-toggle],.global-run-panel [data-run-tab]")) {
      setTimeout(queueRender, 0);
    }
  });

  // Stable low-frequency reconciliation only; no DOM-wide MutationObserver and no
  // sub-second renderer that can make the panel bounce while ticks are arriving.
  state.statusTimer = setInterval(() => {
    if (!document.hidden) readServerStatus();
  }, 4000);
  window.addEventListener("pagehide", () => {
    clearInterval(state.statusTimer);
    clearTimeout(state.stopRetry);
  }, { once: true });

  const style = document.createElement("style");
  style.id = "direct-run-panel-authority-v6-style";
  style.textContent = `
    /* One visible execution control. */
    .global-run-panel [data-run-execution-toggle],
    .global-run-panel .run-panel-execution,
    .global-run-panel .direct-final-run-state-v5,
    .global-run-panel .direct-final-run-state-v6,
    .global-run-panel .direct-bot-state-pill,
    .global-run-panel .direct-execution-state,
    .global-run-panel .direct-loaded-strategy-badge{display:none!important}
    .global-run-panel .run-panel-bar{grid-template-columns:1fr!important;min-height:48px!important;height:48px!important;flex:0 0 48px!important}
    .global-run-panel{font-family:Inter,Segoe UI,Roboto,Arial,sans-serif!important;color:#f4fbff!important}
    .global-run-panel .run-panel-run{width:100%!important;height:48px!important;min-height:48px!important;border:0!important;border-radius:0!important;color:#ffffff!important;font-weight:800!important;font-size:16px!important;letter-spacing:0!important;display:flex!important;align-items:center!important;justify-content:center!important;gap:8px!important;background:#11936f!important;box-shadow:0 -1px rgba(255,255,255,.12) inset!important}
    .global-run-panel .run-panel-run>span,.global-run-panel .run-panel-run>svg{display:none!important}
    .global-run-panel .run-panel-run::before{content:"▶";font-size:13px}
    .global-run-panel .run-panel-run::after{content:"Start"}
    html[data-final-run-state="running"] .global-run-panel .run-panel-run{background:#d13f4d!important}
    html[data-final-run-state="running"] .global-run-panel .run-panel-run::before{content:"■"}
    html[data-final-run-state="running"] .global-run-panel .run-panel-run::after{content:"Stop"}

    /* Phones use a full sheet. Desktop geometry belongs to the final theme drawer. */
    @media(max-width:900px){
      .global-run-panel.open{position:fixed!important;top:72px!important;left:0!important;right:0!important;bottom:0!important;height:calc(100dvh - 72px)!important;max-height:none!important;display:flex!important;flex-direction:column!important;overflow:hidden!important;background:#061526!important;transition:none!important}
      .global-run-panel.open .run-panel-sheet{position:relative!important;inset:auto!important;transform:none!important;display:flex!important;flex-direction:column!important;flex:1 1 auto!important;min-height:0!important;max-height:none!important;overflow:hidden!important;transition:none!important}
    }
    .global-run-panel .run-panel-top{min-height:38px!important;height:38px!important;padding:4px 12px!important;flex:0 0 38px!important}
    .global-run-panel .run-panel-tabs{min-height:38px!important;height:38px!important;flex:0 0 38px!important;margin:0!important}
    .global-run-panel .run-panel-tabs button{min-height:38px!important;padding:0 8px!important;font-size:12px!important}
    .global-run-panel .run-panel-body{flex:1 1 auto!important;min-height:0!important;overflow:auto!important;padding:0!important;overscroll-behavior:contain;scrollbar-gutter:stable;transition:none!important}

    /* Transactions get the space. Strategy analysis is Journal-only. */
    .global-run-panel .direct-strategy-checker.compact{display:none!important}
    .global-run-panel .transaction-table{margin:0!important;border-radius:0!important;border-left:0!important;border-right:0!important}
    .global-run-panel .transaction-head-v6,.global-run-panel .transaction-row-v6{display:grid!important;grid-template-columns:1.25fr .72fr 1fr .78fr .9fr!important;gap:5px!important;align-items:center!important}
    .global-run-panel .transaction-head-v6{position:sticky;top:0;z-index:3;padding:8px 8px!important;background:#092344!important;border-bottom:1px solid rgba(122,194,255,.28)!important;font-size:8px!important;line-height:1.15!important;color:#f8fcff!important}
    .global-run-panel .transaction-row-v6{padding:8px!important;min-height:48px!important;border-bottom:1px solid rgba(125,180,230,.18)!important;font-size:9px!important;font-variant-numeric:tabular-nums;transition:none!important;background:#071421!important;color:#f5fbff!important}
    .global-run-panel .transaction-row-v6 span,.global-run-panel .transaction-row-v6 strong{min-width:0!important;overflow-wrap:anywhere}
    .global-run-panel .transaction-row-v6 small{display:block!important;font-size:7px!important;line-height:1.2!important;color:#bed4e9!important;margin-top:2px!important}
    .global-run-panel .transaction-row-v6 b,.global-run-panel .transaction-row-v6 strong{font-size:9px!important;line-height:1.25!important}
    .global-run-panel .tx-time-market b{color:#66e7ff!important}.global-run-panel .tx-time-market small{margin:0 0 2px!important;color:#d7e8f6!important}
    .global-run-panel .tx-spots b::before{content:"● ";color:#ff506b}.global-run-panel .tx-spots small::before{content:"○ ";color:#a9b8c8}

    .global-run-panel .run-panel-reset{min-height:30px!important;height:30px!important;padding:0 14px!important;font-size:11px!important}

    @media(max-width:520px){
      .global-run-panel .transaction-head-v6,.global-run-panel .transaction-row-v6{grid-template-columns:1.15fr .68fr .95fr .78fr .9fr!important;gap:3px!important}
      .global-run-panel .transaction-row-v6{padding:7px 5px!important;font-size:8px!important}
      .global-run-panel .transaction-row-v6 b,.global-run-panel .transaction-row-v6 strong{font-size:8px!important}
      .global-run-panel .transaction-head-v6{padding:7px 5px!important;font-size:7px!important}
    }

  `;
  document.head.appendChild(style);

  readServerStatus();
  queueRender();

  window.DERIVADMIN_DIRECT_RUN_PANEL_AUTHORITY_V6 = Object.freeze({
    version: "20260818-single-start-stop-v6",
    stop: hardStopEverything,
    reset: resetTrades,
    refresh: () => { readServerStatus(); queueRender(); },
    state: () => ({
      running: effectiveRunning(),
      browser_running: browserRunning(),
      server_active: state.serverActive,
      stopped_by_user: state.userStopLatch,
    }),
  });
})();
