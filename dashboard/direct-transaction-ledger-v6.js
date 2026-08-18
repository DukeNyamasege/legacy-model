(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V8__) return;
  window.__DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V8__ = true;

  /*
   * DIRECT TRANSACTION + KPI AUTHORITY V8
   *
   * The direct Deriv journal is the source of truth for browser-owned live trades.
   * A canonical per-account snapshot is retained so a slower VPS shell refresh can
   * never temporarily replace confirmed rows with "No transactions yet".  The
   * same contract snapshot owns BOTH Transactions and the six summary KPIs.
   *
   * There is no polling/reinsertion timer. DOM repairs happen synchronously from a
   * narrow MutationObserver before the browser gets another paint opportunity.
   * Only an explicit Reset/Clear event deletes the retained snapshot.
   */

  const JOURNAL_PREFIX = "derivadmin-direct-journal-v1:";
  const SNAPSHOT_PREFIX = "derivadmin-direct-ledger-snapshot-v8:";
  let observer = null;
  let observedPanel = null;
  let rootObserver = null;
  let applying = false;
  let lastSignature = "";
  let lastAccountKey = "";
  const memorySnapshots = new Map();

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

  function accountKey() {
    const selected = selectedManagedId();
    if (selected) lastAccountKey = selected;
    return selected || lastAccountKey;
  }

  function readJson(key, fallback) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "null");
      return value == null ? fallback : value;
    } catch (_) {
      return fallback;
    }
  }

  function writeJson(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {}
  }

  function rawJournalRows(key) {
    if (!key) return [];
    const rows = readJson(`${JOURNAL_PREFIX}${key}`, []);
    return Array.isArray(rows) ? rows : [];
  }

  function retainedRows(key) {
    if (!key) return [];
    const memory = memorySnapshots.get(key);
    if (Array.isArray(memory) && memory.length) return memory;
    const rows = readJson(`${SNAPSHOT_PREFIX}${key}`, []);
    if (Array.isArray(rows) && rows.length) {
      memorySnapshots.set(key, rows);
      return rows;
    }
    return [];
  }

  function rememberRows(key, rows) {
    if (!key || !Array.isArray(rows) || !rows.length) return;
    const copy = rows.slice(-500);
    memorySnapshots.set(key, copy);
    writeJson(`${SNAPSHOT_PREFIX}${key}`, copy);
  }

  function journalRows() {
    const key = accountKey();
    if (!key) return [];
    const live = rawJournalRows(key);
    if (live.length) {
      rememberRows(key, live);
      return live;
    }
    return retainedRows(key);
  }

  function clearSnapshot() {
    const key = accountKey();
    if (key) {
      memorySnapshots.delete(key);
      try { localStorage.removeItem(`${SNAPSHOT_PREFIX}${key}`); } catch (_) {}
    }
    lastSignature = "";
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
      "1HZ10V": "V10 1S",
      "1HZ25V": "V25 1S",
      "1HZ50V": "V50 1S",
      "1HZ75V": "V75 1S",
      "1HZ100V": "V100 1S",
      "R_10": "V10",
      "R_25": "V25",
      "R_50": "V50",
      "R_75": "V75",
      "R_100": "V100",
    };
    return labels[raw] || "Deriv";
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
    return `<div class="transaction-row transaction-row-v6 direct-local-transaction-row-v6 direct-local-transaction-row-v8" data-direct-contract-id="${esc(row.contract_id)}">
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
      totalStake += stake;
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
      if (!applying && retainedRows(accountKey()).length && activeTransactions()) render(true);
    });
    observer.observe(panel, { childList: true, subtree: true });
  }

  function connectRootObserver() {
    const root = document.getElementById("derivadmin-root");
    if (!root || !("MutationObserver" in window) || rootObserver) return;
    rootObserver = new MutationObserver(() => {
      if (applying) return;
      const panel = document.querySelector(".global-run-panel");
      if (panel !== observedPanel) {
        connectObserver();
        if (retainedRows(accountKey()).length && activeTransactions()) render(true);
      }
    });
    rootObserver.observe(root, { childList: true, subtree: false });
  }

  function render(force = false) {
    if (!activeTransactions()) {
      connectObserver();
      return;
    }

    const rows = contracts();
    if (!rows.length) {
      lastSignature = "";
      connectObserver();
      return;
    }

    const panel = document.querySelector(".global-run-panel");
    const body = panel?.querySelector(".run-panel-body");
    const summary = panel?.querySelector(".run-panel-stats");
    if (!panel || !body || !summary) return;

    const nextSignature = signature(rows);
    const values = stats(rows);
    const expectedRows = body.querySelectorAll(".direct-local-transaction-row-v8").length;
    const displayedRuns = String(summary.children?.[2]?.querySelector("span")?.textContent || "").trim();
    const canonicalPresent = Boolean(body.querySelector(".direct-canonical-table-v8"));
    if (!force && canonicalPresent && nextSignature === lastSignature && expectedRows === rows.length && displayedRuns === String(values.runs)) {
      connectObserver();
      return;
    }

    applying = true;
    disconnectObserver();
    try {
      lastSignature = nextSignature;
      body.innerHTML = `<div class="transaction-table transaction-table-v6 direct-canonical-table-v8">
        <div class="transaction-head transaction-head-v6"><span>Time / Market</span><span>Type</span><span>Entry / Exit</span><span>Buy price</span><span>Profit / Loss</span></div>
        <div class="transaction-rows">${rows.map(rowMarkup).join("")}</div>
      </div>`;
      summary.innerHTML = statsMarkup(values);
      panel.dataset.directLedgerAuthority = "v8";
    } finally {
      applying = false;
      connectObserver();
    }
  }

  function renderNow() {
    render(true);
  }

  window.addEventListener("derivadmin:direct-trade", renderNow);
  window.addEventListener("derivadmin:direct-clear", () => { clearSnapshot(); renderNow(); });
  window.addEventListener("derivadmin:direct-reset-all", () => { clearSnapshot(); renderNow(); });
  window.addEventListener("pageshow", renderNow);
  document.addEventListener("foa:vps-live", renderNow);
  document.addEventListener("click", (event) => {
    if (event.target?.closest?.('[data-run-tab="transactions"]')) queueMicrotask(renderNow);
  });

  connectRootObserver();
  connectObserver();
  renderNow();

  const api = Object.freeze({
    version: "20260818-direct-transaction-ledger-v8",
    refresh: renderNow,
    contracts,
    stats: () => stats(contracts()),
    clearSnapshot,
  });
  window.DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6 = api;
  window.DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V8 = api;
})();
