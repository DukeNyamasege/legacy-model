(() => {
  "use strict";

  if (window.__DERIVADMIN_PUBLIC_TESTING_RUNTIME_V1__) return;
  window.__DERIVADMIN_PUBLIC_TESTING_RUNTIME_V1__ = true;

  const PUBLIC_WS = "wss://api.derivws.com/trading/v1/options/ws/public";
  const FALLBACK_MARKETS = ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V", "R_10", "R_25", "R_50", "R_75", "R_100"];
  const TOGGLE_SELECTORS = ["[data-run-start]", "[data-run-execution-toggle]"].join(",");
  const START_ONLY_SELECTORS = [
    "[data-builder-trade]",
    "[data-ready-trade]",
    "[data-trade-now-selected]",
    "[data-start-trading]",
  ].join(",");
  const START_SELECTORS = [TOGGLE_SELECTORS, START_ONLY_SELECTORS].join(",");
  const STOP_SELECTORS = ["[data-stop-trading]", "[data-pause-trading]"].join(",");
  const CRITICAL_WRITE_ROUTES = new Set([
    "/me/custom-strategy",
    "/me/resume-trading",
    "/me/auto-trade",
    "/me/stop-trading",
    "/me/pause-trading",
    "/me/automation-schedules",
  ]);
  const ACTIVE_RUNTIME_STATES = new Set(["STARTING", "WAITING_FOR_CONDITION", "EXECUTING", "RUNNING"]);
  const boundaryFetch = window.fetch.bind(window);

  const state = {
    socket: null,
    reconnectTimer: null,
    lifecycleTimer: null,
    markets: [],
    ticks: [],
    analysisCount: 0,
    running: false,
    connected: false,
    transition: "",
    defaultTransactionsApplied: false,
    renderQueued: false,
    strategyRefreshAt: 0,
    testingFree: false,
    lastRuntimeState: "STOPPED",
  };

  function asUrl(input) {
    try {
      if (input instanceof Request) return new URL(input.url, window.location.origin);
      return new URL(String(input), window.location.origin);
    } catch (_) { return null; }
  }

  function unproxiedRoute(url) {
    if (!url) return "";
    const path = String(url.pathname || "");
    return path.startsWith("/api/") ? path.slice(4) || "/" : path;
  }

  function apiUrl(url) {
    if (!url || url.origin !== window.location.origin) return url?.href || "";
    const route = unproxiedRoute(url);
    if (!route.startsWith("/me/")) return url.href;
    return `${window.location.origin}/api${route}${url.search || ""}`;
  }

  function timeoutForRoute(route) {
    if (route === "/me/custom-strategy") return 60000;
    if (route === "/me/automation-schedules") return 45000;
    return 30000;
  }

  function headersObject(headers) {
    const result = {};
    try {
      new Headers(headers || {}).forEach((value, key) => { result[key] = value; });
    } catch (_) {}
    return result;
  }

  function directXhrFetch(input, options = {}) {
    const url = asUrl(input);
    const route = unproxiedRoute(url);
    const method = String(options.method || (input instanceof Request ? input.method : "GET") || "GET").toUpperCase();
    if (input instanceof Request && options.body == null && !["GET", "HEAD"].includes(method)) return boundaryFetch(input, options);

    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(method, apiUrl(url), true);
      xhr.withCredentials = true;
      xhr.timeout = timeoutForRoute(route);
      const headers = headersObject(options.headers || (input instanceof Request ? input.headers : {}));
      if (options.body != null && !Object.keys(headers).some((key) => key.toLowerCase() === "content-type")) headers["Content-Type"] = "application/json";
      Object.entries(headers).forEach(([key, value]) => {
        try { xhr.setRequestHeader(key, value); } catch (_) {}
      });
      xhr.onload = () => {
        const responseHeaders = new Headers();
        String(xhr.getAllResponseHeaders() || "").trim().split(/[\r\n]+/).forEach((line) => {
          const index = line.indexOf(":");
          if (index > 0) responseHeaders.append(line.slice(0, index).trim(), line.slice(index + 1).trim());
        });
        resolve(new Response(xhr.responseText || "", {
          status: xhr.status || 500,
          statusText: xhr.statusText || "",
          headers: responseHeaders,
        }));
      };
      xhr.onerror = () => reject(new Error("Backend connection failed. Check the API service and try again."));
      xhr.ontimeout = () => reject(new Error(`Backend did not answer ${route} within ${(xhr.timeout / 1000).toFixed(0)}s.`));
      xhr.onabort = () => reject(new DOMException("Request aborted", "AbortError"));
      try { xhr.send(options.body ?? null); } catch (error) { reject(error); }
    });
  }

  // The original VPS boundary aborts all writes after 8 seconds. Keep its GET/live
  // cache behaviour, but route execution-critical writes through a same-origin XHR
  // transport with realistic timeouts. This preserves cookies and never exposes
  // account credentials or financial purchase authority to the browser.
  window.fetch = async (input, options = {}) => {
    const url = asUrl(input);
    const route = unproxiedRoute(url);
    const method = String(options.method || (input instanceof Request ? input.method : "GET") || "GET").toUpperCase();
    if (url?.origin === window.location.origin && !["GET", "HEAD"].includes(method) && CRITICAL_WRITE_ROUTES.has(route)) {
      return directXhrFetch(input, options);
    }
    return boundaryFetch(input, options);
  };

  async function loadAccessMode() {
    try {
      const response = await fetch("/me/public-testing-access", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const payload = response.ok ? await response.json() : {};
      state.testingFree = payload?.public_testing_free_access === true;
    } catch (_) {
      state.testingFree = false;
    }
    document.documentElement.dataset.publicTestingAccess = state.testingFree ? "free" : "paid";
    queueRender();
  }

  function isPremiumNoise(node) {
    const text = String(node?.textContent || "").toLowerCase();
    return text.includes("premium use only")
      || text.includes("pay kes 250")
      || text.includes("weekly access will soon")
      || text.includes("premium renewal reminder");
  }

  function isObsoleteTimeoutNoise(node) {
    return /backend request timed out after 8\.0s/i.test(String(node?.textContent || ""));
  }

  function removeTestingPhasePremiumUi() {
    if (!state.testingFree) return;
    document.querySelectorAll(".global-message.error,.global-message.success,.premium-message").forEach((node) => {
      if (isPremiumNoise(node) || (state.running && isObsoleteTimeoutNoise(node))) node.remove();
    });
  }

  function runLooksActive() {
    if (state.transition === "starting") return true;
    if (state.transition === "stopping") return false;
    const toggle = document.querySelector("[data-run-execution-toggle]");
    if (toggle?.classList.contains("on")) return true;
    const runButton = document.querySelector("[data-run-start]");
    if (/\bstop\b/i.test(String(runButton?.textContent || ""))) return true;
    const status = String(document.querySelector(".run-status")?.textContent || "").toLowerCase();
    return status.includes("running") || status.includes("active") || status.includes("waiting for condition") || status.includes("starting");
  }

  function setOptimisticRunUi(running) {
    state.running = Boolean(running);
    document.querySelectorAll("[data-run-execution-toggle]").forEach((toggle) => {
      toggle.classList.toggle("on", running);
      toggle.setAttribute("aria-label", running ? "Stop trading" : "Start trading");
    });
    document.querySelectorAll("[data-run-start]").forEach((runButton) => {
      const label = runButton.querySelector("span");
      if (label) label.textContent = running ? "Stop" : "Run";
      else runButton.textContent = running ? "Stop" : "Run";
    });
  }

  function chooseTransactions({ force = false } = {}) {
    const tab = document.querySelector('[data-run-tab="transactions"]');
    if (!tab) return false;
    if (!force && state.defaultTransactionsApplied) return true;
    state.defaultTransactionsApplied = true;
    if (!tab.classList.contains("active")) tab.click();
    return true;
  }

  function openRunPanelToTransactions() {
    const panel = document.querySelector(".global-run-panel");
    if (panel?.classList.contains("collapsed")) panel.querySelector("[data-run-panel-toggle]")?.click();
    window.setTimeout(() => chooseTransactions({ force: true }), 0);
  }

  async function currentMarkets() {
    const now = Date.now();
    if (state.markets.length && now < state.strategyRefreshAt) return state.markets;
    state.strategyRefreshAt = now + 10000;
    try {
      const response = await boundaryFetch("/me/custom-strategy", {
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
        try { socket.send(JSON.stringify({ ticks: symbol, subscribe: 1, req_id: 90000 + index })); } catch (_) {}
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
      state.ticks.unshift({ symbol, quote, digit, epoch: Number(tick.epoch || 0), at: Date.now() });
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
    if (!state.ticks.length) return `<div class="testing-tick-empty">Waiting for the first Deriv tick…</div>`;
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
      html[data-public-testing-access="free"] .paid-soon-banner,
      html[data-public-testing-access="free"] .premium-reminder,
      html[data-public-testing-access="free"] .premium-profile,
      html[data-public-testing-access="free"] article.panel:has(.premium-profile){display:none!important}
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
    const phase = state.transition === "starting" ? "starting"
      : state.connected ? "connected"
      : state.running ? "connecting"
      : "stopped";
    const markup = `<div class="testing-tick-head"><span><i class="testing-live-dot"></i>Live Deriv tick analysis</span><small>${phase} · ${state.analysisCount} ticks</small></div><div class="testing-tick-list">${latestRowsMarkup()}</div>`;
    if (block.innerHTML !== markup) block.innerHTML = markup;
  }

  function queueRender() {
    if (state.renderQueued) return;
    state.renderQueued = true;
    requestAnimationFrame(() => {
      state.renderQueued = false;
      removeTestingPhasePremiumUi();
      injectStyles();
      if (state.running || state.transition === "starting") setOptimisticRunUi(true);
      if (state.transition === "stopping") setOptimisticRunUi(false);
      renderTickJournal();
    });
  }

  async function readLifecycleDirect() {
    try {
      const response = await directXhrFetch("/api/me/execution-runtime", { method: "GET", headers: { Accept: "application/json" } });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) { return null; }
  }

  async function syncLifecycle() {
    const payload = await readLifecycleDirect();
    if (!payload?.authenticated) return;
    const runtime = String(payload.runtime_state || "STOPPED").toUpperCase();
    state.lastRuntimeState = runtime;
    if (ACTIVE_RUNTIME_STATES.has(runtime)) {
      state.transition = "";
      state.running = true;
      setOptimisticRunUi(true);
      startMirror();
      if (!state.defaultTransactionsApplied) chooseTransactions();
    } else if (["STOPPED", "ERROR"].includes(runtime) && state.transition !== "starting") {
      state.running = false;
      setOptimisticRunUi(false);
      closeMirror();
      state.defaultTransactionsApplied = false;
    }
    queueRender();
  }

  function scheduleLifecycleSync(delay = 350) {
    window.clearTimeout(state.lifecycleTimer);
    state.lifecycleTimer = window.setTimeout(syncLifecycle, delay);
  }

  async function directMainRun(starting) {
    state.transition = starting ? "starting" : "stopping";
    setOptimisticRunUi(starting);
    if (starting) {
      openRunPanelToTransactions();
      startMirror();
    } else {
      state.defaultTransactionsApplied = false;
      closeMirror();
    }
    queueRender();

    try {
      const path = starting ? "/me/resume-trading" : "/me/stop-trading";
      const body = starting ? JSON.stringify({ mode: "continue" }) : "{}";
      const response = await window.fetch(path, {
        method: "POST",
        body,
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
      });
      const payload = await response.clone().json().catch(() => ({}));
      if (!response.ok) throw new Error(payload?.detail || payload?.message || `Backend returned ${response.status}`);
      state.transition = "";
      state.running = starting;
      if (starting) {
        state.lastRuntimeState = String(payload.runtime_state || "STARTING").toUpperCase();
        setOptimisticRunUi(true);
        startMirror();
        openRunPanelToTransactions();
      } else {
        state.lastRuntimeState = "STOPPED";
        setOptimisticRunUi(false);
        closeMirror();
      }
      try { window.FOA_FINAL_UI?.refresh?.(); } catch (_) {}
      scheduleLifecycleSync(250);
    } catch (error) {
      state.transition = "";
      state.running = false;
      setOptimisticRunUi(false);
      closeMirror();
      state.defaultTransactionsApplied = false;
      const existing = document.querySelector(".instant-run-error");
      if (existing) existing.remove();
      const banner = document.createElement("div");
      banner.className = "global-message error instant-run-error";
      banner.textContent = String(error?.message || "Trading could not be started.");
      const app = document.querySelector(".app-main") || document.getElementById("derivadmin-root");
      app?.prepend(banner);
    }
    queueRender();
  }

  function syncFromDom() {
    if (state.transition === "starting") {
      setOptimisticRunUi(true);
      startMirror();
      queueRender();
      return;
    }
    if (state.transition === "stopping") {
      setOptimisticRunUi(false);
      queueRender();
      return;
    }
    const running = runLooksActive();
    if (running !== state.running) {
      state.running = running;
      if (running) startMirror();
      else {
        state.defaultTransactionsApplied = false;
        closeMirror();
      }
    }
    if (running && !state.defaultTransactionsApplied) chooseTransactions();
    queueRender();
  }

  document.addEventListener("click", (event) => {
    const target = event.target?.closest?.(START_SELECTORS);
    if (target) {
      const wasRunning = runLooksActive();
      const isToggle = target.matches(TOGGLE_SELECTORS);
      const starting = isToggle ? !wasRunning : true;

      if (isToggle) {
        // Main Run is a pure execution control: do not re-save the Builder first.
        // This makes an already-saved strategy start immediately and prevents a
        // slow Builder save from blocking the Run/Stop control.
        event.preventDefault();
        event.stopImmediatePropagation();
        directMainRun(starting);
        return;
      }

      // Builder/AI Trade Now still owns save-then-start semantics in the shell.
      // Keep this optimistic work deferred until the shell has read its own state.
      window.setTimeout(() => {
        setOptimisticRunUi(true);
        state.transition = "starting";
        state.defaultTransactionsApplied = false;
        chooseTransactions({ force: true });
        startMirror();
        queueRender();
        scheduleLifecycleSync(900);
      }, 0);
      return;
    }

    if (event.target?.closest?.(STOP_SELECTORS)) {
      window.setTimeout(() => {
        state.transition = "stopping";
        setOptimisticRunUi(false);
        state.defaultTransactionsApplied = false;
        closeMirror();
        queueRender();
        scheduleLifecycleSync(500);
      }, 0);
      return;
    }
    if (event.target?.closest?.('[data-run-tab="journal"]')) window.setTimeout(renderTickJournal, 0);
  }, true);

  document.addEventListener("foa:vps-live", () => window.setTimeout(syncFromDom, 0));
  document.addEventListener("foa:backend-lifecycle", () => window.setTimeout(() => { syncFromDom(); scheduleLifecycleSync(50); }, 0));
  window.addEventListener("pageshow", () => { syncFromDom(); scheduleLifecycleSync(300); });
  window.addEventListener("focus", () => { loadAccessMode(); syncFromDom(); scheduleLifecycleSync(100); });
  window.addEventListener("beforeunload", closeMirror);

  let observerQueued = false;
  new MutationObserver((mutations) => {
    if (mutations.length && mutations.every((mutation) => mutation.target?.closest?.(".testing-tick-journal"))) return;
    if (observerQueued) return;
    observerQueued = true;
    requestAnimationFrame(() => {
      observerQueued = false;
      syncFromDom();
    });
  }).observe(document.documentElement, { childList: true, subtree: true });

  injectStyles();
  loadAccessMode();
  window.setTimeout(() => { syncFromDom(); scheduleLifecycleSync(500); }, 0);
  window.setInterval(() => { if (!document.hidden) scheduleLifecycleSync(0); }, 3000);
  window.DERIVADMIN_PUBLIC_TESTING_RUNTIME_V1 = Object.freeze({
    version: "20260818-public-testing-run-v6",
    publicWebSocket: PUBLIC_WS,
    refresh: () => { syncFromDom(); scheduleLifecycleSync(0); },
  });
})();
