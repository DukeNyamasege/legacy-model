(() => {
  "use strict";

  const root = document.getElementById("derivadmin-root");
  if (!root) return;

  const ROUTES = new Set(["home", "builder", "ai", "ready", "schedule", "profile", "trades", "timezone"]);
  const STORE_TEMPLATES = "foa-user-strategy-templates-v2";
  const STORE_READY = "foa-text-strategy-result-v2";
  const DEFAULT_TZ = "Africa/Nairobi";
  const TIMEZONES = [
    ["Africa/Nairobi", "Nairobi", "East Africa Time"],
    ["Africa/Kampala", "Kampala", "East Africa Time"],
    ["Africa/Dar_es_Salaam", "Dar es Salaam", "East Africa Time"],
    ["Europe/London", "London", "United Kingdom"],
    ["America/New_York", "New York", "United States"],
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
    loaded: false,
  };

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

  function shell(content, options = {}) {
    const showNav = options.nav !== false;
    const title = options.title || "DerivAdmin";
    return `<div class="app-shell ${showNav ? "with-nav" : ""}">
      <header class="topbar">
        <button class="brand-lockup" data-route="home" aria-label="DerivAdmin Home">
          <span class="brand-mark">D</span><span><b>DerivAdmin</b><small>Home of Automation</small></span>
        </button>
        <div class="topbar-actions">
          ${state.me?.authenticated ? `<button class="run-shortcut" data-route="trades">${miniIcon("trades")}<span>Runs</span></button>` : ""}
          <span class="page-kicker">${esc(title)}</span>
        </div>
      </header>
      ${messages()}
      <main class="app-main">${content}</main>
      ${showNav ? nav() : ""}
    </div>`;
  }

  function messages() {
    return `${state.error ? `<div class="global-message error">${esc(state.error)}</div>` : ""}${state.notice ? `<div class="global-message success">${esc(state.notice)}</div>` : ""}`;
  }

  function landing() {
    return `<div class="landing-page">
      <div class="landing-glow one"></div><div class="landing-glow two"></div>
      <header class="landing-header"><div class="brand-lockup static"><span class="brand-mark">D</span><span><b>DerivAdmin</b><small>Home of Automation</small></span></div></header>
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
    const m = metrics();
    const currency = state.me?.currency || "USD";
    const nextSchedule = (state.schedules?.schedules || []).find((item) => ["scheduled", "waiting", "starting", "running"].includes(String(item.status || "").toLowerCase()));
    const templates = readJSON(STORE_TEMPLATES, []);
    const winRate = m.wins + m.losses ? (m.wins / (m.wins + m.losses)) : 0;
    const content = `<section class="home-hero">
      <div><span class="eyebrow">AUTOMATION CONTROL CENTER</span><h1>Good ${greeting()}, trader.</h1><p>Build, describe and schedule your next automated session.</p></div>
      <button class="balance-pill" data-route="trades"><span>${quill(state.me?.account_type === "real" ? "realAccount" : "demoAccount")}</span><b>${money(m.balance, currency)}</b><small>${esc(String(state.me?.account_type || "demo").toUpperCase())}</small></button>
    </section>
    <section class="kpi-strip">
      <article><small>Balance</small><b>${money(m.balance, currency)}</b></article>
      <article><small>Runs</small><b>${m.runs}</b></article>
      <article><small>Wins</small><b class="positive">${m.wins}</b></article>
      <article><small>Win rate</small><b>${pct(winRate)}</b></article>
      <article><small>P/L</small><b class="${m.profit >= 0 ? "positive" : "negative"}">${money(m.profit, currency)}</b></article>
    </section>
    <section class="section-head"><div><span class="eyebrow">CREATE</span><h2>Choose how you automate</h2></div></section>
    <section class="automation-grid">
      ${featureCard("builder", "builder", "Strategy Builder", "Build conditions visually", "Manual control with deterministic execution.")}
      ${featureCard("ai", "spark", "Text to Strategy", "Describe it in plain language", "Up to 250 words. We compile the closest supported strategy.")}
      ${featureCard("schedule", "schedule", "Schedule Trading", "Set the time. We run it.", "Persistent VPS sessions continue even when your browser is closed.")}
    </section>
    <section class="split-grid">
      <article class="panel automation-status"><div class="panel-title"><div><span class="eyebrow">MY AUTOMATION</span><h3>${nextSchedule ? esc(nextSchedule.strategy_name || "Scheduled session") : "No scheduled session"}</h3></div><button class="text-button" data-route="schedule">View all</button></div>
        ${nextSchedule ? `<div class="schedule-status-line"><span class="status-dot ${esc(nextSchedule.status)}"></span><b>${esc(nextSchedule.status)}</b><span>${esc(nextSchedule.scheduled_local || nextSchedule.scheduled_for_utc || "")}</span></div>` : `<p class="muted">Your next automated trading session will appear here.</p>`}
      </article>
      <article class="panel"><div class="panel-title"><div><span class="eyebrow">STRATEGY LIBRARY</span><h3>${templates.length} saved strateg${templates.length === 1 ? "y" : "ies"}</h3></div><button class="text-button" data-route="builder">New strategy</button></div>
        <div class="library-row">${templates.slice(0, 3).map((item) => `<button data-load-template="${esc(item.id)}"><span>${miniIcon("builder")}</span><span><b>${esc(item.name || "Strategy")}</b><small>${esc(item.market || item.strategy?.markets?.[0] || "Deriv Options")}</small></span>${miniIcon("arrow")}</button>`).join("") || `<p class="muted">Save a Builder or AI strategy to build your library.</p>`}</div>
      </article>
    </section>`;
    return shell(content, { title: "Home" });
  }

  function featureCard(route, icon, title, subtitle, body) {
    return `<button class="feature-card" data-route="${route}"><span class="feature-icon">${miniIcon(icon)}</span><span><small>${esc(subtitle)}</small><b>${esc(title)}</b><em>${esc(body)}</em></span><span class="feature-arrow">${miniIcon("arrow")}</span></button>`;
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
    return shell(content, { title: "Timezone", nav: false });
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
    const content = `<section class="page-intro"><span class="eyebrow">TEXT TO STRATEGY</span><h1>Describe the trade you want.</h1><p>Write naturally. We turn your idea into the closest supported deterministic strategy, then you review it before anything can trade.</p></section>
    <section class="panel ai-compose">
      <div class="panel-title"><div><span class="eyebrow">YOUR IDEA</span><h3>What should the strategy do?</h3></div><span class="word-count" id="word-count">0 / 250 words</span></div>
      <textarea id="strategy-text" maxlength="5000" placeholder="Example: Trade Volatility 100 (1s) Digit Over 3 when the last digit is 4 or greater. Use $0.50 stake, stop after $10 profit or $5 loss, and use virtual protection after two losses.">${esc(state.generated?.source_text || "")}</textarea>
      <div class="prompt-chips"><button data-prompt="Trade Volatility 100 (1s) Digit Over 3 when the last digit is 4 or greater.">Over 3</button><button data-prompt="Trade Digit Under 6 when the last digit is 5 or lower with virtual protection after two losses.">Under 6</button><button data-prompt="Trade Even when even digits are at least 55 percent in the last 100 ticks.">Even filter</button></div>
      <div class="ai-steps"><span><i>1</i>Describe</span><span><i>2</i>Review</span><span><i>3</i>Trade or schedule</span></div>
      <button class="btn primary xl" data-generate-strategy>${miniIcon("spark")} Generate Strategy</button>
    </section>`;
    return shell(content, { title: "Text to Strategy" });
  }

  function generatedCanonical() {
    const g = state.generated || {};
    return g.canonical || g.strategy || g.config || g.canonical_strategy || null;
  }

  function readyPage() {
    const g = state.generated;
    if (!g) return shell(`<section class="empty-state"><h2>No AI strategy yet</h2><p>Describe a strategy first.</p><button class="btn primary" data-route="ai">Open Text to Strategy</button></section>`, { title: "Strategy Ready" });
    const canonical = generatedCanonical() || {};
    const name = g.name || g.strategy_name || "AI Generated Strategy";
    const market = g.market_label || canonical.markets?.[0] || canonical.market || "Supported Deriv market";
    const contract = g.contract_label || canonical.trade_type || canonical.contract_type || "Custom Strategy";
    const rules = g.rules || g.entry_rules || canonical.conditions || [];
    const adjustments = g.unsupported_or_adjusted_items || g.adjustments || [];
    const interpretation = g.best_possible_interpretation || g.interpretation || "Compiled to the nearest supported deterministic strategy.";
    const content = `<section class="ready-hero"><span class="ai-badge">${miniIcon("spark")} Risk Managers AI · Generated</span><h1>Strategy Ready</h1><p>Review the exact strategy before saving, trading or scheduling it.</p></section>
    <section class="panel ready-card">
      <div class="ready-title"><div><span class="eyebrow">${esc(market)}</span><h2>${esc(name)}</h2><p>${esc(String(contract).replaceAll("_", " "))}</p></div><span class="ready-check">${miniIcon("check")}</span></div>
      <div class="strategy-facts"><article><small>Market</small><b>${esc(market)}</b></article><article><small>Contract</small><b>${esc(String(contract).replaceAll("_", " "))}</b></article><article><small>Stake</small><b>${money(canonical.execution_settings?.stake_amount || state.me?.settings?.stake_amount || 0.5)}</b></article></div>
      <div class="rule-block"><span class="eyebrow">ENTRY RULES</span>${Array.isArray(rules) && rules.length ? rules.map((rule, index) => `<div><i>${index + 1}</i><span>${esc(typeof rule === "string" ? rule : JSON.stringify(rule))}</span></div>`).join("") : `<p>${esc(g.summary || g.description || "Validated supported conditions")}</p>`}</div>
      <div class="interpretation good"><b>Best possible interpretation</b><p>${esc(typeof interpretation === "string" ? interpretation : JSON.stringify(interpretation))}</p></div>
      <div class="interpretation adjust"><b>Unsupported or adjusted items</b><p>${adjustments.length ? adjustments.map((item) => esc(typeof item === "string" ? item : JSON.stringify(item))).join(" · ") : "None — no unsupported items were reported."}</p></div>
      <div class="ready-actions"><button class="btn primary" data-ready-trade>${miniIcon("play")} Trade Now</button><button class="btn secondary" data-ready-schedule>${miniIcon("schedule")} Schedule</button><button class="btn ghost" data-ready-save>Save Strategy</button><button class="btn ghost" data-ready-builder>Edit in Builder</button></div>
    </section>`;
    return shell(content, { title: "Strategy Ready" });
  }

  function currentBuilderConfig() {
    const config = state.custom?.config || {};
    const firstCondition = (config.conditions || [])[0] || {};
    return {
      name: "My Strategy",
      market: config.markets?.[0] || "1HZ100V",
      side: config.trade_type || "over",
      prediction: config.prediction ?? 3,
      window: firstCondition.window || 1,
      operator: firstCondition.operator || ">=",
      value: firstCondition.value ?? 4,
      ticks: config.duration_ticks || 1,
      stake: state.me?.settings?.stake_amount || 0.5,
      takeProfit: state.me?.settings?.take_profit || 0,
      stopLoss: state.me?.settings?.stop_loss || 0,
      virtual: config.virtual_hook_enabled !== false,
      enterLosses: config.virtual_hook?.enter_after_losses || 2,
      exitWins: config.virtual_hook?.exit_after_consecutive_wins || 2,
    };
  }

  function builderPage() {
    const b = state.selectedStrategy?.builder || currentBuilderConfig();
    const markets = state.custom?.supported?.markets || ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V", "R_10", "R_25", "R_50", "R_75", "R_100"];
    const content = `<section class="page-intro compact"><span class="eyebrow">STRATEGY BUILDER</span><h1>Build the rules. Keep execution deterministic.</h1><p>Every saved block becomes canonical server strategy JSON. The browser never sends a direct Deriv BUY.</p></section>
    <section class="builder-layout">
      <article class="panel builder-panel">
        <div class="form-grid two"><label><span>Strategy name</span><input id="b-name" value="${esc(b.name || "My Strategy")}"></label><label><span>Market</span><select id="b-market">${markets.map((market) => `<option value="${esc(market)}" ${market === b.market ? "selected" : ""}>${esc(market)}</option>`).join("")}</select></label></div>
        <div class="builder-section"><div class="section-number">01</div><div><span class="eyebrow">CONTRACT</span><h3>What should be purchased?</h3></div></div>
        <div class="contract-choice">${["over", "under", "matches", "differs", "even", "odd", "rise", "fall"].map((side) => `<label><input type="radio" name="b-side" value="${side}" ${side === b.side ? "checked" : ""}><span>${quill(side)}<b>${side[0].toUpperCase() + side.slice(1)}</b></span></label>`).join("")}</div>
        <div class="form-grid three"><label><span>Prediction / barrier</span><input id="b-prediction" type="number" min="0" max="9" value="${esc(b.prediction)}"></label><label><span>Duration</span><input id="b-ticks" type="number" min="1" max="10" value="${esc(b.ticks)}"></label><label><span>Unit</span><input value="ticks" disabled></label></div>
        <div class="builder-section"><div class="section-number">02</div><div><span class="eyebrow">ENTRY CONDITION</span><h3>When may it qualify?</h3></div></div>
        <div class="condition-card"><span class="condition-handle">⋮⋮</span><label>Last digit</label><select id="b-operator">${[">=","<=",">","<","==","!="].map((op) => `<option ${op === b.operator ? "selected" : ""}>${esc(op)}</option>`).join("")}</select><input id="b-value" type="number" min="0" max="9" value="${esc(b.value)}"><label>Window</label><input id="b-window" type="number" min="1" max="5000" value="${esc(b.window)}"></div>
        <div class="builder-section"><div class="section-number">03</div><div><span class="eyebrow">RISK & PROTECTION</span><h3>Control the session.</h3></div></div>
        <div class="form-grid three"><label><span>Stake USD</span><input id="b-stake" type="number" min="0.35" step="0.01" value="${esc(b.stake)}"></label><label><span>Take profit</span><input id="b-tp" type="number" min="0" step="0.01" value="${esc(b.takeProfit)}"></label><label><span>Stop loss</span><input id="b-sl" type="number" min="0" step="0.01" value="${esc(b.stopLoss)}"></label></div>
        <label class="toggle-line"><input id="b-virtual" type="checkbox" ${b.virtual ? "checked" : ""}><span><b>Virtual loss protection</b><small>Mirror after losses before returning to monetary execution.</small></span></label>
        <div class="form-grid two"><label><span>Enter virtual after losses</span><input id="b-enter-losses" type="number" min="1" value="${esc(b.enterLosses)}"></label><label><span>Exit after consecutive virtual wins</span><input id="b-exit-wins" type="number" min="1" value="${esc(b.exitWins)}"></label></div>
      </article>
      <aside class="panel builder-preview"><span class="eyebrow">LIVE PREVIEW</span><h3 id="builder-preview-title">${esc(b.side?.toUpperCase() || "OVER")} ${esc(b.prediction)}</h3><p>On ${esc(b.market)} when last digit ${esc(b.operator)} ${esc(b.value)}.</p><div class="preview-icon">${quill(b.side || "over")}</div><small>Server validator remains execution authority.</small></aside>
    </section>
    <div class="builder-sticky"><button class="btn ghost" data-builder-save>Save Strategy</button><button class="btn secondary" data-builder-schedule>${miniIcon("schedule")} Schedule</button><button class="btn primary" data-builder-trade>${miniIcon("play")} Trade Now</button></div>`;
    return shell(content, { title: "Strategy Builder" });
  }

  function builderPayload() {
    const side = document.querySelector('input[name="b-side"]:checked')?.value || "over";
    const prediction = Number(document.getElementById("b-prediction")?.value || 0);
    const virtualEnabled = Boolean(document.getElementById("b-virtual")?.checked);
    return {
      market_mode: "single",
      markets: [document.getElementById("b-market")?.value || "1HZ100V"],
      trade_type: side,
      prediction: ["over", "under", "matches", "differs"].includes(side) ? prediction : null,
      duration_ticks: Number(document.getElementById("b-ticks")?.value || 1),
      conditions: [{
        kind: "digit_compare",
        window: Number(document.getElementById("b-window")?.value || 1),
        operator: document.getElementById("b-operator")?.value || ">=",
        value: Number(document.getElementById("b-value")?.value || 0),
      }],
      match: "all",
      reanalyze: {},
      virtual_hook_enabled: virtualEnabled,
      virtual_hook: {
        enabled: virtualEnabled,
        enter_after_losses: Number(document.getElementById("b-enter-losses")?.value || 2),
        exit_after_consecutive_wins: Number(document.getElementById("b-exit-wins")?.value || 2),
      },
      execution_settings: {
        stake_amount: Number(document.getElementById("b-stake")?.value || 0.5),
        take_profit: Number(document.getElementById("b-tp")?.value || 0),
        stop_loss: Number(document.getElementById("b-sl")?.value || 0),
        martingale_enabled: false,
      },
    };
  }

  function builderSnapshot() {
    const payload = builderPayload();
    return {
      name: document.getElementById("b-name")?.value?.trim() || "My Strategy",
      source: "builder",
      strategy: payload,
      builder: {
        name: document.getElementById("b-name")?.value?.trim() || "My Strategy",
        market: payload.markets[0], side: payload.trade_type, prediction: payload.prediction,
        window: payload.conditions[0].window, operator: payload.conditions[0].operator, value: payload.conditions[0].value,
        ticks: payload.duration_ticks, stake: payload.execution_settings.stake_amount,
        takeProfit: payload.execution_settings.take_profit, stopLoss: payload.execution_settings.stop_loss,
        virtual: payload.virtual_hook_enabled, enterLosses: payload.virtual_hook.enter_after_losses,
        exitWins: payload.virtual_hook.exit_after_consecutive_wins,
      },
    };
  }

  function schedulePage() {
    const now = new Date(Date.now() + 10 * 60 * 1000);
    const localDate = new Intl.DateTimeFormat("en-CA", { timeZone: state.preferences?.timezone || DEFAULT_TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
    const localTime = new Intl.DateTimeFormat("en-GB", { timeZone: state.preferences?.timezone || DEFAULT_TZ, hour: "2-digit", minute: "2-digit", hour12: false }).format(now);
    const selected = state.selectedStrategy || strategyForSchedule();
    const active = (state.schedules?.schedules || []).filter((item) => !["completed", "cancelled", "skipped", "failed"].includes(String(item.status || "").toLowerCase()));
    const content = `<section class="page-intro"><span class="eyebrow">SCHEDULE TRADING</span><h1>Set it once. The VPS handles the session.</h1><p>Scheduled sessions are persisted server-side and do not require this browser to remain open.</p></section>
    <section class="schedule-layout">
      <article class="panel schedule-form">
        <div class="panel-title"><div><span class="eyebrow">SESSION SETUP</span><h3>${esc(selected.name || "Current Strategy")}</h3></div><span class="strategy-source">${esc(selected.source || "saved")}</span></div>
        <div class="form-grid two"><label><span>Date</span><input id="s-date" type="date" value="${esc(localDate)}"></label><label><span>Time</span><input id="s-time" type="time" value="${esc(localTime)}"></label></div>
        <label><span>Timezone</span><select id="s-timezone">${TIMEZONES.map(([zone, city]) => `<option value="${esc(zone)}" ${zone === (state.preferences?.timezone || DEFAULT_TZ) ? "selected" : ""}>${esc(city)} · ${esc(zone)}</option>`).join("")}</select></label>
        <div class="form-grid three"><label><span>Stake USD</span><input id="s-stake" type="number" min="0.35" step="0.01" value="${esc(selected.stake || state.me?.settings?.stake_amount || 0.5)}"></label><label><span>Take profit</span><input id="s-tp" type="number" min="0" step="0.01" value="${esc(selected.takeProfit || state.me?.settings?.take_profit || 0)}"></label><label><span>Stop loss</span><input id="s-sl" type="number" min="0" step="0.01" value="${esc(selected.stopLoss || state.me?.settings?.stop_loss || 0)}"></label></div>
        <fieldset class="overlap-field"><legend>If another session is still active</legend>${[["wait","Wait until it finishes"],["skip","Skip this session"],["replace","Stop previous and start this one"]].map(([value, label], index) => `<label><input type="radio" name="overlap" value="${value}" ${index === 0 ? "checked" : ""}><span><b>${label}</b><small>${value === "wait" ? "Recommended" : value === "skip" ? "Never overlap execution" : "Uses the existing Stop authority first"}</small></span></label>`).join("")}</fieldset>
        <div class="session-preview"><span class="eyebrow">SESSION PREVIEW</span><b>${esc(selected.name || "Strategy")}</b><p><span id="preview-date">${esc(localDate)}</span> · <span id="preview-time">${esc(localTime)}</span> · ${esc(state.preferences?.timezone || DEFAULT_TZ)}</p></div>
        <div class="ready-actions"><button class="btn primary" data-create-schedule>${miniIcon("schedule")} Schedule Session</button><button class="btn ghost" data-trade-now-selected>${miniIcon("play")} Trade Now Instead</button></div>
      </article>
      <aside class="panel upcoming"><div class="panel-title"><div><span class="eyebrow">UPCOMING</span><h3>Trading sessions</h3></div><span>${active.length}</span></div>${active.length ? active.slice(0, 8).map((item) => scheduleRow(item)).join("") : `<div class="empty-mini"><span>${miniIcon("clock")}</span><p>No upcoming sessions.</p></div>`}</aside>
    </section>`;
    return shell(content, { title: "Schedule" });
  }

  function strategyForSchedule() {
    const config = state.custom?.config;
    if (config?.configured) return { name: "Current Custom Strategy", source: "saved", strategy: config, stake: state.me?.settings?.stake_amount || .5, takeProfit: state.me?.settings?.take_profit || 0, stopLoss: state.me?.settings?.stop_loss || 0 };
    return {
      name: "Over 3 Starter",
      source: "built-in",
      strategy: {
        market_mode: "single", markets: ["1HZ100V"], trade_type: "over", prediction: 3, duration_ticks: 1,
        conditions: [{ kind: "digit_compare", window: 1, operator: ">=", value: 4 }], match: "all", reanalyze: {},
        virtual_hook_enabled: true, virtual_hook: { enabled: true, enter_after_losses: 2, exit_after_consecutive_wins: 2 },
      },
      stake: .5, takeProfit: 0, stopLoss: 0,
    };
  }

  function scheduleRow(item) {
    const status = String(item.status || "scheduled").toLowerCase();
    return `<div class="schedule-row"><span class="status-dot ${esc(status)}"></span><span><b>${esc(item.strategy_name || "Strategy")}</b><small>${esc(item.scheduled_local || item.scheduled_for_utc || "")}</small></span><em>${esc(status)}</em>${["scheduled","waiting","starting"].includes(status) ? `<button data-cancel-schedule="${esc(item.id)}">Cancel</button>` : ""}</div>`;
  }

  function profilePage() {
    const accounts = state.accounts?.accounts || [];
    const tz = state.preferences?.timezone || DEFAULT_TZ;
    const premium = state.premium || {};
    const content = `<section class="page-intro compact"><span class="eyebrow">PROFILE & SETTINGS</span><h1>Your automation environment.</h1><p>Account selection and timezone settings are linked to your Deriv login.</p></section>
    <section class="profile-grid">
      <article class="panel"><div class="panel-title"><div><span class="eyebrow">LINKED OPTIONS ACCOUNTS</span><h3>${accounts.length} account${accounts.length === 1 ? "" : "s"}</h3></div></div><div class="account-list">${accounts.map(accountRow).join("")}</div></article>
      <article class="panel"><div class="panel-title"><div><span class="eyebrow">TIMEZONE</span><h3>${esc(tz)}</h3></div><button class="text-button" data-route="timezone">Change</button></div><p class="muted">All future session times are interpreted in this timezone and persisted as UTC.</p></article>
      <article class="panel"><div class="panel-title"><div><span class="eyebrow">PREMIUM ACCESS</span><h3>${premium.active || premium.has_access ? "Active" : "Subscription"}</h3></div></div><p class="muted">${esc(premium.message || premium.status || "Premium access is verified by the server before protected mutations.")}</p></article>
    </section>`;
    return shell(content, { title: "Profile" });
  }

  function accountRow(account) {
    const type = String(account.account_type || "demo").toLowerCase();
    return `<button class="account-row ${account.selected ? "selected" : ""}" data-account-id="${esc(account.managed_account_id)}"><span class="account-icon">${quill(type === "real" ? "realAccount" : "demoAccount")}</span><span><b>${esc(account.label || account.account_id_masked)}</b><small>${esc(account.account_id_masked)} · ${type.toUpperCase()}</small></span><span class="account-money">${quill(String(account.currency || "USD").toLowerCase() === "usd" ? "usd" : "usd")}<b>${money(account.balance, account.currency)}</b></span></button>`;
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

  function tradesPage() {
    const accounts = state.accounts?.accounts || [];
    const selected = accounts.find((a) => a.selected) || accounts[0] || { account_type: state.me?.account_type, balance: state.me?.balance, currency: state.me?.currency, account_id_masked: state.me?.account_id };
    const lifecycle = String(state.lifecycle?.lifecycle || state.lifecycle?.runtime_state || (state.me?.enabled ? "running" : "stopped")).toLowerCase();
    const content = `<section class="run-panel">
      <div class="run-account-bar">
        <div class="account-select-visual"><span class="account-icon large">${quill(selected.account_type === "real" ? "realAccount" : "demoAccount")}</span><span><small>${esc(String(selected.account_type || "demo").toUpperCase())} ACCOUNT</small><b>${money(selected.balance, selected.currency)}</b><em>${esc(selected.account_id_masked || "")}</em></span>${quill("usd", "currency-icon")}</div>
        <label class="account-select-native"><span>Trading account</span><select id="run-account-select">${accounts.map((account) => `<option value="${esc(account.managed_account_id)}" ${account.selected ? "selected" : ""}>${esc(account.account_type.toUpperCase())} · ${esc(account.account_id_masked)} · ${money(account.balance, account.currency)}</option>`).join("")}</select></label>
      </div>
      <div class="run-status"><span class="live-led ${lifecycle}"></span><span><small>Execution</small><b>${esc(lifecycle.replaceAll("_", " "))}</b></span><div class="run-controls">${lifecycle.includes("running") || lifecycle.includes("active") ? `<button data-pause-trading>${miniIcon("pause")} Pause</button><button data-stop-trading>${miniIcon("stop")} Stop</button>` : `<button class="primary" data-start-trading>${miniIcon("play")} Start</button>`}<button data-clear-trades>${miniIcon("trash")} Clear</button></div></div>
      <div class="run-ledger-head"><span>Type</span><span>Entry / Exit spot</span><span>Stake and P/L</span></div>
      <div class="run-ledger">${tradeRows()}</div>
      ${runSummary()}
    </section>`;
    return shell(content, { title: "Live Runs" });
  }

  function render() {
    if (!state.loaded) {
      root.innerHTML = `<div class="boot-screen"><span class="brand-mark">D</span><b>DerivAdmin</b><small>Loading automation workspace…</small></div>`;
      return;
    }
    if (!state.me?.authenticated) { root.innerHTML = landing(); bind(); return; }
    const shouldOnboard = state.preferences?.requires_timezone_onboarding && state.route !== "timezone";
    if (shouldOnboard) state.route = "timezone";
    const pages = { home, builder: builderPage, ai: aiPage, ready: readyPage, schedule: schedulePage, profile: profilePage, trades: tradesPage, timezone: timezonePage };
    root.innerHTML = pages[state.route]();
    bind();
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
    render();
  }

  async function task(name, action, success) {
    if (state.busy) return;
    state.busy = name; state.error = ""; state.notice = "";
    try {
      await action();
      if (success) state.notice = success;
      await refresh({ quiet: true });
    } catch (error) {
      state.error = error?.message || "Action failed";
      render();
    } finally { state.busy = ""; }
  }

  async function saveBuilder({ trade = false, schedule = false } = {}) {
    const snapshot = builderSnapshot();
    await json("/me/custom-strategy", { method: "POST", body: JSON.stringify(snapshot.strategy) });
    saveTemplate(snapshot);
    state.selectedStrategy = snapshot;
    if (schedule) { go("schedule"); return; }
    if (trade) {
      await json("/me/resume-trading", { method: "POST", body: JSON.stringify({ mode: "start_again" }) });
      go("trades");
    }
  }

  function saveTemplate(snapshot) {
    const templates = readJSON(STORE_TEMPLATES, []);
    const id = snapshot.id || `strategy-${Date.now()}`;
    const next = [{ ...snapshot, id, saved_at: new Date().toISOString() }, ...templates.filter((item) => item.id !== id)].slice(0, 40);
    writeJSON(STORE_TEMPLATES, next);
  }

  async function saveGeneratedToServer() {
    const canonical = generatedCanonical();
    if (!canonical) throw new Error("Generated strategy is missing its canonical execution payload.");
    await json("/me/custom-strategy", { method: "POST", body: JSON.stringify(canonical) });
    const snapshot = { name: state.generated.name || state.generated.strategy_name || "AI Generated Strategy", source: "ai", strategy: canonical, generated: state.generated };
    saveTemplate(snapshot);
    state.selectedStrategy = snapshot;
    return snapshot;
  }

  function scheduleSnapshot(selected) {
    const strategy = selected.strategy || selected.canonical || selected.config || {};
    if (strategy.market_mode) return strategy;
    if (strategy.builder) return strategy;
    return strategy;
  }

  function bind() {
    root.querySelectorAll("[data-route]").forEach((el) => el.addEventListener("click", () => go(el.dataset.route)));

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

    root.querySelectorAll("[data-ready-save]").forEach((button) => button.addEventListener("click", () => task("save-ready", saveGeneratedToServer, "Strategy saved.")));
    root.querySelectorAll("[data-ready-trade]").forEach((button) => button.addEventListener("click", () => task("trade-ready", async () => { await saveGeneratedToServer(); await json("/me/resume-trading", { method: "POST", body: JSON.stringify({ mode: "start_again" }) }); go("trades"); })));
    root.querySelectorAll("[data-ready-schedule]").forEach((button) => button.addEventListener("click", () => { const canonical = generatedCanonical(); state.selectedStrategy = { name: state.generated?.name || "AI Generated Strategy", source: "ai", strategy: canonical, stake: canonical?.execution_settings?.stake_amount }; go("schedule"); }));
    root.querySelectorAll("[data-ready-builder]").forEach((button) => button.addEventListener("click", () => { const canonical = generatedCanonical() || {}; const condition = canonical.conditions?.[0] || {}; state.selectedStrategy = { builder: { name: state.generated?.name || "AI Strategy", market: canonical.markets?.[0] || "1HZ100V", side: canonical.trade_type || "over", prediction: canonical.prediction ?? 3, window: condition.window || 1, operator: condition.operator || ">=", value: condition.value ?? 4, ticks: canonical.duration_ticks || 1, stake: canonical.execution_settings?.stake_amount || .5, takeProfit: canonical.execution_settings?.take_profit || 0, stopLoss: canonical.execution_settings?.stop_loss || 0, virtual: canonical.virtual_hook_enabled !== false, enterLosses: canonical.virtual_hook?.enter_after_losses || 2, exitWins: canonical.virtual_hook?.exit_after_consecutive_wins || 2 } }; go("builder"); }));

    root.querySelectorAll("[data-builder-save]").forEach((button) => button.addEventListener("click", () => task("builder-save", () => saveBuilder(), "Strategy saved.")));
    root.querySelectorAll("[data-builder-trade]").forEach((button) => button.addEventListener("click", () => task("builder-trade", () => saveBuilder({ trade: true }))));
    root.querySelectorAll("[data-builder-schedule]").forEach((button) => button.addEventListener("click", () => { try { state.selectedStrategy = builderSnapshot(); go("schedule"); } catch (error) { state.error = error.message; render(); } }));

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
    }, "Session scheduled on the VPS. You may close this browser.")));
    root.querySelectorAll("[data-trade-now-selected]").forEach((button) => button.addEventListener("click", () => task("schedule-trade", async () => {
      const selected = state.selectedStrategy || strategyForSchedule();
      if (selected.strategy?.market_mode) await json("/me/custom-strategy", { method: "POST", body: JSON.stringify(selected.strategy) });
      await json("/me/resume-trading", { method: "POST", body: JSON.stringify({ mode: "start_again" }) }); go("trades");
    })));
    root.querySelectorAll("[data-cancel-schedule]").forEach((button) => button.addEventListener("click", () => task("schedule-cancel", () => json(`/me/automation-schedules/${encodeURIComponent(button.dataset.cancelSchedule)}/cancel`, { method: "POST", body: "{}" }), "Schedule cancelled.")));

    const accountSelect = document.getElementById("run-account-select");
    if (accountSelect) accountSelect.addEventListener("change", () => task("switch-account", () => json("/me/switch-account", { method: "POST", body: JSON.stringify({ managed_account_id: Number(accountSelect.value) }) }), "Account switched."));
    root.querySelectorAll("[data-account-id]").forEach((button) => button.addEventListener("click", () => task("switch-account", () => json("/me/switch-account", { method: "POST", body: JSON.stringify({ managed_account_id: Number(button.dataset.accountId) }) }))));
    root.querySelectorAll("[data-start-trading]").forEach((button) => button.addEventListener("click", () => task("start", () => json("/me/resume-trading", { method: "POST", body: JSON.stringify({ mode: "continue" }) }), "Trading started.")));
    root.querySelectorAll("[data-pause-trading]").forEach((button) => button.addEventListener("click", () => task("pause", () => json("/me/pause-trading", { method: "POST", body: "{}" }), "Trading paused.")));
    root.querySelectorAll("[data-stop-trading]").forEach((button) => button.addEventListener("click", () => task("stop", () => json("/me/stop-trading", { method: "POST", body: "{}" }), "Trading stopped.")));
    root.querySelectorAll("[data-clear-trades]").forEach((button) => button.addEventListener("click", () => task("clear", () => json("/me/clear-trades", { method: "POST", body: JSON.stringify({ scope: "today" }) }), "Today's run history cleared.")));
  }

  window.addEventListener("hashchange", () => { state.route = routeFromHash(); render(); });
  document.addEventListener("foa:vps-live", () => { if (state.me?.authenticated) refresh({ quiet: true }); });
  document.addEventListener("foa:backend-lifecycle", () => { if (state.me?.authenticated) refresh({ quiet: true }); });
  window.addEventListener("focus", () => { if (state.me?.authenticated) refresh({ quiet: true }); });
  window.setInterval(() => { if (state.me?.authenticated && ["trades", "home", "schedule"].includes(state.route)) refresh({ quiet: true }); }, 5000);

  render();
  refresh();
  window.FOA_FINAL_UI = Object.freeze({ version: "20260817-6f2-1", refresh, go });
})();
