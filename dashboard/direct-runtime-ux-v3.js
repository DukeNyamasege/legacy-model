(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_RUNTIME_UX_V3__) return;
  window.__DERIVADMIN_DIRECT_RUNTIME_UX_V3__ = true;

  const TAB_STORE = "derivadmin-run-panel-tab-v3";
  const MAX_HISTORY = 1001;
  const upstreamFetch = window.fetch.bind(window);
  const accounts = new Map();
  const histories = new Map();
  const marketResults = new Map();
  let selectedManagedId = 0;
  let providerBalance = null;
  let providerCurrency = "USD";
  let latestLive = null;
  let lastExecution = "";
  let accountRetry = null;
  let renderQueued = false;
  let restoringTab = false;
  let observer = null;
  let observing = false;

  function observe() {
    if (!observer || observing) return;
    observer.observe(document.documentElement, { childList: true, subtree: true });
    observing = true;
  }

  function unobserve() {
    if (!observer || !observing) return;
    observer.disconnect();
    observing = false;
  }

  function pathFor(input) {
    try {
      const raw = typeof input === "string" ? input : input?.url;
      return new URL(String(raw || ""), location.origin).pathname.replace(/^\/api(?=\/)/, "");
    } catch (_) { return ""; }
  }

  function methodFor(input, init) {
    return String(init?.method || (typeof input !== "string" ? input?.method : "") || "GET").toUpperCase();
  }

  function runtime() {
    try { return window.DERIVADMIN_DIRECT_EXECUTION_V1?.state?.() || {}; }
    catch (_) { return {}; }
  }

  function activeStrategy() {
    return runtime().strategy || null;
  }

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function numberText(value) {
    if (value === null || value === undefined || value === "") return "0";
    let raw = String(value).trim().replace(/[$,\s]/g, "");
    const numeric = Number(raw);
    if (!Number.isFinite(numeric)) return "0";
    if (/e/i.test(raw)) raw = numeric.toFixed(12).replace(/0+$/, "").replace(/\.$/, "");
    const negative = raw.startsWith("-");
    if (negative) raw = raw.slice(1);
    let [whole, decimal = ""] = raw.split(".");
    whole = String(Number(whole || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    const result = decimal ? `${whole}.${decimal}` : whole;
    return negative ? `-${result}` : result;
  }

  function money(value, currency = "USD") {
    const code = String(currency || "USD").toUpperCase();
    return `${code === "USD" ? "$" : ""}${numberText(value)} ${code}`;
  }

  function fullId(account) {
    return String(account?.account_id || account?.loginid || account?.account_id_masked || "").trim();
  }

  function selectedAccount() {
    return accounts.get(selectedManagedId) || Array.from(accounts.values()).find((item) => item.selected) || null;
  }

  function cacheAccounts(payload) {
    const rows = Array.isArray(payload?.accounts) ? payload.accounts : [];
    const selectedFromPayload = Number(payload?.selected_managed_account_id || 0);
    if (selectedFromPayload) selectedManagedId = selectedFromPayload;
    rows.forEach((row) => {
      const id = Number(row?.managed_account_id || 0);
      if (!id) return;
      accounts.set(id, { ...(accounts.get(id) || {}), ...row });
      if (row.selected) {
        selectedManagedId = id;
        if (providerBalance === null) providerBalance = row.balance;
        providerCurrency = String(row.currency || providerCurrency || "USD").toUpperCase();
      }
    });
    queueRender();
    clearTimeout(accountRetry);
    if (payload?.linked_accounts_loading) accountRetry = setTimeout(refreshAccounts, 900);
  }

  async function refreshAccounts() {
    try {
      const response = await upstreamFetch("/api/me/accounts", { credentials: "include", cache: "no-store" });
      if (!response.ok) return;
      cacheAccounts(await response.json());
    } catch (_) {}
  }

  window.fetch = async function directRuntimeUxFetch(input, init) {
    const path = pathFor(input);
    const method = methodFor(input, init);
    const response = await upstreamFetch(input, init);
    if (path === "/me/accounts" && method === "GET" && response?.ok) {
      response.clone().json().then(cacheAccounts).catch(() => {});
    }
    if (path === "/me/switch-account" && method === "POST" && response?.ok) {
      response.clone().json().then((payload) => {
        selectedManagedId = Number(payload?.managed_account_id || selectedManagedId) || selectedManagedId;
        providerBalance = null;
        histories.clear();
        marketResults.clear();
        latestLive = null;
        lastExecution = "";
        try { window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1?.close_authenticated?.(); } catch (_) {}
        setTimeout(async () => {
          try { await upstreamFetch("/api/me", { credentials: "include", cache: "no-store" }); } catch (_) {}
          await refreshAccounts();
          try { await window.DERIVADMIN_DIRECT_EXECUTION_V1?.prewarm?.(); } catch (_) {}
          queueRender();
        }, 80);
      }).catch(() => {});
    }
    return response;
  };

  async function switchAccount(id) {
    const managedId = Number(id || 0);
    if (!managedId || managedId === selectedManagedId) return;
    if (runtime().running) {
      try { window.DERIVADMIN_DIRECT_EXECUTION_V1?.stop?.("Switching trading account"); } catch (_) {}
    }
    try {
      const response = await window.fetch("/api/me/switch-account", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ managed_account_id: managedId }),
      });
      if (!response.ok) return;
      selectedManagedId = managedId;
      accounts.forEach((item, key) => { item.selected = key === managedId; });
      const selected = selectedAccount();
      providerBalance = selected?.balance ?? null;
      providerCurrency = String(selected?.currency || "USD").toUpperCase();
      document.querySelector(".top-account-switch")?.classList.remove("open");
      queueRender();
    } catch (_) {}
  }

  function addMissingAccountRows() {
    const host = document.querySelector(".top-account-switch .account-dropdown-rows");
    if (!host) return;
    accounts.forEach((account) => {
      const id = Number(account.managed_account_id || 0);
      if (!id || host.querySelector(`[data-account-id="${CSS.escape(String(id))}"]`)) return;
      const type = String(account.account_type || "demo").toLowerCase();
      const row = document.createElement("button");
      row.type = "button";
      row.className = `account-dropdown-row ${type} direct-generated-account-row`;
      row.dataset.accountId = String(id);
      row.innerHTML = `<span class="direct-account-symbol">${type === "real" ? "R" : "D"}</span><span><b>${type === "real" ? "Real account" : "Demo account"}</b><small>${esc(fullId(account))}</small></span>${type === "demo" ? `<em><span class="direct-demo-balance">${esc(money(account.balance, account.currency))}</span><span data-demo-reset class="direct-demo-reset">Reset balance</span></em>` : `<strong>${esc(money(account.balance, account.currency))}</strong>`}`;
      host.appendChild(row);
    });
  }

  function renderAccounts() {
    const selected = selectedAccount();
    const balance = providerBalance !== null ? providerBalance : selected?.balance;
    const currency = providerCurrency || selected?.currency || "USD";
    document.querySelectorAll(".top-account-switch .account-switch-summary strong,.balance-pill b").forEach((node) => {
      const text = money(balance, currency);
      if (node.textContent !== text) node.textContent = text;
      node.title = text;
    });
    document.querySelectorAll(".account-dropdown-row[data-account-id],.account-row[data-account-id]").forEach((row) => {
      const account = accounts.get(Number(row.getAttribute("data-account-id") || 0));
      if (!account) return;
      const idText = fullId(account);
      const type = String(account.account_type || "demo").toUpperCase();
      const small = row.querySelector("small");
      const expectedIdText = row.classList.contains("account-row") ? `${idText} · ${type}` : idText;
      if (small && idText && small.textContent !== expectedIdText) small.textContent = expectedIdText;
      if (small) small.title = idText;
      const moneyNode = row.querySelector("strong,.account-money b");
      if (moneyNode) {
        const text = money(account.balance, account.currency);
        if (moneyNode.textContent !== text) moneyNode.textContent = text;
        moneyNode.title = text;
      }
      if (String(account.account_type || "").toLowerCase() === "demo") {
        const em = row.querySelector("em");
        if (em && !em.querySelector("[data-demo-reset]")) {
          em.innerHTML = `<span class="direct-demo-balance">${esc(money(account.balance, account.currency))}</span><span data-demo-reset class="direct-demo-reset">Reset balance</span>`;
        } else if (em) {
          const balanceNode = em.querySelector(".direct-demo-balance");
          if (balanceNode) balanceNode.textContent = money(account.balance, account.currency);
        }
      }
    });
    addMissingAccountRows();
  }

  function pip(symbol, tick) {
    const direct = Number(tick?.pip_size);
    if (Number.isInteger(direct) && direct >= 0 && direct <= 12) return direct;
    try {
      const cached = Number(window.DERIVADMIN_DIRECT_PIP_PRECISION_V1?.precision?.(symbol));
      return Number.isInteger(cached) && cached >= 0 && cached <= 12 ? cached : null;
    } catch (_) { return null; }
  }

  function finalDigit(symbol, tick) {
    const quote = Number(tick?.quote);
    if (!Number.isFinite(quote)) return null;
    const precision = pip(symbol, tick);
    const text = precision === null ? String(tick.quote) : quote.toFixed(precision);
    const digits = text.replace(/\D/g, "");
    return digits ? Number(digits[digits.length - 1]) : null;
  }

  function record(symbol, tick) {
    const quote = Number(tick?.quote);
    const digit = finalDigit(symbol, tick);
    if (!Number.isFinite(quote) || digit === null) return null;
    if (!histories.has(symbol)) histories.set(symbol, { quotes: [], digits: [] });
    const history = histories.get(symbol);
    history.quotes.push(quote);
    history.digits.push(digit);
    if (history.quotes.length > MAX_HISTORY) history.quotes.splice(0, history.quotes.length - MAX_HISTORY);
    if (history.digits.length > MAX_HISTORY) history.digits.splice(0, history.digits.length - MAX_HISTORY);
    return { history, quote, digit };
  }

  function compare(value, operator, target) {
    if (operator === "<") return value < target;
    if (operator === "<=") return value <= target;
    if (operator === "==") return Math.abs(value - target) < 0.000001;
    if (operator === "!=") return Math.abs(value - target) >= 0.000001;
    if (operator === ">=") return value >= target;
    if (operator === ">") return value > target;
    return false;
  }

  function conditionMatches(condition, history) {
    const c = condition || {};
    const n = Math.max(1, Number(c.window || 1));
    const digits = history.digits;
    const quotes = history.quotes;
    if (c.kind === "digit_parity") {
      if (digits.length < n) return false;
      const even = String(c.parity || "even") === "even";
      return digits.slice(-n).every((digit) => (digit % 2 === 0) === even);
    }
    if (c.kind === "digit_compare") {
      if (digits.length < n) return false;
      const sample = digits.slice(-n);
      if (c.operator === "all_same") return sample.every((digit) => digit === sample[0]);
      if (c.operator === "all_even") return sample.every((digit) => digit % 2 === 0);
      if (c.operator === "all_odd") return sample.every((digit) => digit % 2 === 1);
      return sample.every((digit) => compare(digit, c.operator, Number(c.value)));
    }
    if (c.kind === "direction") {
      if (quotes.length < n + 1) return false;
      const sample = quotes.slice(-(n + 1));
      const moves = sample.slice(1).map((q, index) => q - sample[index]);
      const direction = String(c.direction || "no_move");
      if (["rise", "rising"].includes(direction)) return moves.every((move) => move > 0);
      if (["fall", "falling"].includes(direction)) return moves.every((move) => move < 0);
      return moves.every((move) => move === 0);
    }
    if (c.kind === "percentage") {
      const target = String(c.target || "");
      let matches = 0;
      let total = 0;
      if (["rise", "fall", "no_move"].includes(target)) {
        if (quotes.length < n + 1) return false;
        const sample = quotes.slice(-(n + 1));
        const moves = sample.slice(1).map((q, index) => q - sample[index]);
        total = moves.length;
        if (target === "rise") matches = moves.filter((move) => move > 0).length;
        else if (target === "fall") matches = moves.filter((move) => move < 0).length;
        else matches = moves.filter((move) => move === 0).length;
      } else {
        if (digits.length < n) return false;
        const sample = digits.slice(-n);
        total = sample.length;
        if (target === "even") matches = sample.filter((digit) => digit % 2 === 0).length;
        else if (target === "odd") matches = sample.filter((digit) => digit % 2 === 1).length;
        else if (target === "over") matches = sample.filter((digit) => digit > Number(c.value)).length;
        else if (target === "under") matches = sample.filter((digit) => digit < Number(c.value)).length;
        else if (target === "digit") matches = sample.filter((digit) => digit === Number(c.value)).length;
      }
      return total > 0 && compare(matches * 100 / total, c.operator, Number(c.threshold || 0));
    }
    return false;
  }

  function conditionLabel(c) {
    const n = Number(c?.window || 1);
    if (c?.kind === "digit_parity") return `Last ${n} digit${n === 1 ? "" : "s"} ${String(c.parity || "even")}`;
    if (c?.kind === "digit_compare") {
      if (["all_same", "all_even", "all_odd"].includes(c.operator)) return `Last ${n} digits ${String(c.operator).replaceAll("_", " ")}`;
      return `Last ${n} digit${n === 1 ? "" : "s"} ${c.operator || "=="} ${c.value ?? 0}`;
    }
    if (c?.kind === "direction") return `Last ${n} move${n === 1 ? "" : "s"} ${String(c.direction || "").replaceAll("_", " ")}`;
    if (c?.kind === "percentage") {
      const value = c.value === null || c.value === undefined ? "" : ` ${c.value}`;
      return `${String(c.target || "").replaceAll("_", " ")}${value} ${c.operator || ">="} ${c.threshold ?? 0}% / ${n} ticks`;
    }
    return "Saved entry condition";
  }

  function strategyName(item) {
    return String(item?.name || item?.strategy_name || "Current strategy");
  }

  function targetLabel(item) {
    const type = String(item?.trade_type || item?.side || "strategy").toUpperCase();
    const prediction = item?.prediction;
    return prediction === null || prediction === undefined || ["EVEN", "ODD", "RISE", "FALL"].includes(type) ? type : `${type} ${prediction}`;
  }

  function onTick(symbol, tick, hydrated) {
    const recorded = record(symbol, tick);
    if (!recorded || hydrated) return;
    const current = runtime();
    const s = current.strategy;
    if (!current.running || !s || !Array.isArray(s.markets) || !s.markets.includes(symbol)) return;
    const conditions = Array.isArray(s.conditions) ? s.conditions : [];
    const statuses = conditions.map((condition) => conditionMatches(condition, recorded.history));
    const met = statuses.length > 0 && statuses.every(Boolean);
    latestLive = { symbol, quote: recorded.quote, digit: recorded.digit, statuses, met, at: Date.now() };
    marketResults.set(symbol, { statuses, met, quote: recorded.quote, digit: recorded.digit, at: Date.now() });
    queueRender();
  }

  function currentTab() {
    return String(document.querySelector(".global-run-panel [data-run-tab].active")?.dataset?.runTab || "");
  }

  function strategyCard(compact) {
    const current = runtime();
    const s = current.strategy;
    if (!s) return `<section class="direct-strategy-checker ${compact ? "compact" : ""}"><div class="direct-strategy-head"><span>STRATEGY</span><b>No strategy loaded</b><small>Create or load a strategy before Run.</small></div></section>`;
    const markets = Array.isArray(s.markets) ? s.markets : [];
    const conditions = Array.isArray(s.conditions) ? s.conditions : [];
    const latest = latestLive && markets.includes(latestLive.symbol) ? latestLive : null;
    const overall = !current.running ? ["stopped", "STOPPED"] : latest?.met ? ["met", "MET · ENTRY FOUND"] : ["not-met", "NOT MET · ANALYZING"];
    const scope = markets.length === 10 ? "Analyzing all 10 markets" : `Analyzing ${markets.length} market${markets.length === 1 ? "" : "s"}`;
    const conditionRows = conditions.map((condition, index) => {
      const met = latest?.statuses?.[index] === true;
      return `<div class="direct-condition-row"><span>${index + 1}</span><b>${esc(conditionLabel(condition))}</b><em class="${met ? "met" : "not-met"}">${met ? "MET" : "NOT MET"}</em></div>`;
    }).join("");
    const chips = markets.map((symbol) => {
      const result = marketResults.get(symbol);
      return `<span class="direct-market-chip ${result?.met ? "met" : "not-met"}"><b>${esc(symbol)}</b><em>${result ? (result.met ? "MET" : "NOT MET") : "WAITING"}</em></span>`;
    }).join("");
    const latestText = latest ? `${latest.symbol} · digit ${latest.digit} · ${new Date(latest.at).toLocaleTimeString()}` : "Waiting for the next live Deriv tick";
    return `<section class="direct-strategy-checker ${compact ? "compact" : ""}">
      <div class="direct-strategy-head"><span>STRATEGY</span><b>${esc(strategyName(s))}</b><small>${esc(targetLabel(s))} · ${esc(scope)}</small></div>
      <div class="direct-strategy-result ${overall[0]}"><b>${overall[1]}</b><span>${esc(latestText)}</span></div>
      ${compact ? "" : `<div class="direct-condition-list">${conditionRows || `<div class="direct-condition-row"><span>1</span><b>Saved entry rule</b><em class="not-met">NOT MET</em></div>`}</div><div class="direct-market-strip">${chips}</div>`}
      ${lastExecution ? `<div class="direct-last-execution">${esc(lastExecution)}</div>` : ""}
    </section>`;
  }

  function renderStrategyCard() {
    const panel = document.querySelector(".global-run-panel");
    const body = panel?.querySelector(".run-panel-body");
    if (!body) return;
    body.querySelectorAll(":scope > .direct-strategy-checker").forEach((node) => node.remove());
    const tab = currentTab();
    if (tab === "journal") {
      body.querySelector(".run-panel-journal")?.classList.add("direct-hide-legacy-journal");
      body.insertAdjacentHTML("afterbegin", strategyCard(false));
    } else if (tab === "transactions") {
      body.insertAdjacentHTML("afterbegin", strategyCard(true));
    }
  }

  function renderRunState() {
    const current = runtime();
    const running = Boolean(current.running);
    document.querySelectorAll(".global-run-panel [data-run-start]").forEach((button) => {
      button.dataset.directRunState = running ? "stop" : "start";
      button.setAttribute("aria-label", running ? "Stop trading" : "Start trading");
      const span = button.querySelector("span");
      if (span) {
        if (span.textContent !== (running ? "Stop" : "Run")) span.textContent = running ? "Stop" : "Run";
      } else if (button.textContent !== (running ? "Stop" : "Run")) button.textContent = running ? "Stop" : "Run";
    });
    document.querySelectorAll(".global-run-panel [data-run-execution-toggle]").forEach((node) => node.remove());
    const panel = document.querySelector(".global-run-panel");
    if (!panel) return;
    let pill = panel.querySelector(".direct-bot-state-pill");
    if (!pill) {
      pill = document.createElement("div");
      pill.className = "direct-bot-state-pill";
      (panel.querySelector(".run-panel-bar") || panel).insertAdjacentElement("beforebegin", pill);
    }
    const server = running && String(current.owner || "") === "server_takeover";
    pill.className = `direct-bot-state-pill ${running ? "running" : "stopped"} ${server ? "server" : ""}`;
    const text = running ? (server ? "Bot continuing trades on server" : "Bot currently executing trades") : "Bot currently stopped";
    if (pill.dataset.label !== text) {
      pill.dataset.label = text;
      pill.innerHTML = `<i></i><span>${esc(text)}</span>`;
    }
  }

  function renderLoadedBadge() {
    const panel = document.querySelector(".global-run-panel");
    if (!panel) return;
    let badge = panel.querySelector(".direct-loaded-strategy-badge");
    if (!badge) {
      badge = document.createElement("div");
      badge.className = "direct-loaded-strategy-badge";
      (panel.querySelector(".run-panel-top") || panel.querySelector(".run-panel-sheet"))?.insertAdjacentElement("afterend", badge);
    }
    const s = activeStrategy();
    const signature = s ? `${strategyName(s)}|${targetLabel(s)}` : "none";
    if (badge.dataset.signature === signature) return;
    badge.dataset.signature = signature;
    badge.innerHTML = s ? `<span>Loaded strategy</span><b>${esc(strategyName(s))}</b><small>${esc(targetLabel(s))}</small>` : `<span>Loaded strategy</span><b>None</b><small>Load or create one</small>`;
  }

  function restoreTab() {
    const saved = localStorage.getItem(TAB_STORE) || "transactions";
    const button = document.querySelector(`.global-run-panel [data-run-tab="${CSS.escape(saved)}"]`);
    if (!button || button.classList.contains("active") || restoringTab) return;
    restoringTab = true;
    button.click();
    setTimeout(() => { restoringTab = false; }, 100);
  }

  function removeNoise() {
    const noise = /backend request timed out|backend did not answer|backend timeout|account[_\s-]*stop[_\s-]*reason[_\s-]*repaired|managed\s*id\s*\d+.*repaired/i;
    document.querySelectorAll(".global-message,.premium-message,[role='alert']").forEach((node) => {
      if (noise.test(String(node.textContent || ""))) node.remove();
    });
  }

  function render() {
    renderQueued = false;
    unobserve();
    try {
      removeNoise();
      restoreTab();
      renderRunState();
      renderLoadedBadge();
      renderStrategyCard();
      renderAccounts();
    } finally {
      observe();
    }
  }

  function queueRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(render);
  }

  document.addEventListener("click", (event) => {
    const tab = event.target?.closest?.(".global-run-panel [data-run-tab]");
    if (tab) {
      const name = String(tab.dataset.runTab || "transactions");
      if (["summary", "transactions", "journal"].includes(name)) localStorage.setItem(TAB_STORE, name);
      setTimeout(queueRender, 0);
      return;
    }
    if (event.target?.closest?.("[data-demo-reset]")) return;
    const accountRow = event.target?.closest?.(".top-account-switch [data-account-id],.account-row[data-account-id]");
    if (!accountRow) return;
    const id = Number(accountRow.getAttribute("data-account-id") || 0);
    if (!id || id === selectedManagedId) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    switchAccount(id);
  }, true);

  window.addEventListener("derivadmin:direct-market-tick", (event) => {
    const detail = event.detail || {};
    onTick(String(detail.symbol || "").toUpperCase(), detail.tick || {}, Boolean(detail.hydrated));
  });

  window.addEventListener("derivadmin:direct-balance", (event) => {
    const detail = event.detail || {};
    providerBalance = detail.balance;
    providerCurrency = String(detail.currency || "USD").toUpperCase();
    const account = selectedAccount();
    if (account) {
      account.balance = detail.balance;
      account.currency = providerCurrency;
      if (detail.loginid) account.account_id = String(detail.loginid);
    }
    queueRender();
  });

  window.addEventListener("derivadmin:direct-balance-live", (event) => {
    const detail = event.detail || {};
    const currency = String(detail.currency || providerCurrency || selectedAccount()?.currency || "USD").toUpperCase();
    const absolute = Number(detail.balance);
    const delta = Number(detail.delta);
    if (Number.isFinite(absolute)) providerBalance = absolute;
    else if (Number.isFinite(delta)) providerBalance = Number(providerBalance ?? selectedAccount()?.balance ?? 0) + delta;
    else return;
    providerBalance = Math.round(Number(providerBalance) * 100000000) / 100000000;
    providerCurrency = currency;
    const account = selectedAccount();
    if (account) {
      account.balance = providerBalance;
      account.currency = providerCurrency;
    }
    queueRender();
  });

  window.addEventListener("derivadmin:demo-balance-reset", (event) => {
    const detail = event.detail || {};
    providerBalance = detail.balance ?? 10000;
    providerCurrency = String(detail.currency || "USD").toUpperCase();
    const account = selectedAccount();
    if (account) {
      account.balance = providerBalance;
      account.currency = providerCurrency;
      if (detail.account_id) account.account_id = String(detail.account_id);
    }
    queueRender();
  });

  window.addEventListener("derivadmin:direct-trade", (event) => {
    const row = event.detail || {};
    if (row.state === "OPEN") lastExecution = `PURCHASED · ${row.symbol || ""} · contract ${String(row.contract_id || "").slice(-8)}`;
    else if (row.state === "SETTLED") lastExecution = `SETTLED ${String(row.outcome || "")} · profit ${row.profit ?? 0}`;
    else if (row.mode === "virtual") lastExecution = `VIRTUAL ${String(row.outcome || "")} · ${row.symbol || ""}`;
    queueRender();
  });

  window.addEventListener("derivadmin:direct-clear", () => { lastExecution = ""; marketResults.clear(); queueRender(); });
  window.addEventListener("derivadmin:direct-reset-all", () => { lastExecution = ""; marketResults.clear(); queueRender(); });

  observer = new MutationObserver(queueRender);
  observe();

  const style = document.createElement("style");
  style.id = "direct-runtime-ux-v3-style";
  style.textContent = `
    .topbar{z-index:12000!important}.topbar-actions,.top-account-switch{position:relative;z-index:12010!important}.top-account-switch .account-dropdown{z-index:12050!important}.global-run-panel{z-index:7000!important}
    .top-account-switch strong,.account-dropdown-row strong,.account-dropdown-row small,.account-row small,.account-money b,.balance-pill b{max-width:none!important;overflow:visible!important;text-overflow:clip!important;white-space:nowrap!important}.account-dropdown{min-width:min(440px,calc(100vw - 24px))!important}.account-dropdown-row{grid-template-columns:auto minmax(0,1fr) auto!important}.account-dropdown-row>span:nth-child(2){min-width:0}.account-dropdown-row em{display:flex!important;flex-direction:column!important;align-items:flex-end!important;gap:3px!important;font-style:normal!important}.direct-demo-balance{font-size:10px;color:#e6f5ff;font-weight:800;white-space:nowrap}.direct-demo-reset{font-size:8px;color:#58dcff;text-decoration:underline;text-underline-offset:2px;cursor:pointer}.direct-account-symbol{width:30px;height:30px;border-radius:10px;display:grid;place-items:center;background:#0d2944;color:#5edcff;font-weight:900}
    .global-run-panel [data-run-execution-toggle]{display:none!important}.global-run-panel .run-panel-bar{grid-template-columns:1fr!important}.global-run-panel .run-panel-run{width:100%!important}.direct-bot-state-pill{display:flex;align-items:center;justify-content:center;gap:7px;margin:0 10px 7px;padding:6px 9px;border-radius:10px;font-size:9px;font-weight:800;border:1px solid rgba(127,170,204,.13);background:rgba(8,20,35,.86);color:#8499ad}.direct-bot-state-pill i{width:7px;height:7px;border-radius:50%;background:#65788a}.direct-bot-state-pill.running{color:#95f1c8;border-color:rgba(52,230,161,.2);background:rgba(14,58,47,.35)}.direct-bot-state-pill.running i{background:#34e6a1;box-shadow:0 0 12px rgba(52,230,161,.7)}.direct-bot-state-pill.server{color:#ffd88b}.direct-bot-state-pill.server i{background:#ffcc66}
    .direct-loaded-strategy-badge{margin:0 12px 8px;padding:8px 10px;border-radius:11px;border:1px solid rgba(70,202,255,.13);background:rgba(7,24,43,.8);display:grid;grid-template-columns:auto 1fr auto;gap:4px 8px;align-items:center}.direct-loaded-strategy-badge span{font-size:7px;text-transform:uppercase;letter-spacing:.12em;color:#5f839e}.direct-loaded-strategy-badge b{font-size:9px;color:#dff5ff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.direct-loaded-strategy-badge small{font-size:8px;color:#53d9ff}
    .direct-hide-legacy-journal{display:none!important}.direct-strategy-checker{border:1px solid rgba(87,192,255,.15);background:linear-gradient(155deg,rgba(8,27,49,.94),rgba(5,17,31,.94));border-radius:15px;padding:12px;display:flex;flex-direction:column;gap:9px}.direct-strategy-head>span{display:block;font-size:7px;letter-spacing:.14em;color:#58a6cf;font-weight:900}.direct-strategy-head>b{display:block;margin-top:4px;font-size:12px;color:#f1f9ff}.direct-strategy-head>small{display:block;margin-top:3px;font-size:8px;color:#7791a7}.direct-strategy-result{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:8px 9px;border-radius:10px;border:1px solid rgba(121,160,193,.1);background:rgba(255,255,255,.025)}.direct-strategy-result>b{font-size:9px}.direct-strategy-result>span{font-size:7px;color:#7890a4}.direct-strategy-result.met{border-color:rgba(52,230,161,.22);background:rgba(52,230,161,.07)}.direct-strategy-result.met>b{color:#4bf0ad}.direct-strategy-result.not-met>b{color:#72dfff}.direct-strategy-result.stopped>b{color:#8092a4}.direct-condition-list{display:flex;flex-direction:column;gap:5px}.direct-condition-row{display:grid;grid-template-columns:20px 1fr auto;gap:7px;align-items:center;padding:6px 7px;border-radius:9px;background:rgba(255,255,255,.025)}.direct-condition-row>span{width:18px;height:18px;border-radius:6px;display:grid;place-items:center;background:#0c2945;color:#62dcff;font-size:7px;font-weight:900}.direct-condition-row>b{font-size:8px;font-weight:700;color:#bcd0df}.direct-condition-row>em{font-size:7px;font-style:normal;font-weight:900}.direct-condition-row>em.met{color:#3ee8a5}.direct-condition-row>em.not-met{color:#ffbd73}.direct-market-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px}.direct-market-chip{padding:5px 3px;border-radius:7px;text-align:center;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.035)}.direct-market-chip b{display:block;font-size:6px;color:#9bb0c1}.direct-market-chip em{display:block;margin-top:2px;font-size:5px;font-style:normal;color:#70879b}.direct-market-chip.met{border-color:rgba(52,230,161,.18)}.direct-market-chip.met em{color:#48e7a9}.direct-last-execution{font-size:7px;padding:6px 8px;border-radius:8px;background:rgba(68,124,255,.08);color:#86baff}.direct-strategy-checker.compact{margin-bottom:8px;padding:9px}.direct-strategy-checker.compact .direct-strategy-head>b{font-size:10px}
    @media(max-width:620px){.account-dropdown{right:-4px!important;left:auto!important;max-width:calc(100vw - 18px)!important}.direct-market-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.direct-loaded-strategy-badge{grid-template-columns:1fr auto}.direct-loaded-strategy-badge span{grid-column:1/-1}.direct-strategy-result{align-items:flex-start;flex-direction:column}}
  `;
  document.head.appendChild(style);

  if (!localStorage.getItem(TAB_STORE)) localStorage.setItem(TAB_STORE, "transactions");
  refreshAccounts();
  setInterval(() => { unobserve(); try { renderRunState(); } finally { observe(); } }, 400);
  queueRender();

  window.DERIVADMIN_DIRECT_RUNTIME_UX_V3 = Object.freeze({
    version: "20260818-runtime-ux-v3",
    refresh_accounts: refreshAccounts,
    state: () => ({ selected_managed_id: selectedManagedId, latest_live: latestLive, accounts: Array.from(accounts.values()) }),
  });
})();
