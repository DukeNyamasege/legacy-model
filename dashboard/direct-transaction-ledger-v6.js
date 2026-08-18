(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V7__) return;
  window.__DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V7__ = true;

  /*
   * DIRECT TRANSACTION + KPI AUTHORITY
   *
   * Browser-direct execution is deliberately ahead of the VPS reporting ledger.
   * While a direct journal exists for the selected account, this file owns BOTH
   * the visible Transactions rows and the six Run summary KPIs from the same
   * contract snapshot. That removes the historic one-run lag and the visual
   * appear/disappear race between the shell refresh and the direct ledger.
   *
   * No timer reinserts rows. A narrow MutationObserver repairs only a shell DOM
   * replacement and is disconnected while this authority writes its own DOM.
   */

  const JOURNAL_PREFIX = "derivadmin-direct-journal-v1:";
  let observer = null;
  let observedPanel = null;
  let applying = false;
  let renderQueued = false;
  let lastSignature = "";

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function selectedManagedId() {
    try {
      const id = Number(window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.state?.()?.selected_managed_id || 0);
      if (id > 0) return String(id);
    } catch (_) {}
    try {
      const snapshot = window.DERIVADMIN_DIRECT_EXECUTION_V1?.state?.() || {};
      const account = snapshot.account || {};
      const id = Number(account.managed_account_id || account.id || 0);
      if (id > 0) return String(id);
    } catch (_) {}
    return "";
  }

  function journalRows() {
    const selected = selectedManagedId();
    const keys = [];
    if (selected) keys.push(`${JOURNAL_PREFIX}${selected}`);
    if (!keys.length) {
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        if (key?.startsWith(JOURNAL_PREFIX)) keys.push(key);
      }
    }
    for (const key of keys) {
      try {
        const rows = JSON.parse(localStorage.getItem(key) || "[]");
        if (Array.isArray(rows) && rows.length) return rows;
      } catch (_) {}
    }
    return [];
  }

  function contracts() {
    const map = new Map();
    for (const row of journalRows()) {
      if (String(row?.mode || "") !== "real") continue;
      const id = String(row?.contract_id || "").trim();
      if (!id) continue;
      const previous = map.get(id) || {};
      map.set(id, {
        ...previous,
        ...row,
        contract_id: id,
        opened_at: previous.opened_at || row.opened_at || row.at || "",
        at: row.at || previous.at || "",
      });
    }
    return Array.from(map.values())
      .sort((a, b) => Date.parse(String(b.opened_at || b.at || 0)) - Date.parse(String(a.opened_at || a.at || 0)))
      .slice(0, 100);
  }

  function marketLabel(symbol) {
    const raw = String(symbol || "").toUpperCase();
    const labels = {
      "1HZ10V": "V10 (1s)", "1HZ25V": "V25 (1s)", "1HZ50V": "V50 (1s)",
      "1HZ75V": "V75 (1s)", "1HZ100V": "V100 (1s)",
      "R_10": "V10", "R_25": "V25", "R_50": "V50", "R_75": "V75", "R_100": "V100",
    };
    return labels[raw] ? `${labels[raw]} · ${raw}` : (raw || "Deriv Options");
  }

  function timeLabel(value) {
    const date = new Date(String(value || ""));
    if (!Number.isFinite(date.getTime())) return "--:--:--";
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  function typeLabel(row) {
    const type = String(row?.trade_type || "TRADE").toUpperCase().replace(/^DIGIT/, "");
    const prediction = row?.prediction;
    if (prediction === null || prediction === undefined || prediction === "") return type;
    return `${type} ${prediction}`;
  }

  function money(value) {
    return `${finite(value, 0).toFixed(2)} USD`;
  }

  function isSettled(row) {
    return String(row?.state || "").toUpperCase() === "SETTLED" || Boolean(row?.outcome);
  }

  function rowMarkup(row) {
    const settled = isSettled(row);
    const profit = finite(row.profit, 0);
    const entry = row.entry_spot ?? row.entry_tick ?? "—";
    const exit = settled ? (row.exit_spot ?? row.exit_tick ?? "—") : "OPEN";
    const pl = settled ? `${profit >= 0 ? "+" : ""}${money(profit)}` : "OPEN";
    return `<div class="transaction-row transaction-row-v6 direct-local-transaction-row-v6 direct-local-transaction-row-v7" data-direct-contract-id="${esc(row.contract_id)}">
      <span class="tx-time-market"><small>${esc(timeLabel(row.opened_at || row.at))}</small><b>${esc(marketLabel(row.symbol))}</b></span>
      <span class="tx-type"><b>${esc(typeLabel(row))}</b></span>
      <span class="tx-spots"><b>${esc(entry)}</b><small>${esc(exit)}</small></span>
      <span class="tx-buy"><b>${esc(money(row.stake))}</b></span>
      <strong class="${settled ? (profit >= 0 ? "positive" : "negative") : "muted"}">${esc(pl)}</strong>
    </div>`;
  }

  function stats(rows) {
    let totalStake = 0;
    let totalPayout = 0;
    let profit = 0;
    let wins = 0;
    let losses = 0;

    for (const row of rows) {
      const stake = Math.max(0, finite(row.stake, 0));
      totalStake += stake; // OPEN positions count immediately as a run + stake.
      if (!isSettled(row)) continue;
      const pnl = finite(row.profit, 0);
      profit += pnl;
      const outcome = String(row.outcome || "").toUpperCase();
      if (outcome === "WIN") {
        wins += 1;
        totalPayout += Math.max(0, finite(row.payout, stake + pnl));
      } else if (outcome === "LOSS") {
        losses += 1;
        totalPayout += Math.max(0, finite(row.payout, 0));
      } else {
        totalPayout += Math.max(0, finite(row.payout, pnl > 0 ? stake + pnl : 0));
      }
    }
    return { totalStake, totalPayout, profit, wins, losses, runs: rows.length };
  }

  function statsMarkup(value) {
    return `<article><b>Total stake</b><span>${esc(money(value.totalStake))}</span></article>
      <article><b>Total payout</b><span>${esc(money(value.totalPayout))}</span></article>
      <article><button type="button" class="run-help">What's this?</button><b>No. of runs</b><span>${value.runs}</span></article>
      <article><b>Contracts lost</b><span>${value.losses}</span></article>
      <article><b>Contracts won</b><span>${value.wins}</span></article>
      <article><b>Total profit/loss</b><span class="${value.profit >= 0 ? "positive" : "negative"}">${value.profit >= 0 ? "+" : ""}${esc(money(value.profit))}</span></article>`;
  }

  function activeTransactions() {
    return String(document.querySelector(".global-run-panel [data-run-tab].active")?.dataset?.runTab || "") === "transactions";
  }

  function signature(rows) {
    return rows.map((row) => [
      row.contract_id,
      row.state,
      row.outcome,
      finite(row.stake, 0),
      finite(row.profit, 0),
      row.entry_spot ?? row.entry_tick ?? "",
      row.exit_spot ?? row.exit_tick ?? "",
      row.at || "",
    ].join("|")).join(";");
  }

  function disconnectObserver() {
    try { observer?.disconnect(); } catch (_) {}
  }

  function connectObserver() {
    const panel = document.querySelector(".global-run-panel");
    if (!panel || !("MutationObserver" in window)) return;
    if (observedPanel === panel && observer) {
      disconnectObserver();
      observer.observe(panel, { childList: true, subtree: true });
      return;
    }
    disconnectObserver();
    observedPanel = panel;
    observer = new MutationObserver(() => {
      if (!applying && journalRows().length) queueRender(true);
    });
    observer.observe(panel, { childList: true, subtree: true });
  }

  function render(force = false) {
    renderQueued = false;
    if (!activeTransactions()) {
      connectObserver();
      return;
    }

    const rows = contracts();
    if (!rows.length) {
      lastSignature = "";
      connectObserver();
      return; // No direct session: allow the normal VPS ledger to own the panel.
    }

    const panel = document.querySelector(".global-run-panel");
    const body = panel?.querySelector(".run-panel-body");
    const summary = panel?.querySelector(".run-panel-stats");
    if (!panel || !body || !summary) return;

    const nextSignature = signature(rows);
    const expectedRows = body.querySelectorAll(".direct-local-transaction-row-v7").length;
    const expectedRuns = String(stats(rows).runs);
    const displayedRuns = String(summary.children?.[2]?.querySelector("span")?.textContent || "").trim();
    if (!force && nextSignature === lastSignature && expectedRows === rows.length && displayedRuns === expectedRuns) {
      connectObserver();
      return;
    }

    applying = true;
    disconnectObserver();
    try {
      lastSignature = nextSignature;
      body.innerHTML = `<div class="transaction-table transaction-table-v6 direct-canonical-table-v7">
        <div class="transaction-head transaction-head-v6"><span>Time / Market</span><span>Type</span><span>Entry / Exit</span><span>Buy price</span><span>Profit / Loss</span></div>
        <div class="transaction-rows">${rows.map(rowMarkup).join("")}</div>
      </div>`;
      summary.innerHTML = statsMarkup(stats(rows));
      panel.dataset.directLedgerAuthority = "v7";
    } finally {
      applying = false;
      connectObserver();
    }
  }

  function queueRender(force = false) {
    if (renderQueued && !force) return;
    renderQueued = true;
    requestAnimationFrame(() => render(force));
  }

  window.addEventListener("derivadmin:direct-trade", () => queueRender(true));
  window.addEventListener("derivadmin:direct-clear", () => { lastSignature = ""; queueRender(true); });
  window.addEventListener("derivadmin:direct-reset-all", () => { lastSignature = ""; queueRender(true); });
  window.addEventListener("pageshow", () => queueRender(true));
  document.addEventListener("foa:vps-live", () => queueRender(true));
  document.addEventListener("click", (event) => {
    if (event.target?.closest?.('[data-run-tab="transactions"]')) setTimeout(() => queueRender(true), 0);
  });

  queueRender(true);
  window.DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6 = Object.freeze({
    version: "20260818-direct-transaction-ledger-v7",
    refresh: () => queueRender(true),
    contracts,
    stats: () => stats(contracts()),
  });
})();
