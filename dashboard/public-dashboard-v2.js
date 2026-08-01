(() => {
  "use strict";

  const THEME_KEY = "foa-theme-v2";
  const MODE_KEY = "foa-mode-v2";
  const VIEW_KEY = "foa-view-v2";
  const REFRESH_MS = 7000;

  const state = {
    theme: localStorage.getItem(THEME_KEY) || "dark",
    mode: localStorage.getItem(MODE_KEY) || "demo",
    view: localStorage.getItem(VIEW_KEY) || "overview",
    me: { authenticated: false },
    summary: {},
    lifecycle: { lifecycle: "stopped" },
    trades: [],
    busy: false,
    error: "",
    notice: "",
  };

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const num = value => Number(value || 0).toLocaleString();
  const money = value => {
    const n = Number(value || 0);
    return `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`;
  };
  const percent = value => `${Number(value || 0).toFixed(1)}%`;

  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    const text = await response.text();
    let body = {};
    try { body = text ? JSON.parse(text) : {}; } catch (_) { body = { detail: text }; }
    if (!response.ok) throw new Error(body.detail || body.message || `Request failed (${response.status})`);
    return body;
  }

  function post(path, body) {
    return request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }

  function performance() {
    const model = state.summary?.system_performance?.today || {};
    const personal = state.me?.stats || {};
    const logged = Boolean(state.me?.authenticated);
    const trades = Number(logged ? personal.trades : (model.total_trades ?? state.summary?.purchased_trades ?? 0));
    const wins = Number(logged ? personal.wins : (model.wins ?? state.summary?.wins ?? 0));
    const losses = Number(logged ? personal.losses : (model.losses ?? state.summary?.losses ?? 0));
    const profit = Number(logged ? personal.profit : (model.with_martingale_pnl ?? model.martingale_pnl ?? state.summary?.net_profit ?? 0));
    return { trades, wins, losses, profit, winRate: trades ? wins / trades * 100 : 0, model };
  }

  function lifecycleName() {
    const raw = String(state.lifecycle?.lifecycle || "").toLowerCase();
    if (["running","paused","stopped"].includes(raw)) return raw;
    const status = String(state.me?.execution_status || "stopped").toLowerCase();
    if (status.includes("pause") || !state.me?.enabled && status !== "stopped") return "paused";
    if (state.me?.enabled) return "running";
    return "stopped";
  }

  function navButton(view, icon, label) {
    return `<button data-view="${view}" class="${state.view === view ? "active" : ""}"><span>${icon}</span>${label}</button>`;
  }

  function header() {
    const logged = Boolean(state.me?.authenticated);
    return `<header class="topbar">
      <div class="mobile-brand"><span class="logo">F</span><b>Father of Automation</b></div>
      <div class="view-title"><h1>${esc({overview:"Overview",trades:"Today's Trades",automation:"Automation",settings:"Settings"}[state.view])}</h1><p>${logged ? `${esc(state.me.account_type || state.mode).toUpperCase()} account` : "Public model dashboard"}</p></div>
      <div class="header-actions">
        <button id="theme-toggle" class="theme-toggle" aria-label="Toggle theme">${state.theme === "light" ? "☀" : "☾"}</button>
        ${logged
          ? `<div class="account-menu"><button id="account-menu-button"><span>${esc((state.me.account_type || state.mode).toUpperCase())}</span> ▾</button><div id="account-menu-panel" hidden><button data-view="settings">Account settings</button><a href="/logout">Log out</a></div></div>`
          : `<a class="login-button" href="/oauth/start">Login with Deriv</a>`}
      </div>
    </header>`;
  }

  function modeToggle() {
    if (!state.me?.authenticated) return "";
    return `<div class="mode-toggle"><button data-mode="demo" class="${state.mode === "demo" ? "active" : ""}">Demo</button><button data-mode="real" class="${state.mode === "real" ? "active" : ""}">Real</button></div>`;
  }

  function publicWelcome() {
    return `<section class="welcome-card">
      <div><span class="eyebrow">AUTOMATED DIGITS TRADING</span><h2>Follow the model. Control your own account.</h2><p>View public model performance without logging in. Login with Deriv to connect your Demo or Real account, choose your risk settings, and start automation.</p></div>
      <a href="/oauth/start">Login with Deriv</a>
    </section>`;
  }

  function metricCards(perf) {
    const logged = Boolean(state.me?.authenticated);
    const balance = logged ? Number(state.me.balance || 0) : Number(state.summary?.primary_account_balance || 0);
    const status = logged ? lifecycleName() : String(state.summary?.status || "Online");
    return `<section class="metrics">
      <article><i>▣</i><div><span>${logged ? "Balance" : "Registered Traders"}</span><strong>${logged ? money(balance) : num(state.summary?.registered_traders ?? state.summary?.registered_accounts ?? 0)}</strong><small>${logged ? "Selected account" : "All time"}</small></div></article>
      <article><i class="green">↗</i><div><span>${logged ? "Today's Profit" : "Model P/L Today"}</span><strong class="${perf.profit < 0 ? "red" : "green"}">${money(perf.profit)}</strong><small>Since 00:00</small></div></article>
      <article><i>◎</i><div><span>Win Rate</span><strong>${percent(perf.winRate)}</strong><small>${num(perf.wins)} wins / ${num(perf.trades)} trades</small></div></article>
      <article><i>🤖</i><div><span>${logged ? "Automation" : "Trading Now"}</span><strong class="green">${logged ? esc(status[0].toUpperCase()+status.slice(1)) : num(state.summary?.trading_now ?? state.summary?.active_traders ?? 0)}</strong><small>${logged ? "Current account state" : "Active traders"}</small></div></article>
    </section>`;
  }

  function actionControls() {
    if (!state.me?.authenticated) return `<a class="wide-login" href="/oauth/start">Login to control your account</a>`;
    const life = lifecycleName();
    if (life === "running") {
      return `<div class="control-actions"><button class="danger primary-action" data-action="stop">■ Stop Auto Trade</button><button class="secondary" data-action="pause">Ⅱ Pause</button></div>`;
    }
    if (life === "paused") {
      return `<div class="control-actions"><button class="danger primary-action" data-action="stop">■ Stop Auto Trade</button><button class="blue secondary" data-action="resume">▶ Resume</button></div>`;
    }
    return `<div class="control-actions single"><button class="blue primary-action" data-action="start">▶ Start Auto Trade</button></div>`;
  }

  function accountCard(perf) {
    const logged = Boolean(state.me?.authenticated);
    return `<article class="card account-card">
      <div class="card-head"><div><h2>${logged ? "My Account" : "Public Model"}</h2><p>${logged ? "Your selected Deriv account" : "Standard $0.50 model reference"}</p></div>${modeToggle()}</div>
      <div class="large-value">${logged ? money(state.me.balance) : money(perf.profit)}</div>
      <div class="triple"><div><span>Today's Trades</span><strong>${num(perf.trades)}</strong></div><div><span>Wins</span><strong class="green">${num(perf.wins)}</strong></div><div><span>Losses</span><strong class="red">${num(perf.losses)}</strong></div></div>
      ${actionControls()}
      ${state.me?.authenticated && !state.me?.has_trading_api_token ? `<div class="warning">This account has no valid trading credential. Reconnect using Login with Deriv before starting.</div>` : ""}
    </article>`;
  }

  function automationCard() {
    const strategy = state.summary?.strategy || {};
    return `<article class="card automation-card"><div class="card-head"><div><h2>Automation</h2><p>Current model and account state</p></div><span class="status-pill">${esc(lifecycleName())}</span></div>
      <div class="details"><div><span>Strategy</span><strong>${esc(state.summary?.strategy_name || "AI Digit Recovery V1")}</strong></div><div><span>Normal trade</span><strong>${esc(strategy.normal || "DIGITOVER 1")}</strong></div><div><span>Recovery trade</span><strong>${esc(strategy.recovery || "DIGITOVER 3")}</strong></div><div><span>Virtual confirmation</span><strong>${esc(strategy.virtual_confirmation_wins || 2)} wins</strong></div></div>
    </article>`;
  }

  function performanceCard(perf) {
    const points = [55,52,58,49,46,41,44,38,35,31,34,28,25,20,22,16,13,10];
    return `<article class="card performance-card"><div class="card-head"><div><h2>Performance</h2><p>Today's settled outcomes</p></div><span>Today</span></div>
      <svg viewBox="0 0 520 170" preserveAspectRatio="none"><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f73ff" stop-opacity=".35"/><stop offset="1" stop-color="#2f73ff" stop-opacity="0"/></linearGradient></defs>${[30,70,110,150].map(y=>`<line x1="10" y1="${y}" x2="510" y2="${y}"/>`).join("")}<polyline points="${points.map((y,i)=>`${10+i*29},${y+35}`).join(" ")}"/><polygon points="10,165 ${points.map((y,i)=>`${10+i*29},${y+35}`).join(" ")} 510,165"/></svg>
      <div class="triple compact"><div><span>P/L</span><strong class="${perf.profit < 0 ? "red" : "green"}">${money(perf.profit)}</strong></div><div><span>Max Drawdown</span><strong class="red">${money(-Math.abs(perf.model?.max_drawdown_martingale || 0))}</strong></div><div><span>Average Trade</span><strong>${money(perf.trades ? perf.profit/perf.trades : 0)}</strong></div></div>
    </article>`;
  }

  function tradeRows(all = false) {
    const rows = all ? state.trades : state.trades.slice(0, 6);
    if (!rows.length) return `<div class="empty">No settled trades today.</div>`;
    return rows.map(row => {
      const rawTime = row.purchase_time || row.settlement_time || row.created_at || row.time;
      let time = "—"; try { time = rawTime ? new Date(rawTime).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "—"; } catch (_) {}
      const type = String(row.contract_type || row.type || "Trade").toUpperCase();
      const profit = Number(row.profit ?? row.profit_loss ?? row.pnl ?? 0);
      const outcome = String(row.outcome || row.status || "").toUpperCase();
      const cls = profit < 0 || outcome.includes("LOSS") ? "red" : "green";
      return `<div class="trade-row"><span>${esc(time)}</span><span><b>${esc(row.market || row.symbol || "Market")}</b><small>${esc(type)}</small></span><span>${money(row.stake_amount ?? row.stake ?? row.amount ?? 0)}</span><strong class="${cls}">${profit ? money(profit) : esc(outcome || "—")}</strong></div>`;
    }).join("");
  }

  function tradesCard(all = false) {
    return `<article class="card trades-card"><div class="card-head"><div><h2>${all ? "All Today's Trades" : "Recent Trades"}</h2><p>${state.me?.authenticated ? `${esc(state.mode.toUpperCase())} account activity` : "Public model activity"}</p></div>${all ? `<span>${num(state.trades.length)} trades</span>` : `<button data-view="trades">View all</button>`}</div><div class="trade-head"><span>Time</span><span>Trade</span><span>Stake</span><span>Result</span></div>${tradeRows(all)}</article>`;
  }

  function overview(perf) {
    return `${!state.me?.authenticated ? publicWelcome() : ""}${metricCards(perf)}<section class="main-grid">${accountCard(perf)}${automationCard()}${performanceCard(perf)}</section>${tradesCard(false)}`;
  }

  function tradesView() {
    return `<section class="page-intro"><div><h2>Today's Trades</h2><p>All settled trades from 00:00 for the selected ${esc(state.mode)} mode.</p></div>${modeToggle()}</section>${tradesCard(true)}`;
  }

  function automationView() {
    const perf = performance();
    return `<section class="page-intro"><div><h2>Automation Control</h2><p>Start, pause, resume, or stop the selected account safely.</p></div>${modeToggle()}</section><section class="two-grid">${accountCard(perf)}${automationCard()}</section><article class="card explanation"><h2>How the strategy works</h2><div class="steps"><div><b>1</b><span><strong>Normal</strong>DIGITOVER 1 while there is no recovery debt.</span></div><div><b>2</b><span><strong>Recovery</strong>DIGITOVER 3 after one real loss.</span></div><div><b>3</b><span><strong>Protection</strong>Virtual mode after a failed recovery until two virtual wins.</span></div><div><b>4</b><span><strong>Split recovery</strong>Recover remaining debt over two real targets.</span></div></div></article>`;
  }

  function input(name, label, value, type="number", attrs="") {
    return `<label><span>${label}</span><input name="${name}" type="${type}" value="${esc(value)}" ${attrs}></label>`;
  }

  function settingsView() {
    if (!state.me?.authenticated) return `<section class="locked"><h2>Login required</h2><p>Login with Deriv to configure stake, take profit, stop loss, and Martingale controls.</p><a href="/oauth/start">Login with Deriv</a></section>`;
    const s = state.me.settings || {};
    const mode = s.martingale_mode || (s.martingale_enabled === false ? "flat" : "system");
    return `<section class="page-intro"><div><h2>Trading Settings</h2><p>Settings apply only to the selected ${esc(state.mode.toUpperCase())} account.</p></div>${modeToggle()}</section><form id="settings-form" class="card settings-card"><div class="settings-section"><h3>Risk controls</h3><div class="form-grid">${input("stake_amount","Stake amount (USD)",s.stake_amount ?? 0.50,"number",'min="0.35" step="0.01"')}${input("take_profit","Take profit (USD)",s.take_profit ?? 0,"number",'min="0" step="0.01"')}${input("stop_loss","Stop loss (USD)",s.stop_loss ?? 0,"number",'min="0" step="0.01"')}</div></div><div class="settings-section"><h3>Martingale control</h3><div class="mode-cards"><label><input type="radio" name="martingale_mode" value="system" ${mode === "system" ? "checked" : ""}><b>System</b><span>Exact-debt recovery managed by the strategy.</span></label><label><input type="radio" name="martingale_mode" value="custom" ${mode === "custom" ? "checked" : ""}><b>Custom</b><span>Use your own trigger and multiplier.</span></label><label><input type="radio" name="martingale_mode" value="flat" ${mode === "flat" ? "checked" : ""}><b>Flat stake</b><span>No stake escalation.</span></label></div><div id="custom-fields" class="form-grid">${input("martingale_trigger_losses","Start after losses",s.martingale_trigger_losses ?? 1,"number",'min="1" max="10" step="1"')}${input("martingale_multiplier","Multiplier",s.martingale_multiplier ?? 2,"number",'min="1.1" max="10" step="0.1"')}${input("martingale_max_levels","Maximum levels",s.martingale_max_levels ?? 6,"number",'min="1" max="10" step="1"')}${input("martingale_max_stake","Maximum stake",s.martingale_max_stake ?? 1000,"number",'min="0.35" step="0.01"')}</div></div><div class="settings-section credential"><div><h3>Deriv connection</h3><p>${state.me.has_trading_api_token ? "Trading credential connected." : "Trading credential missing or invalid."}</p></div><a href="/oauth/start">Reconnect Deriv</a></div><button class="save-button" type="submit">Save Settings</button></form>`;
  }

  function content() {
    const perf = performance();
    if (state.view === "trades") return tradesView();
    if (state.view === "automation") return automationView();
    if (state.view === "settings") return settingsView();
    return overview(perf);
  }

  function render() {
    document.documentElement.dataset.theme = state.theme;
    const app = document.getElementById("foa-app");
    if (!app) return;
    app.dataset.theme = state.theme;
    app.innerHTML = `<div class="shell"><aside><div class="brand"><span class="logo">F</span><b>Father of<br>Automation</b></div><nav>${navButton("overview","⌂","Overview")}${navButton("trades","↗","Trades")}${navButton("automation","🤖","Automation")}${navButton("settings","⚙","Settings")}</nav></aside><main>${header()}${state.error ? `<div class="alert error">${esc(state.error)}</div>` : ""}${state.notice ? `<div class="alert notice">${esc(state.notice)}</div>` : ""}<div class="content">${content()}</div></main></div><nav class="bottom-nav">${navButton("overview","⌂","Home")}${navButton("trades","↗","Trades")}${navButton("automation","🤖","Automation")}${navButton("settings","⚙","Settings")}</nav>`;
    bind();
  }

  async function changeLifecycle(action) {
    if (!state.me?.authenticated) { location.href = "/oauth/start"; return; }
    state.busy = true; state.error = ""; state.notice = ""; render();
    try {
      if (action === "start") await post("/me/resume-trading", { mode: "start_again" });
      if (action === "resume") await post("/me/resume-trading", { mode: "continue" });
      if (action === "pause") await post("/me/pause-trading");
      if (action === "stop") await post("/me/stop-trading");
      state.notice = `${action[0].toUpperCase()+action.slice(1)} request completed.`;
      await refresh();
    } catch (error) { state.error = error.message || String(error); }
    finally { state.busy = false; render(); }
  }

  async function saveSettings(form) {
    const data = new FormData(form);
    const mode = String(data.get("martingale_mode") || "system");
    const payload = {
      stake_amount: Number(data.get("stake_amount")),
      take_profit: Number(data.get("take_profit")),
      stop_loss: Number(data.get("stop_loss")),
      martingale_enabled: mode !== "flat",
      martingale_mode: mode,
      martingale_trigger_losses: Number(data.get("martingale_trigger_losses")),
      martingale_multiplier: Number(data.get("martingale_multiplier")),
      martingale_max_levels: Number(data.get("martingale_max_levels")),
      martingale_max_stake: Number(data.get("martingale_max_stake")),
    };
    state.error = ""; state.notice = "";
    try { await post("/me/trading-settings", payload); state.notice = "Trading settings saved successfully."; await refresh(); }
    catch (error) { state.error = error.message || String(error); render(); }
  }

  function bind() {
    document.querySelectorAll("[data-view]").forEach(el => el.onclick = () => { state.view = el.dataset.view; localStorage.setItem(VIEW_KEY,state.view); render(); window.scrollTo(0,0); });
    document.querySelectorAll("[data-mode]").forEach(el => el.onclick = async () => { state.mode = el.dataset.mode; localStorage.setItem(MODE_KEY,state.mode); if (state.me?.authenticated) { try { await post("/me/switch-account", { account_type: state.mode }); } catch (e) { state.error=e.message; } } await refresh(); });
    document.querySelectorAll("[data-action]").forEach(el => el.onclick = () => !state.busy && changeLifecycle(el.dataset.action));
    const theme = document.getElementById("theme-toggle"); if (theme) theme.onclick = () => { state.theme = state.theme === "light" ? "dark" : "light"; localStorage.setItem(THEME_KEY,state.theme); render(); };
    const menu = document.getElementById("account-menu-button"); if (menu) menu.onclick = () => { const panel=document.getElementById("account-menu-panel"); panel.hidden=!panel.hidden; };
    const form = document.getElementById("settings-form"); if (form) { form.onsubmit = e => { e.preventDefault(); saveSettings(form); }; form.querySelectorAll('[name="martingale_mode"]').forEach(r=>r.onchange=()=>{ const custom=document.getElementById("custom-fields"); custom.style.display=r.value==="custom"&&r.checked?"grid":custom.style.display; if(r.checked&&r.value!=="custom")custom.style.display="none"; }); const selected=form.querySelector('[name="martingale_mode"]:checked'); const custom=document.getElementById("custom-fields"); if(custom&&selected)custom.style.display=selected.value==="custom"?"grid":"none"; }
  }

  async function refresh() {
    if (state.busy) return;
    try {
      state.me = await request("/me");
      if (state.me?.authenticated && state.me.account_type) { state.mode=state.me.account_type; localStorage.setItem(MODE_KEY,state.mode); }
      state.summary = await request(`/metrics/summary?mode=${encodeURIComponent(state.mode)}`);
      if (state.me?.authenticated) { try { state.lifecycle=await request("/me/trading-lifecycle"); } catch (_) { state.lifecycle={lifecycle:state.me.enabled?"running":"stopped"}; } }
      const response = await request(`/metrics/recent-trades?limit=500&activity_type=actual&mode=${encodeURIComponent(state.mode)}`);
      state.trades = Array.isArray(response.trades) ? response.trades : [];
      state.error = "";
    } catch (error) { state.error = `Dashboard refresh failed: ${error.message || error}`; }
    render();
  }

  function styles() {
    const s=document.createElement("style"); s.textContent=`
    *{box-sizing:border-box}html,body{margin:0;min-height:100%;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}body{background:#071120;color:#f8fafc}#foa-app{--bg:#071120;--panel:#111d30;--panel2:#142238;--line:rgba(148,163,184,.18);--text:#f8fafc;--muted:#9eabc0;--blue:#2f73ff;--green:#3ddc64;--red:#ff4b55;min-height:100vh;background:radial-gradient(circle at 28% 0,rgba(47,115,255,.14),transparent 34rem),linear-gradient(135deg,#030a14,#081322 50%,#0c1a2d);color:var(--text)}#foa-app[data-theme=light]{--bg:#f6f8fc;--panel:#fff;--panel2:#fff;--line:#e4e9f1;--text:#101828;--muted:#607089;--blue:#1769ff;--green:#14a83c;--red:#e62e3c;background:#f7f9fc}.shell{display:grid;grid-template-columns:230px minmax(0,1fr);min-height:100vh}aside{padding:28px 14px;border-right:1px solid var(--line);background:rgba(2,8,20,.6)}[data-theme=light] aside{background:#fff}.brand,.mobile-brand{display:flex;align-items:center;gap:12px;font-size:18px;line-height:1.05}.logo{display:grid;place-items:center;width:44px;height:38px;border-radius:10px 10px 18px 7px;background:linear-gradient(135deg,#1769ff,#6aa4ff);color:#fff;font-weight:900;font-size:24px;transform:skewX(-12deg)}aside nav{display:grid;gap:9px;margin-top:34px}nav button{border:0;background:transparent;color:var(--muted);border-radius:11px;padding:14px 15px;text-align:left;font-size:16px;cursor:pointer}nav button span{display:inline-block;width:30px}nav button.active{background:rgba(47,115,255,.18);color:var(--blue);font-weight:700}main{min-width:0}.topbar{min-height:82px;padding:17px 26px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:18px}.view-title h1{margin:0;font-size:24px}.view-title p{margin:3px 0 0;color:var(--muted);font-size:13px}.mobile-brand{display:none}.header-actions{display:flex;align-items:center;gap:10px}.theme-toggle,.account-menu>button{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:999px;padding:10px 14px;cursor:pointer}.login-button,.wide-login,.locked a,.welcome-card a{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;background:var(--blue);color:#fff;border-radius:12px;padding:12px 18px;font-weight:750}.account-menu{position:relative}.account-menu>div{position:absolute;right:0;top:48px;z-index:20;min-width:170px;padding:8px;background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 20px 50px rgba(0,0,0,.25)}.account-menu>div button,.account-menu>div a{display:block;width:100%;text-align:left;padding:10px;border:0;background:transparent;color:var(--text);text-decoration:none;cursor:pointer}.content{padding:24px;max-width:1440px;margin:auto}.alert{margin-bottom:14px;padding:13px 15px;border-radius:12px}.alert.error{background:rgba(255,75,85,.12);border:1px solid rgba(255,75,85,.3);color:#ffb8bd}.alert.notice{background:rgba(61,220,100,.11);border:1px solid rgba(61,220,100,.25);color:#bff5cb}.welcome-card{display:flex;justify-content:space-between;align-items:center;gap:25px;padding:28px;margin-bottom:18px;border:1px solid rgba(47,115,255,.28);border-radius:18px;background:linear-gradient(120deg,rgba(47,115,255,.18),rgba(47,115,255,.03))}.welcome-card h2{font-size:28px;margin:7px 0}.welcome-card p{max-width:760px;margin:0;color:var(--muted);line-height:1.6}.eyebrow{color:#74a3ff;font-size:12px;font-weight:800;letter-spacing:.1em}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:16px}.metrics article,.card{background:linear-gradient(145deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:17px;box-shadow:0 16px 42px rgba(0,0,0,.13)}[data-theme=light] .metrics article,[data-theme=light] .card{box-shadow:0 12px 28px rgba(15,23,42,.06)}.metrics article{padding:20px;display:flex;gap:14px;align-items:center}.metrics i{width:46px;height:46px;display:grid;place-items:center;border-radius:13px;background:rgba(47,115,255,.2);font-style:normal;font-size:22px}.metrics span,.metrics small{display:block}.metrics span{font-size:15px}.metrics strong{display:block;font-size:26px;margin:5px 0}.metrics small,.card p,.details span,.triple span,.trade-head,.settings-card label>span{color:var(--muted)}.green{color:var(--green)!important}.red{color:var(--red)!important}.main-grid{display:grid;grid-template-columns:1.35fr .8fr 1.35fr;gap:16px}.two-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:16px}.card{padding:22px}.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:15px}.card h2{margin:0;font-size:21px}.card-head p{margin:4px 0 0;font-size:13px}.mode-toggle{display:flex;padding:3px;border:1px solid var(--line);border-radius:10px}.mode-toggle button{border:0;background:transparent;color:var(--text);padding:8px 20px;border-radius:8px;cursor:pointer}.mode-toggle button.active{background:var(--blue);color:#fff}.large-value{font-size:44px;font-weight:800;margin:28px 0 20px}.triple{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:18px 0;margin-bottom:18px}.triple div{text-align:center;border-right:1px solid var(--line)}.triple div:last-child{border:0}.triple span,.triple strong{display:block}.triple strong{font-size:23px;margin-top:6px}.control-actions{display:grid;grid-template-columns:1.25fr .75fr;gap:10px}.control-actions.single{grid-template-columns:1fr}.control-actions button,.save-button{min-height:50px;border:0;border-radius:11px;color:#fff;font-size:16px;font-weight:750;cursor:pointer}.blue{background:var(--blue)!important}.danger{background:linear-gradient(135deg,#b6242e,#ef3b45)}.secondary{background:rgba(148,163,184,.13);border:1px solid var(--line)!important;color:var(--text)!important}.warning{margin-top:12px;padding:11px;border-radius:10px;background:rgba(245,158,11,.12);color:#f6cf7b;font-size:13px}.details{margin-top:16px}.details div{padding:13px 0;border-bottom:1px solid var(--line)}.details span,.details strong{display:block}.details strong{margin-top:4px;color:#6fa1ff}.status-pill{padding:7px 10px;border-radius:999px;background:rgba(61,220,100,.13);color:var(--green);text-transform:capitalize;font-size:12px}.performance-card svg{width:100%;height:170px;margin-top:12px}.performance-card line{stroke:var(--line)}.performance-card polyline{fill:none;stroke:var(--blue);stroke-width:4}.performance-card polygon{fill:url(#fill)}.triple.compact{margin-bottom:0}.trades-card{margin-top:16px}.card-head>button{border:0;background:transparent;color:var(--blue);cursor:pointer;font-weight:700}.trade-head,.trade-row{display:grid;grid-template-columns:1.1fr 1.5fr 1fr 1fr;gap:10px;align-items:center;padding:12px 0;border-bottom:1px solid var(--line)}.trade-row span:nth-child(2) small{display:block;color:var(--muted);margin-top:3px}.trade-row strong{text-align:right}.empty{padding:28px 0;color:var(--muted)}.page-intro{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}.page-intro h2{margin:0;font-size:27px}.page-intro p{margin:5px 0 0;color:var(--muted)}.explanation{margin-top:16px}.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:18px}.steps div{padding:16px;background:rgba(47,115,255,.08);border:1px solid var(--line);border-radius:13px}.steps b{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:var(--blue);color:#fff}.steps span,.steps strong{display:block}.steps strong{margin:10px 0 5px}.settings-card{max-width:1050px}.settings-section{padding:4px 0 22px;margin-bottom:20px;border-bottom:1px solid var(--line)}.settings-section h3{margin:0 0 14px}.form-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}.form-grid label span{display:block;font-size:13px;margin-bottom:7px}.form-grid input{width:100%;min-height:46px;border:1px solid var(--line);border-radius:10px;padding:0 12px;background:rgba(255,255,255,.04);color:var(--text);font-size:16px}.mode-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:15px}.mode-cards label{padding:16px;border:1px solid var(--line);border-radius:12px;cursor:pointer}.mode-cards b,.mode-cards span{display:block}.mode-cards span{margin-top:6px;color:var(--muted);font-size:13px}.credential{display:flex;align-items:center;justify-content:space-between}.credential p{margin:5px 0}.credential a{color:var(--blue);text-decoration:none}.save-button{background:var(--blue);padding:0 24px}.locked{text-align:center;padding:70px 20px}.locked p{color:var(--muted)}.bottom-nav{display:none}
    @media(max-width:1050px){.shell{grid-template-columns:1fr}aside{display:none}.mobile-brand{display:flex}.view-title{display:none}.main-grid{grid-template-columns:1fr 1fr}.account-card{grid-column:1/-1}.metrics{grid-template-columns:repeat(2,1fr)}}
    @media(max-width:720px){body{padding-bottom:74px}.topbar{padding:14px}.mobile-brand b{font-size:15px}.header-actions{gap:6px}.login-button{padding:10px 12px;font-size:13px}.content{padding:14px 11px}.welcome-card{display:block;padding:20px}.welcome-card a{margin-top:16px;width:100%}.metrics{gap:10px}.metrics article{display:block;padding:14px;min-height:126px}.metrics i{margin-bottom:12px}.metrics strong{font-size:22px}.main-grid,.two-grid{grid-template-columns:1fr;gap:12px}.card{padding:18px 14px}.large-value{font-size:39px}.card-head{align-items:center}.mode-toggle button{padding:8px 16px}.control-actions button{min-height:56px}.trade-head,.trade-row{grid-template-columns:.9fr 1.3fr .8fr .9fr;font-size:12px}.steps{grid-template-columns:1fr}.form-grid,.mode-cards{grid-template-columns:1fr}.credential{align-items:flex-start;gap:14px}.bottom-nav{position:fixed;z-index:50;left:0;right:0;bottom:0;height:72px;display:grid;grid-template-columns:repeat(4,1fr);background:rgba(4,12,23,.94);backdrop-filter:blur(18px);border-top:1px solid var(--line)}[data-theme=light] .bottom-nav{background:rgba(255,255,255,.94)}.bottom-nav button{display:grid;place-items:center;text-align:center;padding:5px;font-size:12px}.bottom-nav button span{width:auto;font-size:20px}.page-intro{align-items:flex-start}.page-intro .mode-toggle{margin-top:3px}}
    @media(max-width:390px){.metrics article{padding:12px}.metrics strong{font-size:19px}.metrics span{font-size:13px}.large-value{font-size:34px}.triple strong{font-size:20px}.trade-head,.trade-row{gap:6px;font-size:11px}.card{border-radius:14px}.topbar{gap:8px}}
    `; document.head.appendChild(s);
  }

  function boot() { styles(); document.body.innerHTML='<div id="foa-app"></div>'; refresh(); setInterval(refresh,REFRESH_MS); }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",boot);else boot();
})();