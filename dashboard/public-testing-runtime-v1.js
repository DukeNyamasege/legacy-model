(() => {
  "use strict";

  if (window.__DERIVADMIN_PUBLIC_TESTING_RUNTIME_V1__) return;
  window.__DERIVADMIN_PUBLIC_TESTING_RUNTIME_V1__ = true;

  const PUBLIC_WS = "wss://api.derivws.com/trading/v1/options/ws/public";
  const FALLBACK_MARKETS = ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V", "R_10", "R_25", "R_50", "R_75", "R_100"];
  const START_SELECTORS = [
    "[data-run-start]",
    "[data-run-execution-toggle]",
    "[data-builder-trade]",
    "[data-ready-trade]",
    "[data-trade-now-selected]",
    "[data-start-trading]",
  ].join(",");
  const STOP_SELECTORS = ["[data-stop-trading]", "[data-pause-trading]"].join(",");

  const state = {
    socket: null,
    reconnectTimer: null,
    markets: [],
    ticks: [],
    analysisCount: 0,
    running: false,
    connected: false,
    defaultTransactionsApplied: false,
    renderQueued: false,
    strategyRefreshAt: 0,
  };

  function isPremiumNoise(node) {
    const text = String(node?.textContent || "").toLowerCase();
    return text.includes("premium use only")
      || text.includes("pay kes 250")
      || text.includes("weekly access will soon")
      || text.includes("premium renewal reminder");
  }

  function removeTestingPhasePremiumUi() {
    document.querySelectorAll(".paid-soon-banner,.premium-reminder,.premium-profile").forEach((node) => node.remove());
    document.querySelectorAll(".global-message.error,.global-message.success,.premium-message").forEach((node) => {
      if (isPremiumNoise(node)) node.remove();
    });
  }

  function runLooksActive() {
    const toggle = document.querySelector("[data-run-execution-toggle]");
    if (toggle?.classList.contains("on")) return true;
    const runButton = document.querySelector("[data-run-start]");
    if (/\bstop\b/i.test(String(runButton?.textContent || ""))) return true;
    const status = String(document.querySelector(".run-status")?.textContent || "").toLowerCase();
    return status.includes("running") || status.includes("active") || status.includes("waiting for condition") || status.includes("starting");
  }

  function setOptimisticRunUi(running) {
    state.running = Boolean(running);
    const toggle = document.querySelector("[data-run-execution-toggle]");
    if (toggle) {
      toggle.classList.toggle("on", running);
      toggle.setAttribute("aria-label", running ? "Stop trading" : "Start trading");
    }
    const runButton = document.querySelector("[data-run-start]");
    if (runButton) {
      const label = runButton.querySelector("span");
      if (label) label.textContent = running ? "Stop" : "Run";
      else runButton.textContent = running ? "Stop" : "Run";
    }
  }

  function chooseTransactions({ force = false } = {}) {
    const tab = document.querySelector('[data-run-tab="transactions"]');
    if (!tab) return;
    if (!force && state.defaultTransactionsApplied) return;
    state.defaultTransactionsApplied = true;
    if (!tab.classList.contains("active")) tab.click();
  }

  async function currentMarkets() {
    const now = Date.now();
    if (state.markets.length && now < state.strategyRefreshAt) return state.markets;
    state.strategyRefreshAt = now + 10_000;
    try {
      const response = await fetch("/me/custom-strategy", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`strategy ${response.status}`);
      const payload = await response.json();
      const config = payload?.config || payload?.custom_strategy || payload?.strategy || {};
      const supported = Array.isArray(payload?.supported?.markets) && payload.supported.markets.length
        ? payload.supported.markets
        : FALLBACK_MARKETS;
      const selected = Array.isArray(config.markets) ? config.markets.filter((symbol) => supported.includes(symbol)) : [];
      state.markets = config.market_mode === "all" || !selected.length ? [...supported] : selected;
    } catch (_) {
      state.markets = [...FALLBACK_MARKETS];
    }
    return state.markets;
  }

  function closeMirror() {
    window.clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
    state.connected = false;
    if (state.socket) {
      try { state.socket.close(1000, "trading_stopped"); } catch (_) {}
    }
    state.socket = null;
    queueRender();
  }

  function scheduleReconnect() {
    window.clearTimeout(state.reconnectTimer);
    if (!state.running) return;
    state.reconnectTimer = window.setTimeout(() => connectMirror(), 1200);
  }

  async function connectMirror() {
    if (!state.running) return;
    if (state.socket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(state.socket.readyState)) return;
    const markets = await currentMarkets();
    if (!state.running || !markets.length) return;

    let socket;
    try {
      socket = new WebSocket(PUBLIC_WS);
    } catch (_) {
      scheduleReconnect();
      return;
    }
    state.socket = socket;
    socket.onopen = () => {
      state.connected = true;
      markets.forEach((symbol, index) => {
        try {
          socket.send(JSON.stringify({ ticks: symbol, subscribe: 1, req_id: 90_000 + index }));
        } catch (_) {}
      });
      queueRender();
    };
    socket.onmessage = (event) => {
      let payload;
      try { payload = JSON.parse(event.data || "{}"); } catch (_) { return; }
      if (payload?.msg_type !== "tick" || !payload.tick) return;
      const tick = payload.tick;
      const symbol = String(tick.symbol || "");
      if (!symbol) return;
      const quote = String(tick.quote ?? "");
      const digitMatch = quote.replace(/\D/g, "").match(/(\d)$/);
      const digit = digitMatch ? digitMatch[1] : "-";
      state.analysisCount += 1;
      state.ticks.unshift({
        symbol,
        quote,
        digit,
        epoch: Number(tick.epoch || 0),
        at: Date.now(),
      });
      if (state.ticks.length > 80) state.ticks.length = 80;
      document.dispatchEvent(new CustomEvent("derivadmin:analysis-tick", {
        detail: { symbol, quote, digit, count: state.analysisCount },
      }));
      queueRender();
    };
    socket.onerror = () => { state.connected = false; queueRender(); };
    socket.onclose = () => {
      if (state.socket === socket) state.socket = null;
      state.connected = false;
      queueRender();
      scheduleReconnect();
    };
  }

  function startMirror() {
    state.running = true;
    state.strategyRefreshAt = 0;
    connectMirror();
  }

  function latestRowsMarkup() {
    if (!state.ticks.length) {
      return `<div class="testing-tick-empty">Waiting for the first Deriv tick…</div>`;
    }
    return state.ticks.slice(0, 18).map((tick) => {
      const when = tick.epoch ? new Date(tick.epoch * 1000) : new Date(tick.at);
      const time = Number.isFinite(when.getTime()) ? when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
      return `<div class="testing-tick-row"><span>${escapeHtml(tick.symbol)}</span><b>${escapeHtml(tick.quote)}</b><em>digit ${escapeHtml(tick.digit)}</em><small>${escapeHtml(time)} · analyzed</small></div>`;
    }).join("");
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function injectStyles() {
    if (document.getElementById("public-testing-runtime-v1-style")) return;
    const style = document.createElement("style");
    style.id = "public-testing-runtime-v1-style";
    style.textContent = `
      .testing-tick-journal{margin-top:12px;border:1px solid rgba(72,181,255,.25);border-radius:14px;overflow:hidden;background:rgba(1,15,34,.42)}
      .testing-tick-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border-bottom:1px solid rgba(72,181,255,.18)}
      .testing-tick-head span{display:flex;align-items:center;gap:8px;font-weight:800}.testing-live-dot{width:8px;height:8px;border-radius:50%;background:#15d991;box-shadow:0 0 10px rgba(21,217,145,.75)}
      .testing-tick-head small{opacity:.68}.testing-tick-list{max-height:280px;overflow:auto}.testing-tick-row{display:grid;grid-template-columns:minmax(72px,.9fr) minmax(80px,1fr) minmax(58px,.7fr) minmax(96px,1.1fr);align-items:center;gap:8px;padding:9px 12px;border-bottom:1px solid rgba(115,167,218,.1);font-size:12px}
      .testing-tick-row b{font-variant-numeric:tabular-nums}.testing-tick-row em{font-style:normal;color:#4bd7ff}.testing-tick-row small{opacity:.6;text-align:right}.testing-tick-empty{padding:16px;opacity:.7;font-size:13px}
      @media(max-width:520px){.testing-tick-row{grid-template-columns:72px 1fr 56px}.testing-tick-row small{grid-column:1/-1;text-align:left;font-size:10px;padding-left:80px}}
    `;
    document.head.appendChild(style);
  }

  function renderTickJournal() {
    const journal = document.querySelector(".run-panel-journal");
    if (!journal) return;
    let block = journal.querySelector(".testing-tick-journal");
    if (!block) {
      block = document.createElement("section");
      block.className = "testing-tick-journal";
      journal.appendChild(block);
    }
    block.innerHTML = `<div class="testing-tick-head"><span><i class="testing-live-dot"></i>Live Deriv tick analysis</span><small>${state.connected ? "connected" : state.running ? "connecting" : "stopped"} · ${state.analysisCount} ticks</small></div><div class="testing-tick-list">${latestRowsMarkup()}</div>`;
  }

  function queueRender() {
    if (state.renderQueued) return;
    state.renderQueued = true;
    requestAnimationFrame(() => {
      state.renderQueued = false;
      removeTestingPhasePremiumUi();
      injectStyles();
      renderTickJournal();
    });
  }

  function syncFromDom() {
    const running = runLooksActive();
    if (running !== state.running) {
      state.running = running;
      if (running) startMirror();
      else closeMirror();
    }
    if (document.querySelector(".global-run-panel") && !state.defaultTransactionsApplied) chooseTransactions();
    queueRender();
  }

  document.addEventListener("click", (event) => {
    const target = event.target?.closest?.(START_SELECTORS);
    if (target) {
      const wasRunning = runLooksActive();
      window.setTimeout(() => {
        const starting = !wasRunning;
        setOptimisticRunUi(starting);
        chooseTransactions({ force: true });
        if (starting) startMirror();
        else closeMirror();
      }, 0);
      return;
    }
    if (event.target?.closest?.(STOP_SELECTORS)) {
      window.setTimeout(() => {
        setOptimisticRunUi(false);
        closeMirror();
      }, 0);
      return;
    }
    if (event.target?.closest?.('[data-run-tab="journal"]')) {
      window.setTimeout(renderTickJournal, 0);
    }
  }, true);

  document.addEventListener("foa:vps-live", () => window.setTimeout(syncFromDom, 0));
  document.addEventListener("foa:backend-lifecycle", () => window.setTimeout(syncFromDom, 0));
  window.addEventListener("pageshow", syncFromDom);
  window.addEventListener("focus", syncFromDom);
  window.addEventListener("beforeunload", closeMirror);

  let observerQueued = false;
  new MutationObserver(() => {
    if (observerQueued) return;
    observerQueued = true;
    requestAnimationFrame(() => {
      observerQueued = false;
      syncFromDom();
    });
  }).observe(document.documentElement, { childList: true, subtree: true });

  injectStyles();
  removeTestingPhasePremiumUi();
  window.setTimeout(syncFromDom, 0);
  window.DERIVADMIN_PUBLIC_TESTING_RUNTIME_V1 = Object.freeze({
    version: "20260818-public-testing-run-v1",
    publicWebSocket: PUBLIC_WS,
    refresh: syncFromDom,
  });
})();
