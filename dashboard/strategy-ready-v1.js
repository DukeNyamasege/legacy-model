(() => {
  "use strict";

  if (window.__FOA_STRATEGY_READY_ACTION3__) return;
  window.__FOA_STRATEGY_READY_ACTION3__ = true;

  const VERSION = "20260817-action3-1";
  const RESULT_KEY = "foa-text-strategy-result-v1";
  const USER_TEMPLATE_KEY = "foa-user-strategy-templates-v1";
  const BUILDER_KEY = "foa-builder-draft-v2";
  const ROUTE_KEY = "foa-automation-route-session-v1";
  const SCHEDULE_HANDOFF_KEY = "foa-schedule-selected-strategy-v1";
  let scheduled = false;
  let working = false;
  let state = null;

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const esc = (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

  function icon(name) {
    const c = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    const map = {
      back: `<svg ${c}><path d="m15 18-6-6 6-6"/></svg>`,
      check: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg>`,
      edit: `<svg ${c}><path d="M4 20h4l11-11-4-4L4 16z"/><path d="m13 7 4 4"/></svg>`,
      save: `<svg ${c}><path d="M5 3h12l2 2v16H5z"/><path d="M8 3v6h8V3M8 17h8"/></svg>`,
      play: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/></svg>`,
      calendar: `<svg ${c}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18"/></svg>`,
      cubes: `<svg ${c}><path d="m12 3 4 2.2v4.6L12 12l-4-2.2V5.2zM7 12l4 2.2v4.6L7 21l-4-2.2v-4.6zM17 12l4 2.2v4.6L17 21l-4-2.2v-4.6z"/></svg>`,
      wand: `<svg ${c}><path d="m4 20 10-10M11 5l1-3 1 3 3 1-3 1-1 3-1-3-3-1zM18 13l.8-2 .7 2 2 .7-2 .8-.7 2-.8-2-2-.8z"/></svg>`,
      market: `<svg ${c}><path d="M4 19V9M9 19V5M14 19v-7M19 19V3"/><path d="m3 8 5-3 5 2 7-5"/></svg>`,
      target: `<svg ${c}><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/></svg>`,
      shield: `<svg ${c}><path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6z"/><path d="m9 12 2 2 4-4"/></svg>`,
      alert: `<svg ${c}><path d="M12 3 2 20h20z"/><path d="M12 9v4M12 17h.01"/></svg>`,
      trash: `<svg ${c}><path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6"/></svg>`,
    };
    return map[name] || map.check;
  }

  function readJSON(storage, key, fallback = null) {
    try {
      const raw = storage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) { return fallback; }
  }

  function writeJSON(storage, key, value) {
    try { storage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; }
  }

  function loadState() {
    if (state) return state;
    const stored = readJSON(sessionStorage, RESULT_KEY, null);
    if (!stored || typeof stored !== "object") return null;
    state = clone(stored);
    state.name = String(state.name || "AI Strategy").slice(0, 80);
    state.settings = state.settings || { stake_amount: 0.5, take_profit: 0, stop_loss: 0 };
    state.custom_strategy = state.custom_strategy || {};
    state.builder = state.builder || {};
    state.adjustments = Array.isArray(state.adjustments) ? state.adjustments : [];
    state.source = "ai";
    return state;
  }

  function saveState() {
    if (!state) return;
    syncBuilder();
    writeJSON(sessionStorage, RESULT_KEY, state);
  }

  function currentRoute() {
    return String(document.body.dataset.automationRoute || String(location.hash || "").replace(/^#\/?/, "").split(/[?&]/)[0]).toLowerCase();
  }

  function isAuthenticated() {
    return Boolean(
      window.FOA_NETLIFY_LIVE_CACHE?.me?.authenticated
      || window.FOA_BOOT_SESSION?.authenticated
      || q(".builder-header #logout")
      || q("#foa-simple-app .account-pill"),
    );
  }

  function navigate(route) {
    if (typeof window.FOA_AUTOMATION_NAVIGATE === "function") {
      window.FOA_AUTOMATION_NAVIGATE(route);
      return;
    }
    try { sessionStorage.setItem(ROUTE_KEY, route); } catch (_) {}
    location.hash = `#/${route}`;
  }

  function tradeGroup(side) {
    if (["over", "under"].includes(side)) return "over_under";
    if (["matches", "differs"].includes(side)) return "matches_differs";
    if (["even", "odd"].includes(side)) return "odd_even";
    return "rise_fall";
  }

  function conditionLabel(condition) {
    if (!condition) return "Condition";
    if (condition.kind === "digit_compare") return `Last ${condition.window} digits ${condition.operator || "<="} ${condition.value ?? 0}`;
    if (condition.kind === "percentage") {
      const target = String(condition.target || "digit").replaceAll("_", " ");
      const digit = condition.value === null || condition.value === undefined || ["even", "odd", "rise", "fall"].includes(target) ? "" : ` ${condition.value}`;
      return `${target}${digit} percentage over ${condition.window} ticks ${condition.operator || ">="} ${Number(condition.threshold || 0)}%`;
    }
    if (condition.kind === "direction") return `Last ${condition.window} ticks are ${condition.direction || "rising"}`;
    if (condition.kind === "digit_parity") return `Last ${condition.window} digits are ${condition.parity || "even"}`;
    return String(condition.kind || "Condition").replaceAll("_", " ");
  }

  function marketLabel(custom) {
    const mode = String(custom.market_mode || "all");
    const markets = Array.isArray(custom.markets) ? custom.markets : [];
    if (mode === "all" || !markets.length) return "All supported markets";
    if (markets.length === 1) return markets[0];
    return `${markets.length} selected markets`;
  }

  function contractLabel(custom) {
    const side = String(custom.trade_type || "over");
    if (["over", "under", "matches", "differs"].includes(side)) return `${side[0].toUpperCase()}${side.slice(1)} ${custom.prediction ?? 0}`;
    return side[0].toUpperCase() + side.slice(1);
  }

  function syncBuilder() {
    if (!state) return;
    const custom = state.custom_strategy || {};
    const builder = state.builder || {};
    const conditions = Array.isArray(custom.conditions) ? custom.conditions : [];
    const digit = conditions.find((item) => item.kind === "digit_compare");
    const percentage = conditions.find((item) => item.kind === "percentage");
    const direction = conditions.find((item) => item.kind === "direction");
    const parity = conditions.find((item) => item.kind === "digit_parity");
    const strategyMode = digit && percentage ? "combined" : percentage ? "percentage" : "last_digit";
    const side = String(custom.trade_type || "over");
    const markets = Array.isArray(custom.markets) ? custom.markets : [];

    builder.version = 3;
    builder.name = state.name;
    builder.strategyMode = strategyMode;
    builder.marketMode = String(custom.market_mode || "all");
    builder.markets = clone(markets);
    builder.oneMarket = markets[0] || builder.oneMarket || "1HZ100V";
    builder.lastRule = {
      window: Number((digit || parity || {}).window || builder.lastRule?.window || 3),
      target: "last_digits",
      operator: String((digit || {}).operator || builder.lastRule?.operator || "<="),
      value: Number((digit || {}).value ?? builder.lastRule?.value ?? 3),
    };
    builder.percentageRule = {
      target: String((percentage || {}).target || builder.percentageRule?.target || side),
      value: Number((percentage || {}).value ?? builder.percentageRule?.value ?? custom.prediction ?? 0),
      window: Number((percentage || {}).window || builder.percentageRule?.window || 1000),
      operator: String((percentage || {}).operator || builder.percentageRule?.operator || ">="),
      threshold: Number((percentage || {}).threshold ?? builder.percentageRule?.threshold ?? 70),
    };
    builder.tickDirectionRule = {
      enabled: Boolean(direction),
      window: Number(direction?.window || builder.tickDirectionRule?.window || 3),
      direction: String(direction?.direction || builder.tickDirectionRule?.direction || "rising"),
    };
    builder.trade = { group: tradeGroup(side), side, prediction: Number(custom.prediction ?? 0) };
    builder.reanalyze = clone(custom.reanalyze || builder.reanalyze || { mode: "after_every_trade", losses: 1, wins: 1 });
    builder.money = {
      ...(builder.money || {}),
      stake: Number(state.settings?.stake_amount ?? builder.money?.stake ?? 0.5),
      takeProfit: Number(state.settings?.take_profit ?? builder.money?.takeProfit ?? 0),
      stopLoss: Number(state.settings?.stop_loss ?? builder.money?.stopLoss ?? 0),
      martingale: Number(builder.money?.martingale || 1.2),
      ticks: Number(custom.duration_ticks || builder.money?.ticks || 1),
    };
    const hook = custom.virtual_hook || {};
    builder.virtualHook = {
      enabled: Boolean(custom.virtual_hook_enabled),
      enterAfterLosses: Number(hook.enter_after_losses || builder.virtualHook?.enterAfterLosses || 2),
      exitAfterConsecutiveWins: Number(hook.exit_after_consecutive_wins || builder.virtualHook?.exitAfterConsecutiveWins || 2),
    };
    state.builder = builder;
    state.rules = conditions.map(conditionLabel);
    state.market_label = marketLabel(custom);
    state.contract_label = contractLabel(custom);
  }

  function option(value, label, selected) {
    return `<option value="${esc(value)}" ${String(value) === String(selected) ? "selected" : ""}>${esc(label)}</option>`;
  }

  function conditionEditor(condition, index) {
    const kind = String(condition.kind || "digit_compare");
    const operators = ["<", "<=", "==", ">=", ">"].map((op) => option(op, op, condition.operator)).join("");
    let fields = "";
    if (kind === "digit_compare") {
      fields = `<label><span>Window</span><input type="number" min="1" max="10000" value="${Number(condition.window || 3)}" data-ready-condition="${index}" data-ready-field="window"></label><label><span>Operator</span><select data-ready-condition="${index}" data-ready-field="operator">${operators}</select></label><label><span>Digit</span><input type="number" min="0" max="9" value="${Number(condition.value ?? 3)}" data-ready-condition="${index}" data-ready-field="value"></label>`;
    } else if (kind === "percentage") {
      const target = String(condition.target || "over");
      fields = `<label><span>Target</span><select data-ready-condition="${index}" data-ready-field="target">${["over","under","even","odd","digit","rise","fall"].map((v) => option(v, v[0].toUpperCase()+v.slice(1), target)).join("")}</select></label><label><span>Digit</span><input type="number" min="0" max="9" value="${Number(condition.value ?? 0)}" data-ready-condition="${index}" data-ready-field="value"></label><label><span>Window</span><input type="number" min="1" max="10000" value="${Number(condition.window || 1000)}" data-ready-condition="${index}" data-ready-field="window"></label><label><span>Operator</span><select data-ready-condition="${index}" data-ready-field="operator">${operators}</select></label><label><span>Percent</span><input type="number" min="0" max="100" step="0.1" value="${Number(condition.threshold || 0)}" data-ready-condition="${index}" data-ready-field="threshold"></label>`;
    } else if (kind === "direction") {
      fields = `<label><span>Window</span><input type="number" min="1" max="10000" value="${Number(condition.window || 3)}" data-ready-condition="${index}" data-ready-field="window"></label><label><span>Direction</span><select data-ready-condition="${index}" data-ready-field="direction">${["rising","falling","no_move"].map((v) => option(v, v.replace("_"," "), condition.direction)).join("")}</select></label>`;
    } else {
      fields = `<label><span>Window</span><input type="number" min="1" max="10000" value="${Number(condition.window || 3)}" data-ready-condition="${index}" data-ready-field="window"></label><label><span>Parity</span><select data-ready-condition="${index}" data-ready-field="parity">${option("even","Even",condition.parity)}${option("odd","Odd",condition.parity)}</select></label>`;
    }
    return `<article class="foa-ready-rule" data-ready-rule="${index}"><div class="foa-ready-rule-head"><span>${icon("check")}</span><div><small>${esc(kind.replaceAll("_", " "))}</small><strong>${esc(conditionLabel(condition))}</strong></div><button type="button" data-ready-remove-condition="${index}" aria-label="Remove condition">${icon("trash")}</button></div><div class="foa-ready-rule-fields">${fields}</div></article>`;
  }

  function markup() {
    const current = loadState();
    if (!current) {
      return `<section class="foa-automation-page foa-strategy-ready-page"><header class="foa-ready-header"><button type="button" data-ready-back>${icon("back")}</button><div><h1>Strategy Ready</h1><p>No generated strategy is waiting for review.</p></div></header><article class="foa-ready-empty"><span>${icon("wand")}</span><strong>Create a strategy first</strong><p>Describe your strategy in Text to Strategy, then return here to review it.</p><button type="button" data-ready-edit-description>Open Text to Strategy</button></article></section>`;
    }
    syncBuilder();
    const custom = current.custom_strategy;
    const settings = current.settings;
    const conditions = Array.isArray(custom.conditions) ? custom.conditions : [];
    const markets = Array.isArray(custom.markets) ? custom.markets.join(", ") : "";
    const predictionRequired = ["over", "under", "matches", "differs"].includes(String(custom.trade_type || ""));
    const hook = custom.virtual_hook || {};
    const reanalyze = custom.reanalyze || {};
    const assumptions = current.adjustments.length
      ? current.adjustments.map((item) => `<li>${icon("alert")}<span>${esc(item)}</span></li>`).join("")
      : `<li class="exact">${icon("check")}<span>Your description mapped directly to supported DerivAdmin rules.</span></li>`;

    return `<section class="foa-automation-page foa-strategy-ready-page" data-strategy-ready-version="${VERSION}">
      <header class="foa-ready-header"><button type="button" data-ready-back aria-label="Back to description">${icon("back")}</button><div><span>AI STRATEGY</span><h1>Strategy Ready</h1><p>Review every rule before you save, trade or schedule.</p></div><b>${icon("check")} Validated</b></header>

      <section class="foa-ready-hero"><div class="foa-ready-hero-icon">${icon("wand")}</div><div class="foa-ready-hero-copy"><small>STRATEGY NAME</small><input id="foa-ready-name" maxlength="80" value="${esc(current.name)}"><span>${esc(current.compiler || "nearest-supported-v1")} · ${Number(current.word_count || 0)} words interpreted</span></div><div class="foa-ready-score"><b>READY</b><span>Review required</span></div></section>

      <section class="foa-ready-summary-grid">
        <article><span>${icon("market")}</span><small>Market</small><strong>${esc(marketLabel(custom))}</strong></article>
        <article><span>${icon("target")}</span><small>Contract</small><strong>${esc(contractLabel(custom))}</strong></article>
        <article><span>${icon("shield")}</span><small>Stake</small><strong>$${Number(settings.stake_amount || 0).toFixed(2)}</strong></article>
      </section>

      <section class="foa-ready-card"><div class="foa-ready-card-head"><div><small>01 · EXECUTION</small><h2>Market & Contract</h2></div><span>${icon("edit")}</span></div><div class="foa-ready-form-grid">
        <label><span>Market mode</span><select data-ready-custom="market_mode">${option("all","All markets",custom.market_mode)}${option("single","One market",custom.market_mode)}${option("selected","Selected markets",custom.market_mode)}</select></label>
        <label class="wide"><span>Markets</span><input data-ready-markets value="${esc(markets)}" placeholder="1HZ100V or comma-separated symbols"><small>Use Deriv symbols. For All markets this list can stay empty.</small></label>
        <label><span>Trade type</span><select data-ready-custom="trade_type">${["over","under","matches","differs","even","odd","rise","fall"].map((v) => option(v, v[0].toUpperCase()+v.slice(1), custom.trade_type)).join("")}</select></label>
        <label><span>Prediction</span><input type="number" min="0" max="9" data-ready-custom="prediction" value="${predictionRequired ? Number(custom.prediction ?? 0) : 0}" ${predictionRequired ? "" : "disabled"}></label>
        <label><span>Duration (ticks)</span><input type="number" min="1" max="100" data-ready-custom="duration_ticks" value="${Number(custom.duration_ticks || 1)}"></label>
      </div></section>

      <section class="foa-ready-card"><div class="foa-ready-card-head"><div><small>02 · ENTRY LOGIC</small><h2>Rules understood</h2><p>These are the actual conditions that will be sent to the Custom Strategy engine.</p></div><span>${conditions.length}</span></div><div class="foa-ready-rules">${conditions.map(conditionEditor).join("")}</div><button type="button" class="foa-ready-builder-link" data-ready-open-builder>${icon("cubes")}<span>Need a more advanced edit?</span><b>Open in Strategy Builder</b></button></section>

      <section class="foa-ready-card"><div class="foa-ready-card-head"><div><small>03 · MONEY</small><h2>Stake & Session Risk</h2></div><span>${icon("shield")}</span></div><div class="foa-ready-money-grid"><label><span>Stake</span><div><b>$</b><input type="number" min="0.35" step="0.01" data-ready-setting="stake_amount" value="${Number(settings.stake_amount || 0.5)}"></div></label><label><span>Take Profit</span><div><b>$</b><input type="number" min="0" step="0.01" data-ready-setting="take_profit" value="${Number(settings.take_profit || 0)}"></div></label><label><span>Stop Loss</span><div><b>$</b><input type="number" min="0" step="0.01" data-ready-setting="stop_loss" value="${Number(settings.stop_loss || 0)}"></div></label></div></section>

      <section class="foa-ready-card"><div class="foa-ready-card-head"><div><small>04 · AFTER RESULT</small><h2>Re-analysis & Virtual Guard</h2></div><span>${icon("shield")}</span></div><div class="foa-ready-form-grid"><label><span>Re-analyze</span><select data-ready-reanalyze="mode">${option("after_every_trade","After every trade",reanalyze.mode)}${option("after_loss","After loss threshold",reanalyze.mode)}${option("after_win","After win threshold",reanalyze.mode)}</select></label><label><span>Losses</span><input type="number" min="1" max="50" data-ready-reanalyze="losses" value="${Number(reanalyze.losses || 1)}"></label><label><span>Wins</span><input type="number" min="1" max="50" data-ready-reanalyze="wins" value="${Number(reanalyze.wins || 1)}"></label><label class="toggle-row"><span>Virtual protection</span><input type="checkbox" data-ready-virtual-enabled ${custom.virtual_hook_enabled ? "checked" : ""}></label><label><span>Enter after losses</span><input type="number" min="1" max="50" data-ready-virtual="enter_after_losses" value="${Number(hook.enter_after_losses || 2)}"></label><label><span>Exit after consecutive wins</span><input type="number" min="1" max="50" data-ready-virtual="exit_after_consecutive_wins" value="${Number(hook.exit_after_consecutive_wins || 2)}"></label></div></section>

      <section class="foa-ready-assumptions"><div><span>${icon(current.adjustments.length ? "alert" : "check")}</span><div><small>BEST POSSIBLE INTERPRETATION</small><h2>${current.adjustments.length ? "We made a few safe assumptions" : "Your description was clear"}</h2></div></div><ul>${assumptions}</ul></section>

      <div id="foa-ready-message" class="foa-ready-message" hidden></div>
      <section class="foa-ready-actions"><button type="button" class="secondary" data-ready-save>${icon("save")}<span>Save Strategy</span></button><button type="button" class="primary" data-ready-trade>${icon("play")}<span>Trade Now</span></button><button type="button" class="schedule" data-ready-schedule>${icon("calendar")}<span>Schedule</span></button></section>
      <button type="button" class="foa-ready-edit-description" data-ready-edit-description>${icon("edit")} Edit original description</button>
      <p class="foa-ready-risk">Generated strategies are automation instructions, not profit guarantees. Trade Now still uses the existing authenticated DerivAdmin execution engine and account-level risk controls.</p>
    </section>`;
  }

  function message(text, tone = "info") {
    const box = q("#foa-ready-message");
    if (!box) return;
    box.hidden = false;
    box.dataset.tone = tone;
    box.textContent = String(text || "");
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function persistLibrary() {
    if (!state) return null;
    saveState();
    const rows = readJSON(localStorage, USER_TEMPLATE_KEY, []);
    const list = Array.isArray(rows) ? rows : [];
    const id = String(state.library_id || `ai-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
    state.library_id = id;
    const existing = list.find((item) => item?.id === id);
    const item = {
      id,
      name: String(state.name || "AI Strategy").trim().slice(0, 80),
      analysis: String(state.builder?.strategyMode || "combined"),
      side: String(state.custom_strategy?.trade_type || "over"),
      summary: `${marketLabel(state.custom_strategy)} · ${contractLabel(state.custom_strategy)} · AI generated`,
      builder: clone(state.builder),
      result: {
        routingEnabled: false,
        afterLoss: null,
        recoveryMode: String(state.custom_strategy?.martingale?.mode || "multiplier"),
        splitCount: Number(state.custom_strategy?.martingale?.split_count || 1),
      },
      predictionMode: "",
      predictionWindow: Number(state.builder?.percentageRule?.window || 100),
      builtIn: false,
      source: "ai",
      compiler: String(state.compiler || "nearest-supported-v1"),
      sourceText: String(state.source_text || ""),
      createdAt: existing?.createdAt || new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    const next = list.filter((row) => row?.id !== id);
    next.push(item);
    writeJSON(localStorage, USER_TEMPLATE_KEY, next.slice(-50));
    saveState();
    document.dispatchEvent(new CustomEvent("foa:strategy-library-updated", { detail: { id, source: "ai" } }));
    return item;
  }

  function backendPayload() {
    saveState();
    const custom = clone(state.custom_strategy || {});
    return {
      market_mode: String(custom.market_mode || "all"),
      markets: Array.isArray(custom.markets) ? custom.markets : [],
      trade_type: String(custom.trade_type || "over"),
      prediction: ["over","under","matches","differs"].includes(String(custom.trade_type || "")) ? Number(custom.prediction ?? 0) : null,
      duration_ticks: Number(custom.duration_ticks || 1),
      conditions: Array.isArray(custom.conditions) ? custom.conditions : [],
      match: String(custom.match || "all"),
      reanalyze: custom.reanalyze || { mode: "after_every_trade", losses: 1, wins: 1 },
      virtual_hook_enabled: Boolean(custom.virtual_hook_enabled),
      virtual_hook: custom.virtual_hook || { enabled: false, enter_after_losses: 2, exit_after_consecutive_wins: 2 },
      martingale: custom.martingale || { mode: "multiplier", multiplier: 1.2, split_count: 1 },
      execution_settings: {
        stake_amount: Math.max(0.35, Number(state.settings?.stake_amount || 0.5)),
        take_profit: Math.max(0, Number(state.settings?.take_profit || 0)),
        stop_loss: Math.max(0, Number(state.settings?.stop_loss || 0)),
        martingale_enabled: true,
      },
    };
  }

  async function saveBackend() {
    const response = await fetch("/me/custom-strategy", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(backendPayload()),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || body.message || `Save failed (${response.status})`);
    return body;
  }

  async function saveAction() {
    if (working || !state) return;
    working = true;
    const local = persistLibrary();
    message(`${local?.name || state.name} saved to My Strategies. Activating it for this account...`, "info");
    try {
      await saveBackend();
      message(`${state.name} is saved in your Strategy Library and is now the current stopped Custom Strategy for this account.`, "success");
    } catch (error) {
      message(`${state.name} is saved in your Strategy Library. ${String(error?.message || error)}`, "warning");
    } finally { working = false; }
  }

  async function tradeNow() {
    if (working || !state) return;
    working = true;
    persistLibrary();
    message("Saving the reviewed strategy to your account...", "info");
    try {
      await saveBackend();
      message("Strategy saved. Initializing the authenticated execution session...", "info");
      const response = await fetch("/me/resume-trading", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Accept": "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "start_again" }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || body.message || `Start failed (${response.status})`);
      message("Execution session is starting. Opening Trades...", "success");
      setTimeout(() => navigate("trades"), 180);
    } catch (error) {
      message(String(error?.message || error || "Unable to start trading."), "error");
    } finally { working = false; }
  }

  function scheduleAction() {
    if (!state) return;
    const item = persistLibrary();
    writeJSON(sessionStorage, SCHEDULE_HANDOFF_KEY, {
      id: item?.id || state.library_id,
      name: state.name,
      builder: state.builder,
      custom_strategy: state.custom_strategy,
      settings: state.settings,
      source: "ai",
      savedAt: new Date().toISOString(),
    });
    navigate("schedule");
  }

  function openBuilder() {
    if (!state) return;
    persistLibrary();
    saveState();
    writeJSON(localStorage, BUILDER_KEY, state.builder);
    navigate("builder");
  }

  function bindReady(root) {
    q("[data-ready-back]", root)?.addEventListener("click", () => navigate("ai"));
    q("[data-ready-edit-description]", root)?.addEventListener("click", () => navigate("ai"));
    q("[data-ready-open-builder]", root)?.addEventListener("click", openBuilder);
    q("[data-ready-save]", root)?.addEventListener("click", saveAction);
    q("[data-ready-trade]", root)?.addEventListener("click", tradeNow);
    q("[data-ready-schedule]", root)?.addEventListener("click", scheduleAction);

    q("#foa-ready-name", root)?.addEventListener("input", (event) => {
      state.name = String(event.currentTarget.value || "AI Strategy").slice(0, 80);
      saveState();
    });

    qa("[data-ready-custom]", root).forEach((field) => field.addEventListener("change", () => {
      const key = field.dataset.readyCustom;
      let value = field.value;
      if (["prediction", "duration_ticks"].includes(key)) value = Number(value);
      state.custom_strategy[key] = value;
      saveState();
      scheduleRender(true);
    }));

    q("[data-ready-markets]", root)?.addEventListener("change", (event) => {
      state.custom_strategy.markets = String(event.currentTarget.value || "").split(",").map((value) => value.trim()).filter(Boolean);
      saveState();
      scheduleRender(true);
    });

    qa("[data-ready-setting]", root).forEach((field) => field.addEventListener("change", () => {
      state.settings[field.dataset.readySetting] = Number(field.value || 0);
      saveState();
      scheduleRender(true);
    }));

    qa("[data-ready-reanalyze]", root).forEach((field) => field.addEventListener("change", () => {
      state.custom_strategy.reanalyze = state.custom_strategy.reanalyze || {};
      const key = field.dataset.readyReanalyze;
      state.custom_strategy.reanalyze[key] = key === "mode" ? field.value : Number(field.value || 1);
      saveState();
    }));

    q("[data-ready-virtual-enabled]", root)?.addEventListener("change", (event) => {
      state.custom_strategy.virtual_hook_enabled = Boolean(event.currentTarget.checked);
      state.custom_strategy.virtual_hook = state.custom_strategy.virtual_hook || {};
      state.custom_strategy.virtual_hook.enabled = Boolean(event.currentTarget.checked);
      saveState();
    });

    qa("[data-ready-virtual]", root).forEach((field) => field.addEventListener("change", () => {
      state.custom_strategy.virtual_hook = state.custom_strategy.virtual_hook || {};
      state.custom_strategy.virtual_hook[field.dataset.readyVirtual] = Number(field.value || 1);
      saveState();
    }));

    qa("[data-ready-condition]", root).forEach((field) => field.addEventListener("change", () => {
      const index = Number(field.dataset.readyCondition);
      const condition = state.custom_strategy.conditions?.[index];
      if (!condition) return;
      const key = field.dataset.readyField;
      condition[key] = ["window", "value", "threshold"].includes(key) ? Number(field.value || 0) : field.value;
      saveState();
      scheduleRender(true);
    }));

    qa("[data-ready-remove-condition]", root).forEach((button) => button.addEventListener("click", () => {
      const index = Number(button.dataset.readyRemoveCondition);
      if (!Array.isArray(state.custom_strategy.conditions) || state.custom_strategy.conditions.length <= 1) {
        message("A Custom Strategy must keep at least one entry condition.", "warning");
        return;
      }
      state.custom_strategy.conditions.splice(index, 1);
      saveState();
      scheduleRender(true);
    }));
  }

  function enhanceAiResult() {
    if (currentRoute() !== "ai") return;
    const result = q("[data-ai-result]");
    if (!result || q("[data-action3-review]", result)) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "foa-action3-review-button";
    button.dataset.action3Review = "true";
    button.innerHTML = `${icon("check")}<span>Review Strategy</span><b>Continue ›</b>`;
    button.addEventListener("click", () => navigate("ready"));
    result.appendChild(button);
  }

  function render() {
    scheduled = false;
    if (!isAuthenticated()) return;
    if (currentRoute() === "ai") {
      enhanceAiResult();
      return;
    }
    if (currentRoute() !== "ready") return;
    const main = q("#telegram-dashboard-snapshot > main");
    if (!main) return;
    const current = q(`.foa-strategy-ready-page[data-strategy-ready-version="${VERSION}"]`, main);
    if (!current) {
      main.innerHTML = markup();
      bindReady(main);
    }
    window.FOA_STRATEGY_READY_ACTION3_VERSION = VERSION;
  }

  function scheduleRender(force = false) {
    if (force) {
      const main = q("#telegram-dashboard-snapshot > main");
      if (currentRoute() === "ready" && main) main.innerHTML = "";
    }
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(render);
  }

  document.addEventListener("foa:strategy-generated", () => scheduleRender());
  addEventListener("hashchange", () => scheduleRender(true));
  addEventListener("pageshow", scheduleRender);
  addEventListener("focus", scheduleRender);
  new MutationObserver(scheduleRender).observe(document.documentElement, { childList: true, subtree: true });

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleRender, { once: true })
    : scheduleRender();
})();
