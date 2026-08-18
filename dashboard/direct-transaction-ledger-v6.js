(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6__) return;
  window.__DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6__ = true;

  const JOURNAL_PREFIX = "derivadmin-direct-journal-v1:";
  let lastSignature = "";
  let timer = null;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function selectedManagedId() {
    try {
      const id = Number(window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.state?.()?.selected_managed_id || 0);
      if (id > 0) return String(id);
    } catch (_) {}
    return "";
  }

  function journalRows() {
    const selected = selectedManagedId();
    const preferred = selected ? `${JOURNAL_PREFIX}${selected}` : "";
    const keys = [];
    if (preferred) keys.push(preferred);
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key?.startsWith(JOURNAL_PREFIX) && !keys.includes(key)) keys.push(key);
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
      const existing = map.get(id) || {};
      map.set(id, {
        ...existing,
        ...row,
        contract_id: id,
        opened_at: existing.opened_at || row.at || "",
        at: row.at || existing.at || "",
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
    const number = Number(value || 0);
    return Number.isFinite(number) ? `${number.toFixed(2)} USD` : "0.00 USD";
  }

  function rowMarkup(row) {
    const settled = String(row.state || "").toUpperCase() === "SETTLED" || Boolean(row.outcome);
    const profit = Number(row.profit || 0);
    const entry = row.entry_spot ?? row.entry_tick ?? "—";
    const exit = settled ? (row.exit_spot ?? row.exit_tick ?? "—") : "OPEN";
    const pl = settled ? `${profit >= 0 ? "+" : ""}${money(profit)}` : "OPEN";
    return `<div class="transaction-row transaction-row-v6 direct-local-transaction-row-v6" data-direct-contract-id="${esc(row.contract_id)}">
      <span class="tx-time-market"><small>${esc(timeLabel(row.opened_at || row.at))}</small><b>${esc(marketLabel(row.symbol))}</b></span>
      <span class="tx-type"><b>${esc(typeLabel(row))}</b></span>
      <span class="tx-spots"><b>${esc(entry)}</b><small>${esc(exit)}</small></span>
      <span class="tx-buy"><b>${esc(money(row.stake))}</b></span>
      <strong class="${settled ? (profit >= 0 ? "positive" : "negative") : "muted"}">${esc(pl)}</strong>
    </div>`;
  }

  function activeTransactions() {
    return String(document.querySelector(".global-run-panel [data-run-tab].active")?.dataset?.runTab || "") === "transactions";
  }

  function render() {
    if (!activeTransactions()) return;
    const panel = document.querySelector(".global-run-panel");
    const body = panel?.querySelector(".run-panel-body");
    if (!body) return;

    const rows = contracts();
    const signature = rows.map((row) => [row.contract_id, row.state, row.profit, row.entry_spot, row.exit_spot, row.at].join("|")).join(";");
    const existingCount = body.querySelectorAll(".direct-local-transaction-row-v6").length;
    if (signature === lastSignature && existingCount === rows.length) return;
    lastSignature = signature;

    body.querySelectorAll(".direct-local-transaction-row-v6,.direct-local-only-table-v6").forEach((node) => node.remove());
    if (!rows.length) return;

    let table = body.querySelector(".transaction-table");
    let targetRows = table?.querySelector(".transaction-rows");
    if (!table || !targetRows) {
      body.querySelector(".run-panel-empty")?.remove();
      table = document.createElement("div");
      table.className = "transaction-table transaction-table-v6 direct-local-only-table-v6";
      table.innerHTML = `<div class="transaction-head transaction-head-v6"><span>Time / Market</span><span>Type</span><span>Entry / Exit</span><span>Buy price</span><span>Profit / Loss</span></div><div class="transaction-rows"></div>`;
      body.prepend(table);
      targetRows = table.querySelector(".transaction-rows");
    }

    const fragment = document.createDocumentFragment();
    const holder = document.createElement("div");
    holder.innerHTML = rows.map(rowMarkup).join("");
    while (holder.firstChild) fragment.appendChild(holder.firstChild);
    targetRows.prepend(fragment);
  }

  window.addEventListener("derivadmin:direct-trade", () => setTimeout(render, 0));
  window.addEventListener("derivadmin:direct-clear", () => { lastSignature = ""; setTimeout(render, 0); });
  window.addEventListener("derivadmin:direct-reset-all", () => { lastSignature = ""; setTimeout(render, 0); });
  window.addEventListener("pageshow", () => setTimeout(render, 0));
  document.addEventListener("foa:vps-live", () => setTimeout(render, 0));
  document.addEventListener("click", (event) => {
    if (event.target?.closest?.('[data-run-tab="transactions"]')) setTimeout(render, 0);
  });

  // Reinsert only if another shell refresh replaced the Transactions DOM. The
  // signature check makes this a no-op during ordinary live ticks, preventing the
  // old visual shaking/reflow loop.
  timer = setInterval(() => {
    if (!document.hidden && activeTransactions()) render();
  }, 2000);
  window.addEventListener("pagehide", () => clearInterval(timer), { once: true });

  window.DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6 = Object.freeze({
    version: "20260818-direct-transaction-ledger-v6",
    refresh: render,
  });
})();
