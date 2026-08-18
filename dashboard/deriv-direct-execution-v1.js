(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_EXECUTION_V1__) return;
  window.__DERIVADMIN_DIRECT_EXECUTION_V1__ = true;

  const VERSION = "20260818-browser-direct-v1";
  const PUBLIC_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public";
  const CACHE_PREFIX = "derivadmin-direct-strategy-v1:";
  const JOURNAL_PREFIX = "derivadmin-direct-journal-v1:";
  const MAX_HISTORY = 1001;
  const HEARTBEAT_MS = 5000;
  const LEASE_SAFETY_MS = 8000;
  const PREWARM_RETRY_MS = 8000;
  const MANUAL_INTENT_MS = 6000;
  const ALL_MARKETS = [
    "1HZ100V", "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V",
    "R_10", "R_25", "R_50", "R_75", "R_100",
  ];
  const CONTRACT_TYPES = {
    even: "DIGITEVEN",
    odd: "DIGITODD",
    over: "DIGITOVER",
    under: "DIGITUNDER",
    matches: "DIGITMATCH",
    differs: "DIGITDIFF",
    rise: "CALL",
    fall: "PUT",
  };

  const originalFetch = window.fetch.bind(window);
  const state = {
    account: null,
    strategy: null,
    running: false,
    epoch: "",
    publicWs: null,
    privateWs: null,
    publicConnectPromise: null,
    privateConnectPromise: null,
    publicReq: 0,
    privateReq: 0,
    publicPending: new Map(),
    privatePending: new Map(),
    subscribedMarkets: new Set(),
    histories: new Map(),
    inFlight: false,
    openContracts: new Map(),
    virtualPending: null,
    virtualMode: false,
    virtualWins: 0,
    consecutiveLosses: 0,
    sessionProfit: 0,
    recoveryDebt: 0,
    currentStake: 0.5,
    lastProfitRatio: 0,
    armed: false,
    ownerLost: false,
    lastLeaseAckAt: 0,
    leaseMs: 20000,
    heartbeatTimer: null,
    keepaliveTimer: null,
    prewarmTimer: null,
    stopRetryTimer: null,
    manualIntentUntil: 0,
    status: "Preparing direct Deriv connection",
  };

  function apiPath(path) {
    const value = String(path || "");
    return value.startsWith("/api/") ? value : `/api${value}`;
  }

  function accountKey() {
    const account = state.account || {};
    return String(account.managed_account_id || account.id || account.account_generation || account.account_id_masked || "default");
  }

  function strategyCacheKey() {
    return CACHE_PREFIX + accountKey();
  }

  function journalKey() {
    return JOURNAL_PREFIX + accountKey();
  }

  function safeJson(value, fallback = null) {
    try { return JSON.parse(String(value || "")); } catch (_) { return fallback; }
  }

  function finiteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clampInt(value, min, max, fallback) {
    const number = Math.trunc(Number(value));
    return Number.isFinite(number) ? Math.max(min, Math.min(max, number)) : fallback;
  }

  function randomEpoch() {
    const cryptoId = globalThis.crypto?.randomUUID?.();
    return cryptoId || `${Date.now()}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;
  }

  function requestPath(input) {
    try {
      const raw = typeof input === "string" ? input : input?.url;
      const url = new URL(String(raw || ""), location.origin);
      return url.pathname.replace(/^\/api(?=\/)/, "");
    } catch (_) {
      return "";
    }
  }

  function requestMethod(input, init) {
    return String(init?.method || (typeof input !== "string" ? input?.method : "") || "GET").toUpperCase();
  }

  async function requestBodyJson(input, init) {
    const body = init?.body;
    if (typeof body === "string") return safeJson(body, null);
    if (body && typeof body === "object" && !(body instanceof FormData) && !(body instanceof Blob)) return body;
    if (typeof input !== "string" && input?.clone) {
      try { return await input.clone().json(); } catch (_) {}
    }
    return null;
  }

  function jsonResponse(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  }

  function loadJournal() {
    const rows = safeJson(localStorage.getItem(journalKey()), []);
    return Array.isArray(rows) ? rows : [];
  }

  function writeJournal(rows) {
    try { localStorage.setItem(journalKey(), JSON.stringify(rows.slice(-500))); } catch (_) {}
  }

  function appendJournal(row) {
    const rows = loadJournal();
    rows.push({ at: new Date().toISOString(), ...row });
    writeJournal(rows);
    window.dispatchEvent(new CustomEvent("derivadmin:direct-trade", { detail: row }));
  }

  function clearLocalTrades() {
    try { localStorage.removeItem(journalKey()); } catch (_) {}
    state.sessionProfit = 0;
    state.recoveryDebt = 0;
    state.consecutiveLosses = 0;
    state.virtualMode = false;
    state.virtualWins = 0;
    state.virtualPending = null;
    state.currentStake = baseStake();
    const panel = document.querySelector(".global-run-panel");
    panel?.querySelectorAll(".run-panel-stats b,.run-stat b").forEach((node) => { node.textContent = "0"; });
    window.dispatchEvent(new CustomEvent("derivadmin:direct-clear"));
  }

  function normalizeCondition(raw) {
    const source = raw && typeof raw === "object" ? raw : {};
    const kind = String(source.kind || "").toLowerCase();
    const windowSize = clampInt(source.window, 1, 1000, 1);
    if (kind === "digit_parity") {
      return { kind, window: windowSize, parity: String(source.parity || "even").toLowerCase() };
    }
    if (kind === "digit_compare") {
      return {
        kind,
        window: windowSize,
        operator: String(source.operator || "=="),
        value: clampInt(source.value, 0, 9, 0),
      };
    }
    if (kind === "direction") {
      let direction = String(source.direction || "no_move").toLowerCase().replaceAll(" ", "_");
      if (["rise", "up"].includes(direction)) direction = "rising";
      if (["fall", "down"].includes(direction)) direction = "falling";
      return { kind, window: windowSize, direction };
    }
    return {
      kind: "percentage",
      window: windowSize,
      target: String(source.target || "even").toLowerCase(),
      operator: String(source.operator || ">="),
      threshold: Math.max(0, Math.min(100, finiteNumber(source.threshold, 0))),
      value: source.value == null ? null : clampInt(source.value, 0, 9, 0),
    };
  }

  function normalizeStrategy(raw) {
    const source = raw && typeof raw === "object" ? raw : {};
    let tradeType = String(source.trade_type || source.side || "even").toLowerCase();
    if (tradeType === "higher") tradeType = "rise";
    if (tradeType === "lower") tradeType = "fall";
    if (!CONTRACT_TYPES[tradeType]) return null;
    const marketMode = ["single", "selected", "all"].includes(String(source.market_mode || "all"))
      ? String(source.market_mode || "all") : "all";
    const requestedMarkets = Array.isArray(source.markets) ? source.markets.map((item) => String(item).toUpperCase()) : [];
    const markets = marketMode === "all"
      ? ALL_MARKETS.slice()
      : requestedMarkets.filter((item, index, array) => ALL_MARKETS.includes(item) && array.indexOf(item) === index);
    const conditions = Array.isArray(source.conditions) ? source.conditions.map(normalizeCondition) : [];
    if (!conditions.length || !markets.length) return null;
    const prediction = ["over", "under", "matches", "differs"].includes(tradeType)
      ? clampInt(source.prediction, 0, 9, 0) : null;
    const execution = source.execution_settings && typeof source.execution_settings === "object"
      ? source.execution_settings : {};
    const martingale = source.martingale && typeof source.martingale === "object"
      ? source.martingale : {};
    const hookRaw = source.virtual_hook && typeof source.virtual_hook === "object" ? source.virtual_hook : {};
    return {
      ...source,
      configured: true,
      market_mode: marketMode,
      markets,
      trade_type: tradeType,
      prediction,
      duration_ticks: clampInt(source.duration_ticks, 1, 100, 1),
      conditions,
      match: "all",
      execution_settings: {
        ...execution,
        stake_amount: finiteNumber(execution.stake_amount, finiteNumber(state.account?.stake_amount, 0.5)),
        take_profit: finiteNumber(execution.take_profit, finiteNumber(state.account?.take_profit, 0)),
        stop_loss: finiteNumber(execution.stop_loss, finiteNumber(state.account?.stop_loss, 0)),
      },
      martingale: {
        mode: ["system", "multiplier", "split"].includes(String(martingale.mode || "system")) ? String(martingale.mode || "system") : "system",
        multiplier: Math.max(1.1, Math.min(10, finiteNumber(martingale.multiplier, 2))),
        split_count: clampInt(martingale.split_count, 1, 3, 2),
      },
      virtual_hook_enabled: source.virtual_hook_enabled !== false && hookRaw.enabled !== false,
      virtual_hook: {
        enabled: source.virtual_hook_enabled !== false && hookRaw.enabled !== false,
        enter_after_losses: clampInt(hookRaw.enter_after_losses ?? hookRaw.enter_after_runs, 1, 50, 2),
        exit_after_consecutive_wins: clampInt(hookRaw.exit_after_consecutive_wins ?? hookRaw.exit_after_wins, 1, 50, 2),
      },
    };
  }

  function cacheStrategy(raw) {
    const normalized = normalizeStrategy(raw);
    if (!normalized) return null;
    state.strategy = normalized;
    try { localStorage.setItem(strategyCacheKey(), JSON.stringify(normalized)); } catch (_) {}
    return normalized;
  }

  function loadCachedStrategy() {
    const direct = safeJson(localStorage.getItem(strategyCacheKey()), null);
    const normalized = normalizeStrategy(direct);
    if (normalized) state.strategy = normalized;
    return normalized;
  }

  function baseStake() {
    return Math.max(0.01, Math.round(finiteNumber(state.strategy?.execution_settings?.stake_amount, finiteNumber(state.account?.stake_amount, 0.5)) * 100) / 100);
  }

  function takeProfit() {
    return Math.max(0, finiteNumber(state.strategy?.execution_settings?.take_profit, finiteNumber(state.account?.take_profit, 0)));
  }

  function stopLoss() {
    return Math.max(0, finiteNumber(state.strategy?.execution_settings?.stop_loss, finiteNumber(state.account?.stop_loss, 0)));
  }

  function updateStatus(message) {
    state.status = String(message || "");
    const panel = document.querySelector(".global-run-panel");
    if (!panel) return;
    let note = panel.querySelector(".direct-execution-state");
    if (!note) {
      note = document.createElement("div");
      note.className = "direct-execution-state";
      const body = panel.querySelector(".run-panel-body") || panel;
      body.prepend(note);
    }
    note.textContent = state.status;
    note.dataset.owner = state.ownerLost ? "server" : state.running ? "browser" : "stopped";
  }

  function updateRunUI() {
    const panel = document.querySelector(".global-run-panel");
    if (panel) {
      panel.dataset.executionState = state.running ? "running" : "stopped";
      panel.dataset.executionTransport = "deriv-direct";
      panel.querySelectorAll("[data-run-start]").forEach((button) => {
        button.dataset.singleRunState = state.running ? "stop" : "start";
        button.setAttribute("aria-label", state.running ? "Stop trading" : "Start trading");
        const span = button.querySelector("span");
        if (span) span.textContent = state.running ? "Stop" : "Run";
      });
      panel.querySelectorAll("[data-run-execution-toggle]").forEach((toggle) => {
        toggle.classList.toggle("on", state.running);
        toggle.dataset.singleRunState = state.running ? "stop" : "start";
        toggle.setAttribute("aria-pressed", state.running ? "true" : "false");
      });
    }
    document.querySelectorAll("[data-start-trading]").forEach((button) => {
      button.textContent = state.running ? "Trading" : button.textContent;
    });
  }

  function compare(value, operator, target) {
    if (operator === "<") return value < target;
    if (operator === "<=") return value <= target;
    if (operator === "==") return Math.abs(value - target) < 0.000001;
    if (operator === "!=") return Math.abs(value - target) >= 0.000001;
    if (operator === ">=") return value >= target;
    if (operator === ">") return value > target;
    return false;
  }

  function conditionMatches(condition, history) {
    const digits = history.digits;
    const quotes = history.quotes;
    const windowSize = Number(condition.window || 0);
    if (windowSize <= 0) return false;
    if (condition.kind === "digit_parity") {
      if (digits.length < windowSize) return false;
      const even = condition.parity === "even";
      return digits.slice(-windowSize).every((digit) => (digit % 2 === 0) === even);
    }
    if (condition.kind === "digit_compare") {
      if (digits.length < windowSize) return false;
      const sample = digits.slice(-windowSize);
      if (condition.operator === "all_same") return sample.length > 0 && sample.every((digit) => digit === sample[0]);
      return sample.every((digit) => compare(digit, condition.operator, Number(condition.value)));
    }
    if (condition.kind === "direction") {
      if (quotes.length < windowSize + 1) return false;
      const sample = quotes.slice(-(windowSize + 1));
      const moves = sample.slice(1).map((quote, index) => quote - sample[index]);
      if (["rise", "rising"].includes(condition.direction)) return moves.every((move) => move > 0);
      if (["fall", "falling"].includes(condition.direction)) return moves.every((move) => move < 0);
      return moves.every((move) => move === 0);
    }
    if (condition.kind === "percentage") {
      let total = 0;
      let matches = 0;
      if (["rise", "fall", "no_move"].includes(condition.target)) {
        if (quotes.length < windowSize + 1) return false;
        const sample = quotes.slice(-(windowSize + 1));
        const moves = sample.slice(1).map((quote, index) => quote - sample[index]);
        total = moves.length;
        if (condition.target === "rise") matches = moves.filter((move) => move > 0).length;
        else if (condition.target === "fall") matches = moves.filter((move) => move < 0).length;
        else matches = moves.filter((move) => move === 0).length;
      } else {
        if (digits.length < windowSize) return false;
        const sample = digits.slice(-windowSize);
        total = sample.length;
        if (condition.target === "even") matches = sample.filter((digit) => digit % 2 === 0).length;
        else if (condition.target === "odd") matches = sample.filter((digit) => digit % 2 === 1).length;
        else if (condition.target === "over") matches = sample.filter((digit) => digit > Number(condition.value)).length;
        else if (condition.target === "under") matches = sample.filter((digit) => digit < Number(condition.value)).length;
        else if (condition.target === "digit") matches = sample.filter((digit) => digit === Number(condition.value)).length;
      }
      if (!total) return false;
      return compare(matches * 100 / total, condition.operator, Number(condition.threshold || 0));
    }
    return false;
  }

  function strategyMatches(history) {
    const strategy = state.strategy;
    return Boolean(strategy && strategy.conditions.every((condition) => conditionMatches(condition, history)));
  }

  function digitFromTick(tick) {
    const quote = finiteNumber(tick?.quote, NaN);
    if (!Number.isFinite(quote)) return null;
    const pipSize = Number(tick?.pip_size);
    let text = Number.isInteger(pipSize) && pipSize >= 0 && pipSize <= 12
      ? quote.toFixed(pipSize)
      : String(tick.quote);
    text = text.replace(/\D/g, "");
    return text ? Number(text[text.length - 1]) : null;
  }

  function historyFor(symbol) {
    if (!state.histories.has(symbol)) state.histories.set(symbol, { quotes: [], digits: [], sequence: 0 });
    return state.histories.get(symbol);
  }

  function recordTick(symbol, tick) {
    const quote = finiteNumber(tick?.quote, NaN);
    const digit = digitFromTick(tick);
    if (!Number.isFinite(quote) || digit == null) return null;
    const history = historyFor(symbol);
    history.quotes.push(quote);
    history.digits.push(digit);
    history.sequence += 1;
    if (history.quotes.length > MAX_HISTORY) history.quotes.splice(0, history.quotes.length - MAX_HISTORY);
    if (history.digits.length > MAX_HISTORY) history.digits.splice(0, history.digits.length - MAX_HISTORY);
    return history;
  }

  function virtualOutcome(pending, exitQuote, exitDigit) {
    const type = pending.tradeType;
    const prediction = pending.prediction;
    if (type === "even") return exitDigit % 2 === 0;
    if (type === "odd") return exitDigit % 2 === 1;
    if (type === "over") return exitDigit > prediction;
    if (type === "under") return exitDigit < prediction;
    if (type === "matches") return exitDigit === prediction;
    if (type === "differs") return exitDigit !== prediction;
    if (type === "rise") return exitQuote > pending.entryQuote;
    if (type === "fall") return exitQuote < pending.entryQuote;
    return false;
  }

  function advanceVirtual(symbol, history) {
    const pending = state.virtualPending;
    if (!pending || pending.symbol !== symbol || history.sequence <= pending.entrySequence) return;
    pending.remaining -= 1;
    if (pending.remaining > 0) return;
    const exitQuote = history.quotes[history.quotes.length - 1];
    const exitDigit = history.digits[history.digits.length - 1];
    const win = virtualOutcome(pending, exitQuote, exitDigit);
    state.virtualPending = null;
    state.virtualWins = win ? state.virtualWins + 1 : 0;
    appendJournal({
      mode: "virtual",
      symbol,
      trade_type: pending.tradeType,
      prediction: pending.prediction,
      outcome: win ? "WIN" : "LOSS",
      profit: 0,
      entry_quote: pending.entryQuote,
      exit_quote: exitQuote,
      exit_digit: exitDigit,
    });
    const required = clampInt(state.strategy?.virtual_hook?.exit_after_consecutive_wins, 1, 50, 2);
    if (win && state.virtualWins >= required) {
      state.virtualMode = false;
      state.virtualWins = 0;
      updateStatus("Direct • virtual protection cleared • waiting for real entry");
    }
  }

  function beginVirtual(symbol, history) {
    if (state.virtualPending) return;
    state.virtualPending = {
      symbol,
      entrySequence: history.sequence,
      entryQuote: history.quotes[history.quotes.length - 1],
      remaining: Math.max(1, Number(state.strategy.duration_ticks || 1)),
      tradeType: state.strategy.trade_type,
      prediction: state.strategy.prediction,
    };
    updateStatus("Direct • virtual protection observing next result");
  }

  function wsErrorMessage(message) {
    const error = message?.error;
    if (!error) return "Deriv request failed";
    return String(error.message || error.code || "Deriv request failed");
  }

  function rejectPending(map, reason) {
    for (const pending of map.values()) {
      clearTimeout(pending.timer);
      pending.reject(new Error(reason));
    }
    map.clear();
  }

  function handleWsMessage(kind, event) {
    let message;
    try { message = JSON.parse(event.data); } catch (_) { return; }
    const map = kind === "public" ? state.publicPending : state.privatePending;
    const reqId = Number(message?.req_id || 0);
    if (reqId && map.has(reqId)) {
      const pending = map.get(reqId);
      map.delete(reqId);
      clearTimeout(pending.timer);
      if (message.error) pending.reject(new Error(wsErrorMessage(message)));
      else pending.resolve(message);
    }
    if (message.msg_type === "tick" && message.tick) {
      const symbol = String(message.tick.symbol || message.echo_req?.ticks || "").toUpperCase();
      if (symbol) onTick(symbol, message.tick);
    }
    if (kind === "private" && message.msg_type === "proposal_open_contract" && message.proposal_open_contract) {
      onContractUpdate(message.proposal_open_contract);
    }
  }

  function sendRequest(kind, payload, timeoutMs = 5000) {
    const ws = kind === "public" ? state.publicWs : state.privateWs;
    const map = kind === "public" ? state.publicPending : state.privatePending;
    if (!ws || ws.readyState !== WebSocket.OPEN) return Promise.reject(new Error("Deriv WebSocket unavailable"));
    const reqId = kind === "public" ? ++state.publicReq : ++state.privateReq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        map.delete(reqId);
        reject(new Error("Deriv request did not answer"));
      }, timeoutMs);
      map.set(reqId, { resolve, reject, timer });
      try { ws.send(JSON.stringify({ ...payload, req_id: reqId })); }
      catch (error) {
        clearTimeout(timer);
        map.delete(reqId);
        reject(error);
      }
    });
  }

  function sendNoWait(kind, payload) {
    const ws = kind === "public" ? state.publicWs : state.privateWs;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    const reqId = kind === "public" ? ++state.publicReq : ++state.privateReq;
    try { ws.send(JSON.stringify({ ...payload, req_id: reqId })); return true; } catch (_) { return false; }
  }

  function connectPublic() {
    if (state.publicWs?.readyState === WebSocket.OPEN) return Promise.resolve(state.publicWs);
    if (state.publicConnectPromise) return state.publicConnectPromise;
    state.publicConnectPromise = new Promise((resolve, reject) => {
      let ws;
      try { ws = new WebSocket(PUBLIC_WS_URL); } catch (error) { reject(error); return; }
      state.publicWs = ws;
      ws.onopen = () => {
        state.publicConnectPromise = null;
        state.subscribedMarkets.clear();
        if (state.running) subscribeMarkets();
        resolve(ws);
      };
      ws.onmessage = (event) => handleWsMessage("public", event);
      ws.onerror = () => {};
      ws.onclose = () => {
        if (state.publicWs === ws) state.publicWs = null;
        state.publicConnectPromise = null;
        state.subscribedMarkets.clear();
        rejectPending(state.publicPending, "Public Deriv WebSocket closed");
        if (state.running) {
          updateStatus("Direct • reconnecting market stream");
          setTimeout(connectPublic, 700);
        } else setTimeout(connectPublic, PREWARM_RETRY_MS);
      };
    });
    return state.publicConnectPromise;
  }

  async function fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await originalFetch(url, { credentials: "include", cache: "no-store", ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  function connectPrivate() {
    if (state.privateWs?.readyState === WebSocket.OPEN) return Promise.resolve(state.privateWs);
    if (state.privateConnectPromise) return state.privateConnectPromise;
    state.privateConnectPromise = (async () => {
      const response = await fetchWithTimeout(apiPath("/me/direct-execution/session"), { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, 7500);
      if (!response.ok) throw new Error("secure session unavailable");
      const payload = await response.json();
      const wsUrl = String(payload?.ws_url || "");
      if (!wsUrl.startsWith("wss://api.derivws.com/")) throw new Error("invalid secure session");
      return await new Promise((resolve, reject) => {
        let ws;
        try { ws = new WebSocket(wsUrl); } catch (error) { reject(error); return; }
        const timer = setTimeout(() => { try { ws.close(); } catch (_) {} reject(new Error("secure session connection delayed")); }, 6000);
        state.privateWs = ws;
        ws.onopen = () => {
          clearTimeout(timer);
          state.privateConnectPromise = null;
          if (state.running && !state.ownerLost) updateStatus("Direct • connected to Deriv • analyzing live ticks");
          resolve(ws);
        };
        ws.onmessage = (event) => handleWsMessage("private", event);
        ws.onerror = () => {};
        ws.onclose = () => {
          clearTimeout(timer);
          if (state.privateWs === ws) state.privateWs = null;
          state.privateConnectPromise = null;
          rejectPending(state.privatePending, "Authenticated Deriv WebSocket closed");
          if (state.running && !state.ownerLost) {
            updateStatus("Direct • analysis active • restoring secure trade session");
            setTimeout(() => connectPrivate().catch(() => {}), 900);
          } else if (!state.running) {
            schedulePrewarm();
          }
        };
      });
    })().catch((error) => {
      state.privateConnectPromise = null;
      if (!state.running) schedulePrewarm();
      throw error;
    });
    return state.privateConnectPromise;
  }

  function schedulePrewarm() {
    clearTimeout(state.prewarmTimer);
    state.prewarmTimer = setTimeout(() => {
      if (document.visibilityState !== "hidden") connectPrivate().catch(() => schedulePrewarm());
    }, PREWARM_RETRY_MS);
  }

  async function prewarmData() {
    try {
      const response = await fetchWithTimeout(apiPath("/me"), { method: "GET" }, 6500);
      if (response.ok) {
        const payload = await response.json();
        if (payload?.authenticated !== false) state.account = payload;
        loadCachedStrategy();
      }
    } catch (_) {}
    try {
      const response = await fetchWithTimeout(apiPath("/me/custom-strategy"), { method: "GET" }, 7000);
      if (response.ok) {
        const payload = await response.json();
        if (payload?.config?.configured) {
          cacheStrategy({
            ...payload.config,
            martingale: payload.martingale || payload.config.martingale,
            execution_settings: {
              stake_amount: state.account?.stake_amount,
              take_profit: state.account?.take_profit,
              stop_loss: state.account?.stop_loss,
            },
          });
        }
      }
    } catch (_) {}
  }

  function subscribeMarkets() {
    if (!state.running || !state.strategy || state.publicWs?.readyState !== WebSocket.OPEN) return;
    for (const symbol of state.strategy.markets) {
      if (state.subscribedMarkets.has(symbol)) continue;
      if (sendNoWait("public", { ticks: symbol, subscribe: 1 })) state.subscribedMarkets.add(symbol);
    }
  }

  function proposalRequest(symbol, stake) {
    const strategy = state.strategy;
    const request = {
      proposal: 1,
      amount: Math.round(stake * 100) / 100,
      basis: "stake",
      contract_type: CONTRACT_TYPES[strategy.trade_type],
      currency: String(state.account?.currency || "USD").toUpperCase(),
      duration: Number(strategy.duration_ticks || 1),
      duration_unit: "t",
      underlying_symbol: symbol,
    };
    if (["over", "under", "matches", "differs"].includes(strategy.trade_type)) {
      request.barrier = String(strategy.prediction);
    }
    return request;
  }

  function proposedProfitRatio(proposal, stake) {
    const payout = finiteNumber(proposal?.payout, 0);
    const ask = finiteNumber(proposal?.ask_price, stake);
    const cost = ask > 0 ? ask : stake;
    return cost > 0 ? Math.max(0, (payout - cost) / cost) : 0;
  }

  function recoveryStake(firstProposal) {
    const base = baseStake();
    const settings = state.strategy?.martingale || { mode: "system", multiplier: 2, split_count: 2 };
    if (state.recoveryDebt <= 0.009) return base;
    if (settings.mode === "multiplier") {
      return Math.ceil(base * (Number(settings.multiplier || 2) ** Math.max(1, state.consecutiveLosses)) * 100) / 100;
    }
    const ratio = proposedProfitRatio(firstProposal, base) || state.lastProfitRatio;
    if (ratio <= 0) return base;
    const buffer = Math.max(0.05, state.recoveryDebt * 0.06);
    let exact = Math.ceil(Math.max(base, (state.recoveryDebt + buffer) / ratio) * 100) / 100;
    if (settings.mode === "split") exact = Math.ceil(Math.max(base, exact / Math.max(1, Number(settings.split_count || 2))) * 100) / 100;
    return exact;
  }

  async function executeReal(symbol, history) {
    if (!state.running || state.ownerLost || state.inFlight || state.openContracts.size) return;
    if (state.privateWs?.readyState !== WebSocket.OPEN) {
      updateStatus("Direct • condition found • secure trade session reconnecting");
      connectPrivate().catch(() => {});
      return;
    }
    const epoch = state.epoch;
    state.inFlight = true;
    try {
      const base = baseStake();
      let stake = state.recoveryDebt > 0.009 && state.currentStake > base ? state.currentStake : base;
      let proposalResponse = await sendRequest("private", proposalRequest(symbol, stake), 4500);
      let proposal = proposalResponse?.proposal || {};
      if (!state.running || state.ownerLost || state.epoch !== epoch) return;

      const planned = state.recoveryDebt > 0.009 ? recoveryStake(proposal) : base;
      if (Math.abs(planned - stake) >= 0.009) {
        stake = planned;
        proposalResponse = await sendRequest("private", proposalRequest(symbol, stake), 4500);
        proposal = proposalResponse?.proposal || {};
      }
      const proposalId = String(proposal?.id || "");
      if (!proposalId) throw new Error("Deriv proposal ID missing");
      if (!state.running || state.ownerLost || state.epoch !== epoch) return;

      // Final browser-side financial fence. JavaScript runs this check immediately
      // before the only BUY send. Stop changes epoch synchronously, so a proposal
      // that resolves after Stop cannot become a purchase.
      const buyResponse = await sendRequest("private", { buy: proposalId, price: Math.round(stake * 100) / 100 }, 5000);
      const buy = buyResponse?.buy || {};
      const contractId = String(buy.contract_id || "");
      if (!contractId) throw new Error("Deriv buy response did not include a contract ID");
      const ratio = proposedProfitRatio(proposal, stake);
      state.lastProfitRatio = ratio;
      state.currentStake = stake;
      state.openContracts.set(contractId, {
        contractId,
        symbol,
        stake,
        tradeType: state.strategy.trade_type,
        prediction: state.strategy.prediction,
        purchasedAt: Date.now(),
        epoch,
      });
      appendJournal({
        mode: "real",
        state: "OPEN",
        contract_id: contractId,
        symbol,
        trade_type: state.strategy.trade_type,
        prediction: state.strategy.prediction,
        stake,
        profit: 0,
      });
      sendNoWait("private", { proposal_open_contract: 1, contract_id: Number(contractId), subscribe: 1 });
      updateStatus(`Direct • contract ${contractId.slice(-6)} open • Deriv owns settlement`);
    } catch (error) {
      if (state.running && !state.ownerLost) updateStatus("Direct • analyzing • last entry was not purchased");
    } finally {
      state.inFlight = false;
    }
  }

  function onContractUpdate(contract) {
    const contractId = String(contract?.contract_id || "");
    if (!contractId || !state.openContracts.has(contractId)) return;
    const sold = Boolean(contract?.is_sold) || ["won", "lost", "sold"].includes(String(contract?.status || "").toLowerCase());
    if (!sold) return;
    const open = state.openContracts.get(contractId);
    state.openContracts.delete(contractId);
    const profit = finiteNumber(contract?.profit, 0);
    const outcome = profit >= 0 ? "WIN" : "LOSS";
    state.sessionProfit = Math.round((state.sessionProfit + profit) * 100000000) / 100000000;
    if (profit < 0) {
      state.consecutiveLosses += 1;
      state.recoveryDebt = Math.max(0, state.recoveryDebt + Math.abs(profit));
    } else {
      state.recoveryDebt = Math.max(0, state.recoveryDebt - profit);
      if (state.recoveryDebt <= 0.009) {
        state.recoveryDebt = 0;
        state.consecutiveLosses = 0;
        state.currentStake = baseStake();
      }
    }
    appendJournal({
      mode: "real",
      state: "SETTLED",
      contract_id: contractId,
      symbol: open.symbol,
      trade_type: open.tradeType,
      prediction: open.prediction,
      stake: open.stake,
      outcome,
      profit,
      session_profit: state.sessionProfit,
      exit_spot: contract?.exit_spot ?? contract?.current_spot ?? null,
    });

    const hook = state.strategy?.virtual_hook;
    if (profit < 0 && state.strategy?.virtual_hook_enabled && hook?.enabled !== false) {
      const enterAfter = clampInt(hook?.enter_after_losses, 1, 50, 2);
      if (state.consecutiveLosses >= enterAfter) {
        state.virtualMode = true;
        state.virtualWins = 0;
      }
    }
    const tp = takeProfit();
    const sl = stopLoss();
    if (tp > 0 && state.sessionProfit >= tp) {
      stopDirect("Take profit reached");
      return;
    }
    if (sl > 0 && state.sessionProfit <= -sl) {
      stopDirect("Stop loss reached");
      return;
    }
    updateStatus(state.virtualMode
      ? "Direct • virtual protection active • no real BUY until confirmation"
      : "Direct • settlement received • analyzing next entry");
  }

  function onTick(symbol, tick) {
    const history = recordTick(symbol, tick);
    if (!history || !state.running || !state.strategy || state.ownerLost) return;
    if (!state.strategy.markets.includes(symbol)) return;
    if (state.virtualPending) advanceVirtual(symbol, history);
    if (state.virtualPending || state.inFlight || state.openContracts.size) return;
    if (!strategyMatches(history)) return;
    if (state.virtualMode) beginVirtual(symbol, history);
    else executeReal(symbol, history);
  }

  async function armOnce(epoch, strategy) {
    const response = await fetchWithTimeout(
      apiPath("/me/direct-execution/arm"),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ epoch, strategy }),
      },
      6500,
    );
    if (!response.ok) throw new Error("arm unavailable");
    const payload = await response.json();
    if (!state.running || state.epoch !== epoch) return false;
    state.armed = true;
    state.lastLeaseAckAt = Date.now();
    state.leaseMs = Math.max(10000, finiteNumber(payload?.lease_seconds, 20) * 1000);
    return true;
  }

  function armInBackground(epoch, strategy) {
    const attempt = async () => {
      if (!state.running || state.epoch !== epoch || state.ownerLost || state.armed) return;
      try {
        if (await armOnce(epoch, strategy)) {
          updateStatus("Direct • browser owns execution • offline continuation armed");
          return;
        }
      } catch (_) {}
      if (state.running && state.epoch === epoch && !state.ownerLost && !state.armed) setTimeout(attempt, 2500);
    };
    attempt();
  }

  async function heartbeatOnce(epoch) {
    const response = await fetchWithTimeout(
      apiPath("/me/direct-execution/heartbeat"),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ epoch }),
      },
      4000,
    );
    if (!response.ok) throw new Error("heartbeat unavailable");
    if (state.running && state.epoch === epoch && !state.ownerLost) state.lastLeaseAckAt = Date.now();
  }

  function ownershipWatch() {
    clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = setInterval(() => {
      if (!state.running || !state.armed || state.ownerLost) return;
      const elapsed = Date.now() - state.lastLeaseAckAt;
      const surrenderAt = Math.max(5000, state.leaseMs - LEASE_SAFETY_MS);
      if (elapsed >= surrenderAt) {
        // Fail closed before the server lease can expire. From this point the
        // browser performs no proposal/BUY and does not send another heartbeat;
        // the VPS may safely assume ownership after the remaining lease window.
        state.ownerLost = true;
        updateStatus("Server continuity • browser surrendered execution ownership");
        return;
      }
      heartbeatOnce(state.epoch).catch(() => {});
    }, HEARTBEAT_MS);
  }

  async function ensureStrategy() {
    if (state.strategy) return state.strategy;
    if (loadCachedStrategy()) return state.strategy;
    try {
      const response = await fetchWithTimeout(apiPath("/me/custom-strategy"), { method: "GET" }, 7000);
      if (response.ok) {
        const payload = await response.json();
        if (payload?.config?.configured) {
          return cacheStrategy({
            ...payload.config,
            martingale: payload.martingale || payload.config.martingale,
            execution_settings: {
              stake_amount: state.account?.stake_amount,
              take_profit: state.account?.take_profit,
              stop_loss: state.account?.stop_loss,
            },
          });
        }
      }
    } catch (_) {}
    return null;
  }

  async function startDirect(strategyOverride = null) {
    if (state.running && !state.ownerLost) return true;
    const strategy = cacheStrategy(strategyOverride) || await ensureStrategy();
    if (!strategy) {
      updateStatus("Direct • save a strategy before Run");
      return false;
    }
    state.running = true;
    state.epoch = randomEpoch();
    state.ownerLost = false;
    state.armed = false;
    state.lastLeaseAckAt = 0;
    state.histories.clear();
    state.subscribedMarkets.clear();
    state.inFlight = false;
    state.virtualPending = null;
    state.virtualMode = false;
    state.virtualWins = 0;
    state.consecutiveLosses = 0;
    state.sessionProfit = 0;
    state.recoveryDebt = 0;
    state.currentStake = baseStake();
    updateRunUI();
    updateStatus("Direct • Run active • analyzing Deriv ticks now");
    connectPublic().then(subscribeMarkets).catch(() => {});
    connectPrivate().catch(() => {});
    armInBackground(state.epoch, strategy);
    ownershipWatch();
    return true;
  }

  function persistStop(epoch) {
    clearTimeout(state.stopRetryTimer);
    let attempts = 0;
    const send = async () => {
      attempts += 1;
      try {
        const response = await fetchWithTimeout(
          apiPath("/me/direct-execution/stop"),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ epoch }),
            keepalive: true,
          },
          4500,
        );
        if (response.ok) return;
      } catch (_) {}
      if (attempts < 12 && !state.running) state.stopRetryTimer = setTimeout(send, Math.min(8000, 800 + attempts * 600));
    };
    send();
  }

  function stopDirect(reason = "Trading stopped") {
    const previousEpoch = state.epoch;
    state.running = false;
    state.epoch = randomEpoch();
    state.ownerLost = false;
    state.armed = false;
    state.inFlight = false;
    state.virtualPending = null;
    clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = null;
    updateRunUI();
    updateStatus(`Direct • ${reason} • no new BUY permitted`);
    persistStop(previousEpoch);
    return true;
  }

  function backgroundClearServer(scope = "today") {
    originalFetch(apiPath("/me/clear-trades"), {
      method: "POST",
      credentials: "include",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope }),
    }).catch(() => {});
  }

  function observeResponse(path, response) {
    if (!response?.clone) return;
    if (path === "/me") {
      response.clone().json().then((payload) => {
        if (!payload || payload.authenticated === false) return;
        const previous = accountKey();
        state.account = payload;
        if (previous !== accountKey()) {
          state.strategy = null;
          loadCachedStrategy();
          if (!state.running) connectPrivate().catch(() => {});
        }
      }).catch(() => {});
    }
    if (path === "/me/custom-strategy") {
      response.clone().json().then((payload) => {
        if (payload?.config?.configured) cacheStrategy({
          ...payload.config,
          martingale: payload.martingale || payload.config.martingale,
          execution_settings: {
            stake_amount: state.account?.stake_amount,
            take_profit: state.account?.take_profit,
            stop_loss: state.account?.stop_loss,
          },
        });
      }).catch(() => {});
    }
  }

  window.fetch = async function derivAdminDirectFetch(input, init) {
    const path = requestPath(input);
    const method = requestMethod(input, init);

    if (path === "/me/custom-strategy" && method === "POST") {
      const body = await requestBodyJson(input, init);
      const strategy = cacheStrategy(body);
      if (strategy) {
        // Saving in the live UI is instant. The authoritative server copy is
        // persisted by /direct-execution/arm in the background when Run starts.
        return jsonResponse({
          success: true,
          config: strategy,
          martingale: strategy.martingale,
          lifecycle: "stopped",
          recovery_reset: true,
          history_preserved: true,
          direct_local_save: true,
          message: "Strategy ready for direct Deriv execution.",
        });
      }
    }

    if ((path === "/me/resume-trading" || path === "/me/auto-trade") && method === "POST" && Date.now() < state.manualIntentUntil) {
      const body = await requestBodyJson(input, init);
      if (path !== "/me/auto-trade" || body?.enabled !== false) {
        startDirect().catch(() => {});
        state.manualIntentUntil = 0;
        return jsonResponse({
          success: true,
          state: "running",
          lifecycle: "running",
          runtime_state: "RUNNING",
          enabled: true,
          transport: "browser_direct_deriv_websocket",
        });
      }
    }

    if ((path === "/me/stop-trading" || path === "/me/pause-trading") && method === "POST" && state.running) {
      stopDirect(path.includes("pause") ? "Trading paused" : "Trading stopped");
      return jsonResponse({ success: true, state: "stopped", lifecycle: "stopped", enabled: false, direct: true });
    }

    if (path === "/me/clear-trades" && method === "POST") {
      const body = await requestBodyJson(input, init);
      clearLocalTrades();
      backgroundClearServer(String(body?.scope || "today"));
      return jsonResponse({ success: true, scope: String(body?.scope || "today"), direct_local_clear: true, message: "Run history cleared." });
    }

    const response = await originalFetch(input, init);
    observeResponse(path, response);
    return response;
  };

  document.addEventListener("click", (event) => {
    const pageStart = event.target?.closest?.("[data-start-trading]");
    if (pageStart && !pageStart.closest(".global-run-panel")) {
      // Let the canonical builder collect the current unsaved DOM into its normal
      // custom-strategy POST. Our fetch shim stores that payload locally instantly
      // and converts only the subsequent manual Resume call to direct execution.
      state.manualIntentUntil = Date.now() + MANUAL_INTENT_MS;
      return;
    }

    const globalControl = event.target?.closest?.(".global-run-panel [data-run-start],.global-run-panel [data-run-execution-toggle]");
    if (globalControl) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (state.running) stopDirect("Trading stopped");
      else {
        state.manualIntentUntil = Date.now() + MANUAL_INTENT_MS;
        startDirect().catch(() => {});
      }
      return;
    }

    const reset = event.target?.closest?.(".global-run-panel [data-run-reset]");
    if (reset) {
      event.preventDefault();
      event.stopImmediatePropagation();
      clearLocalTrades();
      backgroundClearServer("today");
      updateStatus(state.running ? "Direct • run history cleared • execution continues" : "Direct • run history cleared");
    }
  }, true);

  window.addEventListener("online", () => {
    connectPublic().catch(() => {});
    if (state.running && !state.ownerLost) connectPrivate().catch(() => {});
    else if (!state.running) connectPrivate().catch(() => {});
  });

  window.addEventListener("offline", () => {
    if (state.running) updateStatus("Direct • device offline • no browser BUY while disconnected");
  });

  window.addEventListener("pageshow", () => {
    connectPublic().catch(() => {});
    if (!state.running) connectPrivate().catch(() => {});
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      connectPublic().catch(() => {});
      if (!state.running || !state.ownerLost) connectPrivate().catch(() => {});
    }
  });

  state.keepaliveTimer = setInterval(() => {
    try { if (state.publicWs?.readyState === WebSocket.OPEN) state.publicWs.send(JSON.stringify({ ping: 1 })); } catch (_) {}
    try { if (state.privateWs?.readyState === WebSocket.OPEN) state.privateWs.send(JSON.stringify({ ping: 1 })); } catch (_) {}
  }, 25000);

  const style = document.createElement("style");
  style.id = "deriv-direct-execution-v1-style";
  style.textContent = `
    .direct-execution-state{margin:0 0 8px;padding:7px 10px;border-radius:9px;background:rgba(10,82,134,.14);font-size:11px;line-height:1.35;color:#9fc8eb;border:1px solid rgba(93,175,236,.15)}
    .direct-execution-state[data-owner="browser"]::before{content:"● ";font-size:9px}
    .direct-execution-state[data-owner="server"]{background:rgba(128,91,19,.13);color:#e3c58a}
    .global-run-panel[data-execution-transport="deriv-direct"] [data-run-start]{cursor:pointer}
  `;
  document.head.appendChild(style);

  loadCachedStrategy();
  connectPublic().catch(() => {});
  prewarmData().finally(() => connectPrivate().catch(() => {}));
  updateRunUI();
  updateStatus("Direct • preparing Deriv connection before Run");

  window.DERIVADMIN_DIRECT_EXECUTION_V1 = Object.freeze({
    version: VERSION,
    start: startDirect,
    stop: stopDirect,
    clear: clearLocalTrades,
    prewarm: () => Promise.allSettled([connectPublic(), connectPrivate(), prewarmData()]),
    state: () => ({
      running: state.running,
      owner: state.ownerLost ? "server_takeover" : state.running ? "browser" : "stopped",
      armed: state.armed,
      secure_ws: state.privateWs?.readyState === WebSocket.OPEN,
      market_ws: state.publicWs?.readyState === WebSocket.OPEN,
      open_contracts: state.openContracts.size,
      session_profit: state.sessionProfit,
      virtual_mode: state.virtualMode,
      strategy: state.strategy,
    }),
  });
})();
