(() => {
  "use strict";

  if (window.__FOA_AUTOMATION_HOME_ACTION1__) return;
  window.__FOA_AUTOMATION_HOME_ACTION1__ = true;

  const VERSION = "20260817-action1-2";
  const ROUTE_KEY = "foa-automation-route-session-v1";
  const ROUTE_ACCOUNT_KEY = "foa-automation-route-account-v1";
  const BUILDER_KEY = "foa-builder-draft-v2";
  const USER_TEMPLATE_KEY = "foa-user-strategy-templates-v1";
  const VALID_ROUTES = new Set(["home", "builder", "ai", "schedule", "profile", "trades"]);

  let scheduled = false;
  let syncingLegacy = false;
  let libraryTab = "built-in";
  let lastSnapshot = { balance: "$0.00", runs: "0", profit: "$0.00", wins: "0", losses: "0" };

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const sessionGet = (key) => { try { return sessionStorage.getItem(key); } catch (_) { return null; } };
  const sessionSet = (key, value) => { try { sessionStorage.setItem(key, value); } catch (_) {} };
  const localGet = (key) => { try { return localStorage.getItem(key); } catch (_) { return null; } };

  function currentMe() {
    const live = window.FOA_NETLIFY_LIVE_CACHE?.me;
    if (live && typeof live === "object") return live;
    const boot = window.FOA_BOOT_SESSION;
    return boot && typeof boot === "object" ? boot : null;
  }

  function isAuthenticated() {
    return Boolean(currentMe()?.authenticated || q(".builder-header #logout") || q("#foa-simple-app .account-pill"));
  }

  function accountIdentity() {
    const me = currentMe();
    return String(
      me?.managed_account_id
      || me?.account_id_full
      || me?.account_id_masked
      || me?.account_id
      || q(".builder-header .account-pill")?.textContent
      || "authenticated",
    ).trim();
  }

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function svg(name) {
    const c = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    const i = {
      logo: `<svg ${c}><path d="M5 4h6.5a7.5 7.5 0 0 1 0 15H5l5-5h1.5a2.5 2.5 0 0 0 0-5H10z"/><path d="M5 4v15"/></svg>`,
      bell: `<svg ${c}><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></svg>`,
      wallet: `<svg ${c}><path d="M3 7h16a2 2 0 0 1 2 2v9H3z"/><path d="M3 7l12-3v3"/><path d="M16 12h5"/></svg>`,
      pulse: `<svg ${c}><path d="M2 12h4l2-6 4 12 3-8 2 2h5"/></svg>`,
      chart: `<svg ${c}><path d="M4 19V9M9 19V5M14 19v-7M19 19V3"/><path d="m3 8 5-3 5 2 7-5"/></svg>`,
      trophy: `<svg ${c}><path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H4v2a4 4 0 0 0 4 4M16 6h4v2a4 4 0 0 1-4 4M12 13v5M8 21h8"/></svg>`,
      shield: `<svg ${c}><path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6z"/><path d="M9 12h6"/></svg>`,
      user: `<svg ${c}><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>`,
      cubes: `<svg ${c}><path d="m12 3 4 2.2v4.6L12 12l-4-2.2V5.2zM7 12l4 2.2v4.6L7 21l-4-2.2v-4.6zM17 12l4 2.2v4.6L17 21l-4-2.2v-4.6zM12 12V7.2"/></svg>`,
      ai: `<svg ${c}><path d="M5 17a4 4 0 0 1-2-3.5V8a4 4 0 0 1 4-4h7a4 4 0 0 1 4 4v5.5a4 4 0 0 1-4 4H9l-4 3zM8 13l2-5 2 5M8.8 11h2.4M15 8v5M21 4v4M19 6h4"/></svg>`,
      calendar: `<svg ${c}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18"/><circle cx="17" cy="17" r="3"/><path d="M17 15.5V17l1 1"/></svg>`,
      home: `<svg ${c}><path d="m3 11 9-8 9 8M5 10v11h14V10M9 21v-7h6v7"/></svg>`,
      profile: `<svg ${c}><circle cx="12" cy="12" r="9"/><circle cx="12" cy="9" r="3"/><path d="M6.5 19a6 6 0 0 1 11 0"/></svg>`,
      back: `<svg ${c}><path d="m15 18-6-6 6-6"/></svg>`,
      star: `<svg ${c}><path d="m12 3 1.4 4.2L18 9l-4.6 1.8L12 15l-1.4-4.2L6 9l4.6-1.8zM19 3v4M17 5h4"/></svg>`,
    };
    return i[name] || i.home;
  }

  function captureLegacySnapshot() {
    const main = q("#telegram-dashboard-snapshot > main");
    if (!main || main.querySelector(".foa-automation-page")) return;
    const values = {};
    qa(".builder-stat", main).forEach((card) => {
      const label = String(card.querySelector("span")?.textContent || "").trim().toLowerCase();
      const value = String(card.querySelector("strong")?.textContent || "").trim();
      if (label && value) values[label] = value;
    });
    lastSnapshot = {
      balance: values.balance || lastSnapshot.balance,
      runs: values["number of runs"] || values.runs || lastSnapshot.runs,
      profit: values["today's p/l"] || values["p/l"] || lastSnapshot.profit,
      wins: values.wins || lastSnapshot.wins,
      losses: values.losses || lastSnapshot.losses,
    };
  }

  function readBuilderMoney() {
    try {
      const draft = JSON.parse(localGet(BUILDER_KEY) || "null");
      return {
        stake: Number(draft?.money?.stake ?? 0.5),
        takeProfit: Number(draft?.money?.takeProfit ?? 0),
        stopLoss: Number(draft?.money?.stopLoss ?? 0),
      };
    } catch (_) {
      return { stake: 0.5, takeProfit: 0, stopLoss: 0 };
    }
  }

  function readUserTemplates() {
    try {
      const parsed = JSON.parse(localGet(USER_TEMPLATE_KEY) || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) { return []; }
  }

  function money(value) {
    return `$${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function greeting() {
    const hour = new Date().getHours();
    const prefix = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    return `${prefix}, ${String(localGet("foa-profile-display-name-v1") || "Trader").trim() || "Trader"}`;
  }

  function accountLabel() {
    const me = currentMe();
    return String(me?.account_id_masked || me?.account_id || q(".account-pill")?.textContent || "Trader").trim();
  }

  function routeFromHash() {
    const raw = String(location.hash || "").replace(/^#\/?/, "").split(/[?&]/)[0].trim();
    return VALID_ROUTES.has(raw) ? raw : "";
  }

  function currentRoute() {
    const account = accountIdentity();
    if (sessionGet(ROUTE_ACCOUNT_KEY) !== account) {
      sessionSet(ROUTE_ACCOUNT_KEY, account);
      sessionSet(ROUTE_KEY, "home");
      return "home";
    }
    const hashRoute = routeFromHash();
    if (hashRoute) return hashRoute;
    const stored = sessionGet(ROUTE_KEY);
    return VALID_ROUTES.has(stored) ? stored : "home";
  }

  function setBodyRoute(route) {
    document.body.classList.add("foa-automation-shell-active");
    VALID_ROUTES.forEach((name) => document.body.classList.remove(`foa-automation-route-${name}`));
    document.body.classList.add(`foa-automation-route-${route}`);
    document.body.dataset.automationRoute = route;
  }

  function syncLegacyView(route) {
    const desired = route === "trades" ? "trades" : "main";
    const button = q(`.builder-header [data-view="${desired}"]`) || q(`[data-view="${desired}"]`);
    if (!button) return;
    const mainHasAutomation = Boolean(q("#telegram-dashboard-snapshot > main .foa-automation-page"));
    if (button.classList.contains("active") && !(desired === "main" && mainHasAutomation)) return;
    syncingLegacy = true;
    try { button.click(); } finally { setTimeout(() => { syncingLegacy = false; }, 0); }
  }

  function navigate(route, { replace = false } = {}) {
    const next = VALID_ROUTES.has(route) ? route : "home";
    sessionSet(ROUTE_KEY, next);
    const hash = `#/${next}`;
    if (location.hash !== hash) {
      const fn = replace ? history.replaceState.bind(history) : history.pushState.bind(history);
      fn({ automationRoute: next }, "", hash);
    }
    setBodyRoute(next);
    syncLegacyView(next);
    scheduleRender();
  }

  function stat(icon, label, value, tone = "") {
    return `<article class="foa-automation-stat" data-tone="${esc(tone)}"><span class="foa-automation-stat-label">${svg(icon)}${esc(label)}</span><strong>${esc(value)}</strong></article>`;
  }

  function feature(featureName, icon, title, description, cta, route) {
    return `<button type="button" class="foa-automation-feature" data-feature="${featureName}" data-automation-route="${route}">
      <span class="foa-automation-feature-icon">${svg(icon)}</span>
      <span class="foa-automation-feature-copy"><strong>${esc(title)}</strong><span>${esc(description)}</span></span>
      <span class="foa-automation-feature-cta">${esc(cta)}</span><i class="foa-automation-chevron" aria-hidden="true"></i>
    </button>`;
  }

  function templateCard(item, index) {
    const path = index % 2 ? "M2 18 12 13 22 16 34 7 44 12 55 6 68 9" : "M2 20 13 15 23 18 34 9 46 12 57 5 68 8";
    return `<article class="foa-automation-template"><div class="foa-automation-template-top"><span class="foa-automation-template-icon">${svg(index % 2 ? "chart" : "shield")}</span><svg class="foa-automation-template-line" viewBox="0 0 70 24" fill="none" aria-hidden="true"><path d="${path}" stroke="${item.tone}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></div><strong>${esc(item.name)}</strong><small>${esc(item.subtitle)}</small></article>`;
  }

  function libraryContent() {
    if (libraryTab === "my") {
      const user = readUserTemplates();
      if (!user.length) return `<div class="foa-automation-empty-library">Your saved strategies will appear here.</div>`;
      return user.slice(0, 4).map((item, index) => templateCard({ name: item.name || item.label || `My Strategy ${index + 1}`, subtitle: "Saved strategy", tone: index % 2 ? "#a864ff" : "#25df8c" }, index)).join("");
    }
    if (libraryTab === "ai") return `<div class="foa-automation-empty-library">AI-generated strategies will appear here after Text to Strategy is activated.</div>`;
    return [
      { name: "Over 1 Recovery Over 4 Golden Bot", subtitle: "Percentage · Over · Multiplier", tone: "#25df8c" },
      { name: "Over 3 Spread Recovery x2", subtitle: "Last Digit · Over · Split Recovery", tone: "#9b62ff" },
      { name: "Over 2 Combined Alignment", subtitle: "Combined · Over · Alignment", tone: "#2fa8ff" },
    ].map(templateCard).join("");
  }

  function miniChart() {
    return `<div class="foa-automation-mini-chart" aria-hidden="true"><svg viewBox="0 0 180 82" fill="none" preserveAspectRatio="none"><defs><linearGradient id="foaHomeChart" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#1fcfff"/><stop offset="1" stop-color="#2f71ff"/></linearGradient></defs><path d="M3 71 26 57 44 61 64 42 86 50 108 30 128 36 150 15 177 7" stroke="url(#foaHomeChart)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 80V70M28 80V62M48 80V66M68 80V52M88 80V58M108 80V43M128 80V48M148 80V31M168 80V22" stroke="#197dff" stroke-width="6" stroke-linecap="round" opacity=".38"/></svg></div>`;
  }

  function homeMarkup() {
    const builderMoney = readBuilderMoney();
    const profitTone = String(lastSnapshot.profit).trim().startsWith("-") ? "loss" : (lastSnapshot.profit === "$0.00" ? "" : "win");
    return `<section class="foa-automation-page foa-automation-home" data-automation-home-version="${VERSION}">
      <div class="foa-automation-topbar"><div class="foa-automation-brand"><span class="foa-automation-logo">${svg("logo")}</span><span class="foa-automation-brand-copy"><strong>DerivAdmin</strong><span>Home of Automation</span></span></div><button type="button" class="foa-automation-bell" aria-label="Notifications">${svg("bell")}</button></div>
      <section class="foa-automation-stats" aria-label="Account statistics">${stat("wallet", "Balance", lastSnapshot.balance)}${stat("pulse", "Runs", lastSnapshot.runs)}${stat("chart", "P/L", lastSnapshot.profit, profitTone)}${stat("trophy", "Wins", lastSnapshot.wins, "win")}${stat("shield", "Losses", lastSnapshot.losses, "loss")}</section>
      <section class="foa-automation-greeting"><span class="foa-automation-avatar">${svg("user")}</span><div class="foa-automation-greeting-copy"><strong>${esc(greeting())}</strong><span>Ready to automate and grow your edge today?</span></div>${miniChart()}</section>
      <section class="foa-automation-features" aria-label="Automation tools">
        ${feature("builder", "cubes", "Strategy Builder", "Build with advanced blocks and conditions.", "Open Builder", "builder")}
        ${feature("ai", "ai", "Text to Strategy", "Describe your idea in plain English. We build it for you.", "Create with AI", "ai")}
        ${feature("schedule", "calendar", "Schedule Trading", "Pick a strategy, date, time, stake, TP and SL.", "Schedule Session", "schedule")}
      </section>
      <section class="foa-automation-section"><div class="foa-automation-section-head"><h2>My Automation</h2><button type="button" data-automation-route="schedule">View all ›</button></div><div class="foa-automation-next"><span class="foa-automation-next-icon">${svg("calendar")}</span><div class="foa-automation-next-copy"><span>Next session: <b>Not scheduled yet</b></span><strong>Current Strategy</strong></div><span class="foa-automation-status">Ready</span><div class="foa-automation-chips"><span class="foa-automation-chip">Stake <b>${money(builderMoney.stake)}</b></span><span class="foa-automation-chip tp">TP <b>${money(builderMoney.takeProfit)}</b></span><span class="foa-automation-chip sl">SL <b>${money(builderMoney.stopLoss)}</b></span><span class="foa-automation-chip">Timezone <b>EAT</b></span></div></div></section>
      <section class="foa-automation-section"><div class="foa-automation-section-head"><h2>Strategy Library</h2><button type="button" data-automation-route="builder">Explore library ›</button></div><div class="foa-automation-library-tabs" role="tablist"><button type="button" class="foa-automation-library-tab ${libraryTab === "built-in" ? "active" : ""}" data-library-tab="built-in">Built-in</button><button type="button" class="foa-automation-library-tab ${libraryTab === "my" ? "active" : ""}" data-library-tab="my">My Strategies</button><button type="button" class="foa-automation-library-tab ${libraryTab === "ai" ? "active" : ""}" data-library-tab="ai">AI Generated</button></div><div class="foa-automation-template-grid">${libraryContent()}</div></section>
    </section>`;
  }

  function scaffoldMarkup(route) {
    const copy = {
      ai: ["Text to Strategy", "Describe what you want to trade", "Describe your strategy in plain English", "This route is now part of the new Automation architecture. The complete 250-word Text to Strategy compiler is implemented in Action 2."],
      schedule: ["Schedule Trading", "Automate a future trading session", "Schedule your automation", "The Schedule workspace is now reserved in the new app structure. Persistent date, time, timezone, stake, TP and SL scheduling is implemented in the scheduling action."],
      profile: ["Profile", accountLabel(), "Account & preferences", "Your authenticated Deriv account remains connected to the existing backend. Global timezone and automation preferences will live here as the remaining actions are completed."],
    }[route];
    const actions = route === "profile"
      ? `<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:18px"><button type="button" data-automation-route="trades">Open Trades</button><button type="button" data-automation-logout>Logout</button></div>`
      : `<button type="button" data-automation-route="home">Back to Home</button>`;
    return `<section class="foa-automation-page foa-automation-scaffold" data-automation-scaffold="${route}"><div class="foa-automation-scaffold-head"><button type="button" class="foa-automation-back" data-automation-route="home" aria-label="Back to Home">${svg("back")}</button><div><h1>${esc(copy[0])}</h1><p>${esc(copy[1])}</p></div></div><article class="foa-automation-scaffold-card"><span class="foa-automation-template-icon">${svg(route === "schedule" ? "calendar" : route === "profile" ? "profile" : "ai")}</span><strong>${esc(copy[2])}</strong><p>${esc(copy[3])}</p>${actions}</article></section>`;
  }

  function bottomNav(route) {
    const item = (name, icon, label) => `<button type="button" class="foa-automation-nav-button ${route === name ? "active" : ""}" data-automation-route="${name}">${svg(icon)}<span>${esc(label)}</span></button>`;
    return `<nav class="foa-automation-bottom-nav" aria-label="Main navigation" data-automation-bottom-nav="${VERSION}" data-automation-active-route="${route}">${item("home", "home", "Home")}${item("builder", "cubes", "Builder")}${item("ai", "star", "AI")}${item("schedule", "calendar", "Schedule")}${item("profile", "profile", "Profile")}</nav>`;
  }

  function ensureBottomNav(route) {
    const app = q("#foa-simple-app");
    if (!app) return;
    const existing = q(".foa-automation-bottom-nav", app);
    if (existing?.dataset?.automationActiveRoute === route && existing?.dataset?.automationBottomNav === VERSION) return;
    if (existing) existing.remove();
    app.insertAdjacentHTML("beforeend", bottomNav(route));
  }

  function renderRoute() {
    scheduled = false;
    if (!isAuthenticated()) {
      document.body.classList.remove("foa-automation-shell-active");
      VALID_ROUTES.forEach((name) => document.body.classList.remove(`foa-automation-route-${name}`));
      return;
    }
    captureLegacySnapshot();
    const route = currentRoute();
    setBodyRoute(route);
    const main = q("#telegram-dashboard-snapshot > main");
    if (!main) return;

    if (route === "home") {
      if (!main.querySelector(`.foa-automation-home[data-automation-home-version="${VERSION}"]`)) main.innerHTML = homeMarkup();
    } else if (["ai", "schedule", "profile"].includes(route)) {
      if (!main.querySelector(`.foa-automation-scaffold[data-automation-scaffold="${route}"]`)) main.innerHTML = scaffoldMarkup(route);
    } else if (route === "builder") {
      if (main.querySelector(".foa-automation-page")) { syncLegacyView("builder"); scheduleRender(); return; }
    } else if (route === "trades") {
      if (main.querySelector(".foa-automation-page") || !main.querySelector(".trades-control-panel")) { syncLegacyView("trades"); scheduleRender(); return; }
    }

    ensureBottomNav(route);
    window.FOA_AUTOMATION_HOME_ACTION1_VERSION = VERSION;
    window.FOA_AUTOMATION_CURRENT_ROUTE = route;
  }

  function scheduleRender() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(renderRoute);
  }

  function handleStartToTrades(button) {
    const action = String(button?.dataset?.mainAction || "").toLowerCase();
    if (!["start", "resume"].includes(action)) return;
    setTimeout(() => {
      if (q("#token-form") || q(".credential-card .inline-warning")) return;
      navigate("trades");
    }, 260);
  }

  document.addEventListener("click", (event) => {
    const routeButton = event.target?.closest?.("[data-automation-route]");
    if (routeButton) { event.preventDefault(); navigate(String(routeButton.dataset.automationRoute || "home")); return; }

    const libraryButton = event.target?.closest?.("[data-library-tab]");
    if (libraryButton) {
      libraryTab = String(libraryButton.dataset.libraryTab || "built-in");
      const main = q("#telegram-dashboard-snapshot > main");
      if (main && currentRoute() === "home") main.innerHTML = homeMarkup();
      ensureBottomNav("home");
      return;
    }

    if (event.target?.closest?.("[data-automation-logout]")) { q(".builder-header #logout")?.click(); return; }

    const legacyView = event.target?.closest?.("[data-view]");
    if (legacyView && !syncingLegacy) {
      const view = String(legacyView.dataset.view || "main");
      if (view === "trades" || view === "main") {
        const next = view === "trades" ? "trades" : "home";
        sessionSet(ROUTE_KEY, next);
        history.replaceState({ automationRoute: next }, "", `#/${next}`);
        scheduleRender();
      }
    }

    const start = event.target?.closest?.("[data-main-action]");
    if (start) handleStartToTrades(start);
  }, true);

  function syncHistoryRoute() {
    const route = routeFromHash();
    if (!route) return;
    sessionSet(ROUTE_KEY, route);
    syncLegacyView(route);
    scheduleRender();
  }

  addEventListener("popstate", syncHistoryRoute);
  addEventListener("hashchange", syncHistoryRoute);
  addEventListener("pageshow", scheduleRender);
  addEventListener("focus", scheduleRender);

  new MutationObserver(scheduleRender).observe(document.documentElement, { childList: true, subtree: true });

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", () => setTimeout(scheduleRender, 0), { once: true })
    : setTimeout(scheduleRender, 0);
})();
