(() => {
  "use strict";

  const root = document.getElementById("derivadmin-root");
  if (!root) return;

  const ROUTES = new Set(["home", "builder", "ai", "ready", "schedule", "profile", "trades", "timezone"]);
  const STORE_TEMPLATES = "foa-user-strategy-templates-v2";
  const STORE_READY = "foa-text-strategy-result-v2";
  const STORE_THEME = "derivadmin-ui-theme-v1";
  const STORE_PAID_SOON = "derivadmin-paid-soon-dismissed-v1";
  const DEFAULT_TZ = "Africa/Nairobi";
  const TIMEZONES = [
    ["Africa/Nairobi", "Nairobi", "East Africa Time"],
    ["Africa/Kampala", "Kampala", "East Africa Time"],
    ["Africa/Dar_es_Salaam", "Dar es Salaam", "East Africa Time"],
    ["Europe/London", "London", "United Kingdom"],
    ["America/New_York", "New York", "United States"],
  ];
  const BUILDER_MODES = [
    ["last_digit", "Last Digit", "9", "Last-digit rules only"],
    ["percentage", "Percentage", "%", "Percentage rules only"],
    ["combined", "Combined", "AND", "Use both rule types"],
  ];
  const BUILDER_COMPARATORS = [
    [">", "Greater than"],
    ["<", "Less than"],
    ["==", "Equal to"],
    ["!=", "Not equal to"],
    [">=", "Greater than or equal to"],
    ["<=", "Less than or equal to"],
    ["all_same", "All same"],
    ["all_even", "All even"],
    ["all_odd", "All odd"],
  ];
  const BUILDER_NUMERIC_COMPARATORS = BUILDER_COMPARATORS.filter(([value]) => !["all_same", "all_even", "all_odd"].includes(value));
  const PERCENTAGE_TARGETS = [
    ["even", "Even"],
    ["odd", "Odd"],
    ["over", "Over digit"],
    ["under", "Under digit"],
    ["digit", "Exact digit"],
    ["rise", "Up ticks"],
    ["fall", "Down ticks"],
    ["no_move", "No-move ticks"],
  ];
  const TICK_DIRECTIONS = [
    ["rising", "Up ticks"],
    ["falling", "Down ticks"],
    ["no_move", "No Move"],
  ];
  const TRADE_GROUPS = [
    ["over_under", "Over/Under", [["over", "Over"], ["under", "Under"]], true],
    ["matches_differs", "Matches/Differs", [["matches", "Matches"], ["differs", "Differs"]], true],
    ["odd_even", "Odd/Even", [["odd", "Odd"], ["even", "Even"]], false],
    ["rise_fall", "Rise/Fall", [["rise", "Rise"], ["fall", "Fall"]], false],
  ];
  const ALL_MARKETS = ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V", "R_10", "R_25", "R_50", "R_75", "R_100"];
  const BUILDER_MARKET_MODES = [["all", "All Markets"], ["selected", "Choose Markets"]];
  const RECOVERY_MODES = [
    ["system", "System Martingale", "Full recovery using the backend exact-debt plan."],
    ["multiplier", "Custom Multiplier", "Use the multiplier set below for recovery entries."],
    ["split", "Split Recovery", "Spread exact debt across 1 to 3 successful recovery trades."],
  ];

  function clone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function baseBuilder(name = "Over 1 Recovery Over 4 Golden Bot") {
    return {
      version: 3,
      name,
      strategyMode: "percentage",
      marketMode: "all",
      markets: [...ALL_MARKETS],
      market: "1HZ100V",
      tradeGroup: "over_under",
      side: "over",
      prediction: 1,
      ticks: 1,
      lastRule: { window: 5, operator: "<=", value: 5 },
      percentageRule: { target: "over", value: 1, window: 1000, operator: ">", threshold: 80 },
      tickDirectionRule: { enabled: false, window: 3, direction: "rising" },
      reanalyze: { mode: "after_every_trade", losses: 1, wins: 1 },
      money: { stake: 0.5, takeProfit: 25, stopLoss: 100, martingale: 2.1, recoveryMode: "system", splitCount: 2 },
      virtualHook: { enabled: true, enterAfterLosses: 2, exitAfterConsecutiveWins: 1 },
      resultRouting: { enabled: false, afterLoss: null },
    };
  }

  function deepMerge(base, patch) {
    const result = clone(base);
    Object.entries(patch || {}).forEach(([key, value]) => {
      if (value && typeof value === "object" && !Array.isArray(value) && result[key] && typeof result[key] === "object" && !Array.isArray(result[key])) {
        result[key] = deepMerge(result[key], value);
      } else {
        result[key] = clone(value);
      }
    });
    return result;
  }

  function templatePreset(id, name, analysis, side, summary, patch = {}, result = {}, extra = {}) {
    const group = tradeGroupForSide(side)[0];
    const builder = deepMerge(baseBuilder(name), {
      strategyMode: analysis,
      tradeGroup: group,
      side,
      prediction: patch?.trade?.prediction ?? (["odd", "even", "rise", "fall"].includes(side) ? 0 : 5),
      money: {
        recoveryMode: result.recoveryMode || "multiplier",
        splitCount: result.splitCount || 1,
      },
      resultRouting: {
        enabled: Boolean(result.routingEnabled),
        afterLoss: result.afterLoss || null,
      },
      predictionMode: extra.predictionMode || "",
      predictionWindow: Number(extra.predictionWindow || 100),
      ...patch,
    });
    if (patch.trade) {
      builder.tradeGroup = patch.trade.group || builder.tradeGroup;
      builder.side = patch.trade.side || builder.side;
      builder.prediction = patch.trade.prediction ?? builder.prediction;
    }
    if (patch.money?.ticks !== undefined) builder.ticks = patch.money.ticks;
    return { id, name, label: extra.label || "Built-in template", analysis, side, summary: extra.summary || "", builder, result, builtIn: true };
  }

  const BUILDER_TEMPLATES = [
    templatePreset("golden-over1-recovery-over4", "Over 1 Recovery Over 4 Golden Bot", "percentage", "over", "All markets - Over 1 > 80% in 1,000 ticks - loss route Over 4 after last 5 digits <= 5.", {
      percentageRule: { target: "over", value: 1, window: 1000, operator: ">", threshold: 80 },
      trade: { group: "over_under", side: "over", prediction: 1 },
      reanalyze: { mode: "after_every_trade", losses: 1, wins: 1 },
      money: { stake: 5.5, takeProfit: 100, stopLoss: 1000, martingale: 2.1, recoveryMode: "multiplier", splitCount: 1, ticks: 1 },
      virtualHook: { enabled: true, enterAfterLosses: 2, exitAfterConsecutiveWins: 1 },
    }, {
      routingEnabled: true,
      recoveryMode: "multiplier",
      splitCount: 1,
      afterLoss: { tradeType: "over", prediction: 4, durationTicks: 1, analysisMode: "last_digit", lastRule: { window: 5, operator: "<=", value: 5 }, percentageRule: { target: "over", value: 4, window: 500, operator: ">=", threshold: 50 }, tickDirectionRule: { enabled: false, window: 3, direction: "rising" } },
    }),
    templatePreset("over3-spread-x2-last-digit", "Over 3 Spread Recovery x2", "last_digit", "over", "Last 5 digits <= 5 - trade Over 3 - exact loss debt recovered across 2 wins.", {
      lastRule: { window: 5, operator: "<=", value: 5 },
      trade: { group: "over_under", side: "over", prediction: 3 },
      money: { stake: 0.5, takeProfit: 25, stopLoss: 100, martingale: 2.1, recoveryMode: "split", splitCount: 2, ticks: 1 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("over2-combined-alignment", "Over 2 Combined Alignment", "combined", "over", "Last 3 digits <= 6 AND Over 2 > 72% in 1,000 ticks - split recovery x2.", {
      lastRule: { window: 3, operator: "<=", value: 6 },
      percentageRule: { target: "over", value: 2, window: 1000, operator: ">", threshold: 72 },
      trade: { group: "over_under", side: "over", prediction: 2 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("under8-percentage-shield", "Under 8 Percentage Shield", "percentage", "under", "Under 8 > 80% across the last 1,000 digits - re-analyze every trade.", {
      percentageRule: { target: "under", value: 8, window: 1000, operator: ">", threshold: 80 },
      trade: { group: "over_under", side: "under", prediction: 8 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("under6-last-digit-cluster", "Under 6 Last-Digit Cluster", "last_digit", "under", "Last 4 digits <= 6 - trade Under 6 - two-part spread recovery.", {
      lastRule: { window: 4, operator: "<=", value: 6 },
      trade: { group: "over_under", side: "under", prediction: 6 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("under7-combined-alignment", "Under 7 Combined Alignment", "combined", "under", "Last 3 digits <= 6 AND Under 7 > 72% in 500 ticks.", {
      lastRule: { window: 3, operator: "<=", value: 6 },
      percentageRule: { target: "under", value: 7, window: 500, operator: ">", threshold: 72 },
      trade: { group: "over_under", side: "under", prediction: 7 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("even-percentage-majority", "Even Percentage Majority", "percentage", "even", "Even digits > 54% in the last 200 digits - re-analyze every trade.", {
      percentageRule: { target: "even", value: 0, window: 200, operator: ">", threshold: 54 },
      trade: { group: "odd_even", side: "even", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("even-last-digit-rebound", "Even Last-Digit Rebound", "last_digit", "even", "Last 3 digits <= 4 - trade Even - two-part exact-debt spread recovery.", {
      lastRule: { window: 3, operator: "<=", value: 4 },
      trade: { group: "odd_even", side: "even", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("even-combined-balance", "Even Combined Balance", "combined", "even", "Last 2 digits <= 7 AND Even > 53% in 500 digits.", {
      lastRule: { window: 2, operator: "<=", value: 7 },
      percentageRule: { target: "even", value: 0, window: 500, operator: ">", threshold: 53 },
      trade: { group: "odd_even", side: "even", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("odd-percentage-majority", "Odd Percentage Majority", "percentage", "odd", "Odd digits > 54% in the last 200 digits - re-analyze every trade.", {
      percentageRule: { target: "odd", value: 0, window: 200, operator: ">", threshold: 54 },
      trade: { group: "odd_even", side: "odd", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("odd-last-digit-rebound", "Odd Last-Digit Rebound", "last_digit", "odd", "Last 3 digits >= 5 - trade Odd - two-part exact-debt spread recovery.", {
      lastRule: { window: 3, operator: ">=", value: 5 },
      trade: { group: "odd_even", side: "odd", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("odd-combined-balance", "Odd Combined Balance", "combined", "odd", "Last 2 digits >= 2 AND Odd > 53% in 500 digits.", {
      lastRule: { window: 2, operator: ">=", value: 2 },
      percentageRule: { target: "odd", value: 0, window: 500, operator: ">", threshold: 53 },
      trade: { group: "odd_even", side: "odd", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("matches7-hot-percentage", "Matches 7 Hot-Digit Percentage", "percentage", "matches", "Exact digit 7 > 13% in the last 100 digits - conservative multiplier recovery.", {
      percentageRule: { target: "digit", value: 7, window: 100, operator: ">", threshold: 13 },
      trade: { group: "matches_differs", side: "matches", prediction: 7 },
      money: { stake: 0.5, takeProfit: 25, stopLoss: 100, martingale: 1.5, recoveryMode: "multiplier", splitCount: 1, ticks: 1 },
    }, { recoveryMode: "multiplier", splitCount: 1 }),
    templatePreset("matches4-last-digit-repeat", "Matches 4 Last-Digit Repeat", "last_digit", "matches", "Last 2 digits equal 4 - trade Matches 4 - experimental high-payout template.", {
      lastRule: { window: 2, operator: "==", value: 4 },
      trade: { group: "matches_differs", side: "matches", prediction: 4 },
      money: { stake: 0.5, takeProfit: 25, stopLoss: 100, martingale: 1.4, recoveryMode: "multiplier", splitCount: 1, ticks: 1 },
    }, { recoveryMode: "multiplier", splitCount: 1 }),
    templatePreset("matches-dominant-combined", "Matches Dominant Combined", "combined", "matches", "Last 2 digits >= 0 AND exact digit concentration > 12.5% - dynamic prediction.", {
      lastRule: { window: 2, operator: ">=", value: 0 },
      percentageRule: { target: "digit", value: 5, window: 100, operator: ">", threshold: 12.5 },
      trade: { group: "matches_differs", side: "matches", prediction: 5 },
      money: { stake: 0.5, takeProfit: 25, stopLoss: 100, martingale: 1.4, recoveryMode: "multiplier", splitCount: 1, ticks: 1 },
    }, { recoveryMode: "multiplier", splitCount: 1 }, { predictionMode: "most_appearing", predictionWindow: 100 }),
    templatePreset("differs4-percentage-rare", "Differs 4 Percentage Filter", "percentage", "differs", "Exact digit 4 < 8% in the last 100 digits - split recovery x2.", {
      percentageRule: { target: "digit", value: 4, window: 100, operator: "<", threshold: 8 },
      trade: { group: "matches_differs", side: "differs", prediction: 4 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("differs-repeat-breaker", "Differs Repeat Breaker", "last_digit", "differs", "Last 2 digits are identical - trade Differs against the trigger digit.", {
      lastRule: { window: 2, operator: "all_same", value: 4 },
      trade: { group: "matches_differs", side: "differs", prediction: 4 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }, { predictionMode: "last_digit" }),
    templatePreset("differs-combined-rare-breaker", "Differs Combined Rare Breaker", "combined", "differs", "Last 2 digits same AND exact digit concentration < 10% - least-appearing prediction.", {
      lastRule: { window: 2, operator: "all_same", value: 4 },
      percentageRule: { target: "digit", value: 4, window: 100, operator: "<", threshold: 10 },
      trade: { group: "matches_differs", side: "differs", prediction: 4 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }, { predictionMode: "least_appearing", predictionWindow: 100 }),
    templatePreset("rise-percentage-momentum", "Rise Percentage Momentum", "percentage", "rise", "Up ticks > 55% in the last 100 ticks - one-tick contract.", {
      percentageRule: { target: "rise", value: 0, window: 100, operator: ">", threshold: 55 },
      trade: { group: "rise_fall", side: "rise", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("rise-last-direction", "Rise Last-Tick Direction", "last_digit", "rise", "Last-digit guard plus last 3 tick directions rising - trade Rise.", {
      lastRule: { window: 1, operator: ">=", value: 0 },
      tickDirectionRule: { enabled: true, window: 3, direction: "rising" },
      trade: { group: "rise_fall", side: "rise", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("rise-combined-momentum", "Rise Combined Momentum", "combined", "rise", "Last-digit guard AND Up ticks > 54% in 200 ticks AND last 3 directions rising.", {
      lastRule: { window: 2, operator: ">=", value: 0 },
      percentageRule: { target: "rise", value: 0, window: 200, operator: ">", threshold: 54 },
      tickDirectionRule: { enabled: true, window: 3, direction: "rising" },
      trade: { group: "rise_fall", side: "rise", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("fall-percentage-momentum", "Fall Percentage Momentum", "percentage", "fall", "Down ticks > 55% in the last 100 ticks - one-tick contract.", {
      percentageRule: { target: "fall", value: 0, window: 100, operator: ">", threshold: 55 },
      trade: { group: "rise_fall", side: "fall", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("fall-last-direction", "Fall Last-Tick Direction", "last_digit", "fall", "Last-digit guard plus last 3 tick directions falling - trade Fall.", {
      lastRule: { window: 1, operator: ">=", value: 0 },
      tickDirectionRule: { enabled: true, window: 3, direction: "falling" },
      trade: { group: "rise_fall", side: "fall", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
    templatePreset("fall-combined-momentum", "Fall Combined Momentum", "combined", "fall", "Last-digit guard AND Down ticks > 54% in 200 ticks AND last 3 directions falling.", {
      lastRule: { window: 2, operator: ">=", value: 0 },
      percentageRule: { target: "fall", value: 0, window: 200, operator: ">", threshold: 54 },
      tickDirectionRule: { enabled: true, window: 3, direction: "falling" },
      trade: { group: "rise_fall", side: "fall", prediction: 0 },
      money: { recoveryMode: "split", splitCount: 2 },
    }, { recoveryMode: "split", splitCount: 2 }),
  ];

  const state = {
    route: routeFromHash(),
    me: null,
    accounts: null,
    trades: null,
    lifecycle: null,
    schedules: null,
    preferences: null,
    premium: null,
    custom: null,
    generated: readJSON(STORE_READY, null),
    selectedStrategy: null,
    busy: "",
    error: "",
    notice: "",
    paidSoonDismissed: readJSON(STORE_PAID_SOON, false),
    loaded: false,
    theme: readTheme(),
    runPanelOpen: false,
    runPanelTab: "summary",
    scheduleDraft: null,
    editingUntil: 0,
    renderedRoute: "",
    scrollPositions: {},
  };
  applyTheme(state.theme);

  function routeFromHash() {
    const route = String(location.hash || "#home").replace(/^#\/?/, "").split("?", 1)[0].toLowerCase();
    return ROUTES.has(route) ? route : "home";
  }

  function readJSON(key, fallback) {
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || "null");
      return parsed == null ? fallback : parsed;
    } catch (_) { return fallback; }
  }

  function writeJSON(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {}
  }

  function readTheme() {
    try {
      const value = localStorage.getItem(STORE_THEME);
      return value === "light" ? "light" : "dark";
    } catch (_) { return "dark"; }
  }

  function applyTheme(theme) {
    const safe = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = safe;
    try { localStorage.setItem(STORE_THEME, safe); } catch (_) {}
  }

  function markEditing() {
    state.editingUntil = Date.now() + 9000;
  }

  function activeEditable() {
    const active = document.activeElement;
    return Boolean(active && root.contains(active) && active.matches?.("input, textarea, select, [contenteditable='true']"));
  }

  function shouldHoldRender(quiet) {
    return Boolean(
      quiet
      && state.loaded
      && ["builder", "ai", "schedule", "timezone"].includes(state.route)
      && (Date.now() < state.editingUntil || activeEditable())
    );
  }

  function scrollHost() {
    return root.querySelector(".app-main") || document.scrollingElement || document.documentElement;
  }

  function rememberScroll(route = state.renderedRoute || state.route) {
    if (!route) return 0;
    const top = Number(scrollHost()?.scrollTop || 0);
    state.scrollPositions[route] = top;
    return top;
  }

  function restoreScroll(route, top) {
    const target = Number.isFinite(Number(top)) ? Number(top) : 0;
    const apply = () => {
      const host = scrollHost();
      if (host) host.scrollTop = target;
    };
    apply();
    requestAnimationFrame(apply);
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function money(value, currency = "USD") {
    const number = Number(value || 0);
    return `${Number.isFinite(number) ? number.toFixed(2) : "0.00"} ${esc(currency || "USD")}`;
  }

  function pct(value) {
    const number = Number(value || 0);
    return `${(number <= 1 ? number * 100 : number).toFixed(1)}%`;
  }

  function num(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function whole(value, fallback, min, max) {
    return Math.round(clamp(num(value, fallback), min, max));
  }

  function optionList(items, selected) {
    return items.map(([value, label]) => `<option value="${esc(value)}" ${String(selected) === String(value) ? "selected" : ""}>${esc(label)}</option>`).join("");
  }

  async function json(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) {
      const message = payload?.detail || payload?.message || `Request failed (${response.status})`;
      throw new Error(typeof message === "string" ? message : JSON.stringify(message));
    }
    return payload || {};
  }

  function quill(name, className = "") {
    const markup = window.DERIV_QUILL_ICONS?.[name];
    if (!markup) return `<span class="quill-missing ${esc(className)}" aria-hidden="true">◆</span>`;
    return `<span class="quill-icon ${esc(className)}" data-quill="${esc(name)}">${markup}</span>`;
  }

  function miniIcon(name) {
    const icons = {
      home: '<path d="M3 11 12 3l9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
      builder: '<path d="M4 5h7v6H4zM13 5h7v4h-7zM13 11h7v8h-7zM4 13h7v6H4z"/>',
      ai: '<path d="M12 3 9.7 8.7 4 11l5.7 2.3L12 19l2.3-5.7L20 11l-5.7-2.3z"/>',
      schedule: '<path d="M6 3v3m12-3v3M4 8h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1zm5 7h4v4h-4z"/>',
      profile: '<circle cx="12" cy="8" r="4"/><path d="M4 21c.8-4.2 3.5-6 8-6s7.2 1.8 8 6"/>',
      trades: '<path d="M4 18V9m5 9V5m6 13v-7m5 7V3"/>',
      arrow: '<path d="m8 4 8 8-8 8"/>',
      check: '<path d="m5 12 4 4 10-10"/>',
      spark: '<path d="m12 2 1.6 5.4L19 9l-5.4 1.6L12 16l-1.6-5.4L5 9l5.4-1.6zM18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z"/>',
      clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/>',
      play: '<path d="m8 5 11 7-11 7z"/>',
      stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
      pause: '<path d="M8 5v14m8-14v14"/>',
      trash: '<path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6"/>',
      wallet: '<path d="M4 7h15a1 1 0 0 1 1 1v10H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h13v3"/><path d="M16 12h4"/>',
      bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
      user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c.8-4.2 3.5-6 8-6s7.2 1.8 8 6"/>',
      cube: '<path d="m12 2 8 4.5v9L12 20l-8-4.5v-9z"/><path d="M12 11 4 6.5m8 4.5 8-4.5M12 11v9"/>',
      plus: '<path d="M12 5v14M5 12h14"/>',
      eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="3"/>',
      save: '<path d="M5 3h12l2 2v16H5z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/>',
      back: '<path d="m15 18-6-6 6-6"/>',
      help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.7 2.7 0 0 1 5 1.4c0 2-2.5 2.2-2.5 4M12 18h.01"/>',
      filter: '<path d="M4 5h16l-6 7v6l-4 2v-8z"/>',
      target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3"/>',
      document: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
      sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
      moon: '<path d="M21 12.8A8.5 8.5 0 1 1 11.2 3a6.7 6.7 0 0 0 9.8 9.8z"/>',
    };
    return `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.spark}</svg>`;
  }

  function nav() {
    const items = [
      ["home", "Home"], ["builder", "Builder"], ["ai", "AI"], ["schedule", "Schedule"], ["profile", "Profile"],
    ];
    return `<nav class="bottom-nav" aria-label="Primary">${items.map(([route, label]) => `
      <button class="nav-item ${state.route === route ? "active" : ""}" data-route="${route}">
        ${miniIcon(route)}<span>${label}</span>
      </button>`).join("")}</nav>`;
  }

  function topAccountControl() {
    if (!state.me?.authenticated) return `<a class="login-button" href="/oauth/start">Login</a>`;
    const accounts = state.accounts?.accounts || [];
    const selected = selectedLinkedAccount(accounts) || {
      managed_account_id: "",
      account_id_masked: state.me.account_id || "",
      account_type: state.me.account_type || "demo",
      balance: state.me.balance || 0,
      currency: state.me.currency || "USD",
    };
    const type = accountType(selected);
    const accountId = (account) => account.account_id || account.loginid || account.account_id_masked || account.label || "";
    const accountForKind = (kind) => accounts.find((account) => accountType(account) === kind);
    const tabs = ["real", "demo"].map((tab) => {
      const account = accountForKind(tab);
      return `<button class="${type === tab ? "active" : ""}" type="button" ${account ? `data-account-id="${esc(account.managed_account_id)}"` : ""} data-account-kind="${tab}">${tab[0].toUpperCase() + tab.slice(1)}</button>`;
    }).join("");
    const rows = accounts.map((account) => {
      const rowType = accountType(account);
      return `<button class="account-dropdown-row ${rowType}" type="button" data-account-id="${esc(account.managed_account_id)}" data-account-kind-row="${rowType}">
        ${iconMarkup(account)}
        <span><b>${esc(account.label || (rowType === "real" ? account.currency || "USD" : "Demo"))}</b><small>${esc(accountId(account))}</small></span>
        ${rowType === "demo" ? `<em>Reset balance</em>` : `<strong>${money(account.balance, account.currency)}</strong>`}
      </button>`;
    }).join("");
    return `<div class="top-account-switch ${type}" tabindex="0">
      <div class="account-switch-summary">
        <span class="currency-pill">${esc(selected.currency || "USD")} ${miniIcon("arrow")}</span>
        ${iconMarkup(selected)}
        <strong>${money(selected.balance, selected.currency)}</strong>
        <span class="switch-caret">${miniIcon("arrow")}</span>
      </div>
      <div class="account-dropdown" role="menu">
        <div class="account-tabs">${tabs}</div>
        <div class="account-dropdown-title"><b>Deriv account${type === "real" ? "s" : ""}</b><span>${miniIcon("arrow")}</span></div>
        <div class="account-dropdown-rows">${rows}</div>
      </div>
    </div>`;
  }

  function selectedLinkedAccount(accounts) {
    const selectedId = Number(state.accounts?.selected_managed_account_id || 0);
    return accounts.find((account) => Number(account.managed_account_id || 0) === selectedId)
      || accounts.find((account) => account.selected)
      || accounts[0]
      || null;
  }

  function accountType(account) {
    const accountId = String(account?.account_id || account?.loginid || account?.account_id_masked || "").trim().toUpperCase();
    // Deriv virtual accounts use VRTC/DOT IDs. The immutable account identity
    // wins over a stale cached label, so the header never shows the wrong mode.
    if (accountId.startsWith("VRTC") || accountId.startsWith("DOT")) return "demo";
    if (accountId) return "real";
    return String(account?.account_type || "demo").trim().toLowerCase() === "real" ? "real" : "demo";
  }

  function iconMarkup(account) {
    return accountType(account) === "real"
      ? `<span class="deriv-real-flag" aria-hidden="true"></span>`
      : `<span class="deriv-demo-coin" aria-hidden="true"></span>`;
  }

  function themeToggle() {
    const light = state.theme === "light";
    return `<button class="theme-toggle ${light ? "light" : "dark"}" type="button" data-theme-toggle aria-label="Toggle light and dark mode">
      ${miniIcon(light ? "sun" : "moon")}<span>${light ? "Light" : "Dark"}</span>
    </button>`;
  }

  function shell(content, options = {}) {
    const showNav = options.nav !== false;
    const title = options.title || "DerivAdmin";
    const homeHeader = ["Dashboard", "DerivAdmin"].includes(title);
    return `<div class="app-shell ${showNav ? "with-nav" : ""}">
      <header class="topbar ${homeHeader ? "home-topbar" : "page-topbar"}">
        ${homeHeader ? `<button class="brand-lockup" data-route="home" aria-label="DerivAdmin Home">
          <span class="brand-mark">D</span><span><b>Dashboard</b></span>
        </button><div class="topbar-tools">${themeToggle()}${topAccountControl()}</div>` : `<button class="icon-button back-button" data-route="home" aria-label="Back">${miniIcon("back")}</button><div class="page-spacer" aria-hidden="true"></div><div class="topbar-tools">${themeToggle()}${topAccountControl()}</div>`}
      </header>
      ${messages()}
      ${paidSoonBanner()}
      <main class="app-main">${content}</main>
      ${state.me?.authenticated ? globalRunPanel() : ""}
      ${showNav ? nav() : ""}
    </div>`;
  }

  function messages() {
    return `${state.error ? `<div class="global-message error">${esc(state.error)}</div>` : ""}${state.notice ? `<div class="global-message success">${esc(state.notice)}</div>` : ""}`;
  }

  function paidSoonBanner() {
    if (!state.me?.authenticated || state.paidSoonDismissed) return "";
    return `<section class="paid-soon-banner" role="status">
      <span>${miniIcon("spark")}</span>
      <div><b>DerivAdmin is free during this testing phase.</b><p>Weekly access will soon be KES 250. You will be notified before billing is turned on. For now, maximize it and get the most from every feature.</p></div>
      <button type="button" data-paid-soon-ok>OK</button>
    </section>`;
  }

  function landing() {
    return `<div class="landing-page">
      <div class="landing-glow one"></div><div class="landing-glow two"></div>
      <header class="landing-header"><div class="brand-lockup static"><span class="brand-mark">D</span><span><b>DerivAdmin</b><small>Home of Automation</small></span></div><div class="topbar-tools">${themeToggle()}</div></header>
      <main class="landing-main">
        <span class="eyebrow">AUTOMATE WITH PRECISION</span>
        <h1>Build it.<br><span>Describe it.</span><br>Schedule it.</h1>
        <p>Create powerful Deriv automation visually, describe a strategy in plain language, or schedule a trading session to run exactly when you want.</p>
        <div class="landing-actions"><a class="btn primary xl" href="/oauth/start">Login with Deriv</a><a class="btn ghost xl" href="https://deriv.com/signup/" rel="noreferrer">Create Deriv account</a></div>
        <div class="landing-feature-grid">
          <article><span>${miniIcon("builder")}</span><b>Strategy Builder</b><small>Build rule by rule.</small></article>
          <article><span>${miniIcon("spark")}</span><b>Text to Strategy</b><small>Describe it naturally.</small></article>
          <article><span>${miniIcon("schedule")}</span><b>Schedule Trading</b><small>Run it automatically.</small></article>
        </div>
      </main>
    </div>`;
  }

  function metrics() {
    const summary = state.trades?.summary || {};
    const meStats = state.me?.stats || {};
    return {
      balance: Number(state.me?.balance || 0),
      runs: Number(summary.total ?? meStats.trades ?? 0),
      wins: Number(summary.wins ?? meStats.wins ?? 0),
      losses: Number(summary.losses ?? meStats.losses ?? 0),
      profit: Number(summary.profit ?? meStats.profit ?? 0),
    };
  }

  function home() {
    const content = `
    <section class="automation-grid">
      ${featureCard("builder", "cube", "Strategy Builder", "Open Builder", "Build with advanced blocks and conditions.")}
      ${featureCard("ai", "ai", "Text to Strategy", "Create with AI", "Describe your idea in plain English. We build it for you.")}
      ${featureCard("schedule", "schedule", "Schedule Trading", "Schedule Session", "Pick a strategy, date, time, stake, TP and SL.")}
    </section>
    ${dashboardBots()}
    `;
    return shell(content, { title: "Dashboard", subtitle: "Home of Automation" });
  }

  function featureCard(route, icon, title, subtitle, body) {
    return `<button class="feature-card" data-route="${route}"><span class="feature-icon">${miniIcon(icon)}</span><span><b>${esc(title)}</b><em>${esc(body)}</em></span><strong>${esc(subtitle)}</strong><span class="feature-arrow">${miniIcon("arrow")}</span></button>`;
  }

  function savedTemplates() {
    const seen = new Set();
    return readJSON(STORE_TEMPLATES, []).filter((item) => {
      if (!(item?.builder || item?.strategy || item?.config)) return false;
      const key = strategyNameKey(item.name || item.builder?.name || item.strategy?.name || item.config?.name || item.id);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function botCatalog() {
    const seen = new Set();
    const local = savedTemplates().map((item) => ({
      id: String(item.id || item.name || ""),
      source: "local",
      name: item.name || "Local Bot",
      label: "Local",
      builder: allMarketBuilder(item.builder || item.strategy || item.config || item),
    }));
    const built = BUILDER_TEMPLATES.map((item) => ({
      id: item.id,
      source: "built",
      name: item.name,
      label: "Built-in",
      builder: allMarketBuilder(item.builder),
    }));
    return [...local, ...built].filter((item) => {
      const key = strategyNameKey(item.name);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 12);
  }

  function botById(source, id) {
    if (source === "built") {
      const item = BUILDER_TEMPLATES.find((row) => String(row.id) === String(id));
      return item ? { ...item, builder: allMarketBuilder(item.builder) } : null;
    }
    const item = savedTemplates().find((row) => String(row.id) === String(id));
    return item ? { ...item, builder: allMarketBuilder(item.builder || item.strategy || item.config || item) } : null;
  }

  function allMarketBuilder(builder) {
    const normalized = normalizeBuilderDraft(builder || {});
    return normalizeBuilderDraft({
      ...normalized,
      marketMode: "all",
      markets: supportedMarkets(),
      market: supportedMarkets()[0] || normalized.market,
    });
  }

  function dashboardBots() {
    const bots = botCatalog();
    return `<section class="dashboard-bots">
      <div class="section-head compact"><div><span class="eyebrow">BOTS</span><h2>Load a strategy</h2></div><button class="text-button" data-route="builder">New bot</button></div>
      <div class="dashboard-bot-grid">${bots.map(botCard).join("")}</div>
    </section>`;
  }

  function botCard(item) {
    return `<article class="dashboard-bot-card ${esc(item.source)}">
      <span class="bot-chip">${esc(item.label)}</span>
      <b>${esc(item.name)}</b>
      <button type="button" data-load-bot-source="${esc(item.source)}" data-load-bot-id="${esc(item.id)}">Load</button>
    </article>`;
  }

  function greeting() {
    const hour = new Date().getHours();
    return hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
  }

  function timezonePage() {
    const current = state.preferences?.timezone || DEFAULT_TZ;
    const content = `<section class="onboarding-wrap">
      <div class="progress-head"><span>Step 1 of 2</span><div><i class="active"></i><i></i></div></div>
      <span class="feature-icon large">${miniIcon("clock")}</span>
      <span class="eyebrow">YOUR TRADING CLOCK</span><h1>Choose your timezone</h1>
      <p class="lead">Schedules use your selected local time and are stored as authoritative UTC on the VPS.</p>
      <label class="search-field"><span>⌕</span><input id="timezone-search" placeholder="Search city or timezone" autocomplete="off"></label>
      <div class="timezone-list" id="timezone-list">${timezoneOptions(current)}</div>
      <div class="sticky-actions"><button class="btn primary" data-save-timezone>Continue</button><button class="btn ghost" data-timezone-value="${DEFAULT_TZ}" data-save-timezone>Use Nairobi default</button></div>
    </section>`;
    return shell(content, { title: "DerivAdmin", subtitle: "Set your timezone", nav: false });
  }

  function timezoneOptions(current, filter = "") {
    const needle = String(filter || "").toLowerCase();
    return TIMEZONES.filter((row) => row.join(" ").toLowerCase().includes(needle)).map(([zone, city, note]) => `
      <label class="timezone-option ${zone === current ? "selected" : ""}">
        <input type="radio" name="timezone" value="${esc(zone)}" ${zone === current ? "checked" : ""}>
        <span><b>${esc(city)}</b><small>${esc(zone)} · ${esc(note)}</small></span>
        ${zone === DEFAULT_TZ ? '<em>Recommended</em>' : ""}
      </label>`).join("");
  }

  function aiPage() {
    const content = `<section class="panel ai-compose">
      <div class="panel-title"><div><span class="eyebrow">YOUR IDEA</span><h3>What should the strategy do?</h3></div><span class="word-count" id="word-count">0 / 250 words</span></div>
      <textarea id="strategy-text" maxlength="5000" placeholder="Example: Trade Volatility 100 (1s) Digit Over 3 when the last digit is 4 or greater. Use $0.50 stake, stop after $10 profit or $5 loss, and use virtual protection after two losses.">${esc(state.generated?.source_text || "")}</textarea>
      <div class="prompt-chips">
        <button data-prompt="Trade Volatility 100 (1s) Digit Over 3 when the last digit is 4 or greater.">Over 3</button>
        <button data-prompt="Use percentage mode: trade Over 1 when Over 1 is above 80% in the last 1000 ticks.">Percentage mode</button>
        <button data-prompt="Use combined mode: last 5 digits are less than or equal to 5 and Over 4 percentage is above 75% in 1000 ticks.">Combined mode</button>
        <button data-prompt="After a loss, switch to Digit Over 4 only when the last 5 digits are less than or equal to 5.">After-loss route</button>
        <button data-prompt="Use virtual hook after 2 losses and leave virtual mode after 1 win.">Virtual hook</button>
      </div>
      <div class="ai-steps"><span><i>1</i>Describe</span><span><i>2</i>Review</span><span><i>3</i>Trade or schedule</span></div>
      <button class="btn primary xl" data-generate-strategy>${miniIcon("spark")} Generate Strategy</button>
    </section>`;
    return shell(content, { title: "Text to Strategy" });
  }

  function generatedCanonical() {
    const g = state.generated || {};
    return g.canonical || g.strategy || g.config || g.canonical_strategy || g.custom_strategy || null;
  }

  function readyPage() {
    const g = state.generated;
    if (!g) return shell(`<section class="empty-state"><h2>No AI strategy yet</h2><p>Describe a strategy first.</p><button class="btn primary" data-route="ai">Open Text to Strategy</button></section>`, { title: "Strategy Ready" });
    const canonical = generatedCanonical() || {};
    const name = g.name || g.strategy_name || "AI Generated Strategy";
    const statement = g.created_statement || aiCreatedStatement(g, canonical);
    const content = `<section class="panel ready-card">
      <div class="ready-title compact"><div><span class="eyebrow">CREATED STRATEGY</span><h2>${esc(name)}</h2><p>${esc(statement)}</p></div><span class="ready-check">${miniIcon("check")}</span></div>
      <div class="ready-actions compact"><button class="btn primary" data-ready-builder>Load to Builder</button><button class="btn ghost" data-ready-edit-idea>Edit Idea</button><button class="btn secondary" data-ready-save>Save</button><button class="btn secondary" data-ready-schedule>${miniIcon("schedule")} Schedule</button><button class="btn primary" data-ready-trade>${tradeActionMarkup("Trade Now")}</button></div>
    </section>`;
    return shell(content, { title: "Strategy Ready" });
  }

  function aiCreatedStatement(g = {}, canonical = {}) {
    const market = g.market_label || marketScopeText(canonical);
    const target = targetText(canonical);
    const conditions = Array.isArray(canonical.conditions) ? canonical.conditions.map(conditionText).join(" and ") : "";
    const stake = money(canonical.execution_settings?.stake_amount || state.me?.settings?.stake_amount || 0.5);
    const risk = `TP ${money(canonical.execution_settings?.take_profit || 0)} / SL ${money(canonical.execution_settings?.stop_loss || 0)}`;
    return `I created ${target} on ${market}${conditions ? ` when ${conditions}` : ""}. Stake ${stake}; ${risk}.`;
  }

  function tradeGroupForSide(side) {
    return TRADE_GROUPS.find((group) => group[2].some(([value]) => value === side)) || TRADE_GROUPS[0];
  }

  function supportedMarkets() {
    const markets = state.custom?.supported?.markets;
    return Array.isArray(markets) && markets.length ? markets : ALL_MARKETS;
  }

  function marketLabel(symbol) {
    const value = String(symbol || "");
    const match1s = value.match(/^1HZ(\d+)V$/);
    if (match1s) return [`Volatility ${match1s[1]} (1s)`, value];
    const matchNormal = value.match(/^R_(\d+)$/);
    if (matchNormal) return [`Volatility ${matchNormal[1]}`, value];
    return [value || "Market", value || ""];
  }

  function conditionParts(conditions) {
    const rows = Array.isArray(conditions) ? conditions : [];
    const digit = rows.find((item) => item?.kind === "digit_compare" || item?.kind === "digit_parity" || item?.source === "last_digit") || {};
    const percentage = rows.find((item) => item?.kind === "percentage") || {};
    const direction = rows.find((item) => item?.kind === "direction") || {};
    return { digit, percentage, direction };
  }

  function modeFromConditions(digit, percentage) {
    return percentage?.kind ? (digit?.kind || digit?.source ? "combined" : "percentage") : "last_digit";
  }

  function normalizeRoute(raw, base = {}) {
    const source = raw && typeof raw === "object" ? raw : {};
    const route = {
      tradeType: source.tradeType || source.trade_type || base.side || "over",
      prediction: whole(source.prediction ?? base.prediction ?? 3, 3, 0, 9),
      durationTicks: whole(source.durationTicks ?? source.duration_ticks ?? base.ticks ?? 1, 1, 1, 100),
      analysisMode: source.analysisMode || base.strategyMode || "last_digit",
      lastRule: { ...(base.lastRule || { window: 5, operator: ">=", value: 3 }), ...(source.lastRule || {}) },
      percentageRule: { ...(base.percentageRule || { target: "even", value: 0, window: 500, operator: ">=", threshold: 70 }), ...(source.percentageRule || {}) },
      tickDirectionRule: { ...(base.tickDirectionRule || { enabled: false, window: 3, direction: "rising" }), ...(source.tickDirectionRule || {}) },
    };
    const group = tradeGroupForSide(route.tradeType);
    route.tradeType = group[2].some(([value]) => value === route.tradeType) ? route.tradeType : "over";
    route.analysisMode = ["last_digit", "percentage", "combined"].includes(route.analysisMode) ? route.analysisMode : "last_digit";
    route.lastRule.window = whole(route.lastRule.window, 5, 1, 1000);
    route.lastRule.operator = BUILDER_COMPARATORS.some(([value]) => value === route.lastRule.operator) ? route.lastRule.operator : ">=";
    route.lastRule.value = whole(route.lastRule.value, 3, 0, 9);
    route.percentageRule.target = PERCENTAGE_TARGETS.some(([value]) => value === route.percentageRule.target) ? route.percentageRule.target : "even";
    route.percentageRule.value = whole(route.percentageRule.value, 0, 0, 9);
    route.percentageRule.window = whole(route.percentageRule.window, 500, 1, 1000);
    route.percentageRule.operator = BUILDER_NUMERIC_COMPARATORS.some(([value]) => value === route.percentageRule.operator) ? route.percentageRule.operator : ">=";
    route.percentageRule.threshold = clamp(num(route.percentageRule.threshold, 70), 0, 100);
    route.tickDirectionRule.enabled = Boolean(route.tickDirectionRule.enabled);
    route.tickDirectionRule.window = whole(route.tickDirectionRule.window, 3, 1, 1000);
    route.tickDirectionRule.direction = TICK_DIRECTIONS.some(([value]) => value === route.tickDirectionRule.direction) ? route.tickDirectionRule.direction : "rising";
    return route;
  }

  function routeFromServer(route, base = {}) {
    if (!route || typeof route !== "object") return null;
    const parts = conditionParts(route.conditions || []);
    const lastOperator = parts.digit.kind === "digit_parity" ? (parts.digit.parity === "odd" ? "all_odd" : "all_even") : parts.digit.operator;
    return normalizeRoute({
      tradeType: route.trade_type,
      prediction: route.prediction,
      durationTicks: route.duration_ticks,
      analysisMode: modeFromConditions(parts.digit, parts.percentage),
      lastRule: { window: parts.digit.window ?? 5, operator: lastOperator || ">=", value: parts.digit.value ?? 3 },
      percentageRule: { target: parts.percentage.target || "even", value: parts.percentage.value ?? 0, window: parts.percentage.window ?? 500, operator: parts.percentage.operator || ">=", threshold: parts.percentage.threshold ?? 70 },
      tickDirectionRule: { enabled: Boolean(parts.direction.kind), window: parts.direction.window ?? 3, direction: parts.direction.direction || "rising" },
    }, base);
  }

  function resultRouteConditions(route) {
    const conditions = [];
    if (route.analysisMode === "last_digit" || route.analysisMode === "combined") {
      conditions.push({
        kind: "digit_compare",
        source: "last_digit",
        window: route.lastRule.window,
        operator: route.lastRule.operator,
        value: ["all_same", "all_even", "all_odd"].includes(route.lastRule.operator) ? null : route.lastRule.value,
      });
    }
    if (route.analysisMode === "percentage" || route.analysisMode === "combined") {
      const percentage = { kind: "percentage", window: route.percentageRule.window, target: route.percentageRule.target, operator: route.percentageRule.operator, threshold: route.percentageRule.threshold };
      if (["over", "under", "digit"].includes(route.percentageRule.target)) percentage.value = route.percentageRule.value;
      conditions.push(percentage);
    }
    if (route.tickDirectionRule.enabled) conditions.push({ kind: "direction", window: route.tickDirectionRule.window, direction: route.tickDirectionRule.direction });
    return conditions.length ? conditions : [{ kind: "digit_compare", source: "last_digit", window: 1, operator: ">=", value: 0 }];
  }

  function normalizeBuilderDraft(source = {}) {
    const custom = source.strategy || source.config || (Array.isArray(source.conditions) ? source : null) || state.custom?.config || state.custom?.custom_strategy || state.custom?.strategy || {};
    const conditions = Array.isArray(custom.conditions) ? custom.conditions : [];
    const { digit, percentage, direction } = conditionParts(conditions);
    const side = source.side || source.trade?.side || custom.trade_type || "over";
    const group = source.tradeGroup || source.trade?.group || tradeGroupForSide(side)[0];
    const strategyMode = source.strategyMode || modeFromConditions(digit, percentage);
    const settings = state.me?.settings || {};
    const execution = custom.execution_settings || {};
    const supported = supportedMarkets();
    const rawMarkets = Array.isArray(source.markets) ? source.markets : Array.isArray(custom.markets) ? custom.markets : [];
    const chosenMarkets = rawMarkets.filter((market) => supported.includes(market));
    const rawMode = source.marketMode || source.market_mode || custom.market_mode || (chosenMarkets.length > 1 ? "selected" : "all");
    const marketMode = rawMode === "all" ? "all" : "selected";
    const market = source.market || source.oneMarket || chosenMarkets[0] || custom.markets?.[0] || "1HZ100V";
    const customMartingale = custom.martingale || state.custom?.martingale || {};
    const virtualHook = source.virtualHook || {};
    const draftBase = {
      name: source.name || custom.name || "Risk Managers",
      lockedName: Boolean(source.lockedName || source.nameLocked),
      market,
      marketMode,
      markets: marketMode === "all" ? [...supported] : (chosenMarkets.length ? chosenMarkets : [market].filter(Boolean)),
      strategyMode: ["last_digit", "percentage", "combined"].includes(strategyMode) ? strategyMode : "combined",
      tradeGroup: group,
      side,
      prediction: whole(source.prediction ?? source.trade?.prediction ?? custom.prediction ?? 3, 3, 0, 9),
      ticks: whole(source.ticks ?? source.money?.ticks ?? custom.duration_ticks ?? 1, 1, 1, 100),
      lastRule: {
        window: whole(source.lastRule?.window ?? source.window ?? digit.window ?? 1, 1, 1, 1000),
        operator: source.lastRule?.operator || source.operator || digit.operator || ">=",
        value: whole(source.lastRule?.value ?? source.value ?? digit.value ?? 3, 3, 0, 9),
      },
      percentageRule: {
        target: source.percentageRule?.target || percentage.target || "even",
        value: whole(source.percentageRule?.value ?? percentage.value ?? 5, 5, 0, 9),
        window: whole(source.percentageRule?.window ?? percentage.window ?? 500, 500, 1, 1000),
        operator: source.percentageRule?.operator || percentage.operator || ">=",
        threshold: clamp(num(source.percentageRule?.threshold ?? percentage.threshold ?? 70, 70), 0, 100),
      },
      tickDirectionRule: {
        enabled: Boolean(source.tickDirectionRule?.enabled ?? direction.kind),
        window: whole(source.tickDirectionRule?.window ?? direction.window ?? 3, 3, 1, 1000),
        direction: source.tickDirectionRule?.direction || direction.direction || "rising",
      },
      reanalyze: {
        mode: source.reanalyze?.mode || custom.reanalyze?.mode || "custom",
        losses: whole(source.reanalyze?.losses ?? custom.reanalyze?.losses ?? 2, 2, 1, 50),
        wins: whole(source.reanalyze?.wins ?? custom.reanalyze?.wins ?? 3, 3, 1, 50),
      },
      money: {
        stake: num(source.money?.stake ?? source.stake ?? execution.stake_amount ?? settings.stake_amount ?? 0.5, 0.5),
        takeProfit: num(source.money?.takeProfit ?? source.takeProfit ?? execution.take_profit ?? settings.take_profit ?? 2, 2),
        stopLoss: Math.abs(num(source.money?.stopLoss ?? source.stopLoss ?? execution.stop_loss ?? settings.stop_loss ?? 3, 3)),
        martingale: num(source.money?.martingale ?? customMartingale.multiplier ?? 2, 2),
        recoveryMode: ["system", "multiplier", "split"].includes(source.money?.recoveryMode || customMartingale.mode) ? (source.money?.recoveryMode || customMartingale.mode) : "system",
        splitCount: whole(source.money?.splitCount ?? customMartingale.split_count ?? 2, 2, 1, 3),
      },
      virtualHook: {
        enabled: Boolean(virtualHook.enabled ?? source.virtual ?? custom.virtual_hook?.enabled ?? custom.virtual_hook_enabled ?? true),
        enterAfterLosses: whole(virtualHook.enterAfterLosses ?? source.enterLosses ?? custom.virtual_hook?.enter_after_losses ?? 2, 2, 1, 50),
        exitAfterConsecutiveWins: whole(virtualHook.exitAfterConsecutiveWins ?? source.exitWins ?? custom.virtual_hook?.exit_after_consecutive_wins ?? 1, 1, 1, 50),
      },
    };
    const resultRaw = source.resultRouting || source.result || source.result_routing || custom.result_routing || {};
    const serverRoute = resultRaw.after_loss ? routeFromServer(resultRaw.after_loss, draftBase) : null;
    const afterLoss = resultRaw.afterLoss ? normalizeRoute(resultRaw.afterLoss, draftBase) : serverRoute;
    draftBase.resultRouting = {
      enabled: Boolean(resultRaw.enabled ?? resultRaw.routingEnabled ?? afterLoss),
      afterLoss,
    };
    return draftBase;
  }

  function currentBuilderConfig() {
    return normalizeBuilderDraft(state.selectedStrategy?.builder || {});
  }

  function builderTemplatesSection() {
    const saved = savedTemplates();
    const savedNames = new Set(saved.map((item) => strategyNameKey(item.name || item.builder?.name || item.id)));
    const built = BUILDER_TEMPLATES.filter((item) => !savedNames.has(strategyNameKey(item.name)));
    const selectedKey = `${state.selectedStrategy?.source || ""}:${state.selectedStrategy?.id || ""}`;
    return `<section class="builder-template-section">
      <label class="compact-select"><span>Template</span><select data-builder-template-select>
        <option value="">Choose a template</option>
        ${saved.length ? `<optgroup label="Local bots">${saved.map((item) => templateOption("local", item.id, item.name || "Saved Strategy", selectedKey)).join("")}</optgroup>` : ""}
        <optgroup label="Built-in bots">${built.map((item) => templateOption("built", item.id, item.name, selectedKey)).join("")}</optgroup>
      </select></label>
    </section>`;
  }

  function templateOption(source, id, name, selectedKey) {
    const value = `${source}:${id}`;
    return `<option value="${esc(value)}" ${selectedKey === value ? "selected" : ""}>${esc(name)}</option>`;
  }

  function builderInput(path, fallback) {
    const field = root.querySelector(`[data-builder="${path}"]`);
    if (!field) return fallback;
    if (field.type === "radio") return root.querySelector(`[data-builder="${path}"]:checked`)?.value || fallback;
    return field.type === "checkbox" ? field.checked : field.value;
  }

  function resultRouteInput(path, fallback) {
    const field = root.querySelector(`[data-result-route="${path}"]`);
    if (!field) return fallback;
    return field.type === "checkbox" ? field.checked : field.value;
  }

  function resultRouteFromDom(base) {
    const current = normalizeRoute(base.resultRouting?.afterLoss || {}, base);
    return normalizeRoute({
      tradeType: resultRouteInput("tradeType", current.tradeType),
      prediction: resultRouteInput("prediction", current.prediction),
      durationTicks: resultRouteInput("durationTicks", current.durationTicks),
      analysisMode: resultRouteInput("analysisMode", current.analysisMode),
      lastRule: {
        window: resultRouteInput("lastRule.window", current.lastRule.window),
        operator: resultRouteInput("lastRule.operator", current.lastRule.operator),
        value: resultRouteInput("lastRule.value", current.lastRule.value),
      },
      percentageRule: {
        target: resultRouteInput("percentageRule.target", current.percentageRule.target),
        value: resultRouteInput("percentageRule.value", current.percentageRule.value),
        window: resultRouteInput("percentageRule.window", current.percentageRule.window),
        operator: resultRouteInput("percentageRule.operator", current.percentageRule.operator),
        threshold: resultRouteInput("percentageRule.threshold", current.percentageRule.threshold),
      },
      tickDirectionRule: {
        enabled: resultRouteInput("tickDirectionRule.enabled", current.tickDirectionRule.enabled),
        window: resultRouteInput("tickDirectionRule.window", current.tickDirectionRule.window),
        direction: resultRouteInput("tickDirectionRule.direction", current.tickDirectionRule.direction),
      },
    }, base);
  }

  function builderDraftFromDom(overrides = {}) {
    const base = normalizeBuilderDraft(state.selectedStrategy?.builder || currentBuilderConfig());
    const tradeGroupButton = root.querySelector("[data-trade-group].active");
    const marketMode = root.querySelector("[data-builder-market-mode-select]")?.value || root.querySelector("[data-builder-market-mode].active")?.dataset.builderMarketMode || base.marketMode;
    const selectedMarkets = Array.from(root.querySelectorAll("[data-builder-market]:checked")).map((field) => field.value).filter(Boolean);
    const nextMarkets = marketMode === "all" ? supportedMarkets() : selectedMarkets;
    const draft = normalizeBuilderDraft({
      ...base,
      name: base.lockedName ? base.name : (document.getElementById("b-name")?.value?.trim() || base.name),
      lockedName: base.lockedName,
      marketMode,
      markets: nextMarkets,
      market: nextMarkets[0] || base.market,
      strategyMode: root.querySelector("[data-builder-mode].active")?.dataset.builderMode || base.strategyMode,
      tradeGroup: tradeGroupButton?.dataset.tradeGroup || base.tradeGroup,
      side: builderInput("trade.side", base.side),
      prediction: builderInput("trade.prediction", base.prediction),
      ticks: builderInput("money.ticks", base.ticks),
      lastRule: {
        window: builderInput("lastRule.window", base.lastRule.window),
        operator: builderInput("lastRule.operator", base.lastRule.operator),
        value: builderInput("lastRule.value", base.lastRule.value),
      },
      percentageRule: {
        target: builderInput("percentageRule.target", base.percentageRule.target),
        value: builderInput("percentageRule.value", base.percentageRule.value),
        window: builderInput("percentageRule.window", base.percentageRule.window),
        operator: builderInput("percentageRule.operator", base.percentageRule.operator),
        threshold: builderInput("percentageRule.threshold", base.percentageRule.threshold),
      },
      tickDirectionRule: {
        enabled: builderInput("tickDirectionRule.enabled", base.tickDirectionRule.enabled),
        window: builderInput("tickDirectionRule.window", base.tickDirectionRule.window),
        direction: builderInput("tickDirectionRule.direction", base.tickDirectionRule.direction),
      },
      reanalyze: {
        mode: builderInput("reanalyze.mode", base.reanalyze.mode),
        losses: builderInput("reanalyze.losses", base.reanalyze.losses),
        wins: builderInput("reanalyze.wins", base.reanalyze.wins),
      },
      money: {
        stake: builderInput("money.stake", base.money.stake),
        takeProfit: builderInput("money.takeProfit", base.money.takeProfit),
        stopLoss: builderInput("money.stopLoss", base.money.stopLoss),
        martingale: builderInput("money.martingale", base.money.martingale),
        recoveryMode: builderInput("money.recoveryMode", base.money.recoveryMode),
        splitCount: builderInput("money.splitCount", base.money.splitCount),
      },
      virtualHook: {
        enabled: builderInput("virtualHook.enabled", base.virtualHook.enabled),
        enterAfterLosses: builderInput("virtualHook.enterAfterLosses", base.virtualHook.enterAfterLosses),
        exitAfterConsecutiveWins: builderInput("virtualHook.exitAfterConsecutiveWins", base.virtualHook.exitAfterConsecutiveWins),
      },
      resultRouting: {
        enabled: builderInput("resultRouting.enabled", base.resultRouting?.enabled),
        afterLoss: resultRouteFromDom(base),
      },
      ...overrides,
    });
    return draft;
  }

  function marketBlock(b) {
    const markets = supportedMarkets();
    const selected = new Set(b.marketMode === "all" ? markets : (b.markets || []));
    return `<section class="builder-section market-section"><div class="section-number">01</div><div><span class="eyebrow">MARKETS</span><h3>${b.marketMode === "all" ? "All supported markets." : `${selected.size} selected.`}</h3></div></section>
      <div class="builder-market-shell compact">
        <label class="compact-select"><span>Market scope</span><select data-builder-market-mode-select data-builder-live>${optionList(BUILDER_MARKET_MODES, b.marketMode)}</select></label>
        <details class="builder-market-dropdown">
          <summary>${b.marketMode === "all" ? `All ${markets.length} supported markets` : `${selected.size || 1} selected market${selected.size === 1 ? "" : "s"}`}</summary>
          <div class="builder-market-grid compact">${markets.map((symbol) => {
          const [label, code] = marketLabel(symbol);
          const checked = selected.has(symbol);
          return `<label class="builder-market-card ${checked ? "selected" : ""} ${b.marketMode === "all" ? "readonly" : ""}">
            <input data-builder-market type="checkbox" value="${esc(symbol)}" ${checked ? "checked" : ""} ${b.marketMode === "all" ? "disabled" : ""}>
            <span>${checked ? miniIcon("check") : ""}</span><b>${esc(label)}</b><small>${esc(code)}</small>
          </label>`;
        }).join("")}</div>
        </details>
      </div>`;
  }

  function modeCards(b) {
    return `<section class="builder-section mode-section"><div class="section-number">02</div><div><span class="eyebrow">STRATEGY MODE</span><h3>Choose condition blocks.</h3></div></section>
      <div class="builder-mode-grid">${BUILDER_MODES.map(([value, label, symbol, caption]) => `<button type="button" data-builder-mode="${value}" class="mode-card ${b.strategyMode === value ? "active" : ""}"><span>${esc(symbol)}</span><b>${esc(label)}</b><small>${esc(caption)}</small></button>`).join("")}</div>`;
  }

  function lastDigitBlock(b) {
    const hideValue = ["all_same", "all_even", "all_odd"].includes(b.lastRule.operator);
    return `<div class="rule-card digit-rule"><div class="rule-title"><span>9</span><strong>Last Digit Rule</strong></div>
      <div class="form-grid three"><label><span>Check last N digits</span><input data-builder="lastRule.window" type="number" min="1" max="1000" step="1" value="${esc(b.lastRule.window)}"></label>
      <label><span>Comparison</span><select data-builder="lastRule.operator" data-builder-live>${optionList(BUILDER_COMPARATORS, b.lastRule.operator)}</select></label>
      ${hideValue ? `<div class="info-box">This comparison does not require a value.</div>` : `<label><span>Value</span><input data-builder="lastRule.value" type="number" min="0" max="9" step="1" value="${esc(b.lastRule.value)}"></label>`}</div>
    </div>`;
  }

  function percentageBlock(b) {
    const needsDigit = ["over", "under", "digit"].includes(b.percentageRule.target);
    return `<div class="rule-card percentage-rule"><div class="rule-title"><span>%</span><strong>Percentage Rule</strong></div>
      <div class="form-grid ${needsDigit ? "three" : "two"}"><label><span>Check percentage of</span><select data-builder="percentageRule.target" data-builder-live>${optionList(PERCENTAGE_TARGETS, b.percentageRule.target)}</select></label>
      ${needsDigit ? `<label><span>Digit</span><input data-builder="percentageRule.value" type="number" min="0" max="9" step="1" value="${esc(b.percentageRule.value)}"></label>` : ""}
      <label><span>Past ticks</span><input data-builder="percentageRule.window" type="number" min="1" max="1000" step="1" value="${esc(b.percentageRule.window)}"></label>
      <label><span>Comparison</span><select data-builder="percentageRule.operator">${optionList(BUILDER_NUMERIC_COMPARATORS, b.percentageRule.operator)}</select></label>
      <label><span>Threshold (%)</span><input data-builder="percentageRule.threshold" type="number" min="0" max="100" step="0.1" value="${esc(b.percentageRule.threshold)}"></label></div>
    </div>`;
  }

  function tickDirectionBlock(b) {
    return `<div class="rule-card tick-direction-rule"><div class="rule-title"><span>D</span><strong>Last Tick Direction</strong></div>
      <label class="toggle-line"><input data-builder="tickDirectionRule.enabled" data-builder-live type="checkbox" ${b.tickDirectionRule.enabled ? "checked" : ""}><span><b>${b.tickDirectionRule.enabled ? "Enabled" : "Optional"}</b><small>Require recent tick direction before entry.</small></span></label>
      ${b.tickDirectionRule.enabled ? `<div class="form-grid two"><label><span>Check last</span><input data-builder="tickDirectionRule.window" type="number" min="1" max="1000" step="1" value="${esc(b.tickDirectionRule.window)}"></label><label><span>Direction</span><select data-builder="tickDirectionRule.direction">${optionList(TICK_DIRECTIONS, b.tickDirectionRule.direction)}</select></label></div>` : ""}
    </div>`;
  }

  function conditionBuilder(b) {
    return `<section class="builder-section"><div class="section-number">03</div><div><span class="eyebrow">CONDITION BUILDER</span><h3>${esc(BUILDER_MODES.find(([value]) => value === b.strategyMode)?.[1] || "Combined")} mode.</h3></div></section>
      <div class="rules-stack">
        ${b.strategyMode === "last_digit" || b.strategyMode === "combined" ? lastDigitBlock(b) : ""}
        ${b.strategyMode === "percentage" || b.strategyMode === "combined" ? percentageBlock(b) : ""}
        ${tickDirectionBlock(b)}
      </div>`;
  }

  function tradeBuilder(b) {
    const group = TRADE_GROUPS.find(([value]) => value === b.tradeGroup) || tradeGroupForSide(b.side);
    const sideOptions = group[2];
    const side = sideOptions.some(([value]) => value === b.side) ? b.side : sideOptions[0][0];
    return `<section class="builder-section"><div class="section-number">04</div><div><span class="eyebrow">CONTRACT</span><h3>What should be purchased?</h3></div></section>
      <div class="trade-tabs">${TRADE_GROUPS.map(([value, label]) => `<button type="button" data-trade-group="${value}" class="${group[0] === value ? "active" : ""}">${esc(label)}</button>`).join("")}</div>
      <div class="form-grid three"><label><span>Contract side</span><select data-builder="trade.side" data-builder-live>${optionList(sideOptions, side)}</select></label>
      ${group[3] ? `<label><span>Prediction / barrier</span><input data-builder="trade.prediction" type="number" min="0" max="9" step="1" value="${esc(b.prediction)}"></label>` : `<div class="info-box">This contract group does not need a prediction.</div>`}
      <label><span>Duration</span><input data-builder="money.ticks" type="number" min="1" max="100" step="1" value="${esc(b.ticks)}"></label></div>`;
  }

  function reanalyzeBlock(b) {
    return `<section class="builder-section"><div class="section-number">05</div><div><span class="eyebrow">RE-ANALYZE</span><h3>When should fresh analysis run?</h3></div></section>
      <div class="form-grid three"><label><span>Re-analyze</span><select data-builder="reanalyze.mode" data-builder-live>${optionList([["after_every_trade", "After every trade"], ["after_loss", "After N losses"], ["after_win", "After N wins"], ["custom", "Custom"]], b.reanalyze.mode)}</select></label>
      ${b.reanalyze.mode === "after_every_trade" ? `<div class="info-box">Fresh analysis after every settled trade.</div>` : ""}
      ${["after_loss", "custom"].includes(b.reanalyze.mode) ? `<label><span>After losses</span><input data-builder="reanalyze.losses" type="number" min="1" max="50" step="1" value="${esc(b.reanalyze.losses)}"></label>` : ""}
      ${["after_win", "custom"].includes(b.reanalyze.mode) ? `<label><span>After wins</span><input data-builder="reanalyze.wins" type="number" min="1" max="50" step="1" value="${esc(b.reanalyze.wins)}"></label>` : ""}</div>`;
  }

  function moneyBlock(b) {
    return `<section class="builder-section"><div class="section-number">06</div><div><span class="eyebrow">MONEY MANAGEMENT</span><h3>Stake, profit, loss and recovery.</h3></div></section>
      <div class="form-grid three"><label><span>Stake USD</span><input data-builder="money.stake" type="number" min="0.35" step="0.01" value="${esc(b.money.stake)}"></label>
      <label><span>Take profit</span><input data-builder="money.takeProfit" type="number" min="0" step="0.01" value="${esc(b.money.takeProfit)}"></label>
      <label><span>Stop loss</span><input data-builder="money.stopLoss" type="number" min="0" step="0.01" value="${esc(b.money.stopLoss)}"></label></div>
      <div class="recovery-mode-grid">${RECOVERY_MODES.map(([value, label, caption]) => `<label class="recovery-card ${b.money.recoveryMode === value ? "active" : ""}"><input data-builder="money.recoveryMode" data-builder-live type="radio" name="builder-recovery-mode" value="${value}" ${b.money.recoveryMode === value ? "checked" : ""}><span>${esc(value.toUpperCase())}</span><b>${esc(label)}</b><small>${esc(caption)}</small></label>`).join("")}</div>
      ${b.money.recoveryMode === "multiplier" ? `<div class="form-grid two recovery-detail"><label><span>Multiplier</span><input data-builder="money.martingale" type="number" min="1.1" max="10" step="0.1" value="${esc(b.money.martingale)}"></label><div class="info-box">Custom multiplier recovery is sent to the backend martingale settings.</div></div>` : ""}
      ${b.money.recoveryMode === "split" ? `<div class="form-grid two recovery-detail"><label><span>Successful recovery parts</span><select data-builder="money.splitCount" data-builder-live>${optionList([[1, "1 part"], [2, "2 parts"], [3, "3 parts"]], b.money.splitCount)}</select></label><div class="info-box">Exact remaining loss is recovered across the selected winning recovery parts.</div></div>` : ""}
      ${b.money.recoveryMode === "system" ? `<div class="info-box recovery-detail">System Martingale uses the backend exact-debt recovery plan.</div>` : ""}`;
  }

  function resultRoutingBlock(b) {
    const route = normalizeRoute(b.resultRouting?.afterLoss || {}, b);
    const predictionHidden = !tradeGroupForSide(route.tradeType)[3];
    const lastValueHidden = ["all_same", "all_even", "all_odd"].includes(route.lastRule.operator);
    const percentageValueHidden = !["over", "under", "digit"].includes(route.percentageRule.target);
    const showLast = route.analysisMode === "last_digit" || route.analysisMode === "combined";
    const showPercentage = route.analysisMode === "percentage" || route.analysisMode === "combined";
    return `<section class="builder-section"><div class="section-number">07</div><div><span class="eyebrow">AFTER LOSS</span><h3>Optional recovery strategy switch.</h3></div></section>
      <label class="toggle-line result-routing-toggle"><input data-builder="resultRouting.enabled" data-builder-live type="checkbox" ${b.resultRouting?.enabled ? "checked" : ""}><span><b>Use a different strategy after a loss</b><small>First trade uses the primary strategy. Actual-loss recovery can wait for this independent route.</small></span></label>
      <div class="result-routing-box ${b.resultRouting?.enabled ? "" : "collapsed"}">
        <div class="form-grid four">
          <label><span>Trade after loss</span><select data-result-route="tradeType" data-result-live>${optionList(TRADE_GROUPS.flatMap((group) => group[2]), route.tradeType)}</select></label>
          ${predictionHidden ? `<div class="info-box">No prediction needed.</div>` : `<label><span>Prediction</span><input data-result-route="prediction" type="number" min="0" max="9" step="1" value="${esc(route.prediction)}"></label>`}
          <label><span>Ticks</span><input data-result-route="durationTicks" type="number" min="1" max="100" step="1" value="${esc(route.durationTicks)}"></label>
          <label><span>Analysis mode</span><select data-result-route="analysisMode" data-result-live>${optionList(BUILDER_MODES.map(([value, label]) => [value, label]), route.analysisMode)}</select></label>
        </div>
        ${showLast ? `<div class="rule-card"><div class="rule-title"><span>9</span><strong>After-loss Last Digit</strong></div><div class="form-grid three">
          <label><span>Check last</span><input data-result-route="lastRule.window" type="number" min="1" max="1000" step="1" value="${esc(route.lastRule.window)}"></label>
          <label><span>Comparison</span><select data-result-route="lastRule.operator" data-result-live>${optionList(BUILDER_COMPARATORS, route.lastRule.operator)}</select></label>
          ${lastValueHidden ? `<div class="info-box">This comparison does not require a value.</div>` : `<label><span>Value</span><input data-result-route="lastRule.value" type="number" min="0" max="9" step="1" value="${esc(route.lastRule.value)}"></label>`}
        </div></div>` : ""}
        ${showPercentage ? `<div class="rule-card"><div class="rule-title"><span>%</span><strong>After-loss Percentage</strong></div><div class="form-grid ${percentageValueHidden ? "three" : "four"}">
          <label><span>Check percentage of</span><select data-result-route="percentageRule.target" data-result-live>${optionList(PERCENTAGE_TARGETS, route.percentageRule.target)}</select></label>
          ${percentageValueHidden ? "" : `<label><span>Digit</span><input data-result-route="percentageRule.value" type="number" min="0" max="9" step="1" value="${esc(route.percentageRule.value)}"></label>`}
          <label><span>Past ticks</span><input data-result-route="percentageRule.window" type="number" min="1" max="1000" step="1" value="${esc(route.percentageRule.window)}"></label>
          <label><span>Threshold</span><input data-result-route="percentageRule.threshold" type="number" min="0" max="100" step="0.1" value="${esc(route.percentageRule.threshold)}"></label>
          <label><span>Comparison</span><select data-result-route="percentageRule.operator">${optionList(BUILDER_NUMERIC_COMPARATORS, route.percentageRule.operator)}</select></label>
        </div></div>` : ""}
        <div class="rule-card"><div class="rule-title"><span>D</span><strong>Optional Tick Direction</strong></div>
          <label class="toggle-line"><input data-result-route="tickDirectionRule.enabled" data-result-live type="checkbox" ${route.tickDirectionRule.enabled ? "checked" : ""}><span><b>${route.tickDirectionRule.enabled ? "Enabled" : "Optional"}</b><small>Require a direction before the after-loss entry.</small></span></label>
          ${route.tickDirectionRule.enabled ? `<div class="form-grid two"><label><span>Check last</span><input data-result-route="tickDirectionRule.window" type="number" min="1" max="1000" step="1" value="${esc(route.tickDirectionRule.window)}"></label><label><span>Direction</span><select data-result-route="tickDirectionRule.direction">${optionList(TICK_DIRECTIONS, route.tickDirectionRule.direction)}</select></label></div>` : ""}
        </div>
      </div>`;
  }

  function virtualHookBlock(b) {
    return `<section class="builder-section"><div class="section-number">08</div><div><span class="eyebrow">VIRTUAL HOOK</span><h3>Zero-cost protection mode.</h3></div></section>
      <label class="toggle-line"><input data-builder="virtualHook.enabled" data-builder-live type="checkbox" ${b.virtualHook.enabled ? "checked" : ""}><span><b>${b.virtualHook.enabled ? "Virtual Hook ON" : "Virtual Hook OFF"}</b><small>Enter virtual mode after actual losses and return after virtual wins.</small></span></label>
      ${b.virtualHook.enabled ? `<div class="form-grid two"><label><span>Enter after losses</span><input data-builder="virtualHook.enterAfterLosses" type="number" min="1" max="50" step="1" value="${esc(b.virtualHook.enterAfterLosses)}"></label><label><span>Leave after consecutive wins</span><input data-builder="virtualHook.exitAfterConsecutiveWins" type="number" min="1" max="50" step="1" value="${esc(b.virtualHook.exitAfterConsecutiveWins)}"></label></div>` : ""}`;
  }

  function builderSummaryText(b) {
    const parts = [];
    if (b.strategyMode === "last_digit" || b.strategyMode === "combined") {
      const last = ["all_same", "all_even", "all_odd"].includes(b.lastRule.operator)
        ? `last ${b.lastRule.window} digits are ${b.lastRule.operator.replaceAll("_", " ")}`
        : `last ${b.lastRule.window} digits ${b.lastRule.operator} ${b.lastRule.value}`;
      parts.push(last);
    }
    if (b.strategyMode === "percentage" || b.strategyMode === "combined") {
      const target = ["over", "under", "digit"].includes(b.percentageRule.target) ? `${b.percentageRule.target} ${b.percentageRule.value}` : b.percentageRule.target;
      parts.push(`${target} percentage over ${b.percentageRule.window} ticks ${b.percentageRule.operator} ${b.percentageRule.threshold}%`);
    }
    if (b.tickDirectionRule.enabled) parts.push(`${b.tickDirectionRule.direction.replaceAll("_", " ")} over ${b.tickDirectionRule.window} ticks`);
    const group = TRADE_GROUPS.find(([value]) => value === b.tradeGroup) || tradeGroupForSide(b.side);
    const tradeLabel = group[2].find(([value]) => value === b.side)?.[1] || b.side;
    const prediction = group[3] ? ` ${b.prediction}` : "";
    const marketText = b.marketMode === "all" ? `all ${supportedMarkets().length} supported markets` : `${(b.markets || []).length || 1} selected market${(b.markets || []).length === 1 ? "" : "s"}`;
    const recoveryText = b.money.recoveryMode === "split" ? `split recovery in ${b.money.splitCount} part${b.money.splitCount === 1 ? "" : "s"}` : b.money.recoveryMode === "multiplier" ? `custom multiplier x${Number(b.money.martingale || 1).toFixed(2)}` : "system martingale";
    const afterLoss = b.resultRouting?.enabled ? " After a loss, switch to the configured recovery strategy." : "";
    return `When ${parts.join(" AND ") || "conditions qualify"}, trade ${tradeLabel}${prediction} on ${marketText}. Recovery uses ${recoveryText}. Virtual Hook ${b.virtualHook.enabled ? "enters after " + b.virtualHook.enterAfterLosses + " losses" : "is off"}.${afterLoss}`;
  }

  function builderPage() {
    const b = currentBuilderConfig();
    const content = `<section class="builder-layout restored-builder">
      <article class="panel builder-panel">
        <div class="form-grid one"><label><span>Strategy name</span><input id="b-name" value="${esc(b.name)}" ${b.lockedName ? "readonly" : ""}></label></div>
        ${builderTemplatesSection()}
        ${marketBlock(b)}
        ${modeCards(b)}
        ${conditionBuilder(b)}
        ${tradeBuilder(b)}
        ${reanalyzeBlock(b)}
        ${moneyBlock(b)}
        ${resultRoutingBlock(b)}
        ${virtualHookBlock(b)}
      </article>
      <aside class="panel builder-preview"><span class="eyebrow">LIVE PREVIEW</span><h3>${esc((b.side || "over").toUpperCase())}${["over", "under", "matches", "differs"].includes(b.side) ? " " + esc(b.prediction) : ""}</h3><p>${esc(builderSummaryText(b))}</p><div class="preview-icon">${quill(b.side || "over")}</div><small>Server validator remains execution authority.</small></aside>
    </section>
    <div class="builder-sticky"><button class="btn ghost" data-builder-save>Save Strategy</button><button class="btn secondary" data-builder-schedule>${miniIcon("schedule")} Schedule</button><button class="btn primary" data-builder-trade>${tradeActionMarkup("Trade Now")}</button></div>`;
    return shell(content, { title: "Strategy Builder" });
  }

  function builderPayload() {
    const draft = builderDraftFromDom();
    const group = TRADE_GROUPS.find(([value]) => value === draft.tradeGroup) || tradeGroupForSide(draft.side);
    const markets = draft.marketMode === "all" ? supportedMarkets() : (draft.markets || []).filter((market) => supportedMarkets().includes(market));
    if (draft.marketMode !== "all" && !markets.length) throw new Error("Select at least one market or choose All Markets.");
    const conditions = [];
    if (draft.strategyMode === "last_digit" || draft.strategyMode === "combined") {
      conditions.push({
        kind: "digit_compare",
        source: "last_digit",
        window: draft.lastRule.window,
        operator: draft.lastRule.operator,
        value: ["all_same", "all_even", "all_odd"].includes(draft.lastRule.operator) ? null : draft.lastRule.value,
      });
    }
    if (draft.strategyMode === "percentage" || draft.strategyMode === "combined") {
      const percentage = {
        kind: "percentage",
        window: draft.percentageRule.window,
        target: draft.percentageRule.target,
        operator: draft.percentageRule.operator,
        threshold: draft.percentageRule.threshold,
      };
      if (["over", "under", "digit"].includes(draft.percentageRule.target)) percentage.value = draft.percentageRule.value;
      conditions.push(percentage);
    }
    if (draft.tickDirectionRule.enabled) {
      conditions.push({ kind: "direction", window: draft.tickDirectionRule.window, direction: draft.tickDirectionRule.direction });
    }
    const afterLoss = normalizeRoute(draft.resultRouting?.afterLoss || {}, draft);
    return {
      configured: true,
      name: draft.name,
      market_mode: draft.marketMode === "all" ? "all" : (markets.length === 1 ? "single" : "selected"),
      markets,
      trade_type: draft.side,
      prediction: group[3] ? draft.prediction : null,
      duration_ticks: draft.ticks,
      conditions,
      match: "all",
      reanalyze: draft.reanalyze,
      virtual_hook_enabled: draft.virtualHook.enabled,
      virtual_hook: {
        enabled: draft.virtualHook.enabled,
        enter_after_losses: draft.virtualHook.enterAfterLosses,
        exit_after_consecutive_wins: draft.virtualHook.exitAfterConsecutiveWins,
      },
      martingale: {
        mode: draft.money.recoveryMode,
        multiplier: draft.money.martingale,
        split_count: draft.money.recoveryMode === "split" ? draft.money.splitCount : 1,
      },
      result_routing: {
        enabled: Boolean(draft.resultRouting?.enabled),
        after_loss: draft.resultRouting?.enabled ? {
          trade_type: afterLoss.tradeType,
          prediction: tradeGroupForSide(afterLoss.tradeType)[3] ? afterLoss.prediction : null,
          duration_ticks: afterLoss.durationTicks,
          conditions: resultRouteConditions(afterLoss),
          match: "all",
        } : null,
      },
      execution_settings: {
        stake_amount: draft.money.stake,
        take_profit: draft.money.takeProfit,
        stop_loss: draft.money.stopLoss,
        martingale_enabled: true,
        martingale_multiplier: draft.money.martingale,
      },
    };
  }

  function builderSnapshot() {
    const draft = builderDraftFromDom();
    const payload = builderPayload();
    return {
      id: state.selectedStrategy?.source === "local" ? state.selectedStrategy?.id : undefined,
      name: draft.name || "My Strategy",
      source: state.selectedStrategy?.source === "local" ? "local" : "builder",
      strategy: payload,
      stake: draft.money.stake,
      takeProfit: draft.money.takeProfit,
      stopLoss: draft.money.stopLoss,
      builder: draft,
    };
  }

  function schedulePage() {
    const now = new Date(Date.now() + 10 * 60 * 1000);
    const tz = state.preferences?.timezone || DEFAULT_TZ;
    const localDate = state.scheduleDraft?.date || new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
    const localTime = state.scheduleDraft?.time || new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false }).format(now);
    const selected = state.selectedStrategy || strategyForSchedule();
    const active = (state.schedules?.schedules || []).filter((item) => !["completed", "cancelled", "skipped", "failed"].includes(String(item.status || "").toLowerCase()));
    const content = `<section class="schedule-layout clean-schedule">
      <article class="panel schedule-form">
        <div class="schedule-title"><span class="eyebrow">SCHEDULE</span><h3>${esc(selected.name || "Current Strategy")}</h3></div>
        <div class="form-grid three"><label><span>Date</span><input id="s-date" type="date" value="${esc(localDate)}"></label><label><span>Time</span><input id="s-time" type="time" value="${esc(localTime)}"></label><label><span>Timezone</span><select id="s-timezone">${TIMEZONES.map(([zone, city]) => `<option value="${esc(zone)}" ${zone === (state.scheduleDraft?.timezone || tz) ? "selected" : ""}>${esc(city)}</option>`).join("")}</select></label></div>
        <div class="form-grid three"><label><span>Stake</span><input id="s-stake" type="number" min="0.35" step="0.01" value="${esc(state.scheduleDraft?.stake ?? selected.stake ?? state.me?.settings?.stake_amount ?? 0.5)}"></label><label><span>TP</span><input id="s-tp" type="number" min="0" step="0.01" value="${esc(state.scheduleDraft?.takeProfit ?? selected.takeProfit ?? state.me?.settings?.take_profit ?? 0)}"></label><label><span>SL</span><input id="s-sl" type="number" min="0" step="0.01" value="${esc(state.scheduleDraft?.stopLoss ?? selected.stopLoss ?? state.me?.settings?.stop_loss ?? 0)}"></label></div>
        <div class="schedule-actions"><button class="btn primary" data-create-schedule>${miniIcon("schedule")} Schedule</button><button class="btn ghost" data-trade-now-selected>${tradeActionMarkup("Trade Now")}</button></div>
      </article>
      <aside class="panel upcoming compact-schedules"><div class="panel-title"><div><span class="eyebrow">SCHEDULED TRADES</span><h3>${active.length}</h3></div></div>${active.length ? active.slice(0, 12).map((item) => scheduleRow(item)).join("") : `<div class="empty-mini compact"><p>No scheduled trades.</p></div>`}</aside>
    </section>`;
    return shell(content, { title: "Schedule Trading" });
  }

  function strategyForSchedule() {
    const config = state.custom?.config || state.custom?.custom_strategy || state.custom?.strategy;
    if (config?.configured) return { name: "Current Custom Strategy", source: "saved", strategy: config, stake: state.me?.settings?.stake_amount || .5, takeProfit: state.me?.settings?.take_profit || 0, stopLoss: state.me?.settings?.stop_loss || 0 };
    return {
      name: "Over 3 Starter",
      source: "built-in",
      strategy: {
        market_mode: "single", markets: ["1HZ100V"], trade_type: "over", prediction: 3, duration_ticks: 1,
        conditions: [{ kind: "digit_compare", window: 1, operator: ">=", value: 4 }], match: "all", reanalyze: {},
        virtual_hook_enabled: true, virtual_hook: { enabled: true, enter_after_losses: 2, exit_after_consecutive_wins: 2 },
      },
      stake: .5, takeProfit: 2, stopLoss: 3,
    };
  }

  function scheduleRow(item) {
    const status = String(item.status || "scheduled").toLowerCase();
    const editable = ["scheduled", "waiting", "starting"].includes(status);
    return `<div class="schedule-row compact">
      <div class="schedule-row-actions">${editable ? `<button data-delete-schedule="${esc(item.id)}">Delete</button><button data-edit-schedule="${esc(item.id)}">Edit</button>` : ""}</div>
      <span><b>${esc(item.strategy_name || "Strategy")}</b><small>${esc(item.scheduled_local || item.scheduled_for_utc || "")}</small></span>
      <em>${esc(status)}</em>
    </div>`;
  }

  function scheduleDraftFromItem(item) {
    const snapshot = item?.strategy_snapshot?.custom_strategy || item?.strategy_snapshot || {};
    const dateTime = String(item?.scheduled_local || item?.date_time_local || "").match(/(\d{4}-\d{2}-\d{2}).*?(\d{2}:\d{2})/);
    return {
      selected: {
        name: item?.strategy_name || "Scheduled Strategy",
        source: item?.strategy_source || "scheduled",
        strategy: snapshot,
        stake: item?.stake,
        takeProfit: item?.take_profit,
        stopLoss: item?.stop_loss,
      },
      draft: {
        date: item?.date || dateTime?.[1] || "",
        time: item?.time || dateTime?.[2] || "",
        timezone: item?.timezone || state.preferences?.timezone || DEFAULT_TZ,
        stake: item?.stake,
        takeProfit: item?.take_profit,
        stopLoss: item?.stop_loss,
      },
    };
  }

  function profilePage() {
    const accounts = state.accounts?.accounts || [];
    const tz = state.preferences?.timezone || DEFAULT_TZ;
    const content = `<section class="profile-grid">
      <article class="panel"><div class="panel-title"><div><span class="eyebrow">PROFILE & SETTINGS</span><h3>Linked Options Accounts (${accounts.length})</h3></div></div><div class="account-list">${accounts.map(accountRow).join("")}</div></article>
      <article class="panel"><div class="panel-title"><div><span class="eyebrow">TIMEZONE</span><h3>${esc(tz)}</h3></div><button class="text-button" data-route="timezone">Change</button></div><p class="muted">All future session times are interpreted in this timezone and persisted as UTC.</p></article>
    </section>`;
    return shell(content, { title: "Profile" });
  }

  function accountRow(account) {
    const type = accountType(account);
    return `<button class="account-row ${account.selected ? "selected" : ""}" data-account-id="${esc(account.managed_account_id)}"><span class="account-icon">${iconMarkup(account)}</span><span><b>${esc(account.label || account.account_id_masked)}</b><small>${esc(account.account_id_masked)} · ${type.toUpperCase()}</small></span><span class="account-money"><b>${money(account.balance, account.currency)}</b></span></button>`;
  }

  function contractKey(trade) {
    const type = String(trade.contract_type || trade.type || "").toUpperCase();
    if (type.includes("OVER")) return "over";
    if (type.includes("UNDER")) return "under";
    if (type.includes("MATCH")) return "matches";
    if (type.includes("DIFF")) return "differs";
    if (type.includes("EVEN")) return "even";
    if (type.includes("ODD")) return "odd";
    if (type.includes("CALL") || type.includes("RISE")) return "rise";
    if (type.includes("PUT") || type.includes("FALL")) return "fall";
    return "rise";
  }

  function contractLabel(trade) {
    const key = contractKey(trade);
    const barrier = trade.barrier ?? trade.prediction ?? "";
    if (["over", "under", "matches", "differs"].includes(key) && barrier !== "") return `${key[0].toUpperCase() + key.slice(1)} ${barrier}`;
    return key[0].toUpperCase() + key.slice(1);
  }

  function tradeRows() {
    const rows = state.trades?.trades || [];
    if (!rows.length) return `<div class="run-empty"><span>${miniIcon("trades")}</span><h3>No runs yet</h3><p>When the strategy purchases or mirrors a contract, it appears here live.</p></div>`;
    return rows.map((trade) => {
      const virtual = Boolean(trade.is_virtual);
      const outcome = String(trade.outcome || "OPEN").toUpperCase();
      const profit = Number(trade.profit || 0);
      const stake = Number(trade.stake ?? trade.buy_price ?? 0);
      const entry = trade.entry_tick ?? trade.entry_spot ?? "—";
      const exit = trade.exit_tick ?? trade.exit_spot ?? "—";
      return `<article class="run-row ${virtual ? "virtual" : "actual"}">
        <div class="run-type"><span class="market-glyph">${quill("volatility")}</span><span class="contract-glyph ${contractKey(trade)}">${quill(contractKey(trade))}</span><span><b>${esc(virtual ? "Virtual · " + contractLabel(trade) : contractLabel(trade))}</b><small>${esc(trade.symbol || trade.market || "Deriv Options")}</small></span></div>
        <div class="spots"><span><i class="entry-dot"></i><b>${esc(entry)}</b></span><span><i class="exit-dot"></i><b>${esc(exit)}</b></span></div>
        <div class="run-money"><b>${money(stake, state.me?.currency || "USD")}</b><span class="${outcome === "WIN" || profit > 0 ? "positive" : outcome === "LOSS" || profit < 0 ? "negative" : "muted"}">${virtual ? esc(trade.display_result || outcome) : `${profit >= 0 ? "+" : ""}${money(profit, state.me?.currency || "USD")}`}</span></div>
      </article>`;
    }).join("");
  }

  function runSummary() {
    const rows = (state.trades?.trades || []).filter((row) => !row.is_virtual);
    const totalStake = rows.reduce((sum, row) => sum + Number(row.stake ?? row.buy_price ?? 0), 0);
    const totalPayout = rows.reduce((sum, row) => sum + Number(row.payout || 0), 0);
    const profit = rows.reduce((sum, row) => sum + Number(row.profit || 0), 0);
    const wins = rows.filter((row) => String(row.outcome || "").toUpperCase() === "WIN").length;
    const losses = rows.filter((row) => String(row.outcome || "").toUpperCase() === "LOSS").length;
    const currency = state.me?.currency || "USD";
    return `<section class="run-summary"><article><small>Total stake</small><b>${money(totalStake, currency)}</b></article><article><small>Total payout</small><b>${money(totalPayout, currency)}</b></article><article><small>No. of runs</small><b>${rows.length}</b></article><article><small>Contracts lost</small><b>${losses}</b></article><article><small>Contracts won</small><b>${wins}</b></article><article><small>Total profit/loss</small><b class="${profit >= 0 ? "positive" : "negative"}">${profit >= 0 ? "+" : ""}${money(profit, currency)}</b></article></section>`;
  }

  function runPanelStats() {
    const rows = (state.trades?.trades || []).filter((row) => !row.is_virtual);
    return {
      rows,
      totalStake: rows.reduce((sum, row) => sum + Number(row.stake ?? row.buy_price ?? 0), 0),
      totalPayout: rows.reduce((sum, row) => sum + Number(row.payout || 0), 0),
      profit: rows.reduce((sum, row) => sum + Number(row.profit || 0), 0),
      wins: rows.filter((row) => String(row.outcome || "").toUpperCase() === "WIN").length,
      losses: rows.filter((row) => String(row.outcome || "").toUpperCase() === "LOSS").length,
    };
  }

  function globalRunPanel() {
    const stats = runPanelStats();
    const currency = state.me?.currency || "USD";
    const running = runPanelRunning();
    const activeTab = ["summary", "transactions", "journal"].includes(state.runPanelTab) ? state.runPanelTab : "summary";
    return `<aside class="global-run-panel ${state.runPanelOpen ? "open" : "collapsed"}" aria-label="Run panel">
      <div class="run-panel-sheet">
        <div class="run-panel-top">
          <button class="run-panel-chevron" type="button" data-run-panel-toggle aria-label="${state.runPanelOpen ? "Collapse run panel" : "Expand run panel"}">${miniIcon("arrow")}</button>
          <button class="run-panel-reset" type="button" data-run-reset title="Clear trades" aria-label="Reset run panel and clear trades">Reset</button>
        </div>
        <div class="run-panel-tabs">${runPanelTabs(activeTab)}</div>
        <div class="run-panel-body">${runPanelContent(activeTab, stats, currency, running)}</div>
        <div class="run-panel-stats">${runPanelStatsMarkup(stats, currency)}</div>
      </div>
      <div class="run-panel-bar">
        <button class="run-panel-run" type="button" data-run-start>${runPanelActionMarkup("Run")}</button>
        <div class="run-panel-execution" data-run-panel-toggle><small>Execution</small><b>FAST</b></div>
        <button class="run-panel-switch ${running ? "on" : ""}" type="button" data-run-execution-toggle aria-label="${running ? "Stop trading" : "Start trading"}"><span></span></button>
      </div>
    </aside>`;
  }

  function runPanelRunning() {
    const lifecycle = String(state.lifecycle?.lifecycle || state.lifecycle?.runtime_state || (state.me?.enabled ? "running" : "stopped")).toLowerCase();
    return lifecycle.includes("running") || lifecycle.includes("active");
  }

  function runPanelStrategySource() {
    try {
      if (state.route === "builder" && root.querySelector(".restored-builder")) {
        const draft = builderDraftFromDom();
        return { name: draft.name || "Builder Strategy", payload: builderPayload(), summary: builderSummaryText(draft) };
      }
    } catch (_) {}
    const selected = state.selectedStrategy || {};
    if (selected.strategy?.market_mode) return { name: selected.name || selected.strategy.name || "Selected Strategy", payload: selected.strategy, summary: selected.name || "Selected strategy" };
    const custom = state.custom?.config || state.custom?.custom_strategy || state.custom?.strategy || {};
    if (custom?.market_mode || custom?.configured) return { name: custom.name || "Current Custom Strategy", payload: custom, summary: state.custom?.preview || custom.name || "Current custom strategy" };
    const fallback = strategyForSchedule();
    return { name: fallback.name, payload: fallback.strategy, summary: fallback.name };
  }

  function marketScopeText(payload = {}) {
    const markets = Array.isArray(payload.markets) ? payload.markets : [];
    if (payload.market_mode === "all") return `All ${supportedMarkets().length} supported markets`;
    if (!markets.length) return "No market selected";
    if (markets.length === 1) return markets[0];
    return `${markets.length} selected markets`;
  }

  function targetText(payload = {}) {
    const side = String(payload.trade_type || "over");
    const group = tradeGroupForSide(side);
    const label = group[2].find(([value]) => value === side)?.[1] || side;
    return `${label}${group[3] && payload.prediction != null ? " " + payload.prediction : ""} for ${Number(payload.duration_ticks || 1)} tick${Number(payload.duration_ticks || 1) === 1 ? "" : "s"}`;
  }

  function conditionText(condition = {}) {
    if (condition.kind === "percentage") {
      const target = ["over", "under", "digit"].includes(condition.target) ? `${condition.target} ${condition.value ?? ""}`.trim() : String(condition.target || "percentage");
      return `${target} ${condition.operator || ">="} ${condition.threshold ?? 0}% over ${condition.window || 1} ticks`;
    }
    if (condition.kind === "direction") return `${condition.direction || "rising"} over last ${condition.window || 1} ticks`;
    if (condition.kind === "digit_parity") return `last ${condition.window || 1} digits are ${condition.parity || "even"}`;
    if (["all_same", "all_even", "all_odd"].includes(condition.operator)) return `last ${condition.window || 1} digits are ${String(condition.operator).replaceAll("_", " ")}`;
    return `last ${condition.window || 1} digits ${condition.operator || ">="} ${condition.value ?? 0}`;
  }

  function tradeActionMarkup(label = "Trade Now") {
    const running = runPanelRunning();
    return `${miniIcon(running ? "stop" : "play")} ${running ? "Stop" : esc(label)}`;
  }

  function runPanelActionMarkup(label = "Run") {
    const running = runPanelRunning();
    return `${miniIcon(running ? "stop" : "play")}<span>${running ? "Stop" : esc(label)}</span>`;
  }

  function runPanelTabs(activeTab) {
    return [["summary", "Summary"], ["transactions", "Transactions"], ["journal", "Journal"]]
      .map(([key, label]) => `<button class="${activeTab === key ? "active" : ""}" type="button" data-run-tab="${key}">${label}</button>`)
      .join("");
  }

  function runPanelContent(activeTab, stats, currency, running) {
    if (activeTab === "transactions") return runPanelTransactions(stats.rows, currency);
    if (activeTab === "journal") return runPanelJournal(running, stats);
    return runPanelSummary(stats, currency);
  }

  function runPanelStatsMarkup(stats, currency) {
    return `<article class="run-stat run-stat-stake"><small>Total stake</small><b>${money(stats.totalStake, currency)}</b></article>
      <article class="run-stat run-stat-payout"><small>Total payout</small><b>${money(stats.totalPayout, currency)}</b></article>
      <article class="run-stat run-stat-runs"><small>No. of runs <button type="button" class="run-help" aria-label="About run summary" title="Completed and active contracts in this run">?</button></small><b>${stats.rows.length}</b></article>
      <article class="run-stat run-stat-losses"><small>Contracts lost</small><b>${stats.losses}</b></article>
      <article class="run-stat run-stat-wins"><small>Contracts won</small><b>${stats.wins}</b></article>
      <article class="run-stat run-stat-profit"><small>Total profit/loss</small><b class="${stats.profit > 0 ? "positive" : stats.profit < 0 ? "negative" : ""}">${money(stats.profit, currency)}</b></article>`;
  }

  function runPanelSummary(stats, currency) {
    if (!stats.rows.length) {
      return `<div class="run-panel-empty"><p>When you're ready to trade, hit <b>Run</b>. You'll be able to track your bot's performance here.</p></div>`;
    }
    return `<div class="run-panel-mini-ledger">${stats.rows.slice(0, 5).map((trade) => {
      const profit = Number(trade.profit || 0);
      return `<article><span><b>${esc(contractLabel(trade))}</b><small>${esc(trade.symbol || trade.market || "Deriv Options")}</small></span><strong class="${profit >= 0 ? "positive" : "negative"}">${profit >= 0 ? "+" : ""}${money(profit, currency)}</strong></article>`;
    }).join("")}</div>`;
  }

  function runPanelTransactions(rows, currency) {
    if (!rows.length) return `<div class="run-panel-empty compact"><p>No transactions yet.</p></div>`;
    return transactionTable(rows, currency);
  }

  function transactionTable(rows, currency) {
    return `<div class="transaction-table">
      <div class="transaction-head"><span>Type</span><span>Entry/Exit spot</span><span>Buy price and P/L</span></div>
      <div class="transaction-rows">${rows.slice(0, 80).map((trade) => transactionRow(trade, currency)).join("")}</div>
    </div>`;
  }

  function transactionRow(trade, currency) {
    const profit = Number(trade.profit || 0);
    const stake = Number(trade.stake ?? trade.buy_price ?? trade.price ?? 0);
    const entry = trade.entry_tick ?? trade.entry_spot ?? trade.entrySpot ?? trade.buy_spot ?? "";
    const exit = trade.exit_tick ?? trade.exit_spot ?? trade.exitSpot ?? trade.sell_spot ?? "";
    const key = contractKey(trade);
    return `<article class="transaction-row">
      <div class="transaction-type"><span class="transaction-type-icon ${esc(key)}" aria-hidden="true"></span><b>${esc(contractLabel(trade))}</b></div>
      <div class="transaction-spots">${spotLine("entry", entry)}${spotLine("exit", exit)}</div>
      <div class="transaction-money"><b>${money(stake, currency)}</b><strong class="${profit > 0 ? "positive" : profit < 0 ? "negative" : ""}">${profit > 0 ? "+" : ""}${money(profit, currency)}</strong></div>
    </article>`;
  }

  function spotLine(kind, value) {
    const text = value === null || value === undefined || value === "" ? "" : String(value);
    return `<span><i class="${kind === "entry" ? "entry-dot" : "exit-dot"}"></i>${text ? `<b>${esc(text)}</b>` : `<em></em>`}</span>`;
  }

  function runPanelJournal(running, stats) {
    const active = runPanelStrategySource();
    const payload = active.payload || {};
    const rows = Array.isArray(payload.conditions) ? payload.conditions : [];
    const transport = String(document.documentElement.dataset.liveTransport || "connecting").replaceAll("_", " ");
    const conditionStatus = stats.rows.length ? ["met", "Met - purchase recorded"] : running ? ["watching", "Not met yet"] : ["ready", "Ready"];
    const routing = payload.result_routing?.enabled && payload.result_routing?.after_loss;
    return `<div class="run-panel-journal">
      <article><b>Connection</b><span>${esc(transport)}</span></article>
      <article><b>Strategy</b><span>${esc(active.name || "Current Strategy")}</span><small>${esc(marketScopeText(payload))} - ${esc(targetText(payload))}</small></article>
      <article><b>Execution</b><span>${running ? "Running in FAST mode" : "Waiting for Run"}</span><small>${stats.rows.length} run${stats.rows.length === 1 ? "" : "s"} recorded</small></article>
      <div class="journal-condition-list">${rows.length ? rows.map((condition, index) => `<div><span>${index + 1}</span><b>${esc(conditionText(condition))}</b><em class="${conditionStatus[0]}">${esc(conditionStatus[1])}</em></div>`).join("") : `<div><span>1</span><b>${esc(active.summary || "Backend strategy loaded")}</b><em class="${conditionStatus[0]}">${esc(conditionStatus[1])}</em></div>`}</div>
      ${routing ? `<article><b>After loss</b><span>${esc(targetText(payload.result_routing.after_loss))}</span><small>${esc((payload.result_routing.after_loss.conditions || []).map(conditionText).join(" AND "))}</small></article>` : ""}
    </div>`;
  }

  function updateRunPanelDom() {
    const panel = root.querySelector(".global-run-panel");
    if (!panel) return;
    const stats = runPanelStats();
    const currency = state.me?.currency || "USD";
    const running = runPanelRunning();
    const activeTab = ["summary", "transactions", "journal"].includes(state.runPanelTab) ? state.runPanelTab : "summary";
    panel.classList.toggle("open", state.runPanelOpen);
    panel.classList.toggle("collapsed", !state.runPanelOpen);
    panel.querySelectorAll("[data-run-panel-toggle]").forEach((button) => button.setAttribute("aria-label", state.runPanelOpen ? "Collapse run panel" : "Expand run panel"));
    panel.querySelectorAll("[data-run-tab]").forEach((button) => button.classList.toggle("active", button.dataset.runTab === activeTab));
    const body = panel.querySelector(".run-panel-body");
    if (body) body.innerHTML = runPanelContent(activeTab, stats, currency, running);
    const summary = panel.querySelector(".run-panel-stats");
    if (summary) summary.innerHTML = runPanelStatsMarkup(stats, currency);
    const toggle = panel.querySelector("[data-run-execution-toggle]");
    if (toggle) {
      toggle.classList.toggle("on", running);
      toggle.setAttribute("aria-label", running ? "Stop trading" : "Start trading");
    }
    const runButton = panel.querySelector("[data-run-start]");
    if (runButton) runButton.innerHTML = runPanelActionMarkup("Run");
  }

  function tradesPage() {
    const accounts = state.accounts?.accounts || [];
    const selected = selectedLinkedAccount(accounts) || { account_type: state.me?.account_type, balance: state.me?.balance, currency: state.me?.currency, account_id_masked: state.me?.account_id };
    const selectedType = accountType(selected);
    const lifecycle = String(state.lifecycle?.lifecycle || state.lifecycle?.runtime_state || (state.me?.enabled ? "running" : "stopped")).toLowerCase();
    const content = `<section class="run-panel">
      <div class="run-account-bar">
        <div class="account-select-visual"><span class="account-icon large">${quill(selectedType === "real" ? "realAccount" : "demoAccount")}</span><span><small>${esc(selectedType.toUpperCase())} ACCOUNT</small><b>${money(selected.balance, selected.currency)}</b><em>${esc(selected.account_id_masked || "")}</em></span>${quill("usd", "currency-icon")}</div>
        <label class="account-select-native"><span>Trading account</span><select id="run-account-select">${accounts.map((account) => `<option value="${esc(account.managed_account_id)}" ${account.selected ? "selected" : ""}>${esc(account.account_type.toUpperCase())} · ${esc(account.account_id_masked)} · ${money(account.balance, account.currency)}</option>`).join("")}</select></label>
      </div>
      <div class="run-status"><span class="live-led ${lifecycle}"></span><span><small>Execution</small><b>${esc(lifecycle.replaceAll("_", " "))}</b></span><div class="run-controls">${lifecycle.includes("running") || lifecycle.includes("active") ? `<button data-pause-trading>${miniIcon("pause")} Pause</button><button data-stop-trading>${miniIcon("stop")} Stop</button>` : `<button class="primary" data-start-trading>${miniIcon("play")} Start</button>`}<button data-clear-trades>${miniIcon("trash")} Clear</button></div></div>
      <div class="run-ledger transaction-ledger">${transactionTable((state.trades?.trades || []).filter((row) => !row.is_virtual), state.me?.currency || "USD")}</div>
      ${runSummary()}
    </section>`;
    return shell(content, { title: "Live Runs" });
  }

  function render() {
    const previousRoute = state.renderedRoute || state.route;
    const previousScroll = rememberScroll(previousRoute);
    if (!state.loaded) {
      root.innerHTML = `<div class="boot-screen"><span class="brand-mark">D</span><b>DerivAdmin</b><small>Loading automation workspace…</small></div>`;
      state.renderedRoute = "";
      return;
    }
    if (!state.me?.authenticated) { root.innerHTML = landing(); bind(); state.renderedRoute = "landing"; return; }
    const shouldOnboard = state.preferences?.requires_timezone_onboarding && state.route !== "timezone";
    if (shouldOnboard) state.route = "timezone";
    const pages = { home, builder: builderPage, ai: aiPage, ready: readyPage, schedule: schedulePage, profile: profilePage, trades: tradesPage, timezone: timezonePage };
    root.innerHTML = pages[state.route]();
    bind();
    const nextRoute = state.route;
    const nextScroll = previousRoute === nextRoute ? previousScroll : (state.scrollPositions[nextRoute] || 0);
    state.renderedRoute = nextRoute;
    restoreScroll(nextRoute, nextScroll);
  }

  async function refresh({ quiet = false } = {}) {
    if (!quiet) state.error = "";
    try {
      const me = await json("/me");
      state.me = me;
      if (!me.authenticated) {
        state.accounts = state.trades = state.lifecycle = state.schedules = state.preferences = state.premium = state.custom = null;
        state.loaded = true; render(); return;
      }
      const results = await Promise.allSettled([
        json("/me/accounts"), json("/me/trades/today?limit=5000"), json("/me/trading-lifecycle"),
        json("/me/automation-schedules?limit=80"), json("/me/automation-preferences"), json("/me/premium-access"), json("/me/custom-strategy"),
      ]);
      [state.accounts, state.trades, state.lifecycle, state.schedules, state.preferences, state.premium, state.custom] = results.map((result, index) => result.status === "fulfilled" ? result.value : [state.accounts, state.trades, state.lifecycle, state.schedules, state.preferences, state.premium, state.custom][index]);
      state.loaded = true;
      if (shouldHoldRender(quiet)) {
        updateRunPanelDom();
        return;
      }
      render();
    } catch (error) {
      state.loaded = true;
      state.error = error?.message || "Could not load DerivAdmin.";
      render();
    }
  }

  function go(route) {
    if (!ROUTES.has(route)) route = "home";
    state.route = route;
    history.replaceState(history.state, "", `#${route}`);
    state.error = ""; state.notice = "";
    state.editingUntil = 0;
    render();
  }

  const SILENT_SUCCESS_TASKS = new Set(["run-start", "run-toggle", "builder-trade", "trade-ready", "schedule-trade", "start", "pause", "stop"]);

  function openRunPanel(tab = "journal") {
    state.runPanelOpen = true;
    state.runPanelTab = ["summary", "transactions", "journal"].includes(tab) ? tab : "journal";
    updateRunPanelDom();
  }

  async function task(name, action, success) {
    if (state.busy) return;
    state.editingUntil = 0;
    state.busy = name; state.error = ""; state.notice = "";
    try {
      await action();
      if (success && !SILENT_SUCCESS_TASKS.has(name)) state.notice = success;
      await refresh({ quiet: true });
    } catch (error) {
      state.error = error?.message || "Action failed";
      render();
    } finally { state.busy = ""; }
  }

  function askStrategyName(fallback) {
    const value = window.prompt("Name this local bot", String(fallback || "My Strategy").trim());
    const name = String(value || "").trim();
    if (!name) throw new Error("Strategy name required.");
    assertUniqueStrategyName(name);
    return name;
  }

  function strategyNameKey(name) {
    return String(name || "").trim().replace(/\s+/g, " ").toLowerCase();
  }

  function assertUniqueStrategyName(name, currentId = "") {
    const key = strategyNameKey(name);
    const duplicate = savedTemplates().find((item) => strategyNameKey(item.name) === key && String(item.id || "") !== String(currentId || ""));
    if (duplicate) throw new Error("A local strategy with this name already exists. Strategy names cannot be reused or renamed.");
  }

  function withStrategyName(snapshot, name) {
    return {
      ...snapshot,
      name,
      source: "local",
      builder: snapshot.builder ? { ...snapshot.builder, name, lockedName: true } : snapshot.builder,
      strategy: snapshot.strategy ? { ...snapshot.strategy, name } : snapshot.strategy,
    };
  }

  async function saveBuilder({ trade = false, schedule = false, askName = false, storeLocal = askName } = {}) {
    let snapshot = builderSnapshot();
    if (askName && !snapshot.builder?.lockedName) snapshot = withStrategyName(snapshot, askStrategyName(snapshot.name));
    if (askName && snapshot.builder?.lockedName) assertUniqueStrategyName(snapshot.name, snapshot.id);
    await json("/me/custom-strategy", { method: "POST", body: JSON.stringify(snapshot.strategy) });
    if (storeLocal) snapshot = saveTemplate(snapshot);
    state.selectedStrategy = snapshot;
    if (schedule) { go("schedule"); return; }
    if (trade) {
      await json("/me/resume-trading", { method: "POST", body: JSON.stringify({ mode: "start_again" }) });
      go("trades");
    }
  }

  async function ensureRunnableStrategy() {
    if (state.route === "builder" && root.querySelector(".restored-builder")) {
      const snapshot = builderSnapshot();
      await json("/me/custom-strategy", { method: "POST", body: JSON.stringify(snapshot.strategy) });
      state.selectedStrategy = snapshot;
      return snapshot;
    }
    if (state.route === "ready" && state.generated) return saveGeneratedToServer({ storeLocal: false });
    const selected = state.selectedStrategy || {};
    if (selected.strategy?.market_mode) {
      await json("/me/custom-strategy", { method: "POST", body: JSON.stringify(selected.strategy) });
      return selected;
    }
    return null;
  }

  async function startTradingFromContext(mode = "continue") {
    await ensureRunnableStrategy();
    await json("/me/resume-trading", { method: "POST", body: JSON.stringify({ mode }) });
  }

  function saveTemplate(snapshot) {
    const templates = readJSON(STORE_TEMPLATES, []);
    const id = snapshot.id || `strategy-${Date.now()}`;
    assertUniqueStrategyName(snapshot.name, id);
    const locked = withStrategyName({ ...snapshot, id }, snapshot.name);
    const saved = { ...locked, id, saved_at: new Date().toISOString() };
    const key = strategyNameKey(saved.name);
    const next = [saved, ...templates.filter((item) => item.id !== id && strategyNameKey(item.name) !== key)].slice(0, 40);
    writeJSON(STORE_TEMPLATES, next);
    return saved;
  }

  async function saveGeneratedToServer({ askName = false, storeLocal = askName } = {}) {
    const canonical = generatedCanonical();
    if (!canonical) throw new Error("Generated strategy is missing its canonical execution payload.");
    const defaultName = state.generated.name || state.generated.strategy_name || "AI Generated Strategy";
    const name = askName ? askStrategyName(defaultName) : defaultName;
    await json("/me/custom-strategy", { method: "POST", body: JSON.stringify(canonical) });
    const snapshot = { name, source: "local", strategy: canonical, builder: normalizeBuilderDraft({ ...canonical, name, lockedName: true }), generated: state.generated };
    const saved = storeLocal ? saveTemplate(snapshot) : snapshot;
    state.selectedStrategy = saved;
    return saved;
  }

  function scheduleSnapshot(selected) {
    const strategy = selected.strategy || selected.canonical || selected.config || {};
    if (strategy.market_mode) return strategy;
    if (strategy.builder) return strategy;
    return strategy;
  }

  function loadBotToBuilder(source, id) {
    const bot = botById(source, id);
    if (!bot) return;
    state.selectedStrategy = {
      id: bot.id,
      builder: allMarketBuilder({ ...(bot.builder || bot.strategy || bot.config || bot), lockedName: source === "local" }),
      source,
    };
    go("builder");
  }

  function bind() {
    const host = root.querySelector(".app-main");
    if (host) host.addEventListener("scroll", () => { state.scrollPositions[state.route] = host.scrollTop; }, { passive: true });
    root.querySelectorAll("[data-route]").forEach((el) => el.addEventListener("click", () => go(el.dataset.route)));
    root.querySelectorAll("input, textarea, select").forEach((field) => {
      field.addEventListener("focus", markEditing);
      field.addEventListener("input", markEditing);
      field.addEventListener("keydown", markEditing);
      field.addEventListener("change", markEditing);
    });
    root.querySelectorAll("[data-theme-toggle]").forEach((button) => button.addEventListener("click", () => {
      state.theme = state.theme === "light" ? "dark" : "light";
      applyTheme(state.theme);
      render();
    }));
    root.querySelectorAll("[data-paid-soon-ok]").forEach((button) => button.addEventListener("click", () => {
      state.paidSoonDismissed = true;
      writeJSON(STORE_PAID_SOON, true);
      render();
    }));
    root.querySelectorAll("[data-run-panel-toggle]").forEach((button) => button.addEventListener("click", () => {
      state.runPanelOpen = !state.runPanelOpen;
      updateRunPanelDom();
    }));
    root.querySelectorAll("[data-run-tab]").forEach((button) => button.addEventListener("click", () => {
      state.runPanelTab = button.dataset.runTab || "summary";
      state.runPanelOpen = true;
      updateRunPanelDom();
    }));
    root.querySelectorAll("[data-run-reset]").forEach((button) => button.addEventListener("click", () => task("run-reset", () => json("/me/clear-trades", { method: "POST", body: JSON.stringify({ scope: "today" }) }), "Run panel reset.")));
    root.querySelectorAll("[data-run-start]").forEach((button) => button.addEventListener("click", () => {
      const stopping = runPanelRunning();
      openRunPanel("journal");
      task("run-start", () => stopping
        ? json("/me/stop-trading", { method: "POST", body: "{}" })
        : startTradingFromContext("continue"),
        stopping ? "Trading stopped." : "Trading started.");
    }));
    root.querySelectorAll("[data-run-execution-toggle]").forEach((button) => button.addEventListener("click", () => {
      const running = button.classList.contains("on");
      openRunPanel("journal");
      return task("run-toggle", () => running
        ? json("/me/stop-trading", { method: "POST", body: "{}" })
        : startTradingFromContext("continue"),
        running ? "Trading stopped." : "Trading started.");
    }));

    const tzSearch = document.getElementById("timezone-search");
    if (tzSearch) tzSearch.addEventListener("input", () => {
      const list = document.getElementById("timezone-list");
      if (list) list.innerHTML = timezoneOptions(document.querySelector('input[name="timezone"]:checked')?.value || state.preferences?.timezone || DEFAULT_TZ, tzSearch.value);
    });
    root.querySelectorAll("[data-save-timezone]").forEach((button) => button.addEventListener("click", () => task("timezone", async () => {
      const zone = button.dataset.timezoneValue || document.querySelector('input[name="timezone"]:checked')?.value || DEFAULT_TZ;
      await json("/me/automation-preferences/timezone", { method: "POST", body: JSON.stringify({ timezone: zone }) });
      state.route = "home"; history.replaceState(history.state, "", "#home");
    }, "Timezone saved.")));

    const text = document.getElementById("strategy-text");
    const count = document.getElementById("word-count");
    const updateCount = () => {
      if (!text || !count) return;
      const words = text.value.trim() ? text.value.trim().split(/\s+/).length : 0;
      count.textContent = `${words} / 250 words`;
      count.classList.toggle("over", words > 250);
    };
    if (text) { text.addEventListener("input", updateCount); updateCount(); }
    root.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => { if (text) { text.value = button.dataset.prompt || ""; updateCount(); text.focus(); } }));
    root.querySelectorAll("[data-generate-strategy]").forEach((button) => button.addEventListener("click", () => task("generate", async () => {
      const source = String(text?.value || "").trim();
      const words = source ? source.split(/\s+/).length : 0;
      if (!source) throw new Error("Describe the strategy first.");
      if (words > 250) throw new Error("Keep the strategy description within 250 words.");
      const result = await json("/me/text-to-strategy/compile", { method: "POST", body: JSON.stringify({ text: source }) });
      state.generated = { ...result, source_text: source };
      writeJSON(STORE_READY, state.generated);
      go("ready");
    })));
    root.querySelectorAll("[data-load-selected-bot]").forEach((button) => button.addEventListener("click", () => {
      const value = root.querySelector("[data-dashboard-bot-select]")?.value || "";
      const [source, ...idParts] = value.split(":");
      loadBotToBuilder(source || "built", idParts.join(":"));
    }));
    root.querySelectorAll("[data-load-bot-id]").forEach((button) => button.addEventListener("click", () => {
      loadBotToBuilder(button.dataset.loadBotSource || "local", button.dataset.loadBotId);
    }));

    root.querySelectorAll("[data-ready-save]").forEach((button) => button.addEventListener("click", () => task("save-ready", () => saveGeneratedToServer({ askName: true }), "Strategy saved locally.")));
    root.querySelectorAll("[data-ready-trade]").forEach((button) => button.addEventListener("click", () => {
      const stopping = runPanelRunning();
      openRunPanel("journal");
      task("trade-ready", async () => {
        if (stopping) { await json("/me/stop-trading", { method: "POST", body: "{}" }); return; }
        await startTradingFromContext("start_again");
        go("trades");
      }, stopping ? "Trading stopped." : "Trading started.");
    }));
    root.querySelectorAll("[data-ready-schedule]").forEach((button) => button.addEventListener("click", () => { const canonical = generatedCanonical(); state.selectedStrategy = { name: state.generated?.name || "AI Generated Strategy", source: "ai", strategy: canonical, stake: canonical?.execution_settings?.stake_amount }; go("schedule"); }));
    root.querySelectorAll("[data-ready-builder]").forEach((button) => button.addEventListener("click", () => { const canonical = generatedCanonical() || {}; state.selectedStrategy = { builder: normalizeBuilderDraft({ ...canonical, name: state.generated?.name || "AI Strategy" }) }; go("builder"); }));
    root.querySelectorAll("[data-ready-edit-idea]").forEach((button) => button.addEventListener("click", () => go("ai")));

    root.querySelectorAll("[data-builder-template-select]").forEach((select) => select.addEventListener("change", () => {
      const [source, ...idParts] = String(select.value || "").split(":");
      const id = idParts.join(":");
      if (!source || !id) return;
      const template = source === "local"
        ? readJSON(STORE_TEMPLATES, []).find((item) => String(item.id) === String(id))
        : BUILDER_TEMPLATES.find((item) => String(item.id) === String(id));
      if (!template) return;
      state.selectedStrategy = {
        id: template.id,
        builder: allMarketBuilder({ ...(template.builder || template.strategy || template.config || template), lockedName: source === "local" }),
        source,
      };
      render();
    }));

    root.querySelectorAll("[data-builder-market-mode]").forEach((button) => button.addEventListener("click", () => {
      if (button.classList.contains("active")) return;
      const mode = button.dataset.builderMarketMode === "selected" ? "selected" : "all";
      const current = builderDraftFromDom({ marketMode: mode });
      state.selectedStrategy = { ...(state.selectedStrategy || {}), builder: normalizeBuilderDraft({ ...current, marketMode: mode, markets: mode === "all" ? supportedMarkets() : current.markets }) };
      render();
    }));
    root.querySelectorAll("[data-builder-market]").forEach((field) => field.addEventListener("change", () => {
      state.selectedStrategy = { ...(state.selectedStrategy || {}), builder: builderDraftFromDom() };
      render();
    }));
    root.querySelectorAll("[data-builder-mode]").forEach((button) => button.addEventListener("click", () => {
      if (button.classList.contains("active")) return;
      state.selectedStrategy = { ...(state.selectedStrategy || {}), builder: builderDraftFromDom({ strategyMode: button.dataset.builderMode || "combined" }) };
      render();
    }));
    root.querySelectorAll("[data-trade-group]").forEach((button) => button.addEventListener("click", () => {
      if (button.classList.contains("active")) return;
      const group = TRADE_GROUPS.find(([value]) => value === button.dataset.tradeGroup) || TRADE_GROUPS[0];
      const current = builderDraftFromDom({ tradeGroup: group[0] });
      const side = group[2].some(([value]) => value === current.side) ? current.side : group[2][0][0];
      state.selectedStrategy = { ...(state.selectedStrategy || {}), builder: normalizeBuilderDraft({ ...current, tradeGroup: group[0], side }) };
      render();
    }));
    root.querySelectorAll("[data-builder-live]").forEach((field) => field.addEventListener(field.type === "checkbox" ? "change" : "change", () => {
      state.selectedStrategy = { ...(state.selectedStrategy || {}), builder: builderDraftFromDom() };
      render();
    }));
    root.querySelectorAll("[data-result-route]").forEach((field) => {
      const structural = field.hasAttribute("data-result-live");
      field.addEventListener(field.tagName === "SELECT" || field.type === "checkbox" ? "change" : "input", () => {
        state.selectedStrategy = { ...(state.selectedStrategy || {}), builder: builderDraftFromDom() };
        if (structural) render();
      });
    });
    root.querySelectorAll("[data-builder-save]").forEach((button) => button.addEventListener("click", () => task("builder-save", () => saveBuilder({ askName: true }), "Strategy saved locally.")));
    root.querySelectorAll("[data-builder-trade]").forEach((button) => button.addEventListener("click", () => {
      const stopping = runPanelRunning();
      openRunPanel("journal");
      task("builder-trade", async () => {
        if (stopping) { await json("/me/stop-trading", { method: "POST", body: "{}" }); return; }
        await saveBuilder({ trade: true });
      }, stopping ? "Trading stopped." : "Trading started.");
    }));
    root.querySelectorAll("[data-builder-schedule]").forEach((button) => button.addEventListener("click", () => { try { state.selectedStrategy = builderSnapshot(); state.scheduleDraft = null; go("schedule"); } catch (error) { state.error = error.message; render(); } }));

    root.querySelectorAll("[data-create-schedule]").forEach((button) => button.addEventListener("click", () => task("schedule-create", async () => {
      const selected = state.selectedStrategy || strategyForSchedule();
      const overlap = document.querySelector('input[name="overlap"]:checked')?.value || "wait";
      const payload = {
        strategy_name: selected.name || "Strategy",
        strategy_source: selected.source || "saved",
        strategy_snapshot: scheduleSnapshot(selected),
        date: document.getElementById("s-date")?.value,
        time: document.getElementById("s-time")?.value,
        timezone: document.getElementById("s-timezone")?.value || DEFAULT_TZ,
        stake: Number(document.getElementById("s-stake")?.value || .5),
        take_profit: Number(document.getElementById("s-tp")?.value || 0),
        stop_loss: Number(document.getElementById("s-sl")?.value || 0),
        overlap_policy: overlap,
      };
      await json("/me/automation-schedules", { method: "POST", body: JSON.stringify(payload) });
      state.selectedStrategy = null;
      state.scheduleDraft = null;
    }, "Session scheduled.")));
    root.querySelectorAll("[data-trade-now-selected]").forEach((button) => button.addEventListener("click", () => {
      const stopping = runPanelRunning();
      openRunPanel("journal");
      task("schedule-trade", async () => {
        if (stopping) { await json("/me/stop-trading", { method: "POST", body: "{}" }); return; }
        const selected = state.selectedStrategy || strategyForSchedule();
        if (selected.strategy?.market_mode) await json("/me/custom-strategy", { method: "POST", body: JSON.stringify(selected.strategy) });
        await json("/me/resume-trading", { method: "POST", body: JSON.stringify({ mode: "start_again" }) }); go("trades");
      }, stopping ? "Trading stopped." : "Trading started.");
    }));
    root.querySelectorAll("[data-delete-schedule]").forEach((button) => button.addEventListener("click", () => task("schedule-delete", () => json(`/me/automation-schedules/${encodeURIComponent(button.dataset.deleteSchedule)}/cancel`, { method: "POST", body: "{}" }), "Schedule deleted.")));
    root.querySelectorAll("[data-edit-schedule]").forEach((button) => button.addEventListener("click", () => {
      const item = (state.schedules?.schedules || []).find((row) => String(row.id) === String(button.dataset.editSchedule));
      if (!item) return;
      const next = scheduleDraftFromItem(item);
      state.selectedStrategy = next.selected;
      state.scheduleDraft = next.draft;
      go("schedule");
    }));

    const accountSelect = document.getElementById("run-account-select");
    if (accountSelect) accountSelect.addEventListener("change", () => task("switch-account", () => json("/me/switch-account", { method: "POST", body: JSON.stringify({ managed_account_id: Number(accountSelect.value) }) }), "Account switched."));
    const topSwitch = root.querySelector(".top-account-switch");
    if (topSwitch) {
      topSwitch.querySelector(".account-switch-summary")?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        topSwitch.classList.toggle("open");
        window.setTimeout(() => {
          document.addEventListener("click", (outside) => {
            if (!topSwitch.contains(outside.target)) topSwitch.classList.remove("open");
          }, { once: true });
        }, 0);
      });
    }
    root.querySelectorAll("[data-account-id]").forEach((button) => button.addEventListener("click", () => task("switch-account", () => json("/me/switch-account", { method: "POST", body: JSON.stringify({ managed_account_id: Number(button.dataset.accountId) }) }))));
    root.querySelectorAll("[data-start-trading]").forEach((button) => button.addEventListener("click", () => { openRunPanel("journal"); task("start", () => startTradingFromContext("continue"), "Trading started."); }));
    root.querySelectorAll("[data-pause-trading]").forEach((button) => button.addEventListener("click", () => task("pause", () => json("/me/pause-trading", { method: "POST", body: "{}" }), "Trading paused.")));
    root.querySelectorAll("[data-stop-trading]").forEach((button) => button.addEventListener("click", () => task("stop", () => json("/me/stop-trading", { method: "POST", body: "{}" }), "Trading stopped.")));
    root.querySelectorAll("[data-clear-trades]").forEach((button) => button.addEventListener("click", () => task("clear", () => json("/me/clear-trades", { method: "POST", body: JSON.stringify({ scope: "today" }) }), "Today's run history cleared.")));
  }

  window.addEventListener("hashchange", () => { state.route = routeFromHash(); state.editingUntil = 0; render(); });
  document.addEventListener("foa:vps-live", () => { if (state.me?.authenticated) refresh({ quiet: true }); });
  document.addEventListener("foa:backend-lifecycle", () => { if (state.me?.authenticated) refresh({ quiet: true }); });
  window.addEventListener("focus", () => { if (state.me?.authenticated) refresh({ quiet: true }); });
  window.setInterval(() => { if (state.me?.authenticated && ["trades", "home", "schedule"].includes(state.route)) refresh({ quiet: true }); }, 5000);

  render();
  refresh();
  window.FOA_FINAL_UI = Object.freeze({ version: "20260818-local-ui-12", refresh, go });
})();
