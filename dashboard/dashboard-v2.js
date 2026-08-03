(() => {
  "use strict";

  const VERSION = "20260801-5";
  const K = {
    theme: "foa-theme-v2",
    mode: "foa-mode-v2",
    view: "foa-view-v2",
    wizard: "foa-onboarding-dismissed-v2",
  };
  const VIEWS = new Set(["overview", "trades", "strategy", "settings"]);
  const S = {
    theme: localStorage.getItem(K.theme) || "dark",
    mode: localStorage.getItem(K.mode) || "demo",
    view: VIEWS.has(localStorage.getItem(K.view)) ? localStorage.getItem(K.view) : "overview",
    me: null,
    life: null,
    summary: null,
    trades: [],
    tradeSummary: {},
    busy: false,
    mutating: false,
    booting: true,
    loaderText: "Opening dashboard…",
    error: "",
    notice: "",
    wizardOpen: localStorage.getItem(K.wizard) !== "1",
  };

  const esc = value => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");
  const num = value => Number(value || 0).toLocaleString();
  const money = (value, currency = "USD") => {
    const amount = Number(value || 0);
    const prefix = String(currency || "USD").toUpperCase() === "USD" ? "$" : `${esc(currency)} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };
  const pct = value => `${(Number(value || 0) * (Math.abs(Number(value || 0)) <= 1 ? 100 : 1)).toFixed(1)}%`;
  const authenticated = () => Boolean(S.me && S.me.authenticated);
  const selectedMode = () => String(S.me?.account_type || S.mode || "demo").toLowerCase() === "real" ? "real" : "demo";
  const strategyName = () => S.summary?.strategy_name || S.summary?.strategy?.name || "AI Digit Recovery V1";

  function setLoader(text) {
    S.loaderText = text || "Loading…";
    render(false);
  }

  function setTheme(value) {
    S.theme = value === "light" ? "light" : "dark";
    localStorage.setItem(K.theme, S.theme);
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
    try { body = text ? JSON.parse(text) : {}; } catch (_) { body = { detail: text }; }
    if (!response.ok) throw new Error(body.detail || body.message || `${response.status} ${response.statusText}`);
    return body;
  }
  const getJSON = url => requestJSON(url);
  const postJSON = (url, body = {}) => requestJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  function publicMetrics() {
    const today = S.summary?.system_performance?.today || {};
    const total = Number(today.total_trades ?? S.summary?.purchased_trades ?? 0);
    const wins = Number(today.wins ?? S.summary?.wins ?? 0);
    const losses = Number(today.losses ?? S.summary?.losses ?? 0);
    return {
      registered: Number(S.summary?.registered_traders ?? S.summary?.total_traders ?? 0),
      active: Number(S.summary?.trading_now ?? S.summary?.active_traders ?? S.summary?.active_accounts ?? S.summary?.trading_ready_accounts ?? 0),
      total,
      wins,
      losses,
      rate: wins + losses ? wins / (wins + losses) : 0,
      profit: Number(today.with_martingale_pnl ?? today.martingale_pnl ?? 0),
    };
  }

  function personalMetrics() {
    const summary = S.tradeSummary || {};
    const rows = Array.isArray(S.trades) ? S.trades : [];
    const wins = Number(summary.wins ?? rows.filter(row => String(row.outcome).toUpperCase() === "WIN").length);
    const losses = Number(summary.losses ?? rows.filter(row => String(row.outcome).toUpperCase() === "LOSS").length);
    const profit = Number(summary.profit ?? rows.reduce((total, row) => total + Number(row.profit || 0), 0));
    return {
      total: Number(summary.total ?? rows.length),
      wins,
      losses,
      open: Number(summary.open ?? Math.max(0, rows.length - wins - losses)),
      profit,
      rate: wins + losses ? wins / (wins + losses) : 0,
    };
  }

  function lifecycle() {
    const value = String(S.life?.lifecycle || "").toLowerCase();
    if (["running", "paused", "stopped"].includes(value)) return value;
    const status = String(S.me?.execution_status || "").toLowerCase();
    if (status.includes("pause")) return "paused";
    if (!S.me?.enabled || status.includes("stop") || status.includes("disable") || status === "inactive") return "stopped";
    return "running";
  }

  function tradeTime(row) {
    const value = row.purchase_time || row.provider_purchase_time || row.settlement_time || row.created_at || "";
    if (!value) return "—";
    try { return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
    catch (_) { return String(value).slice(0, 8); }
  }

  function contractType(row) {
    const type = String(row.contract_type || row.type || "TRADE").toUpperCase();
    const barrier = String(row.barrier || "").trim();
    return type === "DIGITOVER" && barrier ? `${type} ${barrier}` : type;
  }

  function resultClass(row) {
    const outcome = String(row.outcome || "OPEN").toUpperCase();
    return outcome === "WIN" ? "win" : outcome === "LOSS" ? "loss" : "neutral";
  }

  function resultText(row) {
    const outcome = String(row.outcome || "OPEN").toUpperCase();
    if (outcome === "WIN" || outcome === "LOSS") return `${outcome} · ${money(row.profit || 0)}`;
    return outcome;
  }

  function series() {
    let total = 0;
    const rows = [...(S.trades || [])].reverse();
    return [0, ...rows.map(row => (total += Number(row.profit || 0)))];
  }

  function chartPoints() {
    const values = authenticated() ? series() : [0, 1, 0.6, 2, 1.7, 3, 2.8, 4];
    const low = Math.min(...values, 0);
    const high = Math.max(...values, 0);
    const span = Math.max(1, high - low);
    return values.map((value, index) => `${(index / (values.length - 1 || 1) * 500).toFixed(1)},${(150 - (value - low) / span * 120).toFixed(1)}`).join(" ");
  }

  function navButton(view, icon, label) {
    return `<button type="button" data-view="${view}" class="${S.view === view ? "active" : ""}"><span>${icon}</span>${label}</button>`;
  }

  function card(type, label, value, caption) {
    const icon = { wallet: "▣", profit: "↗", target: "◎", bot: "◉", users: "♙", trades: "↕" }[type] || "•";
    return `<article class="foa-kpi ${type}"><div class="foa-kpi-icon">${icon}</div><div><span>${esc(label)}</span><strong>${value}</strong><small>${esc(caption)}</small></div></article>`;
  }

  function modeToggle() {
    if (!authenticated()) return "";
    const available = Array.isArray(S.me?.available_account_types) ? S.me.available_account_types : ["demo", "real"];
    return `<div class="foa-mode-toggle">${["demo", "real"].map(value => `<button type="button" data-mode="${value}" class="${selectedMode() === value ? "active" : ""}" ${available.includes(value) ? "" : "disabled"}>${value[0].toUpperCase() + value.slice(1)}</button>`).join("")}</div>`;
  }

  function header() {
    const sub = authenticated() ? `${selectedMode()} account` : "Public dashboard";
    const title = S.view[0].toUpperCase() + S.view.slice(1);
    return `<header class="foa-topbar">
      <div class="foa-mobile-brand"><div class="foa-logo">F</div><strong>Father of<br>Automation</strong></div>
      <div class="foa-page-title"><small>${esc(sub)}</small><strong>${esc(title)}</strong></div>
      <div class="foa-top-actions">
        <button class="foa-theme" id="theme" aria-label="Toggle theme"><i></i></button>
        ${authenticated()
          ? `<span class="foa-account-pill"><b>${esc(selectedMode())}</b><span>${esc(S.me?.account_id || S.me?.account_id_masked || S.me?.label || "Account")}</span></span><button class="foa-logout" id="logout">Logout</button>`
          : `<a class="foa-login" href="/oauth/start">Login with Deriv</a>`}
      </div>
    </header>`;
  }

  function wizardSteps() {
    const settings = S.me?.settings || {};
    const tokenReady = Boolean(S.me?.has_trading_api_token) && !Boolean(S.me?.requires_api_token) && !Boolean(S.me?.trading_api_token_invalid);
    const stakeReady = Number(settings.stake_amount ?? 0) >= 0.35;
    const runState = lifecycle();
    return [
      { title: "Login", text: "Connect your Deriv profile.", done: authenticated(), action: "/oauth/start", label: "Login" },
      { title: "Choose account", text: "Pick Demo or Real before saving controls.", done: authenticated(), view: "overview", label: "Choose" },
      { title: "Trading credential", text: "Save the token used by private WebSocket purchases.", done: tokenReady, view: "settings", label: "Add token" },
      { title: "Risk controls", text: "Set stake, take profit, stop loss and recovery mode.", done: stakeReady, view: "settings", label: "Configure" },
      { title: "Start bot", text: "Start or resume only when ready.", done: runState !== "stopped", view: "overview", label: "Start" },
    ];
  }

  function wizard({ compact = false } = {}) {
    const steps = wizardSteps();
    const done = steps.filter(step => step.done).length;
    const complete = done === steps.length;
    if (!S.wizardOpen && !compact) return "";
    if (complete && !compact) return "";
    const next = steps.find(step => !step.done) || steps[steps.length - 1];
    return `<section class="foa-card foa-wizard-card ${compact ? "compact" : ""}">
      <div class="foa-card-head">
        <div><span class="foa-eyebrow">START HERE</span><h2>${complete ? "Onboarding complete" : "Setup wizard"}</h2><p>${complete ? "Your account is ready." : "Follow these steps before public users start trading."}</p></div>
        <strong>${done}/${steps.length}</strong>
      </div>
      <div class="foa-wizard-progress"><i style="width:${Math.round((done / steps.length) * 100)}%"></i></div>
      <div class="foa-wizard-steps">${steps.map((step, index) => `<button type="button" class="${step.done ? "done" : ""}" ${step.view ? `data-view="${step.view}"` : step.action ? `data-action-link="${step.action}"` : ""}><b>${step.done ? "✓" : index + 1}</b><span><strong>${esc(step.title)}</strong><small>${esc(step.text)}</small></span></button>`).join("")}</div>
      <div class="foa-wizard-actions">
        ${next.action ? `<a class="foa-primary-link" href="${next.action}">${esc(next.label)}</a>` : `<button class="foa-primary" data-view="${next.view || "overview"}">${esc(next.label)}</button>`}
        <button class="foa-muted" id="dismiss-wizard">Hide wizard</button>
      </div>
    </section>`;
  }

  function publicView() {
    const metrics = publicMetrics();
    return `${wizard()}<section class="foa-kpis">
      ${card("users", "Registered Traders", num(metrics.registered), "All registered accounts")}
      ${card("bot", "Trading Now", num(metrics.active), "Accounts currently active")}
      ${card("trades", "Model Trades Today", num(metrics.total), "Settled strategy outcomes")}
      ${card("target", "Model Win Rate", pct(metrics.rate), `${metrics.wins} wins / ${metrics.losses} losses`)}
    </section>
    <section class="foa-public-grid">
      <article class="foa-card foa-welcome-card"><span class="foa-eyebrow">AUTOMATED DIGITS TRADING</span><h1>Simple control for your Deriv account.</h1><p>Log in to view your personal balance, add your trading credential, configure risk controls and start automation.</p><div class="foa-welcome-actions"><a class="foa-primary-link" href="/oauth/start">Login with Deriv</a><button class="foa-secondary-link" data-view="strategy">View strategy</button></div></article>
      <article class="foa-card"><h2>System Status</h2><div class="foa-big-status"><i></i><span><strong>Online</strong><small>Scanning synthetic markets</small></span></div><div class="foa-simple-list"><div><span>Strategy</span><strong>${esc(strategyName())}</strong></div><div><span>Normal</span><strong>OVER 1</strong></div><div><span>Recovery</span><strong>OVER 3</strong></div></div></article>
    </section>`;
  }

  function controls() {
    const state = lifecycle();
    const primary = state === "stopped"
      ? `<button class="foa-primary" data-control="start">▶ Start Auto Trade</button>`
      : `<button class="foa-danger" data-control="stop">■ Stop Auto Trade</button>`;
    const secondary = state === "paused"
      ? `<button class="foa-primary-soft" data-control="resume">▶ Resume</button>`
      : `<button class="foa-muted" data-control="pause" ${state === "stopped" ? "disabled" : ""}>Ⅱ Pause</button>`;
    return `<div class="foa-actions-row">${primary}${secondary}</div>`;
  }

  function tradeRows(limit = 6, wide = false) {
    const rows = (S.trades || []).slice(0, limit);
    if (!rows.length) return `<div class="foa-empty">No trades have been taken on this account today.</div>`;
    return rows.map(row => `<div class="foa-trade-row ${wide ? "foa-trade-row-wide" : ""}"><span>${esc(tradeTime(row))}</span><span class="foa-trade-name"><b>${esc(row.symbol || row.market || "—")}</b><em>${esc(contractType(row))}</em></span><span>${money(row.buy_price ?? row.stake ?? row.amount ?? 0)}</span>${wide ? `<span>${row.payout == null ? "—" : money(row.payout)}</span>` : ""}<strong class="${resultClass(row)}">${esc(resultText(row))}</strong></div>`).join("");
  }

  function overview() {
    if (!authenticated()) return publicView();
    const metrics = personalMetrics();
    const currency = S.me?.currency || "USD";
    const balance = Number(S.me?.balance || 0);
    const run = lifecycle();
    const status = run === "running" ? "Running" : run === "paused" ? "Paused" : "Stopped";
    return `${wizard()}<section class="foa-kpis">
      ${card("wallet", "Balance", money(balance, currency), `${selectedMode()} account balance`)}
      ${card("profit", "Today’s Profit", money(metrics.profit, currency), `${metrics.wins + metrics.losses} settled trades`)}
      ${card("target", "Win Rate", pct(metrics.rate), `${metrics.wins} wins / ${metrics.losses} losses`)}
      ${card("bot", "Bot Status", `${status}<span class="dot ${run}"></span>`, run === "running" ? "Live and trading" : run === "paused" ? "State preserved" : "Ready to start")}
    </section>
    <section class="foa-grid">
      <article class="foa-card foa-account-card"><div class="foa-card-head"><h2>My Account</h2>${modeToggle()}</div><p>Account Balance</p><div class="foa-balance">${money(balance, currency)}</div><div class="foa-account-stats"><div><span>Today’s Trades</span><strong>${metrics.total}</strong></div><div><span>Wins</span><strong class="win">${metrics.wins}</strong></div><div><span>Losses</span><strong class="loss">${metrics.losses}</strong></div></div>${controls()}${S.me?.requires_api_token ? `<button class="foa-inline-warning" data-view="settings">Trading credential required — open Settings</button>` : ""}</article>
      <article class="foa-card foa-strategy-card"><h2>Strategy Status</h2><div class="foa-simple-list"><div><span>Strategy</span><strong>${esc(strategyName())}</strong></div><div><span>Normal</span><strong>OVER 1</strong></div><div><span>Recovery</span><strong>OVER 3</strong></div><div><span>Protection</span><strong>${esc(S.me?.virtual_protection?.active ? "Virtual mode" : "Ready")}</strong></div></div><button class="foa-text-button" data-view="strategy">View details →</button></article>
      <article class="foa-card foa-performance-card"><div class="foa-card-head"><h2>Performance</h2><span class="foa-period">Today</span></div><svg class="foa-chart" viewBox="0 0 500 180" preserveAspectRatio="none"><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f73ff" stop-opacity=".42"/><stop offset="1" stop-color="#2f73ff" stop-opacity="0"/></linearGradient></defs><g class="grid">${[30,70,110,150].map(y => `<line x1="0" y1="${y}" x2="500" y2="${y}"/>`).join("")}</g><polyline points="${chartPoints()}" fill="none" stroke="#2f73ff" stroke-width="4"/><polygon points="0,170 ${chartPoints()} 500,170" fill="url(#fill)"/></svg><div class="foa-perf-stats"><div><span>P/L</span><strong class="${metrics.profit < 0 ? "loss" : "win"}">${money(metrics.profit)}</strong></div><div><span>Open</span><strong>${metrics.open}</strong></div><div><span>Avg Trade</span><strong>${money(metrics.wins + metrics.losses ? metrics.profit / (metrics.wins + metrics.losses) : 0)}</strong></div></div></article>
    </section>
    <section class="foa-card foa-trades-card"><div class="foa-card-head"><h2>Today’s Recent Trades</h2><button class="foa-text-button" data-view="trades">View all</button></div><div class="foa-trade-head"><span>Time</span><span>Trade</span><span>Stake</span><span>Result</span></div>${tradeRows()}</section>`;
  }

  function needLogin(text) {
    return `<section class="foa-card foa-login-required"><div class="foa-logo foa-login-logo">F</div><h1>Login required</h1><p>${esc(text)}</p><a class="foa-primary-link" href="/oauth/start">Login with Deriv</a></section>`;
  }

  function tradesView() {
    if (!authenticated()) return needLogin("Login to view every trade taken today on your personal account.");
    const metrics = personalMetrics();
    return `<section class="foa-page-intro"><div><span class="foa-eyebrow">PERSONAL ACCOUNT ACTIVITY</span><h1>Today’s Trades</h1><p>All trades taken today on the selected ${esc(selectedMode())} account.</p></div>${modeToggle()}</section><section class="foa-kpis foa-kpis-compact">${card("trades", "Total", num(metrics.total), "All trades today")}${card("profit", "Profit / Loss", money(metrics.profit), "Actual account result")}${card("target", "Win Rate", pct(metrics.rate), `${metrics.wins} wins / ${metrics.losses} losses`)}${card("bot", "Open Trades", num(metrics.open), "Awaiting settlement")}</section><section class="foa-card foa-all-trades"><div class="foa-card-head"><h2>Complete trade history for today</h2><span class="foa-period">${esc(S.tradeSummary?.date || "Today")}</span></div><div class="foa-trade-head foa-trade-head-wide"><span>Time</span><span>Market / Contract</span><span>Stake</span><span>Payout</span><span>Result</span></div>${tradeRows(5000, true)}</section>`;
  }

  function strategyView() {
    return `<section class="foa-page-intro"><div><span class="foa-eyebrow">ACTIVE AUTOMATION MODEL</span><h1>${esc(strategyName())}</h1><p>OVER 1 normal entries with controlled OVER 3 recovery.</p></div><span class="foa-online-pill">● System online</span></section><section class="foa-strategy-flow">${[["1", "Normal Mode", "DIGITOVER 1", "Used while there is no debt."], ["2", "First Recovery", "DIGITOVER 3", "Triggered after one real loss."], ["3", "Virtual Protection", "2 virtual wins", "Used after the OVER 3 recovery loses."], ["4", "Full Recovery", "1 real DIGITOVER 4", "One winning contract targets all recorded debt."]].map(step => `<article class="foa-card"><span class="foa-step">${step[0]}</span><h2>${step[1]}</h2><strong>${step[2]}</strong><p>${step[3]}</p></article>`).join("")}</section><section class="foa-two-col"><article class="foa-card"><h2>Risk rule</h2><div class="foa-simple-list"><div><span>Virtual wins required</span><strong>2 consecutive</strong></div><div><span>Recovery family</span><strong>OVER only</strong></div><div><span>PUT contracts</span><strong>Disabled</strong></div></div></article><article class="foa-card"><h2>Supported markets</h2><p>The worker scans configured synthetic markets and chooses only qualifying digit opportunities.</p><div class="foa-market-tags">${["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V", "R_10", "R_25", "R_50", "R_75", "R_100"].map(market => `<span>${market}</span>`).join("")}</div></article></section>`;
  }

  function settingsView() {
    if (!authenticated()) return needLogin("Login to connect a trading credential and configure your account.");
    const settings = S.me?.settings || {};
    const ready = Boolean(S.me?.has_trading_api_token) && !Boolean(S.me?.requires_api_token) && !Boolean(S.me?.trading_api_token_invalid);
    const martingaleMode = settings.martingale_mode || (settings.martingale_enabled === false ? "flat" : "system");
    return `${wizard({ compact: true })}<section class="foa-page-intro"><div><span class="foa-eyebrow">PERSONAL ACCOUNT CONTROLS</span><h1>Settings</h1><p>Changes apply only to the selected ${esc(selectedMode())} account.</p></div>${modeToggle()}</section><section class="foa-settings-grid"><form class="foa-card foa-settings-card" id="settings-form"><div class="foa-card-head"><div><h2>Trading Controls</h2><p>Stake, profit limits, and recovery mode.</p></div><span class="foa-save-state">${ready ? "Connected" : "Credential needed"}</span></div><div class="foa-form-grid"><label><span>Stake amount (USD)</span><input name="stake_amount" type="number" min="0.35" step="0.01" required value="${esc(settings.stake_amount ?? 0.5)}"></label><label><span>Take profit (USD)</span><input name="take_profit" type="number" min="0" step="0.01" value="${esc(settings.take_profit ?? 0)}"><small>0 disables the limit.</small></label><label><span>Stop loss (USD)</span><input name="stop_loss" type="number" min="0" step="0.01" value="${esc(settings.stop_loss ?? 0)}"><small>0 disables the limit.</small></label><label><span>Martingale mode</span><select name="martingale_mode" id="mm"><option value="system" ${martingaleMode === "system" ? "selected" : ""}>System recovery</option><option value="custom" ${martingaleMode === "custom" ? "selected" : ""}>Custom multiplier</option><option value="flat" ${martingaleMode === "flat" ? "selected" : ""}>Flat stake</option></select></label></div><div class="foa-custom-fields ${martingaleMode === "custom" ? "show" : ""}" id="custom"><label><span>Start after losses</span><input name="martingale_trigger_losses" type="number" min="1" max="10" value="${esc(settings.martingale_trigger_losses ?? 1)}"></label><label><span>Multiplier</span><input name="martingale_multiplier" type="number" min="1.1" max="10" step=".1" value="${esc(settings.martingale_multiplier ?? 2)}"></label><label><span>Maximum levels</span><input name="martingale_max_levels" type="number" min="1" max="10" value="${esc(settings.martingale_max_levels ?? 6)}"></label><label><span>Maximum stake</span><input name="martingale_max_stake" type="number" min=".35" step=".01" value="${esc(settings.martingale_max_stake ?? 1000)}"></label></div><button class="foa-primary foa-save-button">Save Trading Settings</button></form><article class="foa-card foa-token-card"><div class="foa-card-head"><div><h2>Deriv Trading Credential</h2><p>Required by the private WebSocket to place trades.</p></div><span class="foa-token-status ${ready ? "ready" : "needed"}">${ready ? "Connected" : "Required"}</span></div>${ready ? `<div class="foa-connected-box"><strong>Credential connected</strong><p>The token is encrypted and never displayed back.</p></div>` : `<form id="token-form"><label><span>Deriv API token</span><input name="api_token" type="password" minlength="8" autocomplete="off" required></label><button class="foa-primary foa-save-button">Verify and Save Token</button></form>`}<div class="foa-security-note"><strong>Important</strong><p>The token must match the selected ${esc(selectedMode())} Options account and include trading permission.</p></div></article></section>`;
  }

  function body() {
    if (S.view === "trades") return tradesView();
    if (S.view === "strategy") return strategyView();
    if (S.view === "settings") return settingsView();
    return overview();
  }

  function loader() {
    const active = S.booting || S.busy || S.mutating;
    return `<div class="foa-route-loader ${active ? "show" : ""}"><div><i></i><strong>${esc(S.loaderText || "Loading…")}</strong><span>Please wait while the dashboard updates.</span></div></div>`;
  }

  function render(force = true) {
    setTheme(S.theme);
    const app = document.querySelector("#foa-simple-app");
    if (!app) return;
    app.innerHTML = `<div class="foa-shell"><aside class="foa-sidebar"><div class="foa-brand"><div class="foa-logo">F</div><strong>Father of<br>Automation</strong></div><nav>${navButton("overview", "⌂", "Overview")}${navButton("trades", "↗", "Trades")}${navButton("strategy", "◉", "Strategy")}${navButton("settings", "⚙", "Settings")}</nav></aside><main class="foa-main">${header()}${S.error ? `<div class="foa-message error">${esc(S.error)}</div>` : ""}${S.notice ? `<div class="foa-message notice">${esc(S.notice)}</div>` : ""}${body()}</main></div><nav class="foa-bottom-nav">${navButton("overview", "⌂", "Home")}${navButton("trades", "↗", "Trades")}${navButton("strategy", "◉", "Strategy")}${navButton("settings", "⚙", "Settings")}</nav>${loader()}`;
    bind(app);
  }

  function switchView(view) {
    S.view = VIEWS.has(view) ? view : "overview";
    localStorage.setItem(K.view, S.view);
    S.error = "";
    S.notice = "";
    setLoader(`Opening ${S.view}…`);
    window.setTimeout(() => {
      S.loaderText = "";
      render();
    }, 180);
  }

  async function mutate(action, successMessage, loadingText = "Saving…") {
    if (S.mutating) return;
    S.mutating = true;
    S.error = "";
    S.notice = "";
    setLoader(loadingText);
    try {
      await action();
      S.notice = successMessage;
      await refresh(true, "Refreshing account…");
    } catch (error) {
      S.error = String(error?.message || error);
      render();
    } finally {
      S.mutating = false;
      S.loaderText = "";
      render();
    }
  }

  function bind(root) {
    root.querySelectorAll("[data-view]").forEach(button => {
      button.onclick = () => switchView(button.dataset.view);
    });
    root.querySelectorAll("[data-action-link]").forEach(button => {
      button.onclick = () => { window.location.href = button.dataset.actionLink; };
    });
    const theme = root.querySelector("#theme");
    if (theme) theme.onclick = () => { setTheme(S.theme === "light" ? "dark" : "light"); render(); };
    const logout = root.querySelector("#logout");
    if (logout) logout.onclick = () => mutate(async () => {
      await postJSON("/me/logout");
      S.me = { authenticated: false };
      S.life = null;
      S.trades = [];
      S.tradeSummary = {};
      S.view = "overview";
      localStorage.setItem(K.view, S.view);
    }, "Logged out successfully.", "Logging out…");
    const dismiss = root.querySelector("#dismiss-wizard");
    if (dismiss) dismiss.onclick = () => {
      S.wizardOpen = false;
      localStorage.setItem(K.wizard, "1");
      render();
    };
    root.querySelectorAll("[data-mode]").forEach(button => {
      button.onclick = () => mutate(async () => {
        await postJSON("/me/switch-account", { account_type: button.dataset.mode });
        S.mode = button.dataset.mode;
        localStorage.setItem(K.mode, S.mode);
      }, `Switched to ${button.dataset.mode}.`, `Switching to ${button.dataset.mode}…`);
    });
    root.querySelectorAll("[data-control]").forEach(button => {
      button.onclick = () => {
        if (S.me?.requires_api_token) {
          S.error = "Connect the Deriv trading credential in Settings before starting.";
          switchView("settings");
          return;
        }
        const control = button.dataset.control;
        if (control === "start") mutate(() => postJSON("/me/resume-trading", { mode: "start_again" }), "Auto trading started.", "Starting auto trading…");
        if (control === "stop") mutate(() => postJSON("/me/stop-trading"), "Auto trading stopped.", "Stopping auto trading…");
        if (control === "pause") mutate(() => postJSON("/me/pause-trading"), "Auto trading paused.", "Pausing auto trading…");
        if (control === "resume") mutate(() => postJSON("/me/resume-trading", { mode: "continue" }), "Auto trading resumed.", "Resuming auto trading…");
      };
    });
    const martingaleMode = root.querySelector("#mm");
    const customFields = root.querySelector("#custom");
    if (martingaleMode && customFields) martingaleMode.onchange = () => customFields.classList.toggle("show", martingaleMode.value === "custom");
    const settingsForm = root.querySelector("#settings-form");
    if (settingsForm) settingsForm.onsubmit = event => {
      event.preventDefault();
      const form = new FormData(settingsForm);
      const mode = String(form.get("martingale_mode") || "system");
      mutate(() => postJSON("/me/trading-settings", {
        stake_amount: Number(form.get("stake_amount") || 0.5),
        take_profit: Number(form.get("take_profit") || 0),
        stop_loss: Number(form.get("stop_loss") || 0),
        martingale_enabled: mode !== "flat",
        martingale_mode: mode,
        martingale_trigger_losses: Number(form.get("martingale_trigger_losses") || 1),
        martingale_multiplier: Number(form.get("martingale_multiplier") || 2),
        martingale_max_levels: Number(form.get("martingale_max_levels") || 6),
        martingale_max_stake: Number(form.get("martingale_max_stake") || 1000),
      }), "Trading settings saved.", "Saving trading settings…");
    };
    const tokenForm = root.querySelector("#token-form");
    if (tokenForm) tokenForm.onsubmit = event => {
      event.preventDefault();
      const form = new FormData(tokenForm);
      mutate(() => postJSON("/me/api-token", { api_token: String(form.get("api_token") || "").trim() }), "Trading credential saved.", "Verifying trading credential…");
    };
  }

  async function refresh(force = false, loadingText = "Refreshing dashboard…") {
    if (S.busy && !force) return;
    S.busy = true;
    S.loaderText = loadingText;
    render(false);
    try {
      S.me = await getJSON("/me");
      if (authenticated()) {
        S.mode = S.me.account_type || S.mode;
        localStorage.setItem(K.mode, S.mode);
      }
      S.summary = await getJSON(`/metrics/summary?mode=${encodeURIComponent(S.mode)}`);
      if (authenticated()) {
        const [life, today] = await Promise.all([
          getJSON("/me/trading-lifecycle"),
          getJSON("/me/trades/today"),
        ]);
        S.life = life;
        S.trades = Array.isArray(today.trades) ? today.trades : [];
        S.tradeSummary = { ...(today.summary || {}), date: today.date };
      } else {
        S.life = null;
        S.trades = [];
        S.tradeSummary = {};
      }
      S.error = "";
    } catch (error) {
      S.error = `Dashboard refresh failed: ${String(error?.message || error)}`;
    } finally {
      S.busy = false;
      S.booting = false;
      S.loaderText = "";
      render();
    }
  }

  function installUXStyles() {
    if (document.querySelector("#foa-ux-styles")) return;
    const style = document.createElement("style");
    style.id = "foa-ux-styles";
    style.textContent = `
      .foa-route-loader{position:fixed;inset:0;z-index:9999;display:none;place-items:center;background:rgba(2,6,23,.34);backdrop-filter:blur(8px)}
      .foa-route-loader.show{display:grid}.foa-route-loader>div{min-width:260px;max-width:92vw;padding:24px;border-radius:18px;border:1px solid var(--line);background:linear-gradient(145deg,var(--panel),var(--panel2));box-shadow:0 30px 90px rgba(0,0,0,.35);text-align:center;color:var(--text)}
      .foa-route-loader i{display:block;width:34px;height:34px;margin:0 auto 13px;border-radius:50%;border:3px solid rgba(148,163,184,.22);border-top-color:var(--blue);animation:foa-spin .85s linear infinite}.foa-route-loader strong{display:block;font-size:17px}.foa-route-loader span{display:block;color:var(--muted);font-size:13px;margin-top:5px}@keyframes foa-spin{to{transform:rotate(360deg)}}
      .foa-wizard-card{margin-bottom:18px}.foa-wizard-card.compact{margin-bottom:18px}.foa-wizard-card .foa-card-head strong{font-size:22px;color:var(--blue)}.foa-wizard-progress{height:8px;background:rgba(148,163,184,.16);border-radius:999px;margin:18px 0;overflow:hidden}.foa-wizard-progress i{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--green));border-radius:999px}.foa-wizard-steps{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.foa-wizard-steps button{text-align:left;border:1px solid var(--line);border-radius:13px;background:rgba(255,255,255,.035);color:var(--text);padding:12px;display:flex;gap:10px;cursor:pointer}.foa-wizard-steps button.done{border-color:rgba(65,215,93,.35);background:rgba(65,215,93,.08)}.foa-wizard-steps b{display:grid;place-items:center;width:26px;height:26px;border-radius:999px;background:rgba(47,115,255,.16);color:#7aa7ff;flex:0 0 26px}.foa-wizard-steps .done b{background:rgba(65,215,93,.16);color:var(--green)}.foa-wizard-steps strong{display:block;font-size:13px}.foa-wizard-steps small{display:block;color:var(--muted);font-size:11px;line-height:1.35;margin-top:3px}.foa-wizard-actions{display:flex;gap:12px;justify-content:flex-end;margin-top:16px}.foa-wizard-actions button{min-height:42px;padding:0 16px;border-radius:10px;border:1px solid var(--line);cursor:pointer}.foa-login-required{text-align:center;padding:64px 20px}.foa-login-logo{margin:0 auto 16px}.foa-bottom-nav{z-index:50}
      @media(max-width:900px){.foa-wizard-steps{grid-template-columns:1fr}.foa-wizard-actions{justify-content:stretch}.foa-wizard-actions>*{flex:1}.foa-route-loader>div{margin:0 16px}.foa-login-required{padding:46px 18px}}
    `;
    document.head.appendChild(style);
  }

  function boot() {
    installUXStyles();
    document.querySelector("#foa-bootstrap")?.remove();
    if (document.querySelector("#foa-simple-app")) return;
    document.body.classList.add("foa-simple-active");
    const app = document.createElement("div");
    app.id = "foa-simple-app";
    app.dataset.theme = S.theme;
    app.dataset.uiVersion = VERSION;
    document.body.appendChild(app);
    render();
    refresh(true, "Opening dashboard…");
    window.setInterval(() => refresh(false, "Refreshing dashboard…"), 8000);
  }

  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", boot, { once: true }) : boot();
})();
