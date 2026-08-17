(() => {
  "use strict";

  if (window.__DERIVADMIN_VPS_API_BOUNDARY_V2__) return;
  window.__DERIVADMIN_VPS_API_BOUNDARY_V2__ = true;

  const nativeFetch = window.fetch.bind(window);
  const API_PREFIX = "/api";
  const READ_TIMEOUT_MS = 10000;
  const WRITE_TIMEOUT_MS = 8000;
  const LIVE_CACHE_MAX_AGE_MS = 5000;
  const LOCAL_PREVIEW = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  const LOCAL_STORE = "derivadmin-local-preview-state-v1";
  const SUPPORTED_MARKETS = ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V", "R_10", "R_25", "R_50", "R_75", "R_100"];
  let lastMe = null;

  window.FOA_FULL_VPS_FRONTEND = true;
  try { window.EventSource = undefined; } catch (_) {}

  function asURL(input) {
    try {
      if (input instanceof Request) return new URL(input.url, window.location.origin);
      return new URL(String(input), window.location.origin);
    } catch (_) { return null; }
  }
  function pathOf(input) {
    const url = asURL(input);
    return url ? `${url.pathname}${url.search}` : "";
  }
  function routeOf(path) { return String(path || "").split("?", 1)[0]; }
  function unproxiedRouteOf(path) {
    const route = routeOf(path);
    return route.startsWith(`${API_PREFIX}/`) ? route.slice(API_PREFIX.length) || "/" : route;
  }
  function shouldProxy(path) {
    const route = routeOf(path);
    if (!route || route.startsWith(`${API_PREFIX}/`)) return false;
    return route === "/health" || route.startsWith("/health/") || route === "/me" || route.startsWith("/me/") || route === "/metrics" || route.startsWith("/metrics/");
  }
  function rewrittenURL(input) {
    const url = asURL(input);
    if (!url || url.origin !== window.location.origin) return input;
    const path = `${url.pathname}${url.search}`;
    if (!shouldProxy(path)) return input;
    const next = `${API_PREFIX}${url.pathname}${url.search}`;
    if (!(input instanceof Request)) return next;
    return new Request(next, {
      method: input.method,
      headers: input.headers,
      body: ["GET", "HEAD"].includes(input.method.toUpperCase()) ? undefined : input.body,
      mode: input.mode,
      credentials: input.credentials,
      cache: input.cache,
      redirect: input.redirect,
      referrer: input.referrer,
      referrerPolicy: input.referrerPolicy,
      integrity: input.integrity,
      keepalive: input.keepalive,
      signal: input.signal,
      duplex: input.duplex,
    });
  }
  function responseJSON(payload, status = 200, headers = {}) {
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers },
    });
  }
  function localPreviewState() {
    const now = new Date();
    const expires = new Date(now.getTime() + 7 * 86400000).toISOString();
    const initial = {
      selectedAccountId: 101,
      enabled: false,
      timezone: "Africa/Nairobi",
      customStrategy: {
        market_mode: "single",
        markets: ["1HZ100V"],
        trade_type: "over",
        prediction: 3,
        duration_ticks: 1,
        conditions: [{ source: "last_digit", operator: ">=", value: 4, window: 1 }],
        execution_settings: { stake_amount: 0.5, take_profit: 10, stop_loss: 5, martingale_enabled: true },
        virtual_hook_enabled: true,
        virtual_hook: { enter_after_losses: 2, exit_after_consecutive_wins: 2 },
      },
      schedules: [],
      trades: [
        { contract_id: "local-1003", symbol: "1HZ100V", contract_type: "DIGITOVER", barrier: 3, stake: 0.5, payout: 0.95, profit: 0.45, outcome: "WIN", entry_spot: 8241.4, exit_spot: 8242.8, purchase_time: new Date(now.getTime() - 120000).toISOString() },
        { contract_id: "local-1002", symbol: "1HZ100V", contract_type: "DIGITUNDER", barrier: 6, stake: 0.5, payout: 0, profit: -0.5, outcome: "LOSS", entry_spot: 8238.7, exit_spot: 8240.1, purchase_time: new Date(now.getTime() - 300000).toISOString() },
        { contract_id: "virtual-1001", is_virtual: true, symbol: "1HZ100V", contract_type: "DIGITOVER", barrier: 3, stake: 0, profit: 0, outcome: "WIN", display_result: "Virtual win", entry_spot: 8235.2, exit_spot: 8237.9, purchase_time: new Date(now.getTime() - 480000).toISOString() },
      ],
      premium: {
        active: true,
        has_access: true,
        status: "active",
        local_dev_preview: true,
        linked_account_count: 2,
        started_at: now.toISOString(),
        expires_at: expires,
        message: "Local testing access is active.",
      },
    };
    try {
      const saved = JSON.parse(localStorage.getItem(LOCAL_STORE) || "null");
      return saved && typeof saved === "object" ? { ...initial, ...saved, premium: { ...initial.premium, ...(saved.premium || {}) } } : initial;
    } catch (_) { return initial; }
  }
  function saveLocalPreviewState(state) {
    try { localStorage.setItem(LOCAL_STORE, JSON.stringify(state)); } catch (_) {}
  }
  function localAccounts(state) {
    return [
      { managed_account_id: 101, account_id: "DOT91317422", account_id_masked: "DOT91317422", label: "Demo", account_type: "demo", currency: "USD", balance: 8630.78, selected: state.selectedAccountId === 101 },
      { managed_account_id: 202, account_id: "ROT90580032", account_id_masked: "ROT90580032", label: "USD", account_type: "real", currency: "USD", balance: 0.51, selected: state.selectedAccountId === 202 },
    ];
  }
  function localMe(state) {
    const account = localAccounts(state).find((item) => item.selected) || localAccounts(state)[0];
    const settled = state.trades.filter((trade) => !trade.is_virtual);
    const wins = settled.filter((trade) => String(trade.outcome).toUpperCase() === "WIN").length;
    const losses = settled.filter((trade) => String(trade.outcome).toUpperCase() === "LOSS").length;
    const profit = settled.reduce((sum, trade) => sum + Number(trade.profit || 0), 0);
    return {
      authenticated: true,
      local_dev_preview: true,
      account_id: account.account_id_masked,
      account_type: account.account_type,
      currency: account.currency,
      balance: account.balance,
      enabled: Boolean(state.enabled),
      settings: state.customStrategy.execution_settings || {},
      stats: { trades: settled.length, wins, losses, profit },
    };
  }
  async function requestPayload(options = {}) {
    if (!options.body) return {};
    try { return JSON.parse(String(options.body)); } catch (_) { return {}; }
  }
  function localTrades(state) {
    const settled = state.trades.filter((trade) => !trade.is_virtual);
    const wins = settled.filter((trade) => String(trade.outcome).toUpperCase() === "WIN").length;
    const losses = settled.filter((trade) => String(trade.outcome).toUpperCase() === "LOSS").length;
    const profit = settled.reduce((sum, trade) => sum + Number(trade.profit || 0), 0);
    return {
      trades: state.trades,
      summary: { total: settled.length, wins, losses, profit, open: state.enabled ? 1 : 0, virtual_open: 0 },
      local_dev_preview: true,
    };
  }
  function firstNumber(text, patterns, fallback) {
    for (const pattern of patterns) {
      const match = String(text || "").match(pattern);
      if (match) {
        const value = Number(match[1]);
        if (Number.isFinite(value)) return value;
      }
    }
    return fallback;
  }
  function compiledStrategy(text) {
    const raw = String(text || "");
    const lowered = raw.toLowerCase();
    const named = raw.match(/(?:called|named|name(?: it)?|strategy name)\s+([A-Za-z0-9][A-Za-z0-9 _-]{1,48})(?:[.,]|$)/i);
    const name = named ? named[1].trim() : "Local AI Strategy";
    const detectTradeType = (value) => {
      const source = String(value || "").toLowerCase();
      return source.includes("differs") ? "differs"
        : source.includes("matches") ? "matches"
        : source.includes("under") ? "under"
        : source.includes("even") ? "even"
        : source.includes("odd") ? "odd"
        : source.includes("fall") ? "fall"
        : source.includes("rise") ? "rise"
        : source.includes("over") ? "over"
        : null;
    };
    const compareOperator = (value, fallback = ">=") => {
      const source = String(value || "").toLowerCase();
      if (/(?:less than|lower than|below|under|<=|less or equal|less than or equal)/.test(source)) return "<=";
      if (/(?:greater than|higher than|above|over|>=|at least|more than)/.test(source)) return ">=";
      return fallback;
    };
    const sentenceForCondition = (condition) => {
      if (condition.kind === "percentage") return `${condition.target}${condition.value == null ? "" : " " + condition.value} ${condition.operator} ${condition.threshold}% in ${condition.window} ticks`;
      return `last ${condition.window} digit${condition.window === 1 ? "" : "s"} ${condition.operator} ${condition.value}`;
    };
    const tradeType = detectTradeType(lowered) || "over";
    const needsPrediction = ["over", "under", "matches", "differs"].includes(tradeType);
    const prediction = needsPrediction
      ? firstNumber(lowered, [new RegExp(`${tradeType}\\s+(\\d)`), /\b(?:digit|barrier|prediction)\s*(?:is|=|over|under|matches|differs)?\s*(\d)\b/, /\b([0-9])\b/], tradeType === "under" ? 6 : 3)
      : null;
    const market = lowered.includes("75") ? "1HZ75V" : lowered.includes("50") ? "1HZ50V" : lowered.includes("25") ? "1HZ25V" : lowered.includes("10") ? "1HZ10V" : "1HZ100V";
    const marketMode = /\b(?:all markets?|every market|all supported markets)\b/.test(lowered) ? "all" : "single";
    const strategyMode = lowered.includes("combined mode") || lowered.includes("combined") ? "combined" : lowered.includes("percentage mode") || lowered.includes("percentage") ? "percentage" : "last_digit";
    const stake = firstNumber(lowered, [/\bstake\s*(?:usd|\$)?\s*([0-9]+(?:\.[0-9]+)?)/, /\$([0-9]+(?:\.[0-9]+)?)/], 0.5);
    const takeProfit = firstNumber(lowered, [/\b(?:take profit|tp|profit)\s*(?:usd|\$)?\s*([0-9]+(?:\.[0-9]+)?)/], 10);
    const stopLoss = firstNumber(lowered, [/\b(?:stop loss|sl|loss)\s*(?:after|at|usd|\$)?\s*([0-9]+(?:\.[0-9]+)?)/], 5);
    const duration = firstNumber(lowered, [/\b(?:duration|contract duration|trade for|for)\s*([0-9]+)\s*ticks?\b/], 1);
    const lastWindow = firstNumber(lowered, [/\blast\s+([0-9]+)\s+digits?/, /\b([0-9]+)\s+digits?\b/], 1);
    const percentageWindow = firstNumber(lowered, [/\bover\s+([0-9]+)\s+ticks?/, /\blast\s+([0-9]+)\s+ticks?/], 1000);
    const percentage = firstNumber(lowered, [/\b(?:above|over|>=|at least)\s*([0-9]+(?:\.[0-9]+)?)\s*%/, /\b([0-9]+(?:\.[0-9]+)?)\s*%/], null);
    const digitContext = (lowered.match(/last[^.,;]*/) || [lowered])[0];
    const percentageContext = (lowered.match(/(?:percentage|percent|%)?[^.,;]*(?:above|over|below|under|at least|>=|<=)[^.,;]*%/) || [lowered])[0];
    const digitConditionValue = firstNumber(digitContext, [
      /\blast\s+(?:[0-9]+\s+)?digits?[^0-9]*(?:<=|>=|=|equal to|less than or equal to|greater than or equal to|less than|greater than|below|above|at least)\s*(\d)\b/,
      /\b(?:is|are)\s+(\d)\s+(?:or\s+)?(?:greater|higher|more|less|lower|below|above)/,
      /\b(?:to|than)\s+(\d)\b/,
    ], prediction ?? 0);
    const conditions = [];
    if (strategyMode !== "percentage" || percentage === null || lowered.includes("last")) {
      conditions.push({ kind: "digit_compare", source: "last_digit", window: Math.max(1, Math.min(1000, Math.round(lastWindow))), operator: compareOperator(digitContext, tradeType === "under" ? "<=" : ">="), value: digitConditionValue });
    }
    if (strategyMode !== "last_digit" && percentage !== null) {
      conditions.push({ kind: "percentage", window: Math.max(1, Math.min(1000, Math.round(percentageWindow))), target: ["over", "under", "matches", "differs"].includes(tradeType) ? (tradeType === "matches" || tradeType === "differs" ? "digit" : tradeType) : tradeType, value: prediction ?? 0, operator: compareOperator(percentageContext, ">="), threshold: Math.max(0, Math.min(100, percentage)) });
    }
    const afterLossText = (lowered.split(/after(?: a)? loss|loss route|different strategy after loss|switch to/).pop() || "").trim();
    const afterLossEnabled = /after(?: a)? loss|loss route|different strategy after loss|switch to/.test(lowered) && afterLossText && afterLossText !== lowered;
    const afterLossTradeType = detectTradeType(afterLossText) || tradeType;
    const afterLossNeedsPrediction = ["over", "under", "matches", "differs"].includes(afterLossTradeType);
    const afterLossPrediction = afterLossNeedsPrediction ? firstNumber(afterLossText, [new RegExp(`${afterLossTradeType}\\s+(\\d)`), /\b(?:digit|barrier|prediction)\s*(?:is|=)?\s*(\d)\b/, /\b([0-9])\b/], prediction ?? 0) : null;
    const afterLossConditionValue = firstNumber(afterLossText, [
      /\blast\s+(?:[0-9]+\s+)?digits?[^0-9]*(?:<=|>=|=|equal to|less than or equal to|greater than or equal to|less than|greater than|below|above|at least)\s*(\d)\b/,
      /\b(?:to|than)\s+(\d)\b/,
    ], afterLossPrediction ?? prediction ?? 0);
    const afterLossCondition = { kind: "digit_compare", source: "last_digit", window: Math.max(1, Math.min(1000, Math.round(firstNumber(afterLossText, [/\blast\s+([0-9]+)\s+digits?/], lastWindow)))), operator: compareOperator(afterLossText, afterLossTradeType === "under" ? "<=" : ">="), value: afterLossConditionValue };
    const custom_strategy = {
      configured: true,
      name,
      strategy_mode: strategyMode,
      market_mode: marketMode,
      markets: marketMode === "all" ? SUPPORTED_MARKETS : [market],
      trade_type: tradeType,
      prediction,
      duration_ticks: Math.max(1, Math.min(100, Math.round(duration))),
      conditions,
      match: "all",
      result_routing: { enabled: afterLossEnabled, after_loss: afterLossEnabled ? { trade_type: afterLossTradeType, prediction: afterLossPrediction, duration_ticks: Math.max(1, Math.min(100, Math.round(duration))), conditions: [afterLossCondition], match: "all" } : null },
      martingale: { mode: lowered.includes("split") ? "split" : "system", multiplier: 2, split_count: lowered.includes("split") ? 2 : 1 },
      execution_settings: { stake_amount: stake, take_profit: takeProfit, stop_loss: stopLoss, martingale_enabled: true, martingale_multiplier: 2 },
      virtual_hook_enabled: lowered.includes("virtual") || lowered.includes("hook") || lowered.includes("loss"),
      virtual_hook: { enabled: lowered.includes("virtual") || lowered.includes("hook") || lowered.includes("loss"), enter_after_losses: firstNumber(lowered, [/\bafter\s+([0-9]+)\s+loss/], 2), exit_after_consecutive_wins: firstNumber(lowered, [/\bafter\s+([0-9]+)\s+win/], 1) },
    };
    return {
      name,
      market_label: marketMode === "all" ? "All supported markets" : market,
      contract_label: tradeType,
      rules: conditions.map(sentenceForCondition),
      created_statement: `I created ${tradeType}${prediction == null ? "" : " " + prediction} on ${marketMode === "all" ? "all supported markets" : market}${conditions.length ? " when " + conditions.map(sentenceForCondition).join(" and ") : ""}.`,
      best_possible_interpretation: `Compiled ${name} as ${strategyMode} ${tradeType}${prediction == null ? "" : " " + prediction} using ${conditions.length} supported condition${conditions.length === 1 ? "" : "s"}${afterLossEnabled ? " plus an after-loss route" : ""}.`,
      unsupported_or_adjusted_items: [],
      custom_strategy,
      canonical: custom_strategy,
      settings: custom_strategy.execution_settings,
      local_dev_preview: true,
    };
  }
  async function localPreviewResponse(path, options = {}) {
    if (!LOCAL_PREVIEW) return null;
    const route = unproxiedRouteOf(path);
    if (!route.startsWith("/me") && !route.startsWith("/metrics") && !route.startsWith("/health")) return null;
    const method = String(options.method || "GET").toUpperCase();
    const state = localPreviewState();
    const now = new Date();
    if (route === "/health" || route.startsWith("/health/")) return responseJSON({ ok: true, local_dev_preview: true });
    if (route === "/me") return responseJSON(localMe(state), 200, { "X-DerivAdmin-Source": "local-preview" });
    if (route === "/me/accounts") return responseJSON({ accounts: localAccounts(state), local_dev_preview: true });
    if (route === "/me/premium-access") return responseJSON(state.premium);
    if (route === "/me/premium-access/renewal-status") return responseJSON({ premium: state.premium, renewal: { stage: "active", reminder_stage: "none" } });
    if (route === "/me/premium-access/renewal-history") return responseJSON({ items: [{ period_start: state.premium.started_at, period_end: state.premium.expires_at, provider: "local-preview" }] });
    if (route === "/me/premium-access/payment-options") return responseJSON({ premium: state.premium, methods: { mpesa: { available: true, local_dev_preview: true } } });
    if (route === "/me/premium-access/mpesa/payments/latest") return responseJSON({ premium: state.premium, payment: null });
    if (route.startsWith("/me/premium-access/mpesa/payments/")) return responseJSON({ premium: state.premium, payment: { id: "local-payment", status: "success", activated: true } });
    if (route === "/me/premium-access/mpesa/stk-push") return responseJSON({ premium: state.premium, payment: { id: "local-payment", status: "success", activated: true } });
    if (route === "/me/trades/today") return responseJSON(localTrades(state));
    if (route === "/me/live-snapshot") return responseJSON({ me: localMe(state), lifecycle: { lifecycle: state.enabled ? "running" : "stopped", runtime_state: state.enabled ? "running" : "stopped" }, trades: localTrades(state) });
    if (route === "/me/live-ticket") return responseJSON({ ticket: "local-preview-no-websocket" });
    if (route === "/me/trading-lifecycle" || route === "/me/execution-runtime") return responseJSON({ lifecycle: state.enabled ? "running" : "stopped", runtime_state: state.enabled ? "running" : "stopped", enabled: Boolean(state.enabled), local_dev_preview: true });
    if (route === "/me/automation-preferences") return responseJSON({ timezone: state.timezone, requires_timezone_onboarding: false, local_dev_preview: true });
    if (route === "/me/automation-preferences/timezone" && method === "POST") {
      state.timezone = String((await requestPayload(options)).timezone || state.timezone || "Africa/Nairobi");
      saveLocalPreviewState(state);
      return responseJSON({ timezone: state.timezone, requires_timezone_onboarding: false, local_dev_preview: true });
    }
    if (route === "/me/custom-strategy") {
      if (method === "POST") {
        state.customStrategy = await requestPayload(options);
        saveLocalPreviewState(state);
      }
      return responseJSON({
        custom_strategy: state.customStrategy,
        strategy: state.customStrategy,
        config: state.customStrategy,
        martingale: state.customStrategy?.martingale || { mode: "system", multiplier: 2, split_count: 2 },
        supported: {
          markets: SUPPORTED_MARKETS,
          market_modes: ["single", "selected", "all"],
          martingale: { modes: ["system", "multiplier", "split"], default_multiplier: 2, minimum_multiplier: 1.1, maximum_multiplier: 10, default_split_count: 2, minimum_split_count: 1, maximum_split_count: 3 },
        },
        local_dev_preview: true,
      });
    }
    if (route === "/me/text-to-strategy/compile" && method === "POST") return responseJSON(compiledStrategy((await requestPayload(options)).text));
    if (route === "/me/automation-schedules") {
      if (method === "POST") {
        const payload = await requestPayload(options);
        state.schedules.unshift({
          id: `local-schedule-${Date.now()}`,
          strategy_name: payload.strategy_name || "Local strategy",
          strategy_source: payload.strategy_source || "local",
          strategy_snapshot: payload.strategy_snapshot || {},
          scheduled_local: `${payload.date || now.toISOString().slice(0, 10)} ${payload.time || "09:00"} ${payload.timezone || state.timezone}`,
          scheduled_for_utc: now.toISOString(),
          date: payload.date || now.toISOString().slice(0, 10),
          time: payload.time || "09:00",
          timezone: payload.timezone || state.timezone,
          status: "scheduled",
          stake: Number(payload.stake || 0.5),
          take_profit: Number(payload.take_profit || 0),
          stop_loss: Number(payload.stop_loss || 0),
        });
        saveLocalPreviewState(state);
      }
      return responseJSON({ items: state.schedules, schedules: state.schedules, local_dev_preview: true });
    }
    const cancelMatch = route.match(/^\/me\/automation-schedules\/([^/]+)\/cancel$/);
    if (cancelMatch && method === "POST") {
      state.schedules = state.schedules.map((item) => String(item.id) === decodeURIComponent(cancelMatch[1]) ? { ...item, status: "cancelled" } : item);
      saveLocalPreviewState(state);
      return responseJSON({ ok: true, schedules: state.schedules, local_dev_preview: true });
    }
    if (["/me/resume-trading", "/me/auto-trade"].includes(route) && method === "POST") {
      state.enabled = true;
      saveLocalPreviewState(state);
      return responseJSON({ ok: true, enabled: true, lifecycle: "running", local_dev_preview: true });
    }
    if (["/me/pause-trading", "/me/stop-trading"].includes(route) && method === "POST") {
      state.enabled = false;
      saveLocalPreviewState(state);
      return responseJSON({ ok: true, enabled: false, lifecycle: "stopped", local_dev_preview: true });
    }
    if (route === "/me/clear-trades" && method === "POST") {
      state.trades = [];
      saveLocalPreviewState(state);
      return responseJSON(localTrades(state));
    }
    if (route === "/me/switch-account" && method === "POST") {
      state.selectedAccountId = Number((await requestPayload(options)).managed_account_id || state.selectedAccountId);
      saveLocalPreviewState(state);
      return responseJSON({ ok: true, account: localAccounts(state).find((item) => item.selected), local_dev_preview: true });
    }
    if (route === "/metrics/summary") return responseJSON({ performance_profile: "local-preview", local_dev_preview: true });
    return responseJSON({ detail: `Local preview route is not mocked: ${route}` }, 404, { "X-DerivAdmin-Source": "local-preview" });
  }
  function livePayload(path) {
    const cache = window.FOA_VPS_LIVE_CACHE;
    if (!cache || Date.now() - Number(cache.savedAt || 0) > LIVE_CACHE_MAX_AGE_MS) return null;
    const route = routeOf(path);
    if (route === "/me") return cache.me || null;
    if (route === "/me/trading-lifecycle" || route === "/me/execution-runtime") return cache.lifecycle || null;
    if (route === "/me/trades/today") return cache.trades || null;
    return null;
  }
  function transformedBody(route, options) {
    if (!options?.body || typeof options.body !== "string") return options;
    let payload;
    try { payload = JSON.parse(options.body); } catch (_) { return options; }

    // 6F-2 exposes stake/TP/SL but not a martingale editor. Preserve the account's
    // existing recovery toggle instead of changing it implicitly during Save.
    if (route === "/me/custom-strategy" && payload?.execution_settings) {
      const current = lastMe?.settings?.martingale_enabled;
      if (typeof current === "boolean") payload.execution_settings.martingale_enabled = current;
    }

    // Action 5 accepts a frozen custom_strategy wrapper. Normalize a direct
    // canonical strategy from Builder/AI without changing scheduler authority.
    if (route === "/me/automation-schedules" && payload?.strategy_snapshot?.market_mode && Array.isArray(payload.strategy_snapshot.conditions)) {
      payload.strategy_snapshot = { custom_strategy: payload.strategy_snapshot };
    }
    return { ...options, body: JSON.stringify(payload) };
  }
  function timeoutFor(method) { return method === "GET" || method === "HEAD" ? READ_TIMEOUT_MS : WRITE_TIMEOUT_MS; }
  async function boundedFetch(input, options = {}) {
    const method = String(options.method || (input instanceof Request ? input.method : "GET") || "GET").toUpperCase();
    const controller = new AbortController();
    const upstreamSignal = options.signal || (input instanceof Request ? input.signal : null);
    let detach = null;
    if (upstreamSignal) {
      detach = () => controller.abort(upstreamSignal.reason);
      if (upstreamSignal.aborted) detach();
      else upstreamSignal.addEventListener("abort", detach, { once: true });
    }
    const timeoutMs = timeoutFor(method);
    const timer = window.setTimeout(() => controller.abort(new Error("backend timeout")), timeoutMs);
    try {
      return await nativeFetch(input, {
        ...options,
        credentials: options.credentials || "same-origin",
        cache: options.cache || "no-store",
        signal: controller.signal,
      });
    } catch (error) {
      if (controller.signal.aborted && !upstreamSignal?.aborted) throw new Error(`Backend request timed out after ${(timeoutMs / 1000).toFixed(1)}s`);
      throw error;
    } finally {
      window.clearTimeout(timer);
      if (upstreamSignal && detach) {
        try { upstreamSignal.removeEventListener("abort", detach); } catch (_) {}
      }
    }
  }
  async function transformResponse(route, response) {
    if (!response.ok) return response;
    if (!["/me", "/me/text-to-strategy/compile", "/me/automation-schedules"].includes(route)) return response;
    let payload;
    try { payload = await response.clone().json(); } catch (_) { return response; }

    if (route === "/me") {
      lastMe = payload;
      if (!payload?.authenticated) {
        try { localStorage.removeItem("foa-session-v2"); } catch (_) {}
      }
    }
    if (route === "/me/text-to-strategy/compile" && payload?.custom_strategy) {
      const settings = payload.settings || {};
      payload.canonical = {
        ...payload.custom_strategy,
        execution_settings: {
          stake_amount: Number(settings.stake_amount ?? lastMe?.settings?.stake_amount ?? 0.5),
          take_profit: Number(settings.take_profit ?? lastMe?.settings?.take_profit ?? 0),
          stop_loss: Number(settings.stop_loss ?? lastMe?.settings?.stop_loss ?? 0),
          martingale_enabled: Boolean(lastMe?.settings?.martingale_enabled ?? true),
        },
      };
      payload.best_possible_interpretation = payload.rules?.length
        ? `${payload.market_label || "Selected market"} · ${payload.contract_label || "Selected contract"} · ${payload.rules.join("; ")}`
        : "Compiled to the nearest supported deterministic strategy.";
      payload.unsupported_or_adjusted_items = Array.isArray(payload.adjustments) ? payload.adjustments : [];
    }
    if (route === "/me/automation-schedules") {
      const items = Array.isArray(payload.items) ? payload.items : [];
      payload.schedules = items.map((item) => ({ ...item, scheduled_local: item.scheduled_local || item.date_time_local || "" }));
    }
    return responseJSON(payload, response.status, { "X-DerivAdmin-Boundary": "6F-2" });
  }

  window.fetch = async (input, options = {}) => {
    const path = pathOf(input);
    const route = routeOf(path);
    const method = String(options.method || (input instanceof Request ? input.method : "GET") || "GET").toUpperCase();
    const local = await localPreviewResponse(path, options);
    if (local) {
      const payload = await local.clone().json().catch(() => null);
      if (unproxiedRouteOf(path) === "/me" && payload) lastMe = payload;
      return local;
    }
    if (method === "GET") {
      const live = livePayload(path);
      if (live) {
        if (route === "/me") lastMe = live;
        return responseJSON(live, 200, { "X-DerivAdmin-Source": "vps-live-cache" });
      }
      if (route === "/metrics/summary") return responseJSON({ performance_profile: "full-vps-background-summary" });
    } else if (window.FOA_VPS_LIVE_CACHE) {
      window.FOA_VPS_LIVE_CACHE.savedAt = 0;
    }
    const nextOptions = transformedBody(route, options);
    const response = await boundedFetch(rewrittenURL(input), nextOptions);
    return transformResponse(route, response);
  };

  window.FOA_API_URL = (path) => {
    const value = String(path || "");
    if (value.startsWith(`${API_PREFIX}/`)) return value;
    return shouldProxy(value) ? `${API_PREFIX}${value}` : value;
  };
  window.FOA_BACKEND_PROXY_MODE = "direct-vps-same-origin-rest-6f2";
  window.FOA_VPS_API_BOUNDARY_VERSION = "20260817-6f2-2";
})();
