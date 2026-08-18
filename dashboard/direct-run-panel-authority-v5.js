(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_RUN_PANEL_AUTHORITY_V5__) return;
  window.__DERIVADMIN_DIRECT_RUN_PANEL_AUTHORITY_V5__ = true;

  /*
   * FINAL MANUAL RUN AUTHORITY
   * --------------------------
   * This layer NEVER starts a second execution engine. Starting is still owned by
   * deriv-direct-execution-v2 through the existing confirmed Run/Trade Now flow.
   *
   * Its jobs are deliberately narrow:
   *   1. reconcile a server-owned scheduled/offline run on page load so the one
   *      visible button says Stop instead of falsely saying Run;
   *   2. make Stop authoritative even when this tab did not start the server run;
   *   3. keep one visual Run/Stop state while legacy shell DOM is re-rendered;
   *   4. restore the expanded Run panel to a full-height sheet; and
   *   5. surface browser-direct trades immediately without waiting for the VPS
   *      transaction ledger.
   *
   * It does not call /me/resume-trading, does not evaluate ticks, does not send a
   * proposal and does not BUY. There is only one financial live engine.
   */

  const STATUS_URL = "/api/me/direct-execution/status";
  const STOP_URL = "/api/me/direct-execution/stop";
  const JOURNAL_PREFIX = "derivadmin-direct-journal-v1:";
  const nativeFetch = window.fetch.bind(window);

  const state = {
    serverActive: false,
    serverOwner: "stopped",
    stopPending: false,
    stopAttempts: 0,
    stopTimer: null,
    renderQueued: false,
    lastEffectiveRunning: false,
    statusMessage: "",
  };

  function engine() {
    return window.DERIVADMIN_DIRECT_EXECUTION_V1 || null;
  }

  function engineState() {
    try { return engine()?.state?.() || {}; }
    catch (_) { return {}; }
  }

  function uxState() {
    try { return window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.state?.() || {}; }
    catch (_) { return {}; }
  }

  function effectiveRunning() {
    return Boolean(engineState().running || state.serverActive || state.stopPending);
  }

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function selectedManagedId() {
    const direct = Number(uxState().selected_managed_id || 0);
    if (direct > 0) return String(direct);
    try {
      const keys = Object.keys(localStorage).filter((key) => key.startsWith(JOURNAL_PREFIX));
      if (keys.length === 1) return keys[0].slice(JOURNAL_PREFIX.length);
    } catch (_) {}
    return "default";
  }

  function localJournal() {
    const keys = [JOURNAL_PREFIX + selectedManagedId(), JOURNAL_PREFIX + "default"];
    for (const key of keys) {
      try {
        const value = JSON.parse(localStorage.getItem(key) || "[]");
        if (Array.isArray(value) && value.length) return value;
      } catch (_) {}
    }
    return [];
  }

  function visibleDirectTrades() {
    const rows = localJournal();
    const contracts = new Map();
    const virtual = [];
    rows.forEach((row, index) => {
      if (String(row?.mode || "") === "virtual") {
        virtual.push({ ...row, __key: `virtual-${index}` });
        return;
      }
      const id = String(row?.contract_id || "");
      if (!id) return;
      contracts.set(id, { ...(contracts.get(id) || {}), ...row, contract_id: id, __key: id });
    });
    return [...contracts.values(), ...virtual]
      .sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")))
      .slice(0, 80);
  }

  function money(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return "0.00 USD";
    return `${number >= 0 ? "+" : ""}${number.toFixed(2)} USD`;
  }

  function tradeLabel(row) {
    const type = String(row?.trade_type || "TRADE").toUpperCase().replace("DIGIT", "");
    const prediction = row?.prediction;
    if (prediction === null || prediction === undefined || prediction === "") return type;
    return `${type} ${prediction}`;
  }

  function directTradesMarkup() {
    const rows = visibleDirectTrades();
    if (!rows.length) return "";
    return `<section class="direct-live-transactions-v5">
      <div class="direct-live-transactions-head"><b>Live trades</b><small>Browser ↔ Deriv · immediate</small></div>
      <div class="direct-live-transactions-list">${rows.map((row) => {
        const settled = String(row.state || "").toUpperCase() === "SETTLED" || row.outcome;
        const profit = Number(row.profit || 0);
        const status = String(row.outcome || row.state || (row.mode === "virtual" ? "VIRTUAL" : "OPEN")).toUpperCase();
        return `<article class="${settled ? (profit >= 0 ? "won" : "lost") : "open"}">
          <span><b>${esc(tradeLabel(row))}</b><small>${esc(row.symbol || "Deriv Options")}${row.contract_id ? ` · ${esc(String(row.contract_id).slice(-8))}` : ""}</small></span>
          <em>${esc(status)}</em>
          <strong class="${profit >= 0 ? "positive" : "negative"}">${settled ? esc(money(profit)) : `${esc(Number(row.stake || 0).toFixed(2))} USD`}</strong>
        </article>`;
      }).join("")}</div>
    </section>`;
  }

  function renderDirectTrades() {
    const panel = document.querySelector(".global-run-panel");
    const body = panel?.querySelector(".run-panel-body");
    if (!body) return;
    body.querySelectorAll(":scope > .direct-live-transactions-v5").forEach((node) => node.remove());
    const active = String(panel.querySelector("[data-run-tab].active")?.dataset?.runTab || "");
    if (active !== "transactions") return;
    const markup = directTradesMarkup();
    if (!markup) return;
    const checker = body.querySelector(":scope > .direct-strategy-checker.compact");
    if (checker) checker.insertAdjacentHTML("afterend", markup);
    else body.insertAdjacentHTML("afterbegin", markup);
  }

  function expandPanelOnce() {
    const panel = document.querySelector(".global-run-panel");
    if (!panel || panel.classList.contains("open")) return;
    const toggle = panel.querySelector("[data-run-panel-toggle]");
    if (toggle) {
      try { toggle.click(); } catch (_) {}
    }
    window.setTimeout(() => {
      const current = document.querySelector(".global-run-panel");
      if (!current || current.classList.contains("open")) return;
      current.classList.add("open");
      current.classList.remove("collapsed");
    }, 40);
  }

  function renderState() {
    const running = effectiveRunning();
    document.documentElement.dataset.finalRunState = running ? "running" : "stopped";
    document.documentElement.dataset.finalRunOwner = engineState().running ? "browser" : state.serverOwner;

    const panel = document.querySelector(".global-run-panel");
    if (panel) {
      panel.dataset.finalRunState = running ? "running" : "stopped";
      panel.querySelectorAll("[data-run-execution-toggle]").forEach((node) => node.remove());
      panel.querySelectorAll("[data-run-start]").forEach((button) => {
        button.setAttribute("aria-label", running ? "Stop trading" : "Start trading");
        button.dataset.finalRunState = running ? "stop" : "start";
      });

      let note = panel.querySelector(".direct-final-run-state-v5");
      if (!note) {
        note = document.createElement("div");
        note.className = "direct-final-run-state-v5";
        const bar = panel.querySelector(".run-panel-bar");
        if (bar) bar.insertAdjacentElement("beforebegin", note);
      }
      if (note) {
        const local = Boolean(engineState().running);
        const text = state.stopPending
          ? "Stopping bot — blocking new browser trades and confirming server stop"
          : local
            ? "Bot currently executing trades"
            : state.serverActive
              ? "Bot currently executing trades on server"
              : "Bot currently stopped";
        note.className = `direct-final-run-state-v5 ${running ? "running" : "stopped"} ${state.stopPending ? "pending" : ""}`;
        if (note.textContent !== text) note.textContent = text;
      }
    }

    if (running && !state.lastEffectiveRunning) expandPanelOnce();
    state.lastEffectiveRunning = running;
    renderDirectTrades();
  }

  function queueRender() {
    if (state.renderQueued) return;
    state.renderQueued = true;
    requestAnimationFrame(() => {
      state.renderQueued = false;
      renderState();
    });
  }

  async function readServerStatus() {
    if (engineState().running || state.stopPending) return;
    try {
      const response = await nativeFetch(STATUS_URL, { credentials: "include", cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const payload = await response.json();
      const owner = String(payload?.owner || "stopped").toLowerCase();
      const enabled = payload?.enabled === true;
      state.serverOwner = owner;
      state.serverActive = enabled && owner !== "stopped";
      state.statusMessage = "";
      queueRender();
    } catch (_) {
      // A status read is advisory only. It must never start/stop anything.
    }
  }

  function beaconStop() {
    try {
      if (!navigator.sendBeacon) return;
      const blob = new Blob([JSON.stringify({ epoch: null })], { type: "application/json" });
      navigator.sendBeacon(STOP_URL, blob);
    } catch (_) {}
  }

  function confirmServerStop() {
    clearTimeout(state.stopTimer);
    state.stopPending = true;
    state.serverActive = false;
    state.stopAttempts = 0;
    beaconStop();
    queueRender();

    const attempt = async () => {
      state.stopAttempts += 1;
      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 4500);
        let response;
        try {
          response = await nativeFetch(STOP_URL, {
            method: "POST",
            credentials: "include",
            cache: "no-store",
            keepalive: true,
            signal: controller.signal,
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ epoch: null }),
          });
        } finally {
          clearTimeout(timer);
        }
        if (response?.ok) {
          state.stopPending = false;
          state.serverActive = false;
          state.serverOwner = "stopped";
          state.statusMessage = "";
          queueRender();
          return;
        }
      } catch (_) {}

      if (state.stopAttempts < 12) {
        state.stopTimer = setTimeout(attempt, Math.min(5000, 500 + state.stopAttempts * 350));
        return;
      }

      // Never print raw backend timeout text. Re-check truth and continue a slow
      // retry if the worker still owns execution.
      state.stopAttempts = 0;
      try {
        const response = await nativeFetch(STATUS_URL, { credentials: "include", cache: "no-store" });
        const payload = response.ok ? await response.json() : {};
        const enabled = payload?.enabled === true;
        const owner = String(payload?.owner || "stopped").toLowerCase();
        if (!enabled || owner === "stopped") {
          state.stopPending = false;
          state.serverActive = false;
          state.serverOwner = "stopped";
          queueRender();
          return;
        }
        state.serverActive = true;
        state.serverOwner = owner;
      } catch (_) {}
      state.stopTimer = setTimeout(attempt, 5000);
      queueRender();
    };

    attempt();
  }

  function stopEverything() {
    if (state.stopPending) return;
    try {
      if (engineState().running) engine()?.stop?.("Trading stopped by user");
    } catch (_) {}
    confirmServerStop();
  }

  // WINDOW capture runs before the document-level legacy shell/direct listeners.
  // We intercept ONLY an active Stop. A stopped Run is allowed through unchanged
  // so the existing confirmation popup and the one direct engine remain the sole
  // start path.
  window.addEventListener("click", (event) => {
    const mainRun = event.target?.closest?.(".global-run-panel [data-run-start]");
    if (mainRun && effectiveRunning()) {
      event.preventDefault();
      event.stopImmediatePropagation();
      stopEverything();
      return;
    }

    const startOnly = event.target?.closest?.("[data-builder-trade],[data-ready-trade],[data-trade-now-selected],[data-start-trading]");
    if (startOnly && (state.serverActive || state.stopPending) && !engineState().running) {
      event.preventDefault();
      event.stopImmediatePropagation();
      expandPanelOnce();
      return;
    }
  }, true);

  window.addEventListener("derivadmin:direct-trade", queueRender);
  window.addEventListener("derivadmin:direct-clear", queueRender);
  window.addEventListener("derivadmin:direct-reset-all", queueRender);
  window.addEventListener("focus", () => { readServerStatus(); queueRender(); });
  window.addEventListener("pageshow", () => { readServerStatus(); queueRender(); });

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.(".global-run-panel [data-run-tab]")) setTimeout(queueRender, 0);
  });

  let observerQueued = false;
  const observer = new MutationObserver((mutations) => {
    if (mutations.length && mutations.every((mutation) => mutation.target?.closest?.(".direct-live-transactions-v5,.direct-final-run-state-v5"))) return;
    if (observerQueued) return;
    observerQueued = true;
    requestAnimationFrame(() => {
      observerQueued = false;
      queueRender();
    });
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("pagehide", () => observer.disconnect(), { once: true });

  const style = document.createElement("style");
  style.id = "direct-run-panel-authority-v5-style";
  style.textContent = `
    /* One visible Run label. Older shell scripts may rewrite the hidden span, but
       they can no longer make the user see Run/Stop/Run flicker. */
    .global-run-panel .run-panel-run>span{display:none!important}
    .global-run-panel .run-panel-run::after{content:"Run";font-size:inherit;font-weight:inherit}
    html[data-final-run-state="running"] .global-run-panel .run-panel-run::after{content:"Stop"}
    .global-run-panel [data-run-execution-toggle]{display:none!important}
    .global-run-panel .run-panel-bar{grid-template-columns:1fr!important}
    .global-run-panel .run-panel-run{width:100%!important}

    /* Restore the expanded panel to the original full working sheet: below the
       application topbar and all the way to the bottom Run bar. */
    .global-run-panel.open{position:fixed!important;top:72px!important;left:0!important;right:0!important;bottom:0!important;height:calc(100dvh - 72px)!important;max-height:none!important;display:flex!important;flex-direction:column!important;background:#03142a!important;overflow:hidden!important}
    .global-run-panel.open .run-panel-sheet{position:relative!important;inset:auto!important;transform:none!important;flex:1 1 auto!important;height:auto!important;max-height:none!important;min-height:0!important;display:flex!important;flex-direction:column!important;overflow:hidden!important;border-radius:0!important}
    .global-run-panel.open .run-panel-top,.global-run-panel.open .run-panel-tabs,.global-run-panel.open .run-panel-stats{flex:0 0 auto!important}
    .global-run-panel.open .run-panel-body{flex:1 1 auto!important;height:auto!important;min-height:0!important;max-height:none!important;overflow-y:auto!important;overflow-x:hidden!important;display:block!important;justify-content:flex-start!important;align-content:start!important;padding-bottom:14px!important;-webkit-overflow-scrolling:touch}
    .global-run-panel.open .run-panel-body>.direct-strategy-checker.compact{margin:0 0 10px!important}
    .global-run-panel.open .transaction-table{margin-top:0!important;min-height:0!important;height:auto!important}
    .global-run-panel.open .transaction-rows{min-height:0!important;height:auto!important;max-height:none!important;overflow:visible!important}
    .global-run-panel.open .run-panel-bar{position:relative!important;left:auto!important;right:auto!important;bottom:auto!important;flex:0 0 auto!important;margin:0!important;z-index:5!important}

    .direct-final-run-state-v5{flex:0 0 auto;margin:0;padding:7px 12px;text-align:center;font-size:9px;font-weight:850;letter-spacing:.01em;color:#7f95aa;background:#061729;border-top:1px solid rgba(103,177,231,.10)}
    .direct-final-run-state-v5.running{color:#81efbe;background:#08271f}.direct-final-run-state-v5.pending{color:#ffd47d;background:#2b2108}

    .direct-live-transactions-v5{margin:0 0 10px;border:1px solid rgba(74,194,255,.14);border-radius:13px;overflow:hidden;background:rgba(4,17,32,.74)}
    .direct-live-transactions-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border-bottom:1px solid rgba(107,177,230,.10)}
    .direct-live-transactions-head b{font-size:9px;color:#e7f7ff}.direct-live-transactions-head small{font-size:7px;color:#66cfff}
    .direct-live-transactions-list{display:flex;flex-direction:column}
    .direct-live-transactions-list article{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:9px;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.045)}
    .direct-live-transactions-list article:last-child{border-bottom:0}.direct-live-transactions-list article>span{min-width:0}.direct-live-transactions-list article b{display:block;font-size:9px;color:#e9f4ff}.direct-live-transactions-list article small{display:block;margin-top:2px;font-size:7px;color:#6f879d;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.direct-live-transactions-list article em{font-size:7px;font-style:normal;font-weight:900;color:#72dfff}.direct-live-transactions-list article strong{font-size:8px;white-space:nowrap}.direct-live-transactions-list article.won em{color:#38e3a1}.direct-live-transactions-list article.lost em{color:#ff7182}

    @media(max-width:620px){
      .global-run-panel.open{top:72px!important;height:calc(100dvh - 72px)!important}
      .global-run-panel.open .run-panel-sheet{padding-bottom:0!important}
      .direct-live-transactions-list article{grid-template-columns:minmax(0,1fr) auto}.direct-live-transactions-list article strong{grid-column:2}.direct-live-transactions-list article em{grid-column:2;grid-row:1}
    }
  `;
  document.head.appendChild(style);

  readServerStatus();
  queueRender();

  // UI-only synchronization. This reads the single direct engine state; it never
  // writes trading lifecycle state and therefore cannot create a Start/Stop loop.
  const visualTimer = setInterval(() => {
    if (!document.hidden) queueRender();
  }, 350);
  const statusTimer = setInterval(() => {
    if (!document.hidden && !engineState().running && !state.stopPending) readServerStatus();
  }, 20000);
  window.addEventListener("pagehide", () => {
    clearInterval(visualTimer);
    clearInterval(statusTimer);
    clearTimeout(state.stopTimer);
  }, { once: true });

  window.DERIVADMIN_DIRECT_RUN_PANEL_AUTHORITY_V5 = Object.freeze({
    version: "20260818-single-run-panel-v5",
    stop: stopEverything,
    refresh_status: readServerStatus,
    state: () => ({
      running: effectiveRunning(),
      local_running: Boolean(engineState().running),
      server_running: state.serverActive,
      server_owner: state.serverOwner,
      stop_pending: state.stopPending,
    }),
  });
})();
