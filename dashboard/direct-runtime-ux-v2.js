(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_RUNTIME_UX_V2__) return;
  window.__DERIVADMIN_DIRECT_RUNTIME_UX_V2__ = true;

  const TAB_STORE = "derivadmin-run-panel-tab-v2";
  const MAX_HISTORY = 1001;
  const upstreamFetch = window.fetch.bind(window);
  const accounts = new Map();
  const histories = new Map();
  const marketResults = new Map();
  let selectedManagedId = 0;
  let selectedBalance = null;
  let selectedCurrency = "USD";
  let restoringTab = false;
  let renderQueued = false;
  let accountRetryTimer = null;
  let latestLive = null;
  let lastTradeState = "";

  function pathFor(input) {
    try {
      const raw = typeof input === "string" ? input : input?.url;
      return new URL(String(raw || ""), location.origin).pathname.replace(/^\/api(?=\/)/, "");
    } catch (_) { return ""; }
  }

  function methodFor(input, init) {
    return String(init?.method || (typeof input !== "string" ? input?.method : "") || "GET").toUpperCase();
  }

  function engineState() {
    try { return window.DERIVADMIN_DIRECT_EXECUTION_V1?.state?.() || {}; }
    catch (_) { return {}; }
  }

  function strategy() {
    return engineState().strategy || null;
  }

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function exactNumberText(value) {
    if (value === null || value === undefined || value === "") return "0";
    let text = typeof value === "string" ? value.trim() : String(value);
    text = text.replace(/[$,\s]/g, "");
    const numeric = Number(text);
    if (!Number.isFinite(numeric)) return "0";
    if (/e/i.test(text)) {
      text = numeric.toFixed(12).replace(/0+$/, "").replace(/\.$/, "");
    }
    const negative = text.startsWith("-");
    if (negative) text = text.slice(1);
    let [whole, decimal = ""] = text.split(".");
    whole = String(Number(whole || 0));
    whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    const joined = decimal ? `${whole}.${decimal}` : whole;
    return negative ? `-${joined}` : joined;
  }

  function exactMoney(value, currency = "USD") {
    const code = String(currency || "USD").toUpperCase();
    const prefix = code === "USD" ? "$" : "";
    return `${prefix}${exactNumberText(value)} ${code}`;
  }

  function accountFullId(account) {
    return String(account?.account_id || account?.loginid || account?.account_id_masked || "").trim();
  }

  function cacheAccounts(payload) {
    const rows = Array.isArray(payload?.accounts) ? payload.accounts : [];
    if (payload?.selected_managed_account_id) selectedManagedId = Number(payload.selected_managed_account_id) || selectedManagedId;
    for (const row of rows) {
      const id = Number(row?.managed_account_id || 0);
      if (!id) continue;
      accounts.set(id, { ...(accounts.get(id) || {}), ...row });
      if (row.selected) {
        selectedManagedId = id;
        selectedBalance = row.balance;
        selectedCurrency = String(row.currency || "USD").toUpperCase();
      }
    }
    renderAccountUi();
    if (payload?.linked_accounts_loading) scheduleAccountRefresh();
  }

  async function refreshAccounts() {
    clearTimeout(accountRetryTimer);
    try {
      const response = await upstreamFetch("/api/me/accounts", { credentials: "include", cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      cacheAccounts(payload);
      if (payload?.linked_accounts_loading) scheduleAccountRefresh();
    } catch (_) {}
  }

  function scheduleAccountRefresh() {
    clearTimeout(accountRetryTimer);
    accountRetryTimer = setTimeout(refreshAccounts, 900);
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
        histories.clear();
        marketResults.clear();
        latestLive = null;
        lastTradeState = "";
        // Close the old authenticated Deriv socket before the new selected account
        // is prewarmed. The public market socket can remain shared.
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

  function selectedAccount() {
    if (accounts.has(selectedManagedId)) return accounts.get(selectedManagedId);
    return Array.from(accounts.values()).find((item) => item.selected) || null;
  }

  function renderAccountUi() {
    const selected = selectedAccount();
    if (selected && selectedBalance === null) {
      selectedBalance = selected.balance;
      selectedCurrency = String(selected.currency || "USD").toUpperCase();
    }
    const balance = selectedBalance !== null ? selectedBalance : selected?.balance;
    const currency = selectedCurrency || selected?.currency || "USD";

    document.querySelectorAll(".top-account-switch .account-switch-summary strong").forEach((node) => {
      node.textContent = exactMoney(balance, currency);
      node.title = node.textContent;
    });
    document.querySelectorAll(".balance-pill b").forEach((node) => {
      node.textContent = exactMoney(balance, currency);
      node.title = node.textContent;
    });

    document.querySelectorAll(".account-dropdown-row[data-account-id],.account-row[data-account-id]").forEach((row) => {
      const id = Number(row.getAttribute("data-account-id") || 0);
      const account = accounts.get(id);
      if (!account) return;
      const fullId = accountFullId(account);
      const small = row.querySelector("small");
      if (small && fullId) {
        const type = String(account.account_type || "").toUpperCase();
        small.textContent = row.classList.contains("account-row") && type ? `${fullId} · ${type}` : fullId;
        small.title = fullId;
      }
      const moneyNode = row.querySelector("strong,.account-money b");
      if (moneyNode) {
        moneyNode.textContent = exactMoney(account.balance, account.currency);
        moneyNode.title = moneyNode.textContent;
      }
      if (String(account.account_type || "").toLowerCase() === "demo") {
        const reset = row.querySelector("em");
        if (reset) {
          reset.innerHTML = `<span class="direct-demo-balance">${esc(exactMoney(account.balance, account.currency))}</span><span class="direct-demo-reset" data-demo-reset>Reset balance</span>`;
          reset.title = `Reset ${fullId || "demo account"} with Deriv`;
        }
      }
    });

    // If stale-while-revalidate returned only the selected account during the first
    // render, add newly discovered linked accounts without waiting for a full app
    // navigation/render cycle.
    const rowsHost = document.querySelector(".top-account-switch .account-dropdown-rows");
    if (rowsHost) {
      for (const account of accounts.values()) {
        const id = Number(account.managed_account_id || 0);
        if (!id || rowsHost.querySelector(`[data-account-id="${CSS.escape(String(id))}"]`)) continue;
        const type = String(account.account_type || "demo").toLowerCase();
        const button = document.createElement("button");
        button.type = "button";
        button.className = `account-dropdown-row ${type} direct-generated-account-row`;
        button.dataset.accountId = String(id);
        button.dataset.accountKindRow = type;
        button.innerHTML = `<span class="direct-account-symbol">${type === "real" ? "R" : "D"}</span><span><b>${esc(type === "real" ? "Real account" : "Demo account")}</b><small>${esc(accountFullId(account))}</small></span>${type === "demo" ? `<em><span class="direct-demo-balance">${esc(exactMoney(account.balance, account.currency))}</span><span class="direct-demo-reset" data-demo-reset>Reset balance</span></em>` : `<strong>${esc(exactMoney(account.balance, account.currency))}</strong>`}`;
        rowsHost.appendChild(button);
      }
    }
  }

  async function switchAccount(managedId) {
    const id = Number(managedId || 0);
    if (!id || id === selectedManagedId) return;
    const runtime = engineState();
    if (runtime.running) {
      try { window.DERIVADMIN_DIRECT_EXECUTION_V1?.stop?.("Switching trading account"); } catch (_) {}
    }
    try {
      const response = await window.fetch("/api/me/switch-account", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ managed_account_id: id }),
      });
      if (!response.ok) return;
      selectedManagedId = id;
      for (const [key, account] of accounts.entries()) accounts.set(key, { ...account, selected: key === id });
      const selected = accounts.get(id);
      if (selected) {
        selectedBalance = selected.balance;
        selectedCurrency = String(selected.currency || "USD").toUpperCase();
      }
      document.querySelector(".top-account-switch")?.classList.remove("open");
      renderAccountUi();
    } catch (_) {}
  }

  function pipSize(symbol, tick) {
    const direct = Number(tick?.pip_size);
    if (Number.isInteger(direct) && direct >= 0 && direct <= 12) return direct;
    try {
      const cached = Number(window.DERIVADMIN_DIRECT_PIP_PRECISION_V1?.precision?.(symbol));
      if (Number.isInteger(cached) && cached >= 0 && cached <= 12) return cached;
    } catch (_) {}
    return null;
  }

  function digitFromTick(symbol, tick) {
    const quote = Number(tick?.quote);
    if (!Number.isFinite(quote)) return null;
    const pip = pipSize(symbol, tick);
    const text = pip === null ? String(tick.quote) : quote.toFixed(pip);
    const digits = text.replace(/\D/g, "");
    return digits ? Number(digits[digits.length - 1]) : null;
  }

  function historyFor(symbol) {
    if (!histories.has(symbol)) histories.set(symbol, { quotes: [], digits: [] });
    return histories.get(symbol);
  }

  function recordTick(symbol, tick) {
    const quote = Number(tick?.quote);
    const digit = digitFromTick(symbol, tick);
    if (!Number.isFinite(quote) || digit === null) return null;
    const history = historyFor(symbol);
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
    const digits = history.digits;
    const quotes = history.quotes;
    const windowSize = Math.max(1, Number(condition?.window || 1));
    if (condition?.kind === "digit_parity") {
      if (digits.length < windowSize) return false;
      const even = String(condition.parity || "even") === "even";
      return digits.slice(-windowSize).every((digit) => (digit % 2 === 0) === even);
    }
    if (condition?.kind === "digit_compare") {
      if (digits.length < windowSize) return false;
      const sample = digits.slice(-windowSize);
      if (condition.operator === "all_same") return sample.length > 0 && sample.every((digit) => digit === sample[0]);
      if (condition.operator === "all_even") return sample.every((digit) => digit % 2 === 0);
      if (condition.operator === "all_odd") return sample.every((digit) => digit % 2 === 1);
      return sample.every((digit) => compare(digit, condition.operator, Number(condition.value)));
    }
    if (condition?.kind === "direction") {
      if (quotes.length < windowSize + 1) return false;
      const sample = quotes.slice(-(windowSize + 1));
      const moves = sample.slice(1).map((quote, index) => quote - sample[index]);
      const direction = String(condition.direction || "no_move");
      if (["rise", "rising"].includes(direction)) return moves.every((move) => move > 0);
      if (["fall", "falling"].includes(direction)) return moves.every((move) => move < 0);
      return moves.every((move) => move === 0);
    }
    if (condition?.kind === "percentage") {
      let total = 0;
      let matches = 0;
      const target = String(condition.target || "");
      if (["rise", "fall", "no_move"].includes(target)) {
        if (quotes.length < windowSize + 1) return false;
        const sample = quotes.slice(-(windowSize + 1));
        const moves = sample.slice(1).map((quote, index) => quote - sample[index]);
        total = moves.length;
        if (target === "rise") matches = moves.filter((move) => move > 0).length;
        else if (target === "fall") matches = moves.filter((move) => move < 0).length;
        else matches = moves.filter((move) => move === 0).length;
      } else {
        if (digits.length < windowSize) return false;
        const sample = digits.slice(-windowSize);
        total = sample.length;
        if (target === "even") matches = sample.filter((digit) => digit % 2 === 0).length;
        else if (target === "odd") matches = sample.filter((digit) => digit % 2 === 1).length;
        else if (target === "over") matches = sample.filter((digit) => digit > Number(condition.value)).length;
        else if (target === "under") matches = sample.filter((digit) => digit < Number(condition.value)).length;
        else if (target === "digit") matches = sample.filter((digit) => digit === Number(condition.value)).length;
      }
      return total > 0 && compare(matches * 100 / total, condition.operator, Number(condition.threshold || 0));
    }
    return false;
  }

  function conditionText(condition) {
    const c = condition || {};
    const windowSize = Number(c.window || 1);
    if (c.kind === "digit_parity") return `Last ${windowSize} digit${windowSize === 1 ? "" : "s"}: ${String(c.parity || "even")}`;
    if (c.kind === "digit_compare") {
      if (["all_same", "all_even", "all_odd"].includes(c.operator)) return `Last ${windowSize} digits: ${String(c.operator).replaceAll("_", " ")}`;
      return `Last ${windowSize} digit${windowSize === 1 ? "" : "s"}: ${c.operator || "=="} ${c.value ?? 0}`;
    }
    if (c.kind === "direction") return `Last ${windowSize} move${windowSize === 1 ? "" : "s"}: ${String(c.direction || "").replaceAll("_", " ")}`;
    if (c.kind === "percentage") {
      const target = String(c.target || "").replaceAll("_", " ");
      const value = c.value === null || c.value === undefined ? "" : ` ${c.value}`;
      return `${target}${value} ${c.operator || ">="} ${c.threshold ?? 0}% / ${windowSize} ticks`;
    }
    return "Saved strategy condition";
  }

  function strategyName(item) {
    return String(item?.name || item?.strategy_name || "Current strategy");
  }

  function strategyTarget(item) {
    const type = String(item?.trade_type || item?.side || "strategy").toUpperCase();
    const prediction = item?.prediction;
    return prediction === null || prediction === undefined || ["EVEN", "ODD", "RISE", "FALL"].includes(type)
      ? type
      : `${type} ${prediction}`;
  }

  function evaluateLiveTick(symbol, tick, hydrated) {
    const recorded = recordTick(symbol, tick);
    if (!recorded) return;
    if (hydrated) return;
    const runtime = engineState();
    const active = runtime.strategy;
    if (!runtime.running || !active || !Array.isArray(active.markets) || !active.markets.includes(symbol)) return;
    const conditions = Array.isArray(active.conditions) ? active.conditions : [];
    const statuses = conditions.map((condition) => conditionMatches(condition, recorded.history));
    const overall = statuses.length > 0 && statuses.every(Boolean);
    latestLive = {
      symbol,
      quote: recorded.quote,
      digit: recorded.digit,
      at: Date.now(),
      overall,
      statuses,
    };
    marketResults.set(symbol, { overall, statuses, digit: recorded.digit, quote: recorded.quote, at: Date.now() });
    queueRender();
  }

  function activeTab() {
    return String(document.querySelector(".global-run-panel [data-run-tab].active")?.dataset?.runTab || "");
  }

  function strategyCardMarkup(compact = false) {
    const runtime = engineState();
    const active = runtime.strategy;
    if (!active) {
      return `<section class="direct-strategy-checker ${compact ? "compact" : ""}"><div class="direct-strategy-head"><span>STRATEGY</span><b>No strategy loaded</b></div><p>Load or create a strategy, then press Run.</p></section>`;
    }
    const markets = Array.isArray(active.markets) ? active.markets : [];
    const conditions = Array.isArray(active.conditions) ? active.conditions : [];
    const latest = latestLive && markets.includes(latestLive.symbol) ? latestLive : null;
    const overallText = !runtime.running
      ? "STOPPED"
      : latest?.overall
        ? "MET · ENTRY FOUND"
        : "NOT MET · ANALYZING";
    const overallClass = !runtime.running ? "stopped" : latest?.overall ? "met" : "not-met";
    const marketScope = markets.length === 10 ? "Analyzing all 10 markets" : `Analyzing ${markets.length} market${markets.length === 1 ? "" : "s"}`;
    const marketChips = markets.map((symbol) => {
      const result = marketResults.get(symbol);
      const cls = result?.overall ? "met" : "not-met";
      const label = result ? (result.overall ? "MET" : "NOT MET") : "WAITING";
      return `<span class="direct-market-chip ${cls}"><b>${esc(symbol)}</b><em>${label}</em></span>`;
    }).join("");
    const rows = conditions.map((condition, index) => {
      const value = latest?.statuses?.[index];
      const cls = value === true ? "met" : "not-met";
      const label = value === true ? "MET" : "NOT MET";
      return `<div class="direct-condition-row"><span>${index + 1}</span><b>${esc(conditionText(condition))}</b><em class="${cls}">${label}</em></div>`;
    }).join("");
    const latestLine = latest
      ? `${latest.symbol} · digit ${latest.digit} · ${new Date(latest.at).toLocaleTimeString()}`
      : "Waiting for the next live Deriv tick";
    return `<section class="direct-strategy-checker ${compact ? "compact" : ""}">
      <div class="direct-strategy-head"><span>STRATEGY</span><b>${esc(strategyName(active))}</b><small>${esc(strategyTarget(active))} · ${esc(marketScope)}</small></div>
      <div class="direct-strategy-result ${overallClass}"><b>${overallText}</b><span>${esc(latestLine)}</span></div>
      ${compact ? "" : `<div class="direct-condition-list">${rows || `<div class="direct-condition-row"><span>1</span><b>Saved entry rule</b><em class="not-met">NOT MET</em></div>`}</div><div class="direct-market-strip">${marketChips}</div>`}
      ${lastTradeState ? `<div class="direct-last-execution">${esc(lastTradeState)}</div>` : ""}
    </section>`;
  }

  function ensureStrategyCard() {
    const panel = document.querySelector(".global-run-panel");
    if (!panel) return;
    const body = panel.querySelector(".run-panel-body");
    if (!body) return;
    const tab = activeTab();
    body.querySelectorAll(".direct-strategy-checker").forEach((node) => node.remove());
    if (tab === "journal") {
      body.querySelector(".run-panel-journal")?.classList.add("direct-hide-legacy-journal");
      body.insertAdjacentHTML("afterbegin", strategyCardMarkup(false));
    } else if (tab === "transactions") {
      body.insertAdjacentHTML("afterbegin", strategyCardMarkup(true));
    }
  }

  function ensureLoadedStrategyBadge() {
    const panel = document.querySelector(".global-run-panel");
    if (!panel) return;
    let badge = panel.querySelector(".direct-loaded-strategy-badge");
    if (!badge) {
      badge = document.createElement("div");
      badge.className = "direct-loaded-strategy-badge";
      const top = panel.querySelector(".run-panel-top") || panel.querySelector(".run-panel-sheet");
      top?.insertAdjacentElement("afterend", badge);
    }
    const active = strategy();
    badge.innerHTML = active
      ? `<span>Loaded strategy</span><b>${esc(strategyName(active))}</b><small>${esc(strategyTarget(active))}</small>`
      : `<span>Loaded strategy</span><b>None</b><small>Create or load one before Run</small>`;
  }

  function syncRunState() {
    const runtime = engineState();
    const running = Boolean(runtime.running);
    const owner = String(runtime.owner || "");
    document.querySelectorAll(".global-run-panel [data-run-start]").forEach((button) => {
      button.dataset.directRunState = running ? "stop" : "start";
      button.setAttribute("aria-label", running ? "Stop trading" : "Start trading");
      const label = button.querySelector("span");
      if (label) label.textContent = running ? "Stop" : "Run";
      else button.textContent = running ? "Stop" : "Run";
    });
    // There is one execution command only. The unexplained bottom-right toggle is
    // deliberately removed and never recreated by mutation re-renders.
    document.querySelectorAll(".global-run-panel [data-run-execution-toggle]").forEach((node) => node.remove());

    const panel = document.querySelector(".global-run-panel");
    if (panel) {
      let pill = panel.querySelector(".direct-bot-state-pill");
      if (!pill) {
        pill = document.createElement("div");
        pill.className = "direct-bot-state-pill";
        const bar = panel.querySelector(".run-panel-bar") || panel;
        bar.insertAdjacentElement("beforebegin", pill);
      }
      const server = running && owner === "server_takeover";
      pill.className = `direct-bot-state-pill ${running ? "running" : "stopped"} ${server ? "server" : ""}`;
      pill.innerHTML = running
        ? `<i></i><span>${server ? "Bot continuing trades on server" : "Bot currently executing trades"}</span>`
        : `<i></i><span>Bot currently stopped</span>`;
    }
  }

  function restoreStickyTab() {
    const saved = localStorage.getItem(TAB_STORE) || "transactions";
    const button = document.querySelector(`.global-run-panel [data-run-tab="${CSS.escape(saved)}"]`);
    if (!button || button.classList.contains("active") || restoringTab) return;
    restoringTab = true;
    button.click();
    setTimeout(() => { restoringTab = false; }, 80);
  }

  function removeNoise() {
    const noise = /backend request timed out|backend did not answer|backend timeout|account[_\s-]*stop[_\s-]*reason[_\s-]*repaired|managed\s*id\s*\d+.*repaired/i;
    document.querySelectorAll(".global-message,.premium-message,[role='alert']").forEach((node) => {
      if (noise.test(String(node.textContent || ""))) node.remove();
    });
  }

  function render() {
    renderQueued = false;
    removeNoise();
    restoreStickyTab();
    syncRunState();
    ensureLoadedStrategyBadge();
    ensureStrategyCard();
    renderAccountUi();
  }

  function queueRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(render);
  }

  document.addEventListener("click", (event) => {
    const tab = event.target?.closest?.(".global-run-panel [data-run-tab]");
    if (tab) {
      const key = String(tab.dataset.runTab || "transactions");
      if (["summary", "transactions", "journal"].includes(key)) localStorage.setItem(TAB_STORE, key);
      setTimeout(queueRender, 0);
      return;
    }

    const resetPart = event.target?.closest?.("[data-demo-reset]");
    if (resetPart) return;

    const accountTarget = event.target?.closest?.(".top-account-switch [data-account-id],.account-row[data-account-id]");
    if (accountTarget) {
      const id = Number(accountTarget.getAttribute("data-account-id") || 0);
      if (id && id !== selectedManagedId) {
        event.preventDefault();
        event.stopImmediatePropagation();
        switchAccount(id);
      }
    }
  }, true);

  window.addEventListener("derivadmin:direct-market-tick", (event) => {
    const detail = event.detail || {};
    evaluateLiveTick(String(detail.symbol || "").toUpperCase(), detail.tick || {}, Boolean(detail.hydrated));
  });

  window.addEventListener("derivadmin:direct-balance", (event) => {
    const detail = event.detail || {};
    selectedBalance = detail.balance;
    selectedCurrency = String(detail.currency || selectedCurrency || "USD").toUpperCase();
    const selected = selectedAccount();
    if (selected) {
      selected.balance = detail.balance;
      selected.currency = selectedCurrency;
      const providerId = String(detail.loginid || "").trim();
      if (providerId) selected.account_id = providerId;
    }
    renderAccountUi();
  });

  window.addEventListener("derivadmin:demo-balance-reset", (event) => {
    const detail = event.detail || {};
    selectedBalance = detail.balance ?? 10000;
    selectedCurrency = String(detail.currency || "USD").toUpperCase();
    const selected = selectedAccount();
    if (selected) {
      selected.balance = selectedBalance;
      selected.currency = selectedCurrency;
      if (detail.account_id) selected.account_id = String(detail.account_id);
    }
    renderAccountUi();
  });

  window.addEventListener("derivadmin:direct-trade", (event) => {
    const row = event.detail || {};
    if (row.state === "OPEN") lastTradeState = `PURCHASED · ${row.symbol || ""} · contract ${String(row.contract_id || "").slice(-8)}`;
    else if (row.state === "SETTLED") lastTradeState = `SETTLED ${String(row.outcome || "")} · profit ${row.profit ?? 0}`;
    else if (row.mode === "virtual") lastTradeState = `VIRTUAL ${String(row.outcome || "")} · ${row.symbol || ""}`;
    queueRender();
  });

  window.addEventListener("derivadmin:direct-clear", () => {
    lastTradeState = "";
    marketResults.clear();
    queueRender();
  });
  window.addEventListener("derivadmin:direct-reset-all", () => {
    lastTradeState = "";
    marketResults.clear();
    queueRender();
  });

  const observer = new MutationObserver(queueRender);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  const style = document.createElement("style");
  style.id = "direct-runtime-ux-v2-style";
  style.textContent = `
    .topbar{z-index:12000!important}.topbar-actions,.top-account-switch{position:relative;z-index:12010!important}.top-account-switch .account-dropdown{z-index:12050!important}.global-run-panel{z-index:7000!important}
    .top-account-switch strong,.account-dropdown-row strong,.account-dropdown-row small,.account-row small,.account-money b,.balance-pill b{max-width:none!important;overflow:visible!important;text-overflow:clip!important;white-space:nowrap!important}
    .account-dropdown{min-width:min(440px,calc(100vw - 24px))!important}.account-dropdown-row{grid-template-columns:auto minmax(0,1fr) auto!important}.account-dropdown-row>span:nth-child(2){min-width:0}.account-dropdown-row small{font-size:9px!important}.account-dropdown-row em{display:flex!important;flex-direction:column!important;align-items:flex-end!important;gap:3px!important;font-style:normal!important}.direct-demo-balance{font-size:10px;color:#e6f5ff;font-weight:800;white-space:nowrap}.direct-demo-reset{font-size:8px;color:#58dcff;text-decoration:underline;text-underline-offset:2px;cursor:pointer}.direct-account-symbol{width:30px;height:30px;border-radius:10px;display:grid;place-items:center;background:#0d2944;color:#5edcff;font-weight:900}
    .direct-bot-state-pill{display:flex;align-items:center;justify-content:center;gap:7px;margin:0 10px 7px;padding:6px 9px;border-radius:10px;font-size:9px;font-weight:800;border:1px solid rgba(127,170,204,.13);background:rgba(8,20,35,.86);color:#8499ad}.direct-bot-state-pill i{width:7px;height:7px;border-radius:50%;background:#65788a}.direct-bot-state-pill.running{color:#95f1c8;border-color:rgba(52,230,161,.2);background:rgba(14,58,47,.35)}.direct-bot-state-pill.running i{background:#34e6a1;box-shadow:0 0 12px rgba(52,230,161,.7)}.direct-bot-state-pill.server{color:#ffd88b;border-color:rgba(255,204,102,.2);background:rgba(73,53,14,.35)}.direct-bot-state-pill.server i{background:#ffcc66}
    .global-run-panel [data-run-execution-toggle]{display:none!important}.global-run-panel .run-panel-bar{grid-template-columns:1fr!important}.global-run-panel .run-panel-run{width:100%!important}
    .direct-loaded-strategy-badge{margin:0 12px 8px;padding:8px 10px;border-radius:11px;border:1px solid rgba(70,202,255,.13);background:rgba(7,24,43,.8);display:grid;grid-template-columns:auto 1fr auto;gap:4px 8px;align-items:center}.direct-loaded-strategy-badge span{font-size:7px;text-transform:uppercase;letter-spacing:.12em;color:#5f839e}.direct-loaded-strategy-badge b{font-size:9px;color:#dff5ff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.direct-loaded-strategy-badge small{font-size:8px;color:#53d9ff}
    .direct-hide-legacy-journal{display:none!important}.direct-strategy-checker{border:1px solid rgba(87,192,255,.15);background:linear-gradient(155deg,rgba(8,27,49,.94),rgba(5,17,31,.94));border-radius:15px;padding:12px;display:flex;flex-direction:column;gap:9px}.direct-strategy-head>span{display:block;font-size:7px;letter-spacing:.14em;color:#58a6cf;font-weight:900}.direct-strategy-head>b{display:block;margin-top:4px;font-size:12px;color:#f1f9ff}.direct-strategy-head>small{display:block;margin-top:3px;font-size:8px;color:#7791a7}.direct-strategy-result{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:8px 9px;border-radius:10px;border:1px solid rgba(121,160,193,.1);background:rgba(255,255,255,.025)}.direct-strategy-result>b{font-size:9px}.direct-strategy-result>span{font-size:7px;color:#7890a4}.direct-strategy-result.met{border-color:rgba(52,230,161,.22);background:rgba(52,230,161,.07)}.direct-strategy-result.met>b{color:#4bf0ad}.direct-strategy-result.not-met>b{color:#72dfff}.direct-strategy-result.stopped>b{color:#8092a4}.direct-condition-list{display:flex;flex-direction:column;gap:5px}.direct-condition-row{display:grid;grid-template-columns:20px 1fr auto;gap:7px;align-items:center;padding:6px 7px;border-radius:9px;background:rgba(255,255,255,.025)}.direct-condition-row>span{width:18px;height:18px;border-radius:6px;display:grid;place-items:center;background:#0c2945;color:#62dcff;font-size:7px;font-weight:900}.direct-condition-row>b{font-size:8px;font-weight:700;color:#bcd0df}.direct-condition-row>em{font-size:7px;font-style:normal;font-weight:900}.direct-condition-row>em.met{color:#3ee8a5}.direct-condition-row>em.not-met{color:#ffbd73}.direct-market-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px}.direct-market-chip{padding:5px 3px;border-radius:7px;text-align:center;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.035)}.direct-market-chip b{display:block;font-size:6px;color:#9bb0c1}.direct-market-chip em{display:block;margin-top:2px;font-size:5px;font-style:normal;color:#70879b}.direct-market-chip.met{border-color:rgba(52,230,161,.18)}.direct-market-chip.met em{color:#48e7a9}.direct-last-execution{font-size:7px;padding:6px 8px;border-radius:8px;background:rgba(68,124,255,.08);color:#86baff}.direct-strategy-checker.compact{margin-bottom:8px;padding:9px}.direct-strategy-checker.compact .direct-strategy-head>b{font-size:10px}.direct-strategy-checker.compact .direct-strategy-result{padding:6px 8px}
    @media(max-width:620px){.account-dropdown{right:-4px!important;left:auto!important;max-width:calc(100vw - 18px)!important}.direct-market-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.direct-loaded-strategy-badge{grid-template-columns:1fr auto}.direct-loaded-strategy-badge span{grid-column:1/-1}.direct-strategy-result{align-items:flex-start;flex-direction:column}.direct-strategy-result>span{white-space:normal}}
  `;
  document.head.appendChild(style);

  if (!localStorage.getItem(TAB_STORE)) localStorage.setItem(TAB_STORE, "transactions");
  refreshAccounts();
  setInterval(syncRunState, 350);
  queueRender();

  window.DERIVADMIN_DIRECT_RUNTIME_UX_V2 = Object.freeze({
    version: "20260818-runtime-ux-v2",
    refresh_accounts: refreshAccounts,
    state: () => ({ selected_managed_id: selectedManagedId, latest_live: latestLive, accounts: Array.from(accounts.values()) }),
  });
})();
