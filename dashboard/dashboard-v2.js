(() => {
  "use strict";

  /*
   * Builder-first dashboard shell.
   * Compatibility markers below intentionally stop legacy UI appenders from
   * re-inserting old strategy/settings panels into /ui/dashboard-v2.js.
   *
   * FOA_ACCOUNT_ID_BADGE
   * FOA_CUSTOM_STRATEGY_BUILDER_V3
   * FOA_CUSTOM_STRATEGY_CARD_VISIBILITY_V4
   * FOA_SETTINGS_PERSISTENCE_VERSION:20260802-1
   * FOA_STRATEGY_V2_UI_VERSION:20260804-2
   * FOA_VIRTUAL_WIN_PROGRESS
   * FOA_LIVE_METRICS_SYNC
   * FOA_DASHBOARD_LOADER_UNLOCK
   * FOA_AIDR_PERSONAL_STATUS
   * FOA_MULTI_STRATEGY_UI_VERSION:20260804-2
   * FOA_PUBLIC_TRADER_STATS
   * FOA_RESET_TRADES_ALWAYS
   * FOA_TRADE_OUTCOME_KPIS_AND_MANUAL_MARTINGALE_V2
   * FOA_SIGNAL_EXECUTION_ALERTS
   */

  const VERSION = "20260812-builder-refinement-3";
  const BUILDER_SCHEMA_VERSION = 3;
  const RISK_DISCLOSURE_URL = "https://deriv.com/terms-and-conditions/risk-disclosure";
  window.FOA_ACCOUNT_ID_BADGE = VERSION;
  window.FOA_CUSTOM_STRATEGY_BUILDER_V3 = VERSION;
  window.FOA_CUSTOM_STRATEGY_BUILDER_V2 = VERSION;
  window.FOA_CUSTOM_STRATEGY_BUILDER_V1 = VERSION;
  window.FOA_CUSTOM_STRATEGY_CARD_VISIBILITY_V4 = VERSION;
  window.FOA_MULTI_STRATEGY_UI_VERSION = VERSION;
  window.FOA_STRATEGY_V2_UI_VERSION = VERSION;
  window.FOA_VIRTUAL_WIN_PROGRESS = VERSION;
  window.FOA_LIVE_METRICS_SYNC = VERSION;
  window.FOA_DASHBOARD_LOADER_UNLOCK = VERSION;
  window.FOA_AIDR_PERSONAL_STATUS = VERSION;
  window.FOA_PUBLIC_TRADER_STATS = VERSION;
  window.FOA_RESET_TRADES_ALWAYS = VERSION;
  window.FOA_TRADE_OUTCOME_KPIS_AND_MANUAL_MARTINGALE_V2 = VERSION;
  window.FOA_SIGNAL_EXECUTION_ALERTS = VERSION;
  window.__FOA_SETTINGS_PERSISTENCE_VERSION__ = VERSION;

  const storageGet = (key) => {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  };
  const storageSet = (key, value) => {
    try {
      localStorage.setItem(key, value);
    } catch (_) {}
  };
  const storageRemove = (key) => {
    try {
      localStorage.removeItem(key);
    } catch (_) {}
  };

  const K = {
    theme: "foa-builder-theme",
    view: "foa-builder-view",
    mode: "foa-mode-v2",
    session: "foa-session-v2",
    builder: "foa-builder-draft-v2",
    legacyBuilder: "foa-builder-draft-v1",
    tradeReset: "foa-trade-session-reset-v1",
    lastGood: "foa-builder-last-good-snapshot-v1",
    limitDismissed: "foa-limit-notice-dismissed-v1",
  };

  const MARKETS = [
    { symbol: "1HZ10V", label: "Volatility 10 (1s)" },
    { symbol: "1HZ25V", label: "Volatility 25 (1s)" },
    { symbol: "1HZ50V", label: "Volatility 50 (1s)" },
    { symbol: "1HZ75V", label: "Volatility 75 (1s)" },
    { symbol: "1HZ100V", label: "Volatility 100 (1s)" },
    { symbol: "R_10", label: "Volatility 10" },
    { symbol: "R_25", label: "Volatility 25" },
    { symbol: "R_50", label: "Volatility 50" },
    { symbol: "R_75", label: "Volatility 75" },
    { symbol: "R_100", label: "Volatility 100" },
  ];

  const COMPARISONS = [
    { value: ">", label: "Greater than" },
    { value: "<", label: "Less than" },
    { value: "==", label: "Equal to" },
    { value: ">=", label: "Greater than or equal to" },
    { value: "<=", label: "Less than or equal to" },
    { value: "all_same", label: "All same" },
  ];

  const NUMERIC_COMPARISONS = COMPARISONS.filter((item) => item.value !== "all_same");

  const TICK_DIRECTIONS = [
    { value: "rising", label: "Up ticks" },
    { value: "falling", label: "Down ticks" },
    { value: "no_move", label: "No Move" },
  ];

  const MODE_CARDS = [
    { value: "last_digit", label: "Last Digit", symbol: "9", caption: "Last-digit rules only" },
    { value: "percentage", label: "Percentage", symbol: "%", caption: "Percentage rules only" },
    { value: "combined", label: "Combined", symbol: "link", caption: "Use both rule types" },
  ];

  const TRADE_GROUPS = [
    {
      value: "over_under",
      label: "Over/Under",
      sides: [
        { value: "over", label: "Over" },
        { value: "under", label: "Under" },
      ],
      prediction: true,
    },
    {
      value: "matches_differs",
      label: "Matches/Differs",
      sides: [
        { value: "matches", label: "Matches" },
        { value: "differs", label: "Differs" },
      ],
      prediction: true,
    },
    {
      value: "odd_even",
      label: "Odd/Even",
      sides: [
        { value: "odd", label: "Odd" },
        { value: "even", label: "Even" },
      ],
      prediction: false,
    },
    {
      value: "rise_fall",
      label: "Rise/Fall",
      sides: [
        { value: "rise", label: "Rise" },
        { value: "fall", label: "Fall" },
      ],
      prediction: false,
    },
  ];

  const DEFAULT_BUILDER = {
    version: BUILDER_SCHEMA_VERSION,
    strategyMode: "combined",
    marketMode: "selected",
    markets: ["1HZ10V", "1HZ25V", "1HZ100V"],
    oneMarket: "1HZ100V",
    lastRule: {
      window: 5,
      target: "last_digits",
      operator: ">=",
      value: 3,
    },
    percentageRule: {
      target: "even",
      value: 5,
      window: 500,
      operator: ">=",
      threshold: 70,
    },
    tickDirectionRule: {
      enabled: false,
      window: 3,
      direction: "rising",
    },
    trade: {
      group: "over_under",
      side: "over",
      prediction: 5,
    },
    reanalyze: {
      mode: "custom",
      losses: 2,
      wins: 3,
    },
    money: {
      stake: 0.5,
      takeProfit: 10,
      stopLoss: 10,
      martingale: 1.2,
      ticks: 1,
    },
    virtualHook: {
      enabled: true,
      enterAfterLosses: 2,
      exitAfterConsecutiveWins: 1,
    },
  };

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function rememberedSession() {
    const boot = window.FOA_BOOT_SESSION && typeof window.FOA_BOOT_SESSION === "object"
      ? window.FOA_BOOT_SESSION
      : null;
    if (boot && boot.authenticated) return boot;
    try {
      const cached = JSON.parse(storageGet(K.session) || "null");
      if (cached && cached.authenticated && Date.now() - Number(cached.saved_at || 0) < 30 * 86400 * 1000) {
        return cached;
      }
    } catch (_) {}
    return null;
  }

  function normalizeMarketMode(value) {
    const mode = String(value || "selected").toLowerCase();
    if (mode === "one" || mode === "single") return "single";
    if (mode === "all") return "all";
    return "selected";
  }

  function normalizeTickDirection(value) {
    const direction = String(value || "rising").trim().toLowerCase().replace(/\s+/g, "_");
    if (direction === "rise" || direction === "rising") return "rising";
    if (direction === "fall" || direction === "falling") return "falling";
    if (direction === "flat" || direction === "same" || direction === "no_move") return "no_move";
    return "rising";
  }

  function migrateBuilderDraft(raw) {
    const source = raw && typeof raw === "object" ? raw : {};
    const migrated = { ...clone(DEFAULT_BUILDER), ...source, version: BUILDER_SCHEMA_VERSION };
    if (source.trade?.group === "higher_lower" || source.trade?.side === "higher" || source.trade?.side === "lower") {
      migrated.trade = { group: "rise_fall", side: source.trade?.side === "lower" ? "fall" : "rise", prediction: null };
    }
    if (source.tickDirectionRule) {
      migrated.tickDirectionRule = {
        ...clone(DEFAULT_BUILDER.tickDirectionRule),
        ...source.tickDirectionRule,
        direction: normalizeTickDirection(source.tickDirectionRule.direction),
      };
    }
    if (!source.virtualHook || typeof source.virtualHook !== "object") {
      migrated.virtualHook = {
        ...clone(DEFAULT_BUILDER.virtualHook),
        enabled: source.virtualHookEnabled === undefined ? true : Boolean(source.virtualHookEnabled),
      };
    }
    return migrated;
  }

  function readBuilderDraft() {
    try {
      const parsed = JSON.parse(storageGet(K.builder) || storageGet(K.legacyBuilder) || "null");
      if (!parsed || typeof parsed !== "object") return clone(DEFAULT_BUILDER);
      const migrated = normalizeBuilder(migrateBuilderDraft(parsed));
      storageSet(K.builder, JSON.stringify(migrated));
      return migrated;
    } catch (_) {
      return clone(DEFAULT_BUILDER);
    }
  }

  const BOOT_SESSION = rememberedSession();
  const S = {
    theme: storageGet(K.theme) || "dark",
    view: storageGet(K.view) === "settings" || storageGet(K.view) === "trades"
      ? storageGet(K.view)
      : "main",
    mode: BOOT_SESSION?.account_type || storageGet(K.mode) || "demo",
    me: BOOT_SESSION?.authenticated ? {
      authenticated: true,
      account_type: BOOT_SESSION.account_type || "demo",
      available_account_types: BOOT_SESSION.available_account_types || ["demo"],
      label: BOOT_SESSION.label || "Restoring session",
      account_id: BOOT_SESSION.account_id_masked || BOOT_SESSION.label || "Restoring session",
      account_id_masked: BOOT_SESSION.account_id_masked || "",
      currency: BOOT_SESSION.currency || "USD",
      balance: 0,
      enabled: Boolean(BOOT_SESSION.enabled),
      has_trading_api_token: Boolean(BOOT_SESSION.has_trading_api_token),
      requires_api_token: Boolean(BOOT_SESSION.requires_api_token),
      trading_api_token_invalid: Boolean(BOOT_SESSION.trading_api_token_invalid),
      settings: BOOT_SESSION.settings || {},
      stats: { trades: 0, wins: 0, losses: 0, profit: 0 },
    } : null,
    life: null,
    summary: null,
    custom: null,
    trades: [],
    tradeSummary: {},
    builder: readBuilderDraft(),
    builderHydratedFromServer: Boolean(storageGet(K.builder)),
    builderDirty: false,
    busy: false,
    mutating: false,
    booting: !BOOT_SESSION?.authenticated,
    pendingRender: false,
    renderTimer: null,
    loaderText: "Opening builder...",
    error: "",
    notice: "",
    riskOpen: false,
  };

  const authenticated = () => Boolean(S.me && S.me.authenticated);
  const selectedMode = () => String(S.me?.account_type || S.mode || "demo").toLowerCase() === "real" ? "real" : "demo";
  const esc = (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
  const number = (value) => Number(value || 0).toLocaleString();
  const fixed = (value, digits = 2) => Number(value || 0).toFixed(digits);
  const money = (value, currency = "USD") => {
    const amount = Number(value || 0);
    const prefix = String(currency || "USD").toUpperCase() === "USD" ? "$" : `${currency} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  };

  function isEditingDashboard() {
    const active = document.activeElement;
    if (!active) return false;
    if (active.matches?.("input, select, textarea")) return true;
    return Boolean(active.closest?.("[contenteditable='true']"));
  }

  function renderWhenIdle(delay = 900) {
    window.clearTimeout(S.renderTimer);
    S.pendingRender = true;
    S.renderTimer = window.setTimeout(() => {
      if (isEditingDashboard()) {
        renderWhenIdle(Math.min(delay + 400, 2200));
        return;
      }
      S.pendingRender = false;
      render();
    }, delay);
  }

  function refreshInlineBuilderText() {
    const summary = document.querySelector(".live-summary p");
    if (summary) summary.textContent = strategySummaryText();
  }
  const comparisonLabel = (operator) => COMPARISONS.find((item) => item.value === operator)?.label || "Greater than or equal to";
  const marketLabel = (symbol) => MARKETS.find((market) => market.symbol === symbol)?.label || symbol;
  const tradeGroup = () => TRADE_GROUPS.find((group) => group.value === S.builder.trade.group) || TRADE_GROUPS[0];
  const tradeSide = () => tradeGroup().sides.find((side) => side.value === S.builder.trade.side) || tradeGroup().sides[0];

  function markBuilderDirty() {
    S.builderDirty = true;
    S.builderHydratedFromServer = true;
  }

  function saveLastGoodSnapshot() {
    if (!authenticated()) return;
    try {
      storageSet(K.lastGood, JSON.stringify({
        saved_at: Date.now(),
        me: S.me,
        life: S.life,
        summary: S.summary,
        custom: S.custom,
        trades: S.trades,
        tradeSummary: S.tradeSummary,
      }));
    } catch (_) {}
  }

  function showRefreshDelayed(message) {
    try {
      const parsed = JSON.parse(storageGet(K.lastGood) || "null");
      if (!parsed || Date.now() - Number(parsed.saved_at || 0) > 5 * 60 * 1000) {
        return false;
      }
      S.me = parsed.me || S.me;
      S.life = parsed.life || S.life;
      S.summary = parsed.summary || S.summary;
      S.custom = parsed.custom || S.custom;
      S.trades = Array.isArray(parsed.trades) ? parsed.trades : S.trades;
      S.tradeSummary = parsed.tradeSummary || S.tradeSummary;
      S.notice = message || "LIVE REFRESH DELAYED - showing last known dashboard data.";
      S.error = "";
      return true;
    } catch (_) {
      return false;
    }
  }

  function normalizeBuilder(builder) {
    const draft = { ...clone(DEFAULT_BUILDER), ...(builder || {}) };
    draft.version = BUILDER_SCHEMA_VERSION;
    draft.lastRule = { ...clone(DEFAULT_BUILDER.lastRule), ...(builder?.lastRule || {}) };
    draft.percentageRule = { ...clone(DEFAULT_BUILDER.percentageRule), ...(builder?.percentageRule || {}) };
    draft.tickDirectionRule = { ...clone(DEFAULT_BUILDER.tickDirectionRule), ...(builder?.tickDirectionRule || {}) };
    draft.trade = { ...clone(DEFAULT_BUILDER.trade), ...(builder?.trade || {}) };
    draft.reanalyze = { ...clone(DEFAULT_BUILDER.reanalyze), ...(builder?.reanalyze || {}) };
    draft.money = { ...clone(DEFAULT_BUILDER.money), ...(builder?.money || {}) };
    draft.virtualHook = { ...clone(DEFAULT_BUILDER.virtualHook), ...(builder?.virtualHook || {}) };
    if (builder?.virtualHookEnabled !== undefined && builder?.virtualHook?.enabled === undefined) {
      draft.virtualHook.enabled = Boolean(builder.virtualHookEnabled);
    }
    if (!["last_digit", "percentage", "combined"].includes(draft.strategyMode)) draft.strategyMode = "combined";
    draft.marketMode = normalizeMarketMode(draft.marketMode);
    if (!MARKETS.some((market) => market.symbol === draft.oneMarket)) draft.oneMarket = "1HZ100V";
    draft.markets = Array.isArray(draft.markets)
      ? draft.markets.filter((symbol, index, list) => MARKETS.some((market) => market.symbol === symbol) && list.indexOf(symbol) === index)
      : [];
    if (!draft.markets.length) draft.markets = ["1HZ10V", "1HZ25V", "1HZ100V"];
    if (draft.marketMode === "single") draft.markets = [draft.oneMarket];
    const group = TRADE_GROUPS.find((item) => item.value === draft.trade.group) || TRADE_GROUPS[0];
    draft.trade.group = group.value;
    if (!group.sides.some((side) => side.value === draft.trade.side)) draft.trade.side = group.sides[0].value;
    draft.trade.prediction = Math.max(0, Math.min(9, Number(draft.trade.prediction ?? 5)));
    draft.lastRule.window = Math.max(1, Math.min(1000, Number(draft.lastRule.window || 5)));
    draft.lastRule.value = Math.max(0, Math.min(9, Number(draft.lastRule.value ?? 3)));
    if (!COMPARISONS.some((item) => item.value === draft.lastRule.operator)) draft.lastRule.operator = ">=";
    draft.percentageRule.window = Math.max(1, Math.min(1000, Number(draft.percentageRule.window || 500)));
    draft.percentageRule.threshold = Math.max(0, Math.min(100, Number(draft.percentageRule.threshold ?? 70)));
    draft.percentageRule.value = Math.max(0, Math.min(9, Number(draft.percentageRule.value ?? 5)));
    if (!NUMERIC_COMPARISONS.some((item) => item.value === draft.percentageRule.operator)) draft.percentageRule.operator = ">=";
    if (!["even", "odd", "over", "under", "digit", "rise", "fall", "no_move"].includes(draft.percentageRule.target)) {
      draft.percentageRule.target = "even";
    }
    draft.tickDirectionRule.enabled = Boolean(draft.tickDirectionRule.enabled);
    draft.tickDirectionRule.window = Math.max(1, Math.min(1000, Number(draft.tickDirectionRule.window || 3)));
    draft.tickDirectionRule.direction = normalizeTickDirection(draft.tickDirectionRule.direction);
    if (!["after_every_trade", "after_loss", "after_win", "custom"].includes(draft.reanalyze.mode)) {
      draft.reanalyze.mode = "custom";
    }
    draft.reanalyze.losses = Math.max(1, Math.min(50, Number(draft.reanalyze.losses || 1)));
    draft.reanalyze.wins = Math.max(1, Math.min(50, Number(draft.reanalyze.wins || 1)));
    draft.money.stake = Math.max(0.35, Number(draft.money.stake || 0.5));
    draft.money.takeProfit = Math.max(0, Number(draft.money.takeProfit || 0));
    draft.money.stopLoss = Math.max(0, Math.abs(Number(draft.money.stopLoss || 0)));
    draft.money.martingale = Math.max(1.1, Math.min(10, Number(draft.money.martingale || 1.2)));
    draft.money.ticks = Math.max(1, Math.min(100, Math.round(Number(draft.money.ticks || 1))));
    draft.virtualHook.enabled = draft.virtualHook.enabled !== false;
    if (draft.virtualHook.enterAfterLosses === undefined && draft.virtualHook.enterAfterRuns !== undefined) {
      draft.virtualHook.enterAfterLosses = draft.virtualHook.enterAfterRuns;
    }
    if (draft.virtualHook.exitAfterConsecutiveWins === undefined && draft.virtualHook.exitAfterWins !== undefined) {
      draft.virtualHook.exitAfterConsecutiveWins = draft.virtualHook.exitAfterWins;
    }
    draft.virtualHook.enterAfterLosses = Math.max(1, Math.min(50, Math.round(Number(draft.virtualHook.enterAfterLosses || 2))));
    draft.virtualHook.exitAfterConsecutiveWins = Math.max(1, Math.min(50, Math.round(Number(draft.virtualHook.exitAfterConsecutiveWins || 1))));
    delete draft.virtualHook.enterAfterRuns;
    delete draft.virtualHook.exitAfterWins;
    return draft;
  }

  function saveDraft() {
    S.builder = normalizeBuilder(S.builder);
    storageSet(K.builder, JSON.stringify(S.builder));
  }

  function setTheme(value) {
    S.theme = value === "light" ? "light" : "dark";
    storageSet(K.theme, S.theme);
    document.documentElement.dataset.theme = S.theme;
    const app = document.querySelector("#foa-simple-app");
    if (app) app.dataset.theme = S.theme;
  }

  async function requestJSON(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await response.text();
    let body = {};
    try {
      body = text ? JSON.parse(text) : {};
    } catch (_) {
      body = { detail: text };
    }
    if (!response.ok) {
      const message = body.detail || body.message || `${response.status} ${response.statusText}`;
      if ((response.status === 401 || response.status === 403 || credentialFailureMessage(message)) && credentialFailureMessage(message)) {
        markCredentialInvalid(message);
      }
      throw new Error(message);
    }
    return body;
  }
  const getJSON = (url) => requestJSON(url);
  const postJSON = (url, body = {}) => requestJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  function lifecycle() {
    const value = String(S.life?.lifecycle || "").toLowerCase();
    if (["running", "paused", "stopped"].includes(value)) return value;
    const status = String(S.me?.execution_status || "").toLowerCase();
    if (status.includes("pause")) return "paused";
    if (!S.me?.enabled || status.includes("stop") || status.includes("disable") || status === "inactive") return "stopped";
    return "running";
  }

  function credentialFailureMessage(message = "") {
    const text = String(message || "").toLowerCase();
    return [
      "invalid token",
      "expired token",
      "revoked",
      "unauthorized",
      "authorization failed",
      "forbidden",
      "trade scope",
      "missing the required trade scope",
      "credential rejected",
      "no longer valid",
    ].some((item) => text.includes(item));
  }

  function markCredentialInvalid(message = "") {
    if (!S.me) return;
    S.me.has_trading_api_token = false;
    S.me.requires_api_token = true;
    S.me.trading_api_token_invalid = true;
    S.me.execution_status = "credential_error";
    S.me.execution_status_reason = String(message || "Deriv trading credential is no longer valid.").slice(0, 180);
  }

  function credentialState() {
    if (!authenticated()) return "unknown";
    if (S.mutating && S.loaderText.toLowerCase().includes("credential")) return "validating";
    if (S.me?.trading_api_token_invalid) return "invalid";
    if (S.me?.requires_api_token || !S.me?.has_trading_api_token) return "disconnected";
    return "connected";
  }

  function credentialNotice() {
    if (credentialState() !== "invalid") return "";
    const message = S.me?.execution_status_reason
      || "Your Deriv trading credential is no longer valid. Go to Settings and connect a new trading token.";
    return `<div class="notice error credential-notice"><span>${esc(message)}</span><button type="button" data-view="settings">Go to Settings</button></div>`;
  }

  function limitNoticeData() {
    if (!authenticated()) return null;
    const status = String(S.life?.execution_status || S.me?.execution_status || "").toLowerCase();
    if (status !== "take_profit" && status !== "stop_loss") return null;
    const currency = S.me?.currency || "USD";
    const serverProfit = Number(S.me?.stats?.profit);
    const localProfit = personalMetrics().profit;
    const rawProfit = Number.isFinite(serverProfit) ? serverProfit : localProfit;
    const configuredLimit = Number(
      status === "take_profit"
        ? S.me?.settings?.take_profit
        : S.me?.settings?.stop_loss
    );
    const amount = status === "take_profit"
      ? Math.abs(rawProfit || configuredLimit || 0)
      : -Math.abs(rawProfit || configuredLimit || 0);
    const account = S.me?.account_id_masked || S.me?.account_id || "account";
    const key = `${K.limitDismissed}:${selectedMode()}:${account}:${status}:${amount.toFixed(2)}`;
    if (storageGet(key) === "1") return null;
    const isTp = status === "take_profit";
    return {
      key,
      tone: isTp ? "tp" : "sl",
      title: isTp ? "TP hit" : "SL hit",
      label: isTp ? "Amount made" : "Amount stopped",
      amount: money(amount, currency),
      message: S.life?.reason || S.me?.execution_status_reason || (
        isTp
          ? "Take profit reached. This account is protected."
          : "Stop loss reached. This account is protected."
      ),
    };
  }

  function limitNotifier() {
    const notice = limitNoticeData();
    if (!notice) return "";
    return `<aside class="limit-notifier ${notice.tone}" role="status" aria-live="polite">
      <div class="limit-icon">OK</div>
      <div><strong>${esc(notice.title)}</strong><span>${esc(notice.label)}: ${esc(notice.amount)}</span><small>${esc(notice.message)}</small></div>
      <button type="button" data-dismiss-limit-notice="${esc(notice.key)}">OK</button>
    </aside>`;
  }

  function devPreviewNotice() {
    if (!S.me?.local_dev_preview) return "";
    return `<div class="notice dev-notice">Development preview is active. UI configuration is allowed locally, but authenticated Deriv trading remains disabled on the server.</div>`;
  }

  function tradeResetKey() {
    const account = S.me?.account_id_masked || S.me?.account_id || "public";
    return `${K.tradeReset}:${selectedMode()}:${account}`;
  }

  function localTradeResetTime() {
    const raw = storageGet(tradeResetKey());
    if (!raw) return 0;
    const parsed = Date.parse(raw);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function visibleTrades() {
    const resetAt = localTradeResetTime();
    const rows = Array.isArray(S.trades) ? S.trades : [];
    if (!resetAt) return rows;
    return rows.filter((row) => {
      const stamp = Date.parse(row.purchase_time || row.provider_purchase_time || row.created_at || "");
      return Number.isFinite(stamp) && stamp >= resetAt;
    });
  }

  function personalMetrics() {
    const rows = visibleTrades();
    const wins = rows.filter((row) => String(row.outcome).toUpperCase() === "WIN").length;
    const losses = rows.filter((row) => String(row.outcome).toUpperCase() === "LOSS").length;
    const profit = rows.reduce((total, row) => total + Number(row.profit || 0), 0);
    const total = rows.length;
    const open = rows.filter((row) => String(row.outcome || "OPEN").toUpperCase() === "OPEN").length;
    return { total, wins, losses, open, profit, rate: wins + losses ? wins / (wins + losses) : 0 };
  }

  function hydrateBuilderMoneyFromAccount() {
    if (!S.me?.settings || S.builderHydratedFromServer || S.builderDirty || isEditingDashboard()) return;
    S.builder.money.stake = Number(S.me.settings.stake_amount ?? S.builder.money.stake);
    S.builder.money.takeProfit = Number(S.me.settings.take_profit ?? S.builder.money.takeProfit);
    S.builder.money.stopLoss = Math.abs(Number(S.me.settings.stop_loss ?? S.builder.money.stopLoss));
    S.builderHydratedFromServer = true;
    saveDraft();
  }

  function mapConfigToBuilder(payload, { force = false } = {}) {
    if (!force && (S.builderHydratedFromServer || S.builderDirty || isEditingDashboard())) return;
    const config = payload?.config;
    if (!config || !config.configured) return;
    const next = clone(S.builder);
    next.marketMode = normalizeMarketMode(config.market_mode);
    next.markets = Array.isArray(config.markets) && config.markets.length ? config.markets : next.markets;
    next.oneMarket = next.marketMode === "single" ? (next.markets[0] || next.oneMarket) : next.oneMarket;
    const percentage = (config.conditions || []).find((item) => item.kind === "percentage");
    const digit = (config.conditions || []).find((item) => item.kind === "digit_compare");
    if (percentage && digit) next.strategyMode = "combined";
    else if (percentage) next.strategyMode = "percentage";
    else next.strategyMode = "last_digit";
    if (digit) {
      next.lastRule.window = Number(digit.window || next.lastRule.window);
      next.lastRule.operator = digit.operator || next.lastRule.operator;
      next.lastRule.value = Number(digit.value ?? next.lastRule.value);
    }
    if (percentage) {
      next.percentageRule.target = percentage.target || next.percentageRule.target;
      next.percentageRule.value = Number(percentage.value ?? next.percentageRule.value);
      next.percentageRule.window = Number(percentage.window || next.percentageRule.window);
      next.percentageRule.operator = percentage.operator || next.percentageRule.operator;
      next.percentageRule.threshold = Number(percentage.threshold ?? next.percentageRule.threshold);
    }
    const tickDirection = (config.conditions || []).find((item) => item.kind === "direction");
    if (tickDirection) {
      next.tickDirectionRule.enabled = true;
      next.tickDirectionRule.window = Number(tickDirection.window || next.tickDirectionRule.window);
      next.tickDirectionRule.direction = normalizeTickDirection(tickDirection.direction);
    }
    const tradeType = String(config.trade_type || "over");
    const group = TRADE_GROUPS.find((item) => item.sides.some((side) => side.value === tradeType)) || TRADE_GROUPS[0];
    next.trade.group = group.value;
    next.trade.side = tradeType;
    if (config.prediction !== null && config.prediction !== undefined) next.trade.prediction = Number(config.prediction);
    next.money.ticks = Number(config.duration_ticks || next.money.ticks);
    if (config.reanalyze) next.reanalyze = { ...next.reanalyze, ...config.reanalyze };
    if (config.virtual_hook && typeof config.virtual_hook === "object") {
      next.virtualHook = {
        ...clone(DEFAULT_BUILDER.virtualHook),
        enabled: config.virtual_hook.enabled !== false,
        enterAfterLosses: Number(config.virtual_hook.enter_after_losses || config.virtual_hook.enter_after_runs || DEFAULT_BUILDER.virtualHook.enterAfterLosses),
        exitAfterConsecutiveWins: Number(config.virtual_hook.exit_after_consecutive_wins || config.virtual_hook.exit_after_wins || DEFAULT_BUILDER.virtualHook.exitAfterConsecutiveWins),
      };
    } else if (config.virtual_hook_enabled !== undefined) {
      next.virtualHook.enabled = Boolean(config.virtual_hook_enabled);
    }
    const martingale = payload?.martingale || {};
    if (martingale.mode === "multiplier") next.money.martingale = Number(martingale.multiplier || next.money.martingale);
    if (S.me?.settings) {
      next.money.stake = Number(S.me.settings.stake_amount ?? next.money.stake);
      next.money.takeProfit = Number(S.me.settings.take_profit ?? next.money.takeProfit);
      next.money.stopLoss = Math.abs(Number(S.me.settings.stop_loss ?? next.money.stopLoss));
    }
    S.builder = normalizeBuilder(next);
    S.builderHydratedFromServer = true;
    saveDraft();
  }

  function builderToCustomPayload() {
    const draft = normalizeBuilder(S.builder);
    const conditions = [];
    if (draft.strategyMode === "last_digit" || draft.strategyMode === "combined") {
      conditions.push({
        kind: "digit_compare",
        window: Math.round(draft.lastRule.window),
        operator: draft.lastRule.operator,
        value: Math.round(draft.lastRule.value),
      });
    }
    if (draft.strategyMode === "percentage" || draft.strategyMode === "combined") {
      const percentage = {
        kind: "percentage",
        window: Math.round(draft.percentageRule.window),
        target: draft.percentageRule.target,
        operator: draft.percentageRule.operator,
        threshold: Number(draft.percentageRule.threshold),
      };
      if (["over", "under", "digit"].includes(draft.percentageRule.target)) {
        percentage.value = Math.round(draft.percentageRule.value);
      }
      conditions.push(percentage);
    }
    if (draft.tickDirectionRule.enabled) {
      conditions.push({
        kind: "direction",
        window: Math.round(draft.tickDirectionRule.window),
        direction: draft.tickDirectionRule.direction,
      });
    }
    const markets = draft.marketMode === "all"
      ? []
      : draft.marketMode === "single"
      ? [draft.oneMarket]
      : draft.markets;
    return {
      market_mode: draft.marketMode,
      markets,
      trade_type: draft.trade.side,
      prediction: tradeGroup().prediction ? Math.round(draft.trade.prediction) : null,
      duration_ticks: Math.round(draft.money.ticks),
      conditions,
      match: "all",
      reanalyze: {
        mode: draft.reanalyze.mode,
        losses: Math.round(draft.reanalyze.losses),
        wins: Math.round(draft.reanalyze.wins),
      },
      virtual_hook_enabled: Boolean(draft.virtualHook.enabled),
      virtual_hook: {
        enabled: Boolean(draft.virtualHook.enabled),
        enter_after_losses: Math.round(draft.virtualHook.enterAfterLosses),
        exit_after_consecutive_wins: Math.round(draft.virtualHook.exitAfterConsecutiveWins),
      },
      martingale: {
        mode: "multiplier",
        multiplier: Number(draft.money.martingale),
        split_count: 1,
      },
    };
  }

  function builderToSettingsPayload() {
    const draft = normalizeBuilder(S.builder);
    return {
      stake_amount: Number(draft.money.stake),
      take_profit: Number(draft.money.takeProfit),
      stop_loss: Math.abs(Number(draft.money.stopLoss || 0)),
      martingale_enabled: true,
      martingale_mode: "custom",
      martingale_trigger_losses: 1,
      martingale_multiplier: Number(draft.money.martingale),
      martingale_max_levels: 10,
      martingale_max_stake: 100000,
    };
  }

  function topStat(label, value, caption, tone = "") {
    return `<article class="builder-stat ${tone}"><span>${esc(label)}</span><strong>${value}</strong><small>${esc(caption)}</small></article>`;
  }

  function navButton(view, label) {
    return `<button type="button" data-view="${view}" class="${S.view === view ? "active" : ""}">${esc(label)}</button>`;
  }

  function header() {
    const ready = authenticated();
    return `<header class="builder-header">
      <div class="builder-brand">
        <div class="builder-logo" aria-hidden="true"><i></i><i></i></div>
        <div><strong>Custom Strategy Builder</strong><span>Build, test, and execute rules</span></div>
      </div>
      <nav class="builder-nav">
        ${navButton("main", "Dashboard")}
        ${navButton("settings", "Settings")}
        ${navButton("trades", "Trades")}
      </nav>
      <div class="builder-head-actions">
        <div class="theme-toggle" role="group" aria-label="Theme">
          <button type="button" data-theme-value="dark" class="${S.theme === "dark" ? "active" : ""}">Dark</button>
          <button type="button" data-theme-value="light" class="${S.theme === "light" ? "active" : ""}">Light</button>
        </div>
        <button type="button" id="risk-disclaimer-toggle" class="ghost-button risk-button" aria-controls="risk-disclaimer-panel" aria-expanded="${S.riskOpen ? "true" : "false"}">Risk Disclaimer</button>
        ${ready ? `<span class="account-pill">${esc(selectedMode())} ${esc(S.me?.account_id || S.me?.account_id_masked || "Account")}</span><button class="ghost-button" id="logout">Logout</button>` : `<a class="primary-link" href="/oauth/start">Login with Deriv</a>`}
      </div>
    </header>`;
  }

  function riskDisclaimerPanel() {
    if (!S.riskOpen) return "";
    return `<aside id="risk-disclaimer-panel" class="risk-disclaimer-panel" role="dialog" aria-modal="false" aria-labelledby="risk-disclaimer-title">
      <div>
        <button type="button" id="risk-disclaimer-close" aria-label="Close risk disclaimer">x</button>
        <span class="eyebrow">Official Risk Disclaimer</span>
        <h2 id="risk-disclaimer-title">Automated trading involves financial risk.</h2>
        <p>Trading derivatives can result in the loss of your capital. Past results, virtual observations, recovery calculations, and displayed statistics do not guarantee future profit.</p>
        <p>Users remain responsible for their account settings, tokens, stake size, take-profit, stop-loss, and all trading outcomes.</p>
        <a href="${RISK_DISCLOSURE_URL}" target="_blank" rel="noopener noreferrer">Read Deriv's official risk disclosure</a>
      </div>
    </aside>`;
  }

  function modeToggle() {
    if (!authenticated()) return "";
    const available = Array.isArray(S.me?.available_account_types) ? S.me.available_account_types : ["demo", "real"];
    return `<div class="account-mode-toggle">${["demo", "real"].map((value) => `<button type="button" data-mode="${value}" class="${selectedMode() === value ? "active" : ""}" ${available.includes(value) ? "" : "disabled"}>${value[0].toUpperCase() + value.slice(1)}</button>`).join("")}</div>`;
  }

  function topStats() {
    const metrics = personalMetrics();
    const currency = S.me?.currency || "USD";
    const balance = Number(S.me?.balance || 0);
    return `<section class="builder-stats">
      ${topStat("Balance", money(balance, currency), `${selectedMode()} account`)}
      ${topStat("Today's P/L", money(metrics.profit, currency), `${metrics.wins + metrics.losses} settled`, metrics.profit < 0 ? "loss" : "win")}
      ${topStat("Number of Runs", number(metrics.total), "All runs today")}
      ${topStat("Wins", number(metrics.wins), "Settled wins", "win")}
      ${topStat("Losses", number(metrics.losses), "Settled losses", "loss")}
    </section>`;
  }

  function marketSelector() {
    const draft = normalizeBuilder(S.builder);
    const selectedSymbols = draft.marketMode === "all"
      ? []
      : draft.marketMode === "single"
      ? [draft.oneMarket]
      : draft.markets;
    const selected = new Set(selectedSymbols);
    const dropdownValue = draft.marketMode === "all"
      ? "__all__"
      : draft.marketMode === "single"
      ? draft.oneMarket
      : "";
    const selectedChips = draft.marketMode === "all"
      ? `<span class="market-chip all">All Markets</span>`
      : selectedSymbols.map((symbol) => `<button type="button" data-market-remove="${esc(symbol)}" class="market-chip active">${esc(marketLabel(symbol))}<b aria-hidden="true">x</b></button>`).join("");
    return `<section class="builder-section">
      <div class="section-label">Markets</div>
      <div class="market-row">
        <label class="field wide">
          <span>Market selector</span>
          <select data-market-select>
            <option value="">${draft.marketMode === "selected" ? "Add market..." : "Choose market..."}</option>
            <option value="__all__" ${dropdownValue === "__all__" ? "selected" : ""}>All Markets</option>
            ${MARKETS.map((market) => `<option value="${esc(market.symbol)}" ${dropdownValue === market.symbol ? "selected" : ""} ${draft.marketMode === "selected" && selected.has(market.symbol) ? "disabled" : ""}>${esc(market.label)}</option>`).join("")}
          </select>
        </label>
        <div class="market-chips" aria-label="Selected markets">
          ${selectedChips || `<span class="market-chip placeholder">No markets selected</span>`}
        </div>
        <div class="segmented market-mode">
          ${[
            ["single", "One Market"],
            ["selected", "Selected Markets"],
            ["all", "All Markets"],
          ].map(([value, label]) => `<button type="button" data-market-mode="${value}" class="${draft.marketMode === value ? "active" : ""}">${label}</button>`).join("")}
        </div>
      </div>
      <p class="builder-hint">One Market trades a single market. Selected Markets chooses multiple markets. All Markets allows every supported market.</p>
    </section>`;
  }

  function strategyModeCards() {
    return `<section class="builder-section">
      <div class="section-label">Strategy Mode</div>
      <div class="mode-grid">
        ${MODE_CARDS.map((item) => `<button type="button" data-strategy-mode="${item.value}" class="mode-card ${S.builder.strategyMode === item.value ? "active" : ""}">
          <span>${esc(item.symbol)}</span><strong>${esc(item.label)}</strong><small>${esc(item.caption)}</small>
        </button>`).join("")}
      </div>
      <p class="builder-hint">Last Digit mode shows digit rules only. Percentage mode shows percentage rules only. Combined mode uses both.</p>
    </section>`;
  }

  function comparisonOptions(selected) {
    return COMPARISONS.map((item) => `<option value="${esc(item.value)}" ${selected === item.value ? "selected" : ""}>${esc(item.label)}</option>`).join("");
  }

  function numericComparisonOptions(selected) {
    return NUMERIC_COMPARISONS.map((item) => `<option value="${esc(item.value)}" ${selected === item.value ? "selected" : ""}>${esc(item.label)}</option>`).join("");
  }

  function lastDigitRule() {
    const rule = S.builder.lastRule;
    const allSame = rule.operator === "all_same";
    return `<div class="rule-card digit-rule">
      <div class="rule-title"><span>9</span><strong>Last Digit Rule</strong></div>
      <label class="field"><span>Check last N digits</span><input data-builder="lastRule.window" type="number" min="1" max="1000" step="1" value="${esc(rule.window)}"></label>
      <label class="field wide"><span>Comparison</span><select data-builder="lastRule.operator">${comparisonOptions(rule.operator)}</select></label>
      ${allSame ? "" : `<label class="field"><span>Value</span><input data-builder="lastRule.value" type="number" min="0" max="9" step="1" value="${esc(rule.value)}"></label>`}
    </div>`;
  }

  function percentageValueInput() {
    const rule = S.builder.percentageRule;
    if (!["over", "under", "digit"].includes(rule.target)) return "";
    return `<label class="field compact"><span>Digit</span><input data-builder="percentageRule.value" type="number" min="0" max="9" step="1" value="${esc(rule.value)}"></label>`;
  }

  function percentageRule() {
    const rule = S.builder.percentageRule;
    return `<div class="rule-card percentage-rule">
      <div class="rule-title"><span>%</span><strong>Percentage Rule</strong></div>
      <label class="field"><span>Check percentage of</span><select data-builder="percentageRule.target">
        ${[
          ["even", "Even"],
          ["odd", "Odd"],
          ["over", "Over digit"],
          ["under", "Under digit"],
          ["digit", "Exact digit"],
          ["rise", "Up ticks"],
          ["fall", "Down ticks"],
          ["no_move", "No-move ticks"],
        ].map(([value, label]) => `<option value="${value}" ${rule.target === value ? "selected" : ""}>${label}</option>`).join("")}
      </select></label>
      ${percentageValueInput()}
      <label class="field"><span>Using past digits/ticks</span><input data-builder="percentageRule.window" type="number" min="1" max="1000" step="1" value="${esc(rule.window)}"></label>
      <label class="field wide"><span>Comparison</span><select data-builder="percentageRule.operator">${numericComparisonOptions(rule.operator)}</select></label>
      <label class="field"><span>Threshold (%)</span><input data-builder="percentageRule.threshold" type="number" min="0" max="100" step="0.1" value="${esc(rule.threshold)}"></label>
    </div>`;
  }

  function tickDirectionRule() {
    const rule = S.builder.tickDirectionRule;
    return `<div class="rule-card tick-direction-rule">
      <div class="rule-title"><span>D</span><strong>Last Tick Direction</strong></div>
      <label class="switch-field">
        <input data-builder="tickDirectionRule.enabled" type="checkbox" ${rule.enabled ? "checked" : ""}>
        <span>${rule.enabled ? "Enabled" : "Optional"}</span>
      </label>
      ${rule.enabled ? `<label class="field"><span>Check last</span><input data-builder="tickDirectionRule.window" type="number" min="1" max="1000" step="1" value="${esc(rule.window)}"></label>
      <label class="field wide"><span>Tick directions are</span><select data-builder="tickDirectionRule.direction">
        ${TICK_DIRECTIONS.map((item) => `<option value="${item.value}" ${rule.direction === item.value ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
      </select></label>` : ""}
    </div>`;
  }

  function conditionBuilder() {
    const mode = S.builder.strategyMode;
    return `<section class="builder-section condition-builder">
      <div class="numbered-title"><b>2</b><strong>Condition Builder <span>(${MODE_CARDS.find((item) => item.value === mode)?.label || "Combined"} Mode)</span></strong></div>
      <div class="rules-stack">
        ${mode === "last_digit" || mode === "combined" ? lastDigitRule() : ""}
        ${mode === "percentage" || mode === "combined" ? percentageRule() : ""}
        ${tickDirectionRule()}
      </div>
    </section>`;
  }

  function tradeBuilder() {
    const group = tradeGroup();
    return `<section class="builder-section trade-builder">
      <div class="numbered-title"><b>3</b><strong>What to Trade</strong></div>
      <div class="trade-tabs">
        ${TRADE_GROUPS.map((item) => `<button type="button" data-trade-group="${item.value}" class="${S.builder.trade.group === item.value ? "active" : ""}">${esc(item.label)}</button>`).join("")}
      </div>
      <div class="trade-config">
        <label class="field"><span>Contract side</span><select data-builder="trade.side">${group.sides.map((side) => `<option value="${side.value}" ${S.builder.trade.side === side.value ? "selected" : ""}>${esc(side.label)}</option>`).join("")}</select></label>
        ${group.prediction ? `<label class="field"><span>Prediction</span><input data-builder="trade.prediction" type="number" min="0" max="9" step="1" value="${esc(S.builder.trade.prediction)}"></label>` : `<div class="info-box">Odd/Even and Rise/Fall do not require a prediction.</div>`}
      </div>
    </section>`;
  }

  function reanalyzeBuilder() {
    const rule = S.builder.reanalyze;
    return `<section class="builder-section reanalyze-builder">
      <div class="numbered-title"><b>4</b><strong>Re-Analyze</strong></div>
      <label class="field"><span>Re-analyze</span><select data-builder="reanalyze.mode">
        ${[
          ["after_every_trade", "After every trade"],
          ["after_loss", "After N losses"],
          ["after_win", "After N wins"],
          ["custom", "Custom"],
        ].map(([value, label]) => `<option value="${value}" ${rule.mode === value ? "selected" : ""}>${label}</option>`).join("")}
      </select></label>
      ${rule.mode === "after_loss" ? `<div class="inline-fields"><label class="field compact"><span>Losses</span><input data-builder="reanalyze.losses" type="number" min="1" max="50" value="${esc(rule.losses)}"></label></div>` : ""}
      ${rule.mode === "after_win" ? `<div class="inline-fields"><label class="field compact"><span>Wins</span><input data-builder="reanalyze.wins" type="number" min="1" max="50" value="${esc(rule.wins)}"></label></div>` : ""}
      ${rule.mode === "custom" ? `<div class="inline-fields"><label class="field compact"><span>After losses</span><input data-builder="reanalyze.losses" type="number" min="1" max="50" value="${esc(rule.losses)}"></label><span>or</span><label class="field compact"><span>After wins</span><input data-builder="reanalyze.wins" type="number" min="1" max="50" value="${esc(rule.wins)}"></label></div>` : ""}
      <p class="builder-hint">Initial analysis is mandatory. After that, these counters decide when continuation pauses for a fresh analysis.</p>
    </section>`;
  }

  function moneyBuilder() {
    const moneyRule = S.builder.money;
    return `<section class="builder-section money-builder">
      <div class="numbered-title"><b>5</b><strong>Money Management</strong></div>
      <div class="money-grid">
        <label class="field"><span>Stake</span><input data-builder="money.stake" type="number" min="0.35" step="0.01" value="${esc(moneyRule.stake)}"></label>
        <label class="field"><span>Take Profit (TP)</span><input data-builder="money.takeProfit" type="number" min="0" step="0.01" value="${esc(moneyRule.takeProfit)}"></label>
        <label class="field"><span>Stop Loss (SL)</span><input data-builder="money.stopLoss" type="number" min="0" step="0.01" value="${esc(moneyRule.stopLoss)}"></label>
        <label class="field"><span>Martingale</span><input data-builder="money.martingale" type="number" min="1.1" max="10" step="0.01" value="${esc(moneyRule.martingale)}"></label>
        <label class="field"><span>Ticks</span><input data-builder="money.ticks" type="number" min="1" max="100" step="1" value="${esc(moneyRule.ticks)}"></label>
      </div>
      <p class="builder-hint">Stop Loss is entered as a positive amount; 10 means stop at -10. Ticks is the contract duration.</p>
    </section>`;
  }

  function virtualHookBuilder() {
    const hook = normalizeBuilder(S.builder).virtualHook;
    return `<section class="builder-section virtual-hook-section">
      <div class="numbered-title"><b>6</b><strong>Virtual Hook</strong></div>
      <label class="toggle-row">
        <span><strong>Virtual Hook</strong><small>Customize when zero-cost virtual protection starts and exits.</small></span>
        <input data-builder="virtualHook.enabled" type="checkbox" ${hook.enabled ? "checked" : ""}>
        <b>${hook.enabled ? "ON" : "OFF"}</b>
      </label>
      ${hook.enabled ? `<div class="inline-fields">
        <label class="field compact"><span>Enter after losses</span><input data-builder="virtualHook.enterAfterLosses" type="number" min="1" max="50" step="1" value="${esc(hook.enterAfterLosses)}"></label>
        <label class="field compact"><span>Leave after consecutive wins</span><input data-builder="virtualHook.exitAfterConsecutiveWins" type="number" min="1" max="50" step="1" value="${esc(hook.exitAfterConsecutiveWins)}"></label>
      </div>` : ""}
    </section>`;
  }

  function strategySummaryText() {
    const draft = normalizeBuilder(S.builder);
    const markets = draft.marketMode === "all"
      ? "all supported markets"
      : draft.marketMode === "single"
      ? marketLabel(draft.oneMarket)
      : `${draft.markets.length} selected markets`;
    const parts = [];
    if (draft.strategyMode === "last_digit" || draft.strategyMode === "combined") {
      parts.push(
        draft.lastRule.operator === "all_same"
          ? `the last ${draft.lastRule.window} digits are all same`
          : `the last ${draft.lastRule.window} digits are ${comparisonLabel(draft.lastRule.operator).toLowerCase()} ${draft.lastRule.value}`
      );
    }
    if (draft.strategyMode === "percentage" || draft.strategyMode === "combined") {
      const percentageTarget = ["over", "under", "digit"].includes(draft.percentageRule.target)
        ? `${draft.percentageRule.target} ${draft.percentageRule.value}`
        : draft.percentageRule.target;
      parts.push(`the percentage of ${percentageTarget} in the past ${draft.percentageRule.window} digits/ticks is ${comparisonLabel(draft.percentageRule.operator).toLowerCase()} ${draft.percentageRule.threshold}%`);
    }
    if (draft.tickDirectionRule.enabled) {
      const direction = TICK_DIRECTIONS.find((item) => item.value === draft.tickDirectionRule.direction)?.label || "Up ticks";
      parts.push(`the last ${draft.tickDirectionRule.window} tick directions are ${direction}`);
    }
    const trade = tradeSide().label + (tradeGroup().prediction ? ` ${draft.trade.prediction}` : "");
    const reanalyze = draft.reanalyze.mode === "custom"
      ? `Re-analyze after ${draft.reanalyze.losses} losses or ${draft.reanalyze.wins} wins`
      : draft.reanalyze.mode === "after_loss"
      ? `Re-analyze after ${draft.reanalyze.losses} losses`
      : draft.reanalyze.mode === "after_win"
      ? `Re-analyze after ${draft.reanalyze.wins} wins`
      : "Re-analyze after every trade";
    const hook = draft.virtualHook.enabled
      ? `Virtual Hook enters after ${draft.virtualHook.enterAfterLosses} loss${draft.virtualHook.enterAfterLosses === 1 ? "" : "es"} and leaves after ${draft.virtualHook.exitAfterConsecutiveWins} consecutive virtual win${draft.virtualHook.exitAfterConsecutiveWins === 1 ? "" : "s"}`
      : "Virtual Hook is disabled";
    return `When ${parts.join(" AND ") || "the configured conditions are satisfied"}, place a ${trade} trade on ${markets}. ${reanalyze}. ${hook}.`;
  }

  function builderCard() {
    const run = lifecycle();
    const running = run === "running";
    return `<section class="strategy-builder-card">
      <div class="builder-card-head">
        <div><span class="eyebrow">Builder</span><h1>Custom Strategy Builder</h1></div>
        ${modeToggle()}
      </div>
      ${marketSelector()}
      ${strategyModeCards()}
      ${conditionBuilder()}
      ${tradeBuilder()}
      <div class="builder-two-col">${reanalyzeBuilder()}${moneyBuilder()}</div>
      ${virtualHookBuilder()}
      <section class="builder-section summary-section">
        <div class="numbered-title"><b>7</b><strong>Strategy Summary</strong></div>
        <div class="live-summary"><span>OK</span><p>${esc(strategySummaryText())}</p></div>
      </section>
      <div class="builder-actions">
        <button type="button" class="secondary-action subtle" data-reset-strategy>Reset Strategy</button>
        <button type="button" class="secondary-action" data-save-builder>Save Builder</button>
        <button type="button" class="primary-action ${running ? "danger" : ""}" data-main-action="${running ? "stop" : run === "paused" ? "resume" : "start"}">${running ? "Stop Auto Trading" : run === "paused" ? "Resume Auto Trading" : "Start Auto Trading"}</button>
      </div>
      <div class="builder-status-line"><i></i><span>${running ? "Running" : run === "paused" ? "Paused" : "Ready"} - ${running ? "Strategy is live" : "Waiting for condition"}</span></div>
    </section>`;
  }

  function tradeTime(row) {
    const value = row.purchase_time || row.provider_purchase_time || row.settlement_time || row.created_at || "";
    if (!value) return "-";
    try {
      return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch (_) {
      return String(value).slice(0, 8);
    }
  }

  function contractType(row) {
    const type = String(row.contract_type || row.type || "TRADE").toUpperCase();
    const barrier = String(row.barrier || row.prediction || "").trim();
    return barrier && ["DIGITOVER", "DIGITUNDER", "DIGITMATCH", "DIGITDIFF"].includes(type) ? `${type} ${barrier}` : type;
  }

  function resultClass(row) {
    const outcome = String(row.outcome || "OPEN").toUpperCase();
    return outcome === "WIN" ? "win" : outcome === "LOSS" ? "loss" : "neutral";
  }

  function resultText(row) {
    const outcome = String(row.outcome || "OPEN").toUpperCase();
    if (outcome === "WIN" || outcome === "LOSS") return `${outcome} - ${money(row.profit || 0, S.me?.currency || "USD")}`;
    return outcome;
  }

  function tradeRows(limit = 8) {
    const rows = visibleTrades().slice(0, limit);
    if (!rows.length) return `<div class="empty-state">No recent trades yet.</div>`;
    return rows.map((row) => `<div class="trade-row">
      <span>${esc(tradeTime(row))}</span>
      <strong>${esc(row.symbol || row.market || "-")}</strong>
      <span>${esc(contractType(row))}</span>
      <span>${money(row.buy_price ?? row.stake ?? row.amount ?? 0, S.me?.currency || "USD")}</span>
      <b class="${resultClass(row)}">${esc(resultText(row))}</b>
    </div>`).join("");
  }

  function recentTrades(limit = 8) {
    return `<section class="builder-panel builder-recent-trades">
      <div class="panel-head"><div><span class="eyebrow">Recent activity</span><h2>Recent Trades</h2></div><button type="button" class="text-button" data-view="trades">View all</button></div>
      <div class="trade-head"><span>Time</span><span>Market</span><span>Trade type</span><span>Stake</span><span>Result</span></div>
      ${tradeRows(limit)}
    </section>`;
  }

  function loginView() {
    return `<section class="public-builder">
      <article class="strategy-builder-card">
        <div class="builder-card-head"><div><span class="eyebrow">Welcome</span><h1>Custom Strategy Builder</h1></div></div>
        <p class="public-copy">Log in with Deriv to configure your strategy builder, connect a trade-scope API token, and start automation.</p>
        <a class="primary-action public-login" href="/oauth/start">Login with Deriv</a>
      </article>
    </section>`;
  }

  function mainView() {
    if (!authenticated()) return loginView();
    return `${topStats()}${builderCard()}${recentTrades(8)}`;
  }

  function settingsView() {
    if (!authenticated()) return loginView();
    const state = credentialState();
    const ready = state === "connected";
    const statusLabel = ready ? "Connected" : state === "validating" ? "Validating" : state === "invalid" ? "Invalid" : "Required";
    return `<section class="settings-shell">
      <article class="builder-panel credential-card">
        <div class="panel-head">
          <div><span class="eyebrow">Settings</span><h1>Deriv Trading Credential</h1></div>
          <span class="connection-pill ${ready ? "ready" : "needed"}">${statusLabel}</span>
        </div>
        ${ready ? `<div class="connected-box"><strong>Deriv Trading Credential</strong><p>Connected. The token is encrypted on the server and never displayed back.</p></div>` : `<form id="token-form" class="token-form">${state === "invalid" ? `<div class="inline-warning">Your Deriv trading credential is no longer valid. Paste a new trade-scope token to reconnect.</div>` : ""}<label class="field full"><span>Deriv API token with trade scope</span><input name="api_token" type="password" minlength="8" autocomplete="off" required placeholder="Paste Deriv trade-scope API token"></label><button class="primary-action">Verify / Connect</button></form>`}
      </article>
    </section>`;
  }

  function tradingStatusPanel() {
    const run = lifecycle();
    const running = run === "running";
    const paused = run === "paused";
    const stateLabel = running ? "Trading active" : paused ? "Trading paused" : "Trading stopped";
    const action = running ? "stop" : paused ? "resume" : "start";
    const buttonText = running ? "Stop Trading" : paused ? "Resume Trading" : "Start Trading";
    const currency = S.me?.currency || "USD";
    return `<section class="builder-panel trades-control-panel">
      <div>
        <span class="eyebrow">Current Account</span>
        <h1>${money(S.me?.balance || 0, currency)}</h1>
        <p>${esc(selectedMode())} account balance</p>
      </div>
      <div>
        <span class="eyebrow">Trading Status</span>
        <h2>${esc(stateLabel)}</h2>
        <p>${esc(S.me?.execution_status_reason || (running ? "The shared trading session is active." : "Use the same engine controls as the dashboard."))}</p>
      </div>
      <button type="button" class="primary-action ${running ? "danger" : ""}" data-main-action="${action}">${esc(buttonText)}</button>
    </section>`;
  }

  function tradesView() {
    if (!authenticated()) return loginView();
    const metrics = personalMetrics();
    return `${tradingStatusPanel()}<section class="builder-stats compact">
      ${topStat("Runs", number(metrics.total), "All trades today")}
      ${topStat("P/L", money(metrics.profit, S.me?.currency || "USD"), "Settled result", metrics.profit < 0 ? "loss" : "win")}
      ${topStat("Wins", number(metrics.wins), "Winning trades", "win")}
      ${topStat("Losses", number(metrics.losses), "Losing trades", "loss")}
    </section>
    <section class="builder-panel session-actions-panel">
      <div><span class="eyebrow">Session tracking</span><h2>Local Trade History</h2><p>Clear only this browser's visible session stats. Deriv account history and balance are untouched.</p></div>
      <button type="button" class="secondary-action danger-outline" data-clear-local-trades>Clear Trades</button>
    </section>
    ${recentTrades(50)}`;
  }

  function body() {
    if (S.view === "settings") return settingsView();
    if (S.view === "trades") return tradesView();
    return mainView();
  }

  function loader() {
    const show = S.booting || S.busy || S.mutating;
    return `<div id="smart-loader" class="builder-loader ${show ? "show active" : ""}"><div><i></i><strong>${esc(S.loaderText || "Loading...")}</strong><span>Please wait while the dashboard updates.</span></div></div>`;
  }

  function updateSnapshotState() {
    const snapshot = document.querySelector("#global-dashboard-snapshot");
    if (!snapshot) return;
    const loading = S.booting || S.busy || S.mutating;
    if (S.error && !loading) {
      snapshot.dataset.snapshotReady = "false";
      snapshot.dataset.snapshotState = "error";
      return;
    }
    if (loading) {
      snapshot.dataset.snapshotReady = "false";
      snapshot.dataset.snapshotState = "loading";
      return;
    }
    snapshot.dataset.snapshotReady = "true";
    snapshot.dataset.snapshotState = "ready";
    window.dispatchEvent(new CustomEvent("dashboard:snapshot-ready"));
  }

  function render() {
    setTheme(S.theme);
    const app = document.querySelector("#foa-simple-app");
    if (!app) return;
    app.innerHTML = `<div id="global-dashboard-snapshot" data-snapshot-state="loading" data-snapshot-ready="false"><div id="telegram-dashboard-snapshot" class="builder-shell">${header()}${devPreviewNotice()}${credentialNotice()}${S.error ? `<div class="notice error">${esc(S.error)}</div>` : ""}${S.notice ? `<div class="notice success">${esc(S.notice)}</div>` : ""}<main>${body()}</main></div></div>${riskDisclaimerPanel()}${limitNotifier()}${loader()}`;
    bind(app);
    updateSnapshotState();
  }

  function switchView(view) {
    S.view = view === "settings" || view === "trades" ? view : "main";
    storageSet(K.view, S.view);
    S.error = "";
    S.notice = "";
    render();
  }

  async function mutate(action, successMessage, loadingText = "Saving...") {
    if (S.mutating) return;
    S.mutating = true;
    S.error = "";
    S.notice = "";
    S.loaderText = loadingText;
    render();
    try {
      await action();
      S.notice = successMessage;
      await refresh(true, "Refreshing account...");
    } catch (error) {
      S.error = String(error?.message || error);
      render();
    } finally {
      S.mutating = false;
      S.loaderText = "";
      render();
    }
  }

  async function saveBuilderToServer() {
    saveDraft();
    if (S.me?.local_dev_preview) {
      S.custom = { success: true, config: builderToCustomPayload(), preview: strategySummaryText(), local_dev_preview: true };
      S.builderDirty = false;
      S.builderHydratedFromServer = true;
      return;
    }
    await postJSON("/me/trading-settings", builderToSettingsPayload());
    const response = await postJSON("/me/custom-strategy", builderToCustomPayload());
    S.custom = response;
    S.builderDirty = false;
    S.builderHydratedFromServer = true;
  }

  async function switchMode(mode) {
    const nextMode = String(mode || "demo").toLowerCase() === "real" ? "real" : "demo";
    const previousMode = S.mode;
    const previousAccountType = S.me?.account_type;
    S.mode = nextMode;
    if (S.me) S.me.account_type = nextMode;
    storageSet(K.mode, S.mode);
    render();
    try {
      await postJSON("/me/switch-account", { account_type: nextMode });
      S.notice = `Switched to ${nextMode}.`;
      await refresh(false, "Refreshing dashboard...");
    } catch (error) {
      S.mode = previousMode;
      if (S.me) S.me.account_type = previousAccountType || previousMode;
      storageSet(K.mode, S.mode);
      S.error = String(error?.message || error);
    } finally {
      render();
    }
  }

  function setNested(path, rawValue, { repaint = true } = {}) {
    const parts = String(path || "").split(".");
    if (!parts.length) return;
    let target = S.builder;
    for (let index = 0; index < parts.length - 1; index += 1) {
      target = target[parts[index]];
      if (!target) return;
    }
    const key = parts[parts.length - 1];
    const current = target[key];
    const value = typeof current === "number" ? Number(rawValue) : rawValue;
    target[key] = value;
    S.builder = normalizeBuilder(S.builder);
    markBuilderDirty();
    saveDraft();
    if (repaint) render();
    else refreshInlineBuilderText();
  }

  function bind(root) {
    root.querySelector("#risk-disclaimer-toggle")?.addEventListener("click", () => {
      S.riskOpen = !S.riskOpen;
      render();
    });
    root.querySelector("#risk-disclaimer-close")?.addEventListener("click", () => {
      S.riskOpen = false;
      render();
    });
    root.querySelectorAll("[data-view]").forEach((button) => {
      button.onclick = () => switchView(button.dataset.view);
    });
    root.querySelectorAll("[data-theme-value]").forEach((button) => {
      button.onclick = () => {
        setTheme(button.dataset.themeValue);
        render();
      };
    });
    root.querySelectorAll("[data-mode]").forEach((button) => {
      button.onclick = () => switchMode(button.dataset.mode);
    });
    root.querySelectorAll("[data-builder]").forEach((field) => {
      if (field.type === "checkbox") {
        field.addEventListener("change", () => setNested(field.dataset.builder, field.checked));
        return;
      }
      if (field.tagName === "SELECT") {
        field.addEventListener("change", () => setNested(field.dataset.builder, field.value));
        return;
      }
      field.addEventListener("input", () => setNested(field.dataset.builder, field.value, { repaint: false }));
      field.addEventListener("change", () => setNested(field.dataset.builder, field.value));
    });
    root.querySelectorAll("[data-strategy-mode]").forEach((button) => {
      button.onclick = () => {
        S.builder.strategyMode = button.dataset.strategyMode;
        markBuilderDirty();
        saveDraft();
        render();
      };
    });
    root.querySelectorAll("[data-market-mode]").forEach((button) => {
      button.onclick = () => {
        S.builder.marketMode = normalizeMarketMode(button.dataset.marketMode);
        if (S.builder.marketMode === "single") {
          S.builder.markets = [S.builder.oneMarket];
        }
        markBuilderDirty();
        saveDraft();
        render();
      };
    });
    root.querySelector("[data-market-select]")?.addEventListener("change", (event) => {
      const value = String(event.currentTarget.value || "");
      if (!value) return;
      if (value === "__all__") {
        S.builder.marketMode = "all";
        markBuilderDirty();
        saveDraft();
        render();
        return;
      }
      if (S.builder.marketMode === "selected") {
        const set = new Set(S.builder.markets || []);
        set.add(value);
        S.builder.markets = Array.from(set);
        if (!S.builder.oneMarket) S.builder.oneMarket = value;
      } else {
        S.builder.marketMode = "single";
        S.builder.oneMarket = value;
        S.builder.markets = [value];
      }
      markBuilderDirty();
      saveDraft();
      render();
    });
    root.querySelectorAll("[data-market-remove]").forEach((button) => {
      button.onclick = () => {
        const symbol = button.dataset.marketRemove;
        const set = new Set(S.builder.markets || []);
        set.delete(symbol);
        S.builder.markets = Array.from(set);
        if (S.builder.marketMode === "single") {
          S.builder.marketMode = "selected";
        }
        if (S.builder.markets.length) S.builder.oneMarket = S.builder.markets[0];
        markBuilderDirty();
        saveDraft();
        render();
      };
    });
    root.querySelectorAll("[data-trade-group]").forEach((button) => {
      button.onclick = () => {
        const group = TRADE_GROUPS.find((item) => item.value === button.dataset.tradeGroup) || TRADE_GROUPS[0];
        S.builder.trade.group = group.value;
        S.builder.trade.side = group.sides[0].value;
        markBuilderDirty();
        saveDraft();
        render();
      };
    });
    root.querySelector("[data-save-builder]")?.addEventListener("click", () => {
      mutate(saveBuilderToServer, "Custom Strategy Builder saved.", "Saving builder...");
    });
    root.querySelectorAll("[data-dismiss-limit-notice]").forEach((button) => {
      button.onclick = () => {
        storageSet(button.dataset.dismissLimitNotice, "1");
        render();
      };
    });
    root.querySelector("[data-reset-strategy]")?.addEventListener("click", () => {
      if (!window.confirm("Reset this strategy? Your current builder configuration will be cleared.")) return;
      S.builder = clone(DEFAULT_BUILDER);
      S.builderDirty = true;
      S.builderHydratedFromServer = true;
      saveDraft();
      S.notice = "Strategy builder reset to defaults. Save Builder when you are ready to apply it.";
      S.error = "";
      render();
    });
    root.querySelector("[data-clear-local-trades]")?.addEventListener("click", () => {
      if (!window.confirm("Reset local trade history and session statistics? This does not affect your Deriv account history or balance.")) return;
      storageSet(tradeResetKey(), new Date().toISOString());
      S.notice = "Local trade session cleared. New trades will start counting from now.";
      S.error = "";
      render();
    });
    root.querySelector("[data-main-action]")?.addEventListener("click", (event) => {
      const action = event.currentTarget.dataset.mainAction;
      if (S.me?.requires_api_token) {
        S.error = "Connect the Deriv trade-scope token in Settings before starting.";
        switchView("settings");
        return;
      }
      if (action === "start") {
        mutate(async () => {
          await saveBuilderToServer();
          await postJSON("/me/resume-trading", { mode: "start_again" });
        }, "Auto trading started.", "Saving builder and starting...");
      } else if (action === "resume") {
        mutate(() => postJSON("/me/resume-trading", { mode: "continue" }), "Auto trading resumed.", "Resuming auto trading...");
      } else {
        mutate(() => postJSON("/me/stop-trading"), "Auto trading stopped.", "Stopping auto trading...");
      }
    });
    const logout = root.querySelector("#logout");
    if (logout) {
      logout.onclick = () => mutate(async () => {
        await postJSON("/me/logout");
        S.me = { authenticated: false };
        S.life = null;
        S.trades = [];
        S.tradeSummary = {};
        S.view = "main";
        storageSet(K.view, S.view);
        storageRemove(K.session);
      }, "Logged out successfully.", "Logging out...");
    }
    const tokenForm = root.querySelector("#token-form");
    if (tokenForm) {
      tokenForm.onsubmit = (event) => {
        event.preventDefault();
        const form = new FormData(tokenForm);
        mutate(
          () => postJSON("/me/api-token", { api_token: String(form.get("api_token") || "").trim() }),
          "Trading credential saved.",
          "Verifying trading credential...",
        );
      };
    }
  }

  async function refresh(force = false, loadingText = "Refreshing dashboard...") {
    if (S.busy && !force) return;
    const blocking = force || S.booting || S.mutating;
    S.busy = true;
    if (blocking) {
      S.loaderText = loadingText;
      render();
    }
    try {
      S.me = await getJSON("/me");
      if (authenticated()) {
        S.mode = S.me.account_type || S.mode;
        storageSet(K.mode, S.mode);
      }
      S.summary = await getJSON(`/metrics/summary?mode=${encodeURIComponent(S.mode)}`);
      if (authenticated()) {
        const [life, today, custom] = await Promise.all([
          getJSON("/me/trading-lifecycle"),
          getJSON("/me/trades/today"),
          getJSON("/me/custom-strategy").catch(() => null),
        ]);
        S.life = life;
        S.trades = Array.isArray(today.trades) ? today.trades : [];
        S.tradeSummary = { ...(today.summary || {}), date: today.date };
        S.custom = custom;
        if (custom?.config?.configured) mapConfigToBuilder(custom);
        else hydrateBuilderMoneyFromAccount();
        saveLastGoodSnapshot();
      } else {
        S.life = null;
        S.trades = [];
        S.tradeSummary = {};
        S.custom = null;
      }
      S.error = "";
    } catch (error) {
      const message = "LIVE REFRESH DELAYED - showing last known dashboard data.";
      if (showRefreshDelayed(message)) return;
      S.error = `Dashboard refresh failed: ${String(error?.message || error)}`;
    } finally {
      S.busy = false;
      S.booting = false;
      S.loaderText = "";
      if (blocking || !isEditingDashboard()) render();
      else renderWhenIdle();
    }
  }

  function boot() {
    document.querySelector("#foa-bootstrap")?.remove();
    document.body.classList.add("foa-builder-active", "foa-simple-active");
    if (!document.querySelector("#foa-simple-app")) {
      const app = document.createElement("div");
      app.id = "foa-simple-app";
      app.dataset.theme = S.theme;
      app.dataset.uiVersion = VERSION;
      document.body.appendChild(app);
    }
    render();
    refresh(true, "Opening builder...");
    window.setInterval(() => refresh(false, "Refreshing dashboard..."), 15000);
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && S.riskOpen) {
        S.riskOpen = false;
        render();
      }
    });
  }

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", boot, { once: true })
    : boot();
})();
