(() => {
  "use strict";

  if (window.__DERIVADMIN_FINAL_UI_SHELL_V1__) return;
  window.__DERIVADMIN_FINAL_UI_SHELL_V1__ = true;

  const VERSION = "20260817-6f1-1";
  const REGISTER_URL = "https://t.deriv.link?t=CZXDLJPXM38M";
  const ROUTES = new Set(["home", "builder", "ai", "schedule", "profile", "trades"]);
  const state = {
    me: null,
    trades: null,
    lifecycle: null,
    schedules: null,
    premium: null,
    route: "home",
    loading: true,
    busy: false,
    error: "",
    notice: "",
  };

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const esc = (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

  function icon(name) {
    const common = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    const icons = {
      home: `<svg ${common}><path d="m3 11 9-8 9 8"/><path d="M5 10v11h14V10"/><path d="M9 21v-7h6v7"/></svg>`,
      cube: `<svg ${common}><path d="m12 3 4 2.2v4.6L12 12l-4-2.2V5.2z"/><path d="M7 12l4 2.2v4.6L7 21l-4-2.2v-4.6z"/><path d="M17 12l4 2.2v4.6L17 21l-4-2.2v-4.6z"/></svg>`,
      ai: `<svg ${common}><path d="M5 17a4 4 0 0 1-2-3.5V8a4 4 0 0 1 4-4h7a4 4 0 0 1 4 4v5.5a4 4 0 0 1-4 4H9l-4 3z"/><path d="M8 13l2-5 2 5M8.8 11h2.4M15 8v5"/><path d="M21 4v4M19 6h4"/></svg>`,
      calendar: `<svg ${common}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18"/><circle cx="17" cy="17" r="3"/><path d="M17 15.5V17l1 1"/></svg>`,
      profile: `<svg ${common}><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>`,
      bell: `<svg ${common}><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></svg>`,
      wallet: `<svg ${common}><path d="M3 7h16a2 2 0 0 1 2 2v9H3z"/><path d="M3 7l12-3v3"/><path d="M16 12h5"/></svg>`,
      pulse: `<svg ${common}><path d="M2 12h4l2-6 4 12 3-8 2 2h5"/></svg>`,
      chart: `<svg ${common}><path d="M4 19V9M9 19V5M14 19v-7M19 19V3"/><path d="m3 8 5-3 5 2 7-5"/></svg>`,
      trophy: `<svg ${common}><path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H4v2a4 4 0 0 0 4 4M16 6h4v2a4 4 0 0 1-4 4"/><path d="M12 13v5M8 21h8"/></svg>`,
      shield: `<svg ${common}><path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6z"/><path d="M9 12h6"/></svg>`,
      chevron: `<svg ${common}><path d="m9 6 6 6-6 6"/></svg>`,
      clock: `<svg ${common}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
      logout: `<svg ${common}><path d="M10 17l5-5-5-5"/><path d="M15 12H3"/><path d="M13 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6"/></svg>`,
      back: `<svg ${common}><path d="m15 18-6-6 6-6"/></svg>`,
      trades: `<svg ${common}><path d="M4 19V9M10 19V5M16 19v-7M22 19V3"/></svg>`,
    };
    return icons[name] || icons.home;
  }

  function logo() {
    return `<svg viewBox="0 0 64 64" aria-hidden="true"><defs><linearGradient id="daLogoGradient" x1="4" y1="4" x2="58" y2="58"><stop stop-color="#2864ff"/><stop offset=".55" stop-color="#138dff"/><stop offset="1" stop-color="#14dfff"/></linearGradient></defs><path fill="url(#daLogoGradient)" d="M11 10h22c13.8 0 25 10.3 25 23S46.8 56 33 56H11l13-13h9c6.4 0 11.5-4.5 11.5-10S39.4 23 33 23H24L11 36z"/><path fill="url(#daLogoGradient)" d="M11 10v26l13-13V10z"/></svg>`;
  }

  function routeFromHash() {
    const value = String(location.hash || "").replace(/^#\/?/, "").split(/[?&]/, 1)[0].trim().toLowerCase();
    return ROUTES.has(value) ? value : "home";
  }

  function navigate(route) {
    const next = ROUTES.has(route) ? route : "home";
    state.route = next;
    if (location.hash !== `#/${next}`) history.pushState({ route: next }, "", `#/${next}`);
    render();
  }

  function money(value, currency = "USD") {
    const amount = Number(value || 0);
    const prefix = String(currency || "USD").toUpperCase() === "USD" ? "$" : `${currency} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function metrics() {
    const summary = state.trades?.summary || {};
    const stats = state.me?.stats || {};
    return {
      runs: Number(summary.total ?? stats.trades ?? 0),
      wins: Number(summary.wins ?? stats.wins ?? 0),
      losses: Number(summary.losses ?? stats.losses ?? 0),
      profit: Number(summary.profit ?? stats.profit ?? 0),
    };
  }

  function greeting() {
    const hour = new Date().getHours();
    const prefix = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    const storedName = (() => {
      try { return String(localStorage.getItem("foa-profile-display-name-v1") || "").trim(); } catch (_) { return ""; }
    })();
    return `${prefix}, ${storedName || "Duke"}`;
  }

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), options.timeout || 12000);
    try {
      const response = await fetch(path, {
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          Accept: "application/json",
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...(options.headers || {}),
        },
        ...options,
        signal: options.signal || controller.signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof payload.detail === "string"
          ? payload.detail
          : payload.detail?.message || payload.message || `Request returned ${response.status}`;
        const error = new Error(detail);
        error.status = response.status;
        error.payload = payload;
        throw error;
      }
      return payload;
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function loadAuthenticatedData() {
    const results = await Promise.allSettled([
      api("/me/trades/today?limit=100"),
      api("/me/trading-lifecycle"),
      api("/me/automation-schedules?limit=20"),
      api("/me/premium-access"),
    ]);
    if (results[0].status === "fulfilled") state.trades = results[0].value;
    if (results[1].status === "fulfilled") state.lifecycle = results[1].value;
    if (results[2].status === "fulfilled") state.schedules = results[2].value;
    if (results[3].status === "fulfilled") state.premium = results[3].value;
  }

  async function refresh({ quiet = false } = {}) {
    if (!quiet) state.loading = true;
    try {
      const me = await api("/me");
      state.me = me;
      window.FOA_BOOT_SESSION = me;
      if (me?.authenticated) {
        await loadAuthenticatedData();
      } else {
        state.trades = null;
        state.lifecycle = null;
        state.schedules = null;
        state.premium = null;
      }
      state.error = "";
    } catch (error) {
      if (!quiet) state.error = String(error?.message || error);
    } finally {
      state.loading = false;
      render();
    }
  }

  function syncRealtimeCache() {
    const cache = window.FOA_NETLIFY_LIVE_CACHE;
    if (!cache?.me?.authenticated || !state.me?.authenticated) return;
    if (String(cache.me.account_id_masked || cache.me.account_id || "") !== String(state.me.account_id_masked || state.me.account_id || "")) return;
    state.me = { ...state.me, ...cache.me };
    if (cache.trades) state.trades = cache.trades;
    if (cache.lifecycle) state.lifecycle = cache.lifecycle;
    if (state.route === "home" || state.route === "trades") render();
  }

  function statCard(kind, label, value, tone = "") {
    return `<article class="da-stat ${tone}"><span>${icon(kind)}${esc(label)}</span><strong>${esc(value)}</strong></article>`;
  }

  function miniChart() {
    return `<svg class="da-greeting-chart" viewBox="0 0 220 100" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="daChartLine" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#00dcff"/><stop offset="1" stop-color="#2e7cff"/></linearGradient><filter id="daGlow"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs><path d="M4 91 31 75 53 79 79 49 105 61 132 27 156 40 181 12 215 24" fill="none" stroke="url(#daChartLine)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" filter="url(#daGlow)"/><path d="M20 95v-10M46 95V83M72 95V76M98 95V68M124 95V59M150 95V47M176 95V35M202 95V24" stroke="#1a8dff" stroke-width="10" stroke-linecap="round" opacity=".38"/></svg>`;
  }

  function featureCard(kind, title, copy, cta, route, tone) {
    return `<button class="da-feature ${tone}" type="button" data-route="${route}">
      <span class="da-feature-icon">${icon(kind)}</span>
      <span class="da-feature-copy"><strong>${esc(title)}</strong><small>${esc(copy)}</small></span>
      <span class="da-feature-cta">${esc(cta)}</span>
      <span class="da-feature-chevron">${icon("chevron")}</span>
    </button>`;
  }

  function nextSchedule() {
    const active = state.schedules?.active;
    if (active) return active;
    const upcoming = Array.isArray(state.schedules?.upcoming) ? [...state.schedules.upcoming] : [];
    return upcoming.sort((a, b) => String(a.scheduled_for_utc || "").localeCompare(String(b.scheduled_for_utc || "")))[0] || null;
  }

  function scheduleTime(item) {
    if (!item) return "Not scheduled yet";
    const raw = item.scheduled_for_utc || item.date_time_local || "";
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return String(raw).replace("T", " ");
    try {
      return new Intl.DateTimeFormat(undefined, {
        weekday: "short",
        hour: "numeric",
        minute: "2-digit",
        timeZone: item.timezone || "Africa/Nairobi",
      }).format(date);
    } catch (_) {
      return date.toLocaleString();
    }
  }

  function homeMarkup() {
    const m = metrics();
    const currency = state.me?.currency || "USD";
    const profitTone = m.profit > 0 ? "positive" : m.profit < 0 ? "negative" : "";
    const schedule = nextSchedule();
    const status = schedule ? String(schedule.status || "scheduled") : "ready";
    const builderDraft = (() => {
      try { return JSON.parse(localStorage.getItem("foa-builder-draft-v2") || "null"); } catch (_) { return null; }
    })();
    const stake = schedule?.stake ?? builderDraft?.money?.stake ?? 0.5;
    const tp = schedule?.take_profit ?? builderDraft?.money?.takeProfit ?? 0;
    const sl = schedule?.stop_loss ?? builderDraft?.money?.stopLoss ?? 0;
    return `<main class="da-page da-home" data-page="home">
      <section class="da-stats" aria-label="Account statistics">
        ${statCard("wallet", "Balance", money(state.me?.balance || 0, currency))}
        ${statCard("pulse", "Runs", m.runs.toLocaleString())}
        ${statCard("chart", "P/L", money(m.profit, currency), profitTone)}
        ${statCard("trophy", "Wins", m.wins.toLocaleString(), "positive")}
        ${statCard("shield", "Losses", m.losses.toLocaleString(), "negative")}
      </section>

      <section class="da-greeting-card">
        <span class="da-avatar">${icon("profile")}</span>
        <div class="da-greeting-copy"><strong>${esc(greeting())}</strong><span>Ready to automate and grow your edge today?</span></div>
        ${miniChart()}
      </section>

      <section class="da-feature-stack">
        ${featureCard("cube", "Strategy Builder", "Build with advanced blocks and conditions.", "Open Builder", "builder", "blue")}
        ${featureCard("ai", "Text to Strategy", "Describe your idea in plain English. We build it for you.", "Create with AI", "ai", "cyan")}
        ${featureCard("calendar", "Schedule Trading", "Pick a strategy, date, time, stake, TP and SL.", "Schedule Session", "schedule", "purple")}
      </section>

      <section class="da-section">
        <div class="da-section-head"><h2>My Automation</h2><button type="button" data-route="schedule">View all ${icon("chevron")}</button></div>
        <article class="da-automation-card">
          <div class="da-automation-main">
            <span class="da-round-icon">${icon("calendar")}</span>
            <div><small>Next session: <b>${esc(scheduleTime(schedule))}</b></small><strong>${esc(schedule?.strategy_name || "Risk Managers")}</strong></div>
            <span class="da-status ${esc(status)}"><i></i>${esc(status === "ready" ? "Ready" : status[0].toUpperCase() + status.slice(1))}</span>
          </div>
          <div class="da-automation-chips">
            <span>◉ ${Number(stake).toFixed(2)} stake</span>
            <span>TP <b class="green">${money(tp, currency)}</b></span>
            <span>SL <b class="red">${money(sl, currency)}</b></span>
            <span>◎ Timezone: <b>${esc((schedule?.timezone || "Africa/Nairobi").includes("Nairobi") ? "EAT" : schedule?.timezone || "EAT")}</b></span>
          </div>
        </article>
      </section>

      <section class="da-section da-library">
        <div class="da-section-head"><h2>Strategy Library</h2><button type="button" data-route="builder">Explore library ${icon("chevron")}</button></div>
        <div class="da-library-tabs">
          <button type="button" class="active">☆ Built-in</button>
          <button type="button">♙ My Strategies</button>
          <button type="button">✧ AI Generated</button>
        </div>
        <div class="da-library-grid">
          <article><strong>Breakout Pro</strong><small>Fast momentum automation</small><svg viewBox="0 0 120 35" preserveAspectRatio="none" aria-hidden="true"><path d="M0 30 14 25 25 27 39 16 52 20 64 11 76 18 91 7 103 13 120 4" fill="none" stroke="#26e88b" stroke-width="2"/></svg></article>
          <article><strong>Mean Reverter</strong><small>Measured reversal entries</small><svg viewBox="0 0 120 35" preserveAspectRatio="none" aria-hidden="true"><path d="M0 29 16 22 29 25 42 14 55 20 68 11 83 16 97 8 109 13 120 4" fill="none" stroke="#a55cff" stroke-width="2"/></svg></article>
        </div>
      </section>
    </main>`;
  }

  function pageHeader(title, subtitle, back = true) {
    return `<div class="da-page-header">
      ${back ? `<button type="button" class="da-back" data-route="home" aria-label="Back">${icon("back")}</button>` : ""}
      <div><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div>
    </div>`;
  }

  function profileAccountSwitchMarkup() {
    const available = Array.isArray(state.me?.available_account_types) ? state.me.available_account_types : ["demo", "real"];
    const current = String(state.me?.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
    return `<div class="da-mode-switch inline" role="group" aria-label="Trading account">
      ${["demo", "real"].map((mode) => `<button type="button" data-account-mode="${mode}" class="${current === mode ? "active" : ""}" ${available.includes(mode) ? "" : "disabled"}>${mode === "demo" ? "Demo" : "Real"}</button>`).join("")}
    </div>`;
  }

  function profileMarkup() {
    const premiumStatus = state.premium?.active ? "Premium Active" : "Premium required for trading";
    return `<main class="da-page da-workspace">
      ${pageHeader("Profile", "Account & platform access")}
      <section class="da-shell-card">
        <span class="da-profile-large">${icon("profile")}</span>
        <h2>${esc(state.me?.account_id_masked || state.me?.account_id || "Deriv account")}</h2>
        <p>${esc(premiumStatus)}</p>
        ${profileAccountSwitchMarkup()}
        <button type="button" class="da-outline-button" data-logout>${icon("logout")} Log out</button>
      </section>
    </main>`;
  }

  function tradesMarkup() {
    const rows = Array.isArray(state.trades?.trades) ? state.trades.trades.slice(0, 20) : [];
    return `<main class="da-page da-workspace">
      ${pageHeader("Trades", "Live execution activity")}
      <section class="da-shell-card">
        <div class="da-workspace-title"><span>${icon("trades")}</span><div><h2>Recent activity</h2><p>${esc(state.lifecycle?.reason || "Execution activity from the selected account.")}</p></div></div>
        <div class="da-trade-list">
          ${rows.length ? rows.map((row) => {
            const outcome = String(row.outcome || "OPEN").toUpperCase();
            return `<article><div><strong>${esc(row.symbol || row.market || "-")}</strong><small>${esc(row.contract_type || row.type || "-")}</small></div><span>${money(row.buy_price ?? row.stake ?? row.amount ?? 0, state.me?.currency || "USD")}</span><b class="${outcome === "WIN" ? "green" : outcome === "LOSS" ? "red" : ""}">${esc(outcome)}</b></article>`;
          }).join("") : `<div class="da-empty">No recent trades yet.</div>`}
        </div>
      </section>
    </main>`;
  }

  function scaffoldMarkup(route) {
    const map = {
      builder: ["Strategy Builder", "Advanced automation lab", "The exact Builder screen from the approved design is reconstructed in 6F-2.", "cube"],
      ai: ["Text to Strategy", "Describe what you want to trade", "The exact AI workspace from the approved design is reconstructed in 6F-2.", "ai"],
      schedule: ["Schedule Trading", "Automate a future trading session", "The exact Schedule screen from the approved design is reconstructed in 6F-2.", "calendar"],
    };
    const [title, subtitle, copy, kind] = map[route] || map.builder;
    return `<main class="da-page da-workspace">
      ${pageHeader(title, subtitle)}
      <section class="da-shell-card">
        <div class="da-workspace-title"><span>${icon(kind)}</span><div><h2>New DerivAdmin UI</h2><p>${esc(copy)}</p></div></div>
        ${route === "builder" ? profileAccountSwitchMarkup() : ""}
      </section>
    </main>`;
  }

  function brandHeader() {
    return `<header class="da-topbar">
      <div class="da-brand"><span class="da-logo">${logo()}</span><div><strong>DerivAdmin</strong><small>Home of Automation</small></div></div>
      <button type="button" class="da-bell" aria-label="Notifications">${icon("bell")}<i></i></button>
    </header>`;
  }

  function bottomNav() {
    const items = [
      ["home", "home", "Home"],
      ["builder", "cube", "Builder"],
      ["ai", "ai", "AI"],
      ["schedule", "calendar", "Schedule"],
      ["profile", "profile", "Profile"],
    ];
    return `<nav class="da-bottom-nav" aria-label="Primary navigation">
      ${items.map(([route, kind, label]) => `<button type="button" data-route="${route}" class="${state.route === route ? "active" : ""}">${icon(kind)}<span>${label}</span></button>`).join("")}
    </nav>`;
  }

  function authenticatedMarkup() {
    const page = state.route === "home"
      ? homeMarkup()
      : state.route === "profile"
      ? profileMarkup()
      : state.route === "trades"
      ? tradesMarkup()
      : scaffoldMarkup(state.route);
    return `<div class="da-app" data-ui-version="${VERSION}" data-route="${esc(state.route)}">
      <div class="da-glow da-glow-one"></div><div class="da-glow da-glow-two"></div>
      <div class="da-shell">${brandHeader()}${state.error ? `<div class="da-inline-notice error">${esc(state.error)}</div>` : ""}${state.notice ? `<div class="da-inline-notice success">${esc(state.notice)}</div>` : ""}${page}</div>
      ${bottomNav()}
    </div>`;
  }

  function publicMarkup() {
    const oauthError = (() => {
      try {
        const url = new URL(location.href);
        return url.searchParams.get("oauth_error") || sessionStorage.getItem("foa-oauth-error-v1") || "";
      } catch (_) { return ""; }
    })();
    return `<div class="da-public" data-ui-version="${VERSION}">
      <div class="da-glow da-glow-one"></div><div class="da-glow da-glow-two"></div>
      <section class="da-public-card">
        <span class="da-public-logo">${logo()}</span>
        <h1>DerivAdmin</h1>
        <p>Home of Automation</p>
        <strong>Build it. Describe it. Schedule it.</strong>
        <small>Log in with Deriv to connect your Options accounts. Authentication and premium trading access are separate.</small>
        ${oauthError ? `<div class="da-inline-notice error">${esc(oauthError)}</div>` : ""}
        <a class="da-primary-button" href="/oauth/start">Login with Deriv</a>
        <a class="da-outline-button link" href="${REGISTER_URL}" target="_blank" rel="noopener noreferrer">Register</a>
      </section>
    </div>`;
  }

  function render() {
    const root = q("#derivadmin-root");
    if (!root) return;
    document.body.dataset.derivadminUi = "6f1";
    if (state.loading && !state.me) {
      root.innerHTML = `<div class="da-loading"><span>${logo()}</span><i></i><strong>DerivAdmin</strong><small>Opening Home of Automation…</small></div>`;
      return;
    }
    root.innerHTML = state.me?.authenticated ? authenticatedMarkup() : publicMarkup();
  }

  async function switchAccount(mode) {
    if (state.busy) return;
    state.busy = true;
    const previous = state.me?.account_type;
    state.notice = "";
    state.error = "";
    if (state.me) state.me.account_type = mode;
    render();
    try {
      await api("/me/switch-account", {
        method: "POST",
        body: JSON.stringify({ account_type: mode }),
      });
      state.notice = `Switched to ${mode === "real" ? "Real" : "Demo"} account.`;
      await refresh({ quiet: true });
    } catch (error) {
      if (state.me) state.me.account_type = previous;
      state.error = String(error?.message || error);
      render();
    } finally {
      state.busy = false;
    }
  }

  async function logout() {
    if (state.busy) return;
    state.busy = true;
    try {
      await api("/me/logout", { method: "POST", body: JSON.stringify({}) });
    } catch (_) {}
    try {
      localStorage.removeItem("foa-session-v2");
      localStorage.removeItem("foa-builder-last-good-snapshot-v1");
    } catch (_) {}
    state.me = { authenticated: false };
    state.trades = null;
    state.lifecycle = null;
    state.schedules = null;
    state.premium = null;
    state.busy = false;
    history.replaceState({ route: "home" }, "", "#/home");
    state.route = "home";
    render();
  }

  document.addEventListener("click", (event) => {
    const routeButton = event.target?.closest?.("[data-route]");
    if (routeButton) {
      event.preventDefault();
      navigate(String(routeButton.dataset.route || "home"));
      return;
    }
    const modeButton = event.target?.closest?.("[data-account-mode]");
    if (modeButton) {
      event.preventDefault();
      switchAccount(String(modeButton.dataset.accountMode || "demo"));
      return;
    }
    if (event.target?.closest?.("[data-logout]")) {
      event.preventDefault();
      logout();
    }
  });

  window.addEventListener("hashchange", () => {
    state.route = routeFromHash();
    render();
  });
  window.addEventListener("popstate", () => {
    state.route = routeFromHash();
    render();
  });
  window.addEventListener("pageshow", () => refresh({ quiet: true }));
  window.addEventListener("focus", () => refresh({ quiet: true }));

  state.route = routeFromHash();
  render();
  refresh();
  window.setInterval(syncRealtimeCache, 2000);
  window.setInterval(() => {
    if (!document.hidden) refresh({ quiet: true });
  }, 30000);

  window.DERIVADMIN_UI = {
    version: VERSION,
    navigate,
    refresh,
    switchAccount,
    state,
  };
})();