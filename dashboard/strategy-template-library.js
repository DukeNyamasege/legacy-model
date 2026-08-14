(() => {
  "use strict";

  if (window.__FOA_STRATEGY_TEMPLATE_LIBRARY__) return;
  window.__FOA_STRATEGY_TEMPLATE_LIBRARY__ = true;

  const VERSION = "20260814-template-library-v2";
  const STORAGE_KEY = "foa-user-strategy-templates-v1";
  const DRAFT_KEY = "foa-builder-draft-v2";
  const DEFAULT_ID = "golden-over1-recovery-over4";
  const INIT_PREFIX = `foa-template-default-initialized:${VERSION}`;
  const ALL_MARKETS = ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V", "R_10", "R_25", "R_50", "R_75", "R_100"];

  let selectedId = DEFAULT_ID;
  let applying = false;
  let scheduled = false;

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  function baseBuilder() {
    return {
      version: 3,
      strategyMode: "percentage",
      marketMode: "all",
      markets: clone(ALL_MARKETS),
      oneMarket: "1HZ100V",
      lastRule: { window: 5, target: "last_digits", operator: "<=", value: 5 },
      percentageRule: { target: "over", value: 1, window: 1000, operator: ">", threshold: 80 },
      tickDirectionRule: { enabled: false, window: 3, direction: "rising" },
      trade: { group: "over_under", side: "over", prediction: 1 },
      reanalyze: { mode: "after_every_trade", losses: 1, wins: 1 },
      money: { stake: 0.5, takeProfit: 25, stopLoss: 100, martingale: 2.1, ticks: 1 },
      virtualHook: { enabled: true, enterAfterLosses: 2, exitAfterConsecutiveWins: 1 },
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

  function preset(id, name, analysis, side, summary, patch = {}, result = {}, extra = {}) {
    const group = ["over", "under"].includes(side)
      ? "over_under"
      : ["matches", "differs"].includes(side)
      ? "matches_differs"
      : ["odd", "even"].includes(side)
      ? "odd_even"
      : "rise_fall";
    const builder = deepMerge(baseBuilder(), {
      strategyMode: analysis,
      trade: { group, side, prediction: patch?.trade?.prediction ?? 5 },
      ...patch,
    });
    return {
      id,
      name,
      analysis,
      side,
      summary,
      builder,
      result: {
        routingEnabled: false,
        afterLoss: null,
        recoveryMode: "multiplier",
        splitCount: 1,
        ...result,
      },
      predictionMode: extra.predictionMode || "",
      predictionWindow: Number(extra.predictionWindow || 100),
      builtIn: true,
    };
  }

  const BUILT_INS = [
    preset(
      DEFAULT_ID,
      "Over 1 Recovery Over 4 Golden Bot",
      "percentage",
      "over",
      "All markets · Over 1 > 80% in 1,000 ticks · loss route Over 4 after last 5 digits <= 5.",
      {
        percentageRule: { target: "over", value: 1, window: 1000, operator: ">", threshold: 80 },
        trade: { group: "over_under", side: "over", prediction: 1 },
        reanalyze: { mode: "after_every_trade", losses: 1, wins: 1 },
        money: { stake: 5.5, takeProfit: 100, stopLoss: 1000, martingale: 2.1, ticks: 1 },
        virtualHook: { enabled: true, enterAfterLosses: 2, exitAfterConsecutiveWins: 1 },
      },
      {
        routingEnabled: true,
        recoveryMode: "multiplier",
        splitCount: 1,
        afterLoss: {
          tradeType: "over",
          prediction: 4,
          durationTicks: 1,
          analysisMode: "last_digit",
          lastRule: { window: 5, operator: "<=", value: 5 },
          percentageRule: { target: "over", value: 4, window: 500, operator: ">=", threshold: 50 },
          tickDirectionRule: { enabled: false, window: 3, direction: "rising" },
        },
      },
    ),
    preset(
      "over3-spread-x2-last-digit",
      "Over 3 Spread Recovery x2",
      "last_digit",
      "over",
      "Last 5 digits <= 5 · trade Over 3 · exact loss debt recovered across 2 successful Over 3 trades.",
      {
        lastRule: { window: 5, operator: "<=", value: 5 },
        trade: { group: "over_under", side: "over", prediction: 3 },
        money: { stake: 0.5, takeProfit: 25, stopLoss: 100, martingale: 2.1, ticks: 1 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "over2-combined-alignment",
      "Over 2 Combined Alignment",
      "combined",
      "over",
      "Last 3 digits <= 6 AND Over 2 > 72% in 1,000 ticks · trade Over 2 · split recovery x2.",
      {
        lastRule: { window: 3, operator: "<=", value: 6 },
        percentageRule: { target: "over", value: 2, window: 1000, operator: ">", threshold: 72 },
        trade: { group: "over_under", side: "over", prediction: 2 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "under8-percentage-shield",
      "Under 8 Percentage Shield",
      "percentage",
      "under",
      "Under 8 > 80% across the last 1,000 digits · trade Under 8 · re-analyze every trade.",
      {
        percentageRule: { target: "under", value: 8, window: 1000, operator: ">", threshold: 80 },
        trade: { group: "over_under", side: "under", prediction: 8 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "under6-last-digit-cluster",
      "Under 6 Last-Digit Cluster",
      "last_digit",
      "under",
      "Last 4 digits <= 6 · trade Under 6 · one-tick contract with two-part spread recovery.",
      {
        lastRule: { window: 4, operator: "<=", value: 6 },
        trade: { group: "over_under", side: "under", prediction: 6 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "under7-combined-alignment",
      "Under 7 Combined Alignment",
      "combined",
      "under",
      "Last 3 digits <= 6 AND Under 7 > 72% in 500 ticks · trade Under 7.",
      {
        lastRule: { window: 3, operator: "<=", value: 6 },
        percentageRule: { target: "under", value: 7, window: 500, operator: ">", threshold: 72 },
        trade: { group: "over_under", side: "under", prediction: 7 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "even-percentage-majority",
      "Even Percentage Majority",
      "percentage",
      "even",
      "Even digits > 54% in the last 200 digits · trade Even · re-analyze every trade.",
      {
        percentageRule: { target: "even", value: 0, window: 200, operator: ">", threshold: 54 },
        trade: { group: "odd_even", side: "even", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "even-last-digit-rebound",
      "Even Last-Digit Rebound",
      "last_digit",
      "even",
      "Last 3 digits <= 4 · trade Even · two-part exact-debt spread recovery.",
      {
        lastRule: { window: 3, operator: "<=", value: 4 },
        trade: { group: "odd_even", side: "even", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "even-combined-balance",
      "Even Combined Balance",
      "combined",
      "even",
      "Last 2 digits <= 7 AND Even > 53% in 500 digits · trade Even.",
      {
        lastRule: { window: 2, operator: "<=", value: 7 },
        percentageRule: { target: "even", value: 0, window: 500, operator: ">", threshold: 53 },
        trade: { group: "odd_even", side: "even", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "odd-percentage-majority",
      "Odd Percentage Majority",
      "percentage",
      "odd",
      "Odd digits > 54% in the last 200 digits · trade Odd · re-analyze every trade.",
      {
        percentageRule: { target: "odd", value: 0, window: 200, operator: ">", threshold: 54 },
        trade: { group: "odd_even", side: "odd", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "odd-last-digit-rebound",
      "Odd Last-Digit Rebound",
      "last_digit",
      "odd",
      "Last 3 digits >= 5 · trade Odd · two-part exact-debt spread recovery.",
      {
        lastRule: { window: 3, operator: ">=", value: 5 },
        trade: { group: "odd_even", side: "odd", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "odd-combined-balance",
      "Odd Combined Balance",
      "combined",
      "odd",
      "Last 2 digits >= 2 AND Odd > 53% in 500 digits · trade Odd.",
      {
        lastRule: { window: 2, operator: ">=", value: 2 },
        percentageRule: { target: "odd", value: 0, window: 500, operator: ">", threshold: 53 },
        trade: { group: "odd_even", side: "odd", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "matches7-hot-percentage",
      "Matches 7 Hot-Digit Percentage",
      "percentage",
      "matches",
      "Exact digit 7 > 13% in the last 100 digits · trade Matches 7 · conservative multiplier recovery.",
      {
        percentageRule: { target: "digit", value: 7, window: 100, operator: ">", threshold: 13 },
        trade: { group: "matches_differs", side: "matches", prediction: 7 },
        money: { stake: 0.5, takeProfit: 25, stopLoss: 100, martingale: 1.5, ticks: 1 },
      },
      { recoveryMode: "multiplier", splitCount: 1 },
    ),
    preset(
      "matches4-last-digit-repeat",
      "Matches 4 Last-Digit Repeat",
      "last_digit",
      "matches",
      "Last 2 digits equal 4 · trade Matches 4 · experimental high-payout template.",
      {
        lastRule: { window: 2, operator: "==", value: 4 },
        trade: { group: "matches_differs", side: "matches", prediction: 4 },
        money: { stake: 0.5, takeProfit: 25, stopLoss: 100, martingale: 1.4, ticks: 1 },
      },
      { recoveryMode: "multiplier", splitCount: 1 },
    ),
    preset(
      "matches-dominant-combined",
      "Matches Dominant Combined",
      "combined",
      "matches",
      "Last 2 digits >= 0 AND exact digit concentration > 12.5% · dynamic most-appearing prediction.",
      {
        lastRule: { window: 2, operator: ">=", value: 0 },
        percentageRule: { target: "digit", value: 5, window: 100, operator: ">", threshold: 12.5 },
        trade: { group: "matches_differs", side: "matches", prediction: 5 },
        money: { stake: 0.5, takeProfit: 25, stopLoss: 100, martingale: 1.4, ticks: 1 },
      },
      { recoveryMode: "multiplier", splitCount: 1 },
      { predictionMode: "most_appearing", predictionWindow: 100 },
    ),
    preset(
      "differs4-percentage-rare",
      "Differs 4 Percentage Filter",
      "percentage",
      "differs",
      "Exact digit 4 < 8% in the last 100 digits · trade Differs 4 · split recovery x2.",
      {
        percentageRule: { target: "digit", value: 4, window: 100, operator: "<", threshold: 8 },
        trade: { group: "matches_differs", side: "differs", prediction: 4 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "differs-repeat-breaker",
      "Differs Repeat Breaker",
      "last_digit",
      "differs",
      "Last 2 digits are identical · trade Differs against the qualifying trigger digit.",
      {
        lastRule: { window: 2, operator: "all_same", value: 4 },
        trade: { group: "matches_differs", side: "differs", prediction: 4 },
      },
      { recoveryMode: "split", splitCount: 2 },
      { predictionMode: "last_digit" },
    ),
    preset(
      "differs-combined-rare-breaker",
      "Differs Combined Rare Breaker",
      "combined",
      "differs",
      "Last 2 digits are the same AND exact-digit concentration < 10% · trade Differs using least-appearing prediction.",
      {
        lastRule: { window: 2, operator: "all_same", value: 4 },
        percentageRule: { target: "digit", value: 4, window: 100, operator: "<", threshold: 10 },
        trade: { group: "matches_differs", side: "differs", prediction: 4 },
      },
      { recoveryMode: "split", splitCount: 2 },
      { predictionMode: "least_appearing", predictionWindow: 100 },
    ),
    preset(
      "rise-percentage-momentum",
      "Rise Percentage Momentum",
      "percentage",
      "rise",
      "Up ticks > 55% in the last 100 ticks · trade Rise · one-tick contract.",
      {
        percentageRule: { target: "rise", value: 0, window: 100, operator: ">", threshold: 55 },
        trade: { group: "rise_fall", side: "rise", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "rise-last-direction",
      "Rise Last-Tick Direction",
      "last_digit",
      "rise",
      "Last-digit guard plus last 3 tick directions rising · trade Rise.",
      {
        lastRule: { window: 1, operator: ">=", value: 0 },
        tickDirectionRule: { enabled: true, window: 3, direction: "rising" },
        trade: { group: "rise_fall", side: "rise", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "rise-combined-momentum",
      "Rise Combined Momentum",
      "combined",
      "rise",
      "Last-digit guard AND Up ticks > 54% in 200 ticks AND last 3 directions rising.",
      {
        lastRule: { window: 2, operator: ">=", value: 0 },
        percentageRule: { target: "rise", value: 0, window: 200, operator: ">", threshold: 54 },
        tickDirectionRule: { enabled: true, window: 3, direction: "rising" },
        trade: { group: "rise_fall", side: "rise", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "fall-percentage-momentum",
      "Fall Percentage Momentum",
      "percentage",
      "fall",
      "Down ticks > 55% in the last 100 ticks · trade Fall · one-tick contract.",
      {
        percentageRule: { target: "fall", value: 0, window: 100, operator: ">", threshold: 55 },
        trade: { group: "rise_fall", side: "fall", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "fall-last-direction",
      "Fall Last-Tick Direction",
      "last_digit",
      "fall",
      "Last-digit guard plus last 3 tick directions falling · trade Fall.",
      {
        lastRule: { window: 1, operator: ">=", value: 0 },
        tickDirectionRule: { enabled: true, window: 3, direction: "falling" },
        trade: { group: "rise_fall", side: "fall", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
    preset(
      "fall-combined-momentum",
      "Fall Combined Momentum",
      "combined",
      "fall",
      "Last-digit guard AND Down ticks > 54% in 200 ticks AND last 3 directions falling.",
      {
        lastRule: { window: 2, operator: ">=", value: 0 },
        percentageRule: { target: "fall", value: 0, window: 200, operator: ">", threshold: 54 },
        tickDirectionRule: { enabled: true, window: 3, direction: "falling" },
        trade: { group: "rise_fall", side: "fall", prediction: 0 },
      },
      { recoveryMode: "split", splitCount: 2 },
    ),
  ];

  function readLocalTemplates() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      return Array.isArray(parsed) ? parsed.filter((item) => item && item.id && item.name && item.builder) : [];
    } catch (_) {
      return [];
    }
  }

  function writeLocalTemplates(rows) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(rows)); } catch (_) {}
  }

  function allTemplates() {
    return [...BUILT_INS, ...readLocalTemplates().map((item) => ({ ...item, builtIn: false }))];
  }

  function selectedTemplate() {
    return allTemplates().find((item) => item.id === selectedId) || BUILT_INS[0];
  }

  function accountIdentity(payload = null) {
    const me = payload?.me || window.FOA_NETLIFY_LIVE_CACHE?.me || window.FOA_BOOT_SESSION || {};
    return String(payload?.managed_account_id || me?.managed_account_id || me?.id || me?.account_id_masked || me?.account_id || "session");
  }

  function defaultInitKey(payload = null) {
    return `${INIT_PREFIX}:${accountIdentity(payload)}`;
  }

  function wasDefaultInitialized(payload = null) {
    try { return localStorage.getItem(defaultInitKey(payload)) === "1"; } catch (_) { return false; }
  }

  function markDefaultInitialized(payload = null) {
    try { localStorage.setItem(defaultInitKey(payload), "1"); } catch (_) {}
  }

  function fire(field, type = null) {
    if (!field) return;
    field.dispatchEvent(new Event(type || (field.tagName === "SELECT" || field.type === "checkbox" ? "change" : "input"), { bubbles: true }));
  }

  function clickChoice(selector) {
    const button = q(selector);
    if (!button) return false;
    if (!button.classList.contains("active")) button.click();
    return true;
  }

  async function setBuilder(path, value) {
    const field = q(`[data-builder="${path}"]`);
    if (!field) return false;
    if (field.type === "checkbox") {
      if (field.checked !== Boolean(value)) {
        field.checked = Boolean(value);
        fire(field, "change");
        await sleep(10);
      }
      return true;
    }
    if (String(field.value) !== String(value)) {
      field.value = String(value);
      fire(field, "change");
      await sleep(10);
    }
    return true;
  }

  async function applyMarketScope(builder) {
    const mode = String(builder.marketMode || "all");
    if (mode === "all") {
      clickChoice('[data-market-mode="all"]');
      await sleep(20);
      return;
    }
    if (mode === "single") {
      clickChoice('[data-market-mode="single"]');
      await sleep(20);
      const select = q("[data-market-select]");
      const symbol = String(builder.oneMarket || builder.markets?.[0] || "1HZ100V");
      if (select) {
        select.value = symbol;
        fire(select, "change");
        await sleep(20);
      }
      return;
    }

    clickChoice('[data-market-mode="selected"]');
    await sleep(20);
    for (const button of qa("[data-market-remove]")) {
      button.click();
      await sleep(12);
    }
    for (const symbol of Array.isArray(builder.markets) ? builder.markets : []) {
      const select = q("[data-market-select]");
      if (!select) break;
      select.value = symbol;
      fire(select, "change");
      await sleep(18);
    }
  }

  async function applyDynamicPrediction(template) {
    if (!["matches", "differs"].includes(String(template.builder?.trade?.side || ""))) return;
    await sleep(40);
    const select = q("[data-last-digit-prediction]");
    if (!select) return;
    const mode = String(template.predictionMode || "");
    const value = mode || String(template.builder?.trade?.prediction ?? 0);
    if (String(select.value) !== value) {
      select.value = value;
      fire(select, "change");
      await sleep(20);
    }
    if (["most_appearing", "second_most_appearing", "least_appearing"].includes(mode)) {
      const windowInput = q("[data-dynamic-prediction-window]");
      if (windowInput) {
        windowInput.value = String(template.predictionWindow || 100);
        fire(windowInput, "change");
      }
    }
  }

  async function applyTemplate(template, { markDefault = false } = {}) {
    if (!template || applying) return;
    applying = true;
    try {
      await applyMarketScope(template.builder);
      clickChoice(`[data-strategy-mode="${template.builder.strategyMode}"]`);
      await sleep(20);

      const paths = [
        ["lastRule.window", template.builder.lastRule?.window],
        ["lastRule.operator", template.builder.lastRule?.operator],
        ["lastRule.value", template.builder.lastRule?.value],
        ["percentageRule.target", template.builder.percentageRule?.target],
        ["percentageRule.value", template.builder.percentageRule?.value],
        ["percentageRule.window", template.builder.percentageRule?.window],
        ["percentageRule.operator", template.builder.percentageRule?.operator],
        ["percentageRule.threshold", template.builder.percentageRule?.threshold],
        ["tickDirectionRule.enabled", template.builder.tickDirectionRule?.enabled],
        ["tickDirectionRule.window", template.builder.tickDirectionRule?.window],
        ["tickDirectionRule.direction", template.builder.tickDirectionRule?.direction],
      ];
      for (const [path, value] of paths) {
        if (value !== undefined) await setBuilder(path, value);
      }

      clickChoice(`[data-trade-group="${template.builder.trade?.group}"]`);
      await sleep(20);
      await setBuilder("trade.side", template.builder.trade?.side);
      if (template.builder.trade?.prediction !== undefined) await setBuilder("trade.prediction", template.builder.trade.prediction);

      for (const [path, value] of [
        ["reanalyze.mode", template.builder.reanalyze?.mode],
        ["reanalyze.losses", template.builder.reanalyze?.losses],
        ["reanalyze.wins", template.builder.reanalyze?.wins],
        ["money.stake", template.builder.money?.stake],
        ["money.takeProfit", template.builder.money?.takeProfit],
        ["money.stopLoss", template.builder.money?.stopLoss],
        ["money.martingale", template.builder.money?.martingale],
        ["money.ticks", template.builder.money?.ticks],
        ["virtualHook.enabled", template.builder.virtualHook?.enabled],
        ["virtualHook.enterAfterLosses", template.builder.virtualHook?.enterAfterLosses],
        ["virtualHook.exitAfterConsecutiveWins", template.builder.virtualHook?.exitAfterConsecutiveWins],
      ]) {
        if (value !== undefined) await setBuilder(path, value);
      }

      await applyDynamicPrediction(template);
      if (window.FOA_RESULT_BASED_API?.applyState) {
        window.FOA_RESULT_BASED_API.applyState(clone(template.result || {}));
      }

      selectedId = template.id;
      if (markDefault) markDefaultInitialized();
      scheduleEnhance();
      window.setTimeout(() => {
        const message = q("#strategy-template-message");
        if (message) message.textContent = `${template.name} loaded as an editable draft. Customize it, then Save Builder or Start Auto Trading.`;
      }, 30);
    } finally {
      applying = false;
    }
  }

  function currentBuilderDraft() {
    try {
      const parsed = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
      return parsed && typeof parsed === "object" ? parsed : baseBuilder();
    } catch (_) {
      return baseBuilder();
    }
  }

  function currentPredictionSettings() {
    const select = q("[data-last-digit-prediction]");
    const value = String(select?.value || "");
    const dynamic = ["last_digit", "most_appearing", "second_most_appearing", "least_appearing"].includes(value) ? value : "";
    return {
      predictionMode: dynamic,
      predictionWindow: Number(q("[data-dynamic-prediction-window]")?.value || 100),
    };
  }

  function saveCurrentAsTemplate(name) {
    const trimmed = String(name || "").trim().slice(0, 80);
    if (!trimmed) return null;
    const rows = readLocalTemplates();
    const prediction = currentPredictionSettings();
    const item = {
      id: `user-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name: trimmed,
      analysis: q("[data-strategy-mode].active")?.dataset?.strategyMode || currentBuilderDraft().strategyMode || "combined",
      side: String(q('[data-builder="trade.side"]')?.value || currentBuilderDraft().trade?.side || "over"),
      summary: "Personal template saved locally on this device.",
      builder: currentBuilderDraft(),
      result: window.FOA_RESULT_BASED_API?.getState ? window.FOA_RESULT_BASED_API.getState() : { routingEnabled: false, recoveryMode: "multiplier", splitCount: 1 },
      predictionMode: prediction.predictionMode,
      predictionWindow: prediction.predictionWindow,
      builtIn: false,
      createdAt: new Date().toISOString(),
    };
    rows.push(item);
    writeLocalTemplates(rows.slice(-50));
    selectedId = item.id;
    return item;
  }

  function deleteSelectedLocal() {
    const rows = readLocalTemplates();
    const target = rows.find((item) => item.id === selectedId);
    if (!target) return false;
    if (!window.confirm(`Delete local template “${target.name}”?`)) return false;
    writeLocalTemplates(rows.filter((item) => item.id !== selectedId));
    selectedId = DEFAULT_ID;
    return true;
  }

  function templateOptions() {
    const built = ["percentage", "last_digit", "combined"].map((analysis) => {
      const label = analysis === "last_digit" ? "Last Digit" : analysis[0].toUpperCase() + analysis.slice(1);
      const rows = BUILT_INS.filter((item) => item.analysis === analysis);
      return `<optgroup label="${label} Templates">${rows.map((item) => `<option value="${item.id}" ${selectedId === item.id ? "selected" : ""}>${item.name}</option>`).join("")}</optgroup>`;
    }).join("");
    const local = readLocalTemplates();
    const mine = local.length
      ? `<optgroup label="My Templates">${local.map((item) => `<option value="${item.id}" ${selectedId === item.id ? "selected" : ""}>${item.name}</option>`).join("")}</optgroup>`
      : "";
    return built + mine;
  }

  function templateDisplay(template) {
    const modeLabel = template.analysis === "last_digit" ? "Last Digit" : template.analysis[0].toUpperCase() + template.analysis.slice(1);
    const recovery = template.result?.recoveryMode === "split" ? `Spread x${template.result?.splitCount || 2}` : "Multiplier";
    return {
      title: template.name,
      meta: `${modeLabel} · ${String(template.side || "").toUpperCase()} · ${recovery}`,
      summary: template.summary,
      isLocal: !template.builtIn,
    };
  }

  function setText(node, value) {
    if (node && node.textContent !== String(value ?? "")) node.textContent = String(value ?? "");
  }

  function syncLibraryState(section) {
    if (!section) return;
    const template = selectedTemplate();
    const display = templateDisplay(template);
    const select = q("#strategy-template-select", section);
    if (select && select.value !== template.id) select.value = template.id;
    setText(q(".strategy-template-preview b", section), display.title);
    setText(q(".strategy-template-preview span", section), display.meta);
    setText(q(".strategy-template-preview p", section), display.summary);
    const deleteButton = q("#strategy-template-delete", section);
    if (deleteButton) {
      deleteButton.hidden = !display.isLocal;
      deleteButton.disabled = !display.isLocal;
    }
  }

  function refreshTemplateOptions(section) {
    if (!section) return;
    const select = q("#strategy-template-select", section);
    if (select) {
      select.innerHTML = templateOptions();
      select.value = selectedTemplate().id;
    }
    syncLibraryState(section);
  }

  function libraryHtml() {
    const template = selectedTemplate();
    const display = templateDisplay(template);
    return `<section class="strategy-template-library" id="strategy-template-library">
      <div class="strategy-template-head">
        <div><span class="eyebrow">Strategy Templates</span><h2>Load. Customize. Trade.</h2><p>24 built-in presets cover Percentage, Last Digit and Combined analysis across every supported trade type. Presets are starting logic, not profit guarantees.</p></div>
        <span class="template-count">24 BUILT-IN</span>
      </div>
      <div class="strategy-template-picker">
        <label><span>Choose template</span><select id="strategy-template-select">${templateOptions()}</select></label>
        <button type="button" class="template-load-button" id="strategy-template-load">Load Template</button>
        <button type="button" class="template-delete-button" id="strategy-template-delete" ${display.isLocal ? "" : "hidden disabled"}>Delete</button>
      </div>
      <div class="strategy-template-preview">
        <div><b>${display.title}</b><span>${display.meta}</span></div>
        <p>${display.summary}</p>
      </div>
      <div class="strategy-template-save">
        <div><strong>My Templates</strong><small>Customize any strategy, give it a name, and save the current setup locally on this device. Builder Reset does not remove saved templates.</small></div>
        <div><input id="strategy-template-name" maxlength="80" placeholder="Example: Duke Over 3 Safe V1"><button type="button" id="strategy-template-save">Save Current as Template</button></div>
      </div>
      <p id="strategy-template-message" class="strategy-template-message">Select a preset and load it. Nothing is written to the trading backend until Save Builder or Start Auto Trading.</p>
    </section>`;
  }

  function bindLibrary(section) {
    q("#strategy-template-select", section)?.addEventListener("change", (event) => {
      selectedId = String(event.currentTarget.value || DEFAULT_ID);
      syncLibraryState(section);
      const message = q("#strategy-template-message", section);
      if (message) message.textContent = `${selectedTemplate().name} selected. Press Load Template to place it into the editable builder.`;
    });
    q("#strategy-template-load", section)?.addEventListener("click", () => applyTemplate(selectedTemplate()));
    q("#strategy-template-save", section)?.addEventListener("click", () => {
      const input = q("#strategy-template-name", section);
      const item = saveCurrentAsTemplate(input?.value || "");
      const message = q("#strategy-template-message", section);
      if (!item) {
        if (message) message.textContent = "Enter a template name first.";
        input?.focus();
        return;
      }
      if (input) input.value = "";
      refreshTemplateOptions(section);
      if (message) message.textContent = `${item.name} saved locally on this device.`;
    });
    q("#strategy-template-delete", section)?.addEventListener("click", () => {
      if (!deleteSelectedLocal()) return;
      refreshTemplateOptions(section);
      const message = q("#strategy-template-message", section);
      if (message) message.textContent = "Local template deleted. The Golden Bot preset is selected.";
    });
  }

  function enhance() {
    scheduled = false;
    const card = q(".strategy-builder-card");
    if (!card) return;
    const current = q("#strategy-template-library", card);
    if (current) {
      syncLibraryState(current);
      return;
    }
    const head = q(".builder-card-head", card);
    if (!head) return;
    head.insertAdjacentHTML("afterend", libraryHtml());
    const section = q("#strategy-template-library", card);
    bindLibrary(section);
    syncLibraryState(section);
  }

  function scheduleEnhance() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(enhance);
  }

  async function maybeApplyDefault(payload = null) {
    if (wasDefaultInitialized(payload) || applying) return;
    if (!q(".strategy-builder-card")) return;
    markDefaultInitialized(payload);
    await applyTemplate(BUILT_INS.find((item) => item.id === DEFAULT_ID), { markDefault: true });
  }

  function installDefaultDetector() {
    const previousFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const response = await previousFetch(input, init);
      const url = typeof input === "string" ? input : String(input?.url || "");
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      if (response.ok && method === "GET" && url.includes("/me/custom-strategy")) {
        response.clone().json().then((payload) => {
          if (payload?.authenticated && payload?.config?.configured === false) window.setTimeout(() => maybeApplyDefault(payload), 40);
        }).catch(() => {});
      }
      return response;
    };

    window.setTimeout(async () => {
      try {
        const response = await fetch("/me/custom-strategy", { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } });
        if (!response.ok) return;
        const payload = await response.json();
        if (payload?.authenticated && payload?.config?.configured === false) await maybeApplyDefault(payload);
      } catch (_) {}
    }, 450);
  }

  document.addEventListener("click", (event) => {
    const reset = event.target?.closest?.("[data-reset-strategy]");
    if (!reset) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!window.confirm("Reset the builder to the Over 1 Recovery Over 4 Golden Bot default? Your locally saved My Templates will stay available.")) return;
    selectedId = DEFAULT_ID;
    applyTemplate(BUILT_INS.find((item) => item.id === DEFAULT_ID), { markDefault: true });
  }, true);

  new MutationObserver(scheduleEnhance).observe(document.documentElement, { childList: true, subtree: true });
  installDefaultDetector();
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleEnhance, { once: true })
    : scheduleEnhance();

  window.FOA_STRATEGY_TEMPLATE_LIBRARY = {
    version: VERSION,
    builtIns: BUILT_INS.map((item) => ({ id: item.id, name: item.name, analysis: item.analysis, side: item.side })),
    load: (id) => {
      const template = allTemplates().find((item) => item.id === id);
      if (template) return applyTemplate(template);
      return Promise.resolve();
    },
  };
})();
