(() => {
  "use strict";

  const THEME_KEY = "foa-simplified-theme";
  const MODE_KEY = "foa-simplified-mode";
  const REFRESH_MS = 5000;

  const state = {
    theme: localStorage.getItem(THEME_KEY) || document.documentElement.dataset.theme || "dark",
    mode: localStorage.getItem(MODE_KEY) || "demo",
    me: null,
    summary: null,
    recent: [],
    busy: false,
    error: "",
  };

  const money = (value, currency = "USD") => {
    const n = Number(value || 0);
    const sign = n < 0 ? "-" : "";
    return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };
  const number = (value) => Number(value || 0).toLocaleString();
  const pct = (value) => `${Number(value || 0).toFixed(0)}%`;
  const safe = (value, fallback = "—") => String(value ?? fallback || fallback);
  const cssValue = (value) => String(value ?? "").replace(/[<>&]/g, "");

  function themeIsLight() {
    return state.theme === "light";
  }

  function setTheme(theme) {
    state.theme = theme === "light" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, state.theme);
    document.documentElement.dataset.theme = state.theme;
    const app = document.getElementById("foa-simple-app");
    if (app) app.dataset.theme = state.theme;
  }

  async function getJSON(url) {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  async function postJSON(url, body) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = { detail: text }; }
    if (!response.ok) {
      throw new Error(payload.detail || payload.message || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  function todayPerformance() {
    const today = state.summary?.system_performance?.today || {};
    const meStats = state.me?.stats || {};
    const profit = state.me?.authenticated ? Number(meStats.profit || 0) : Number(today.with_martingale_pnl ?? today.martingale_pnl ?? state.summary?.net_profit ?? 0);
    const trades = state.me?.authenticated ? Number(meStats.trades || 0) : Number(today.total_trades ?? state.summary?.purchased_trades ?? 0);
    const wins = state.me?.authenticated ? Number(meStats.wins || 0) : Number(today.wins ?? state.summary?.wins ?? 0);
    const losses = state.me?.authenticated ? Number(meStats.losses || 0) : Number(today.losses ?? state.summary?.losses ?? 0);
    return { today, profit, trades, wins, losses, winRate: trades ? (wins / trades) * 100 : Number(today.win_rate ? today.win_rate * 100 : 0) };
  }

  function latestBotStatus() {
    const enabled = Boolean(state.me?.enabled);
    const raw = String(state.me?.execution_status || state.summary?.status || "running").toLowerCase();
    if (enabled && !["stopped", "disabled", "manual_pause", "inactive"].includes(raw)) return "Running";
    if (raw.includes("pause")) return "Paused";
    if (raw.includes("stop") || raw.includes("disabled")) return "Stopped";
    return state.summary?.status === "RUNNING" ? "Running" : "Ready";
  }

  function chartPath(value) {
    const finalValue = Number(value || 0);
    const points = [0, 18, 8, 42, 35, 70, 62, 94, 85, 126, 118, 155, 148, 178, 168, 210, 198, 225];
    const scale = Math.max(1, Math.abs(finalValue));
    const width = 460;
    const height = 150;
    return points.map((p, i) => {
      const x = (i / (points.length - 1)) * width;
      const y = height - ((p / 225) * 115 + 18);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
  }

  function tradeRows() {
    const rows = Array.isArray(state.recent) ? state.recent.slice(0, 4) : [];
    if (!rows.length) {
      return `<div class="foa-empty">No recent trades yet. When the bot settles trades, they will appear here.</div>`;
    }
    return rows.map((row) => {
      const timeRaw = row.time || row.purchase_time || row.settlement_time || row.created_at || row.updated_at || "";
      let time = "—";
      try { time = timeRaw ? new Date(timeRaw).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"; } catch (_) { time = String(timeRaw).slice(0, 8) || "—"; }
      const symbol = row.market || row.symbol || row.trade || row.contract_type || "Trade";
      const type = String(row.contract_type || row.type || row.action || "").toUpperCase();
      const outcome = String(row.outcome || row.status || "").toUpperCase();
      const profit = Number(row.profit ?? row.profit_loss ?? row.pnl ?? 0);
      const resultClass = profit < 0 || outcome === "LOSS" || outcome === "LOST" ? "loss" : "win";
      const result = Number.isFinite(profit) && profit !== 0 ? money(profit) : (outcome ? outcome : "—");
      const badge = type.includes("UNDER") || type.includes("SELL") || type.includes("PUT") ? "SELL" : type.includes("OVER") || type.includes("BUY") ? "BUY" : "TRADE";
      return `
        <div class="foa-trade-row">
          <span>${cssValue(time)}</span>
          <span class="foa-trade-name">${cssValue(symbol)} <em class="${badge === "SELL" ? "sell" : "buy"}">${badge}</em></span>
          <span>${money(row.stake_amount ?? row.stake ?? row.amount ?? state.me?.settings?.stake_amount ?? 0)}</span>
          <strong class="${resultClass}">${cssValue(result)}</strong>
        </div>`;
    }).join("");
  }

  function render() {
    setTheme(state.theme);
    const app = document.getElementById("foa-simple-app");
    if (!app) return;
    const perf = todayPerformance();
    const me = state.me || {};
    const loggedIn = Boolean(me.authenticated);
    const balance = loggedIn ? me.balance : state.summary?.primary_account_balance || 0;
    const currency = me.currency || state.summary?.primary_account_currency || "USD";
    const accountMode = loggedIn ? (me.account_type || state.mode) : state.mode;
    const botStatus = latestBotStatus();
    const botRunning = botStatus === "Running";
    const strategyName = state.summary?.strategy_name || "AI Digit Recovery V1";
    const marketName = "Synthetic Digits";
    const lastSignal = state.recent?.[0]?.purchase_time || state.recent?.[0]?.settlement_time || "Waiting";
    const lastSignalText = lastSignal === "Waiting" ? "Waiting" : (() => { try { return new Date(lastSignal).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch (_) { return "Recent"; } })();

    app.innerHTML = `
      <div class="foa-shell">
        <aside class="foa-sidebar">
          <div class="foa-brand"><div class="foa-logo">F</div><strong>Father of<br>Automation</strong></div>
          <nav>
            <button class="active"><span>⌂</span>Overview</button>
            <button><span>↗</span>Trades</button>
            <button><span>🤖</span>Bot</button>
            <button><span>⚙</span>Settings</button>
          </nav>
        </aside>
        <main class="foa-main">
          <header class="foa-topbar">
            <div class="foa-mobile-brand"><div class="foa-logo">F</div><strong>Father of<br>Automation</strong></div>
            <label class="foa-search"><span>⌕</span><input placeholder="Search..." /></label>
            <div class="foa-top-actions">
              <span class="foa-moon">☾</span>
              <button class="foa-theme" id="foa-theme-toggle" aria-label="Toggle light and dark mode"><i></i></button>
              <span class="foa-sun">☼</span>
              ${loggedIn ? `<button class="foa-account-pill" id="foa-account-pill"><b>${accountMode}</b></button>` : `<a class="foa-login" href="/oauth/start">Login</a>`}
            </div>
          </header>

          ${state.error ? `<div class="foa-error">${cssValue(state.error)}</div>` : ""}

          <section class="foa-kpis">
            ${summaryCard("wallet", "Balance", money(balance, currency), "Account Balance")}
            ${summaryCard("profit", "Today’s Profit", money(perf.profit), perf.profit >= 0 ? "+ Live P/L" : "Needs recovery")}
            ${summaryCard("target", "Win Rate", pct(perf.winRate), `${number(perf.wins)} Wins / ${number(perf.trades)} Trades`)}
            ${summaryCard("bot", "Bot Status", `${botStatus}<span class="dot"></span>`, botRunning ? "Live and Trading" : "Ready to Start")}
          </section>

          <section class="foa-grid">
            <article class="foa-card foa-account-card">
              <div class="foa-card-head"><h2>My Account</h2><div class="foa-mode-toggle"><button class="${accountMode === "demo" ? "active" : ""}" data-mode="demo">Demo</button><button class="${accountMode === "real" ? "active" : ""}" data-mode="real">Real</button></div></div>
              <p>Account Balance</p>
              <div class="foa-balance">${money(balance, currency)}</div>
              <div class="foa-account-stats">
                <div><span>Today’s Trades</span><strong>${number(perf.trades)}</strong></div>
                <div><span>Wins</span><strong class="win">${number(perf.wins)}</strong></div>
                <div><span>Losses</span><strong class="loss">${number(perf.losses)}</strong></div>
              </div>
              <div class="foa-actions-row">
                <button class="foa-primary" data-action="start">▶ Start</button>
                <button class="foa-muted" data-action="pause">Ⅱ Pause</button>
                <button class="foa-danger" data-action="stop">■ Stop</button>
              </div>
              ${!loggedIn ? `<small class="foa-auth-note">Login with Deriv to control your personal account.</small>` : ""}
            </article>

            <article class="foa-card foa-bot-card">
              <h2>Bot Status</h2>
              ${statusLine("↗", "Strategy", strategyName)}
              ${statusLine("◎", "Market", marketName)}
              ${statusLine("◴", "Last Signal", lastSignalText)}
              ${statusLine("盾", "Risk Level", "Medium", "risk")}
            </article>

            <article class="foa-card foa-performance-card">
              <div class="foa-card-head"><h2>Performance</h2><button class="foa-small-select">Today⌄</button></div>
              <svg class="foa-chart" viewBox="0 0 500 190" preserveAspectRatio="none" aria-hidden="true">
                <defs><linearGradient id="foaChartFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f73ff" stop-opacity=".42"/><stop offset="1" stop-color="#2f73ff" stop-opacity="0"/></linearGradient></defs>
                <g class="grid">${[30,70,110,150].map(y => `<line x1="20" y1="${y}" x2="480" y2="${y}"/>`).join("")}</g>
                <polyline points="${chartPath(perf.profit)}" fill="none" stroke="#2f73ff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
                <polygon points="0,180 ${chartPath(perf.profit)} 500,180" fill="url(#foaChartFill)"/>
              </svg>
              <div class="foa-perf-stats"><div><span>P/L</span><strong class="${perf.profit < 0 ? "loss" : "win"}">${money(perf.profit)}</strong></div><div><span>Max Drawdown</span><strong class="loss">-${money(Math.max(0, Math.abs(perf.today?.max_drawdown_martingale || perf.today?.max_drawdown_fixed || 0))).replace("-$", "$")}</strong></div><div><span>Avg Trade</span><strong class="win">${money(perf.trades ? perf.profit / Math.max(1, perf.trades) : 0)}</strong></div></div>
            </article>
          </section>

          <section class="foa-card foa-trades-card">
            <div class="foa-card-head"><h2>Recent Trades</h2><a href="#">View all</a></div>
            <div class="foa-trade-head"><span>Time</span><span>Trade</span><span>Stake</span><span>Result</span></div>
            ${tradeRows()}
          </section>
        </main>
      </div>
      <nav class="foa-bottom-nav"><button class="active">⌂<span>Home</span></button><button>↗<span>Trades</span></button><button>🤖<span>Bot</span></button><button>⚙<span>Settings</span></button></nav>
    `;

    bindActions(app);
  }

  function summaryCard(type, title, value, caption) {
    const icon = { wallet: "▣", profit: "↗", target: "◎", bot: "🤖" }[type] || "•";
    return `<article class="foa-kpi ${type}"><div class="foa-kpi-icon">${icon}</div><div><span>${title}</span><strong>${value}</strong><small>${caption}</small></div></article>`;
  }

  function statusLine(icon, label, value, extra = "") {
    return `<div class="foa-status-line ${extra}"><span>${icon}</span><div><small>${label}</small><strong>${cssValue(value)}</strong></div></div>`;
  }

  function bindActions(root) {
    const themeBtn = root.querySelector("#foa-theme-toggle");
    if (themeBtn) themeBtn.onclick = () => { setTheme(themeIsLight() ? "dark" : "light"); render(); };
    root.querySelectorAll(".foa-mode-toggle button").forEach((btn) => {
      btn.onclick = async () => {
        const mode = btn.dataset.mode;
        state.mode = mode;
        localStorage.setItem(MODE_KEY, mode);
        if (state.me?.authenticated) {
          try { await postJSON("/me/switch-account", { account_type: mode }); } catch (err) { state.error = String(err.message || err); }
        }
        await refresh();
      };
    });
    root.querySelectorAll("[data-action]").forEach((btn) => {
      btn.onclick = async () => {
        if (!state.me?.authenticated) { window.location.href = "/oauth/start"; return; }
        const action = btn.dataset.action;
        state.error = "";
        try {
          if (action === "start") await postJSON("/me/auto-trade", { enabled: true });
          else await postJSON("/me/auto-trade", { enabled: false });
        } catch (err) { state.error = String(err.message || err); }
        await refresh();
      };
    });
  }

  async function refresh() {
    if (state.busy) return;
    state.busy = true;
    try {
      const me = await getJSON("/me");
      state.me = me;
      if (me?.authenticated && me.account_type) {
        state.mode = me.account_type;
        localStorage.setItem(MODE_KEY, state.mode);
      }
      state.summary = await getJSON(`/metrics/summary?mode=${encodeURIComponent(state.mode)}`);
      try {
        const recent = await getJSON(`/metrics/recent-trades?limit=4&activity_type=actual&mode=${encodeURIComponent(state.mode)}`);
        state.recent = Array.isArray(recent.trades) ? recent.trades : [];
      } catch (_) {
        state.recent = [];
      }
    } catch (err) {
      state.error = `Dashboard refresh failed: ${err.message || err}`;
    } finally {
      state.busy = false;
      render();
    }
  }

  function installStyles() {
    const style = document.createElement("style");
    style.id = "foa-simplified-styles";
    style.textContent = `
      body.foa-simple-active > *:not(#foa-simple-app):not(.foa-bottom-nav) { display: none !important; }
      body.foa-simple-active { margin: 0 !important; min-height: 100vh !important; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important; overflow-x: hidden; }
      #foa-simple-app { --bg:#071120; --panel:#101b2d; --panel2:#121f34; --line:rgba(148,163,184,.16); --text:#f8fafc; --muted:#aab6c8; --blue:#2f73ff; --green:#41d75d; --red:#ef4444; --yellow:#facc15; color:var(--text); min-height:100vh; background:radial-gradient(circle at 25% 0%, rgba(47,115,255,.18), transparent 32rem), linear-gradient(135deg,#030a14,#081322 48%,#0d1b2e); }
      #foa-simple-app[data-theme="light"] { --bg:#f7f9fc; --panel:#fff; --panel2:#fff; --line:#e6ebf2; --text:#0f172a; --muted:#52627a; --blue:#1266ff; --green:#0faa38; --red:#ef1f35; --yellow:#d97706; background:linear-gradient(180deg,#fff,#f7f9fc); }
      .foa-shell { display:grid; grid-template-columns:236px minmax(0,1fr); min-height:100vh; }
      .foa-sidebar { padding:30px 14px; background:rgba(2,9,23,.65); border-right:1px solid var(--line); }
      [data-theme="light"] .foa-sidebar { background:#fff; }
      .foa-brand,.foa-mobile-brand { display:flex; align-items:center; gap:14px; font-size:18px; line-height:1.05; }
      .foa-mobile-brand { display:none; }
      .foa-logo { width:48px; height:38px; display:grid; place-items:center; color:#fff; font-weight:900; font-size:26px; background:linear-gradient(135deg,#2f73ff,#69a6ff); border-radius:12px 12px 20px 8px; transform:skewX(-12deg); }
      .foa-sidebar nav { margin-top:34px; display:grid; gap:12px; }
      .foa-sidebar button { border:0; background:transparent; color:var(--muted); display:flex; align-items:center; gap:14px; padding:14px 16px; border-radius:10px; font-size:16px; cursor:pointer; }
      .foa-sidebar button.active { background:linear-gradient(135deg,rgba(47,115,255,.32),rgba(47,115,255,.14)); color:#fff; }
      [data-theme="light"] .foa-sidebar button.active { color:var(--blue); background:#eaf1ff; }
      .foa-main { padding:28px 22px 40px; max-width:1360px; width:100%; margin:0 auto; }
      .foa-topbar { display:grid; grid-template-columns:1fr minmax(260px,420px) 1fr; align-items:center; gap:20px; margin-bottom:28px; }
      .foa-search { grid-column:2; min-height:42px; display:flex; align-items:center; gap:10px; border:1px solid var(--line); border-radius:13px; padding:0 16px; background:rgba(255,255,255,.03); color:var(--muted); }
      [data-theme="light"] .foa-search { background:#fff; box-shadow:0 8px 22px rgba(15,23,42,.04); }
      .foa-search input { width:100%; border:0; outline:0; background:transparent; color:var(--text); font-size:15px; }
      .foa-top-actions { justify-self:end; display:flex; align-items:center; gap:12px; }
      .foa-theme { width:46px; height:24px; border:0; border-radius:999px; background:var(--blue); padding:3px; cursor:pointer; }
      .foa-theme i { display:block; width:18px; height:18px; border-radius:50%; background:#fff; margin-left:auto; }
      [data-theme="light"] .foa-theme i { margin-left:0; }
      .foa-account-pill,.foa-login { border:0; color:var(--text); background:transparent; font-size:16px; text-decoration:none; }
      .foa-account-pill { display:flex; gap:8px; align-items:center; cursor:pointer; }
      .foa-account-pill b,.foa-login { padding:10px 16px; border-radius:999px; background:var(--blue); color:#fff; text-transform:capitalize; }
      .foa-error { margin-bottom:16px; padding:12px 14px; border-radius:14px; background:rgba(239,68,68,.12); color:#ffb4b4; border:1px solid rgba(239,68,68,.28); }
      [data-theme="light"] .foa-error { color:#b91c1c; background:#fff1f2; }
      .foa-kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:18px; margin-bottom:18px; }
      .foa-card,.foa-kpi { background:linear-gradient(145deg,var(--panel),var(--panel2)); border:1px solid var(--line); border-radius:18px; box-shadow:0 18px 50px rgba(0,0,0,.16); }
      [data-theme="light"] .foa-card,[data-theme="light"] .foa-kpi { box-shadow:0 14px 35px rgba(15,23,42,.07); }
      .foa-kpi { min-height:118px; padding:24px 18px; display:flex; gap:16px; align-items:center; }
      .foa-kpi-icon { width:52px; height:52px; border-radius:15px; display:grid; place-items:center; font-size:24px; color:#fff; background:rgba(47,115,255,.28); }
      .foa-kpi.profit .foa-kpi-icon { background:rgba(65,215,93,.2); color:var(--green); }
      .foa-kpi.bot .foa-kpi-icon { background:rgba(47,115,255,.22); }
      .foa-kpi span { display:block; color:var(--text); font-size:16px; margin-bottom:6px; }
      .foa-kpi strong { display:block; font-size:28px; line-height:1.05; color:var(--text); }
      .foa-kpi.profit strong,.foa-kpi.bot strong { color:var(--green); }
      .foa-kpi small { display:block; color:var(--muted); margin-top:8px; font-size:14px; }
      .dot { display:inline-block; width:9px; height:9px; margin-left:6px; border-radius:50%; background:var(--green); vertical-align:middle; }
      .foa-grid { display:grid; grid-template-columns:1.45fr .8fr 1.45fr; gap:18px; align-items:stretch; }
      .foa-card { padding:24px; }
      .foa-card h2 { margin:0; font-size:22px; letter-spacing:-.02em; }
      .foa-card-head { display:flex; align-items:center; justify-content:space-between; gap:14px; }
      .foa-mode-toggle { display:flex; gap:0; padding:4px; border-radius:10px; border:1px solid var(--line); background:rgba(255,255,255,.04); }
      .foa-mode-toggle button { border:0; border-radius:8px; padding:9px 24px; color:var(--text); background:transparent; cursor:pointer; font-size:15px; text-transform:capitalize; }
      .foa-mode-toggle .active { background:var(--blue); color:#fff; }
      .foa-account-card p { color:var(--muted); margin:28px 0 6px; font-size:16px; }
      .foa-balance { font-size:46px; font-weight:800; letter-spacing:-.04em; }
      .foa-account-stats { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; padding:22px 0; margin-top:18px; border-top:1px solid var(--line); }
      .foa-account-stats div { text-align:center; border-right:1px solid var(--line); }
      .foa-account-stats div:last-child { border-right:0; }
      .foa-account-stats span,.foa-status-line small,.foa-perf-stats span { display:block; color:var(--muted); font-size:14px; }
      .foa-account-stats strong { display:block; margin-top:8px; font-size:26px; }
      .win { color:var(--green) !important; } .loss { color:var(--red) !important; }
      .foa-actions-row { display:grid; grid-template-columns:1.2fr .8fr .8fr; gap:12px; }
      .foa-actions-row button { border:1px solid var(--line); min-height:54px; border-radius:12px; color:var(--text); font-weight:700; font-size:16px; cursor:pointer; }
      .foa-primary { background:var(--blue); color:#fff !important; border-color:transparent !important; }
      .foa-muted { background:rgba(255,255,255,.05); }
      .foa-danger { background:linear-gradient(135deg,#c23838,#ef4444); color:#fff !important; border-color:transparent !important; }
      .foa-auth-note { display:block; margin-top:14px; color:var(--muted); }
      .foa-status-line { display:grid; grid-template-columns:34px 1fr; gap:12px; align-items:center; padding:14px 0; border-bottom:1px solid var(--line); }
      .foa-status-line:last-child { border-bottom:0; }
      .foa-status-line > span { color:var(--muted); font-size:21px; }
      .foa-status-line strong { color:var(--blue); font-size:16px; }
      .foa-status-line.risk strong { color:var(--green); }
      .foa-small-select { border:1px solid var(--line); background:rgba(255,255,255,.04); color:var(--text); border-radius:10px; padding:9px 13px; }
      .foa-chart { width:100%; height:190px; margin-top:8px; }
      .foa-chart .grid line { stroke:var(--line); stroke-width:1; }
      .foa-perf-stats { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:10px; padding-top:16px; border-top:1px solid var(--line); text-align:center; }
      .foa-perf-stats strong { display:block; margin-top:6px; font-size:19px; }
      .foa-trades-card { margin-top:18px; }
      .foa-card-head a { color:var(--blue); text-decoration:none; font-weight:700; }
      .foa-trade-head,.foa-trade-row { display:grid; grid-template-columns:1.1fr 1.5fr 1fr 1fr; gap:12px; align-items:center; padding:12px 0; border-bottom:1px solid var(--line); }
      .foa-trade-head { color:var(--muted); font-size:14px; padding-top:20px; }
      .foa-trade-row { font-size:15px; }
      .foa-trade-row strong { text-align:right; }
      .foa-trade-name em { font-style:normal; font-size:12px; font-weight:800; padding:3px 7px; border-radius:6px; margin-left:8px; }
      .foa-trade-name em.buy { color:var(--green); background:rgba(65,215,93,.14); }
      .foa-trade-name em.sell { color:var(--red); background:rgba(239,68,68,.14); }
      .foa-empty { padding:22px 0; color:var(--muted); }
      .foa-bottom-nav { display:none; }
      @media (max-width: 1050px) { .foa-shell { grid-template-columns:1fr; } .foa-sidebar { display:none; } .foa-mobile-brand { display:flex; } .foa-topbar { grid-template-columns:1fr auto; } .foa-search { display:none; } .foa-grid { grid-template-columns:1fr 1fr; } .foa-account-card { grid-column:1 / -1; } }
      @media (max-width: 720px) { body.foa-simple-active { padding-bottom:86px; } #foa-simple-app { min-height:100vh; } .foa-main { padding:18px 14px 22px; } .foa-topbar { margin-bottom:18px; } .foa-mobile-brand { font-size:24px; } .foa-logo { width:45px; height:36px; } .foa-top-actions { gap:8px; } .foa-moon,.foa-sun { display:none; } .foa-kpis { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; } .foa-kpi { min-height:132px; padding:18px; align-items:flex-start; flex-direction:column; } .foa-kpi strong { font-size:27px; } .foa-grid { grid-template-columns:1fr; gap:14px; } .foa-card { padding:20px 18px; border-radius:18px; } .foa-card h2 { font-size:24px; } .foa-balance { font-size:46px; } .foa-mode-toggle button { padding:10px 22px; font-size:17px; } .foa-account-stats strong { font-size:28px; } .foa-actions-row { grid-template-columns:1fr 1fr 1fr; } .foa-actions-row button { min-height:64px; font-size:20px; } .foa-bot-card,.foa-performance-card { min-height:260px; } .foa-trade-head,.foa-trade-row { grid-template-columns:1fr 1.45fr .9fr .9fr; font-size:15px; } .foa-bottom-nav { position:fixed; left:0; right:0; bottom:0; z-index:2147483647; height:78px; display:grid; grid-template-columns:repeat(4,1fr); background:rgba(4,13,24,.92); backdrop-filter:blur(18px); border-top:1px solid var(--line); } [data-theme="light"] ~ .foa-bottom-nav, body:has(#foa-simple-app[data-theme="light"]) .foa-bottom-nav { background:rgba(255,255,255,.92); } .foa-bottom-nav button { border:0; background:transparent; color:var(--muted); display:grid; place-items:center; gap:2px; font-size:24px; } .foa-bottom-nav button span { font-size:13px; } .foa-bottom-nav .active { color:var(--blue); } }
      @media (max-width: 420px) { .foa-main { padding:14px 10px 18px; } .foa-mobile-brand { font-size:20px; gap:10px; } .foa-kpis { gap:10px; } .foa-kpi { padding:14px; min-height:122px; } .foa-kpi-icon { width:44px; height:44px; } .foa-kpi span { font-size:14px; } .foa-kpi strong { font-size:22px; } .foa-kpi small { font-size:12px; } .foa-card { padding:18px 14px; } .foa-balance { font-size:40px; } .foa-actions-row { gap:8px; } .foa-actions-row button { min-height:58px; font-size:17px; } .foa-trade-head,.foa-trade-row { grid-template-columns:.95fr 1.3fr .8fr .9fr; gap:8px; font-size:13px; } .foa-trade-name em { display:inline-block; margin-left:0; margin-top:4px; } }
    `;
    document.head.appendChild(style);
  }

  function boot() {
    if (document.getElementById("foa-simple-app")) return;
    installStyles();
    document.body.classList.add("foa-simple-active");
    const app = document.createElement("div");
    app.id = "foa-simple-app";
    app.dataset.theme = state.theme;
    document.body.appendChild(app);
    render();
    refresh();
    setInterval(refresh, REFRESH_MS);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
