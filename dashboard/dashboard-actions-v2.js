(() => {
  "use strict";

  const VERSION = "20260801-6";
  let lastEnhance = 0;
  let lastTradeRender = 0;
  let tradeRefreshInFlight = false;
  let lastTradePayload = null;

  function ensureStyles() {
    if (document.getElementById("foa-final-action-styles")) return;
    const style = document.createElement("style");
    style.id = "foa-final-action-styles";
    style.textContent = `
      .foa-action-loader{position:fixed;inset:0;z-index:99999;display:none;place-items:center;background:rgba(2,6,23,.38);backdrop-filter:blur(8px)}
      .foa-action-loader.show{display:grid}.foa-action-loader-box{min-width:240px;max-width:84vw;padding:22px 24px;border-radius:18px;background:linear-gradient(145deg,#0f1a2d,#132239);border:1px solid rgba(148,163,184,.22);box-shadow:0 24px 70px rgba(0,0,0,.32);color:#f8fafc;text-align:center}
      .foa-action-loader-box i{display:block;width:34px;height:34px;margin:0 auto 13px;border:3px solid rgba(255,255,255,.15);border-top-color:#2f73ff;border-radius:50%;animation:foa-final-spin .75s linear infinite}.foa-action-loader-box strong{display:block;font-size:16px}.foa-action-loader-box small{display:block;color:#aab6c8;margin-top:6px}
      @keyframes foa-final-spin{to{transform:rotate(360deg)}}
      .foa-reset-actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:flex-end}.foa-reset-actions button{border:1px solid var(--line,rgba(148,163,184,.22));border-radius:10px;padding:9px 12px;background:rgba(255,255,255,.04);color:var(--text,#f8fafc);font-weight:760;cursor:pointer}.foa-reset-actions button.danger{background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.35);color:#ff9a9a}
      .foa-final-trade-table{margin-top:14px}.foa-final-trade-head,.foa-final-trade-row{display:grid;grid-template-columns:1fr 1.35fr .75fr .75fr .8fr 1fr;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid var(--line,rgba(148,163,184,.18))}.foa-final-trade-head{color:var(--muted,#aab6c8);font-size:13px}.foa-final-trade-row{font-size:14px}.foa-final-trade-row strong{text-align:right}.foa-final-trade-row .win{color:var(--green,#41d75d)}.foa-final-trade-row .loss{color:var(--red,#ef4444)}.foa-exit-digit{display:inline-flex;align-items:center;justify-content:center;min-width:30px;height:30px;border-radius:999px;background:rgba(47,115,255,.15);color:#83adff;font-weight:900}.foa-exit-cell{display:flex;align-items:center;gap:8px;color:var(--muted,#aab6c8)}
      .foa-final-recent .foa-final-trade-head,.foa-final-recent .foa-final-trade-row{grid-template-columns:1fr 1.4fr .8fr .85fr 1fr}.foa-final-recent .payout-col{display:none}.foa-final-empty{padding:24px 0;color:var(--muted,#aab6c8)}
      @media(max-width:760px){.foa-reset-actions{width:100%;justify-content:stretch}.foa-reset-actions button{flex:1}.foa-final-trade-head{display:none}.foa-final-trade-row,.foa-final-recent .foa-final-trade-row{grid-template-columns:1fr auto;gap:8px;padding:14px 0}.foa-final-trade-row span,.foa-final-trade-row strong{display:block;text-align:right}.foa-final-trade-row span:nth-child(1),.foa-final-trade-row span:nth-child(2){text-align:left}.foa-final-trade-row span:nth-child(n+3)::before,.foa-final-trade-row strong::before{display:block;color:var(--muted,#aab6c8);font-size:11px;font-weight:600}.foa-final-trade-row span:nth-child(3)::before{content:'Stake'}.foa-final-trade-row span:nth-child(4)::before{content:'Payout'}.foa-final-trade-row span:nth-child(5)::before{content:'Exit'}.foa-final-trade-row strong::before{content:'Result'}}
    `;
    document.head.appendChild(style);
  }

  function loader(text = "Loading…") {
    ensureStyles();
    let el = document.getElementById("foa-action-loader");
    if (!el) {
      el = document.createElement("div");
      el.id = "foa-action-loader";
      el.className = "foa-action-loader";
      el.innerHTML = `<div class="foa-action-loader-box"><i></i><strong></strong><small>Please wait</small></div>`;
      document.body.appendChild(el);
    }
    el.querySelector("strong").textContent = text;
    el.classList.add("show");
    clearTimeout(loader._timer);
    loader._timer = setTimeout(() => el.classList.remove("show"), 1800);
  }

  function hideLoaderSoon() {
    const el = document.getElementById("foa-action-loader");
    if (!el) return;
    clearTimeout(loader._timer);
    loader._timer = setTimeout(() => el.classList.remove("show"), 250);
  }

  function money(value) {
    const amount = Number(value || 0);
    return `${amount < 0 ? "-" : ""}$${Math.abs(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function timeOf(row) {
    const raw = row.purchase_time || row.provider_purchase_time || row.settlement_time || row.provider_settlement_time;
    if (!raw) return "—";
    const date = new Date(raw);
    if (!Number.isFinite(date.getTime())) return "—";
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function contract(row) {
    const type = row.contract_type || row.contract || "—";
    const barrier = row.barrier == null || row.barrier === "" ? "" : ` ${row.barrier}`;
    return `${type}${barrier}`;
  }

  function exitDigit(row) {
    if (row.exit_digit !== null && row.exit_digit !== undefined && row.exit_digit !== "") return String(row.exit_digit);
    const spot = row.exit_spot ?? row.exit_tick;
    if (spot === null || spot === undefined || spot === "") return "—";
    const text = String(spot).replace(/[^0-9]/g, "");
    return text ? text[text.length - 1] : "—";
  }

  function exitCell(row) {
    const digit = exitDigit(row);
    const spot = row.exit_spot ?? row.exit_tick;
    return `<span class="foa-exit-cell"><b class="foa-exit-digit">${digit}</b><small>${spot == null ? "—" : Number(spot).toFixed(2)}</small></span>`;
  }

  function result(row) {
    const outcome = String(row.outcome || row.result || "OPEN").toUpperCase();
    const profit = Number(row.profit || row.actual_profit_loss || 0);
    const cls = outcome === "WIN" ? "win" : outcome === "LOSS" ? "loss" : "neutral";
    return `<strong class="${cls}">${outcome}${outcome === "OPEN" ? "" : ` ${money(profit)}`}</strong>`;
  }

  function rowHtml(row, wide = false) {
    return `<div class="foa-final-trade-row">
      <span>${timeOf(row)}</span>
      <span><b>${row.symbol || row.market || "—"}</b><br><small>${contract(row)}</small></span>
      <span>${money(row.buy_price ?? row.stake ?? row.amount ?? 0)}</span>
      ${wide ? `<span class="payout-col">${row.payout == null ? "—" : money(row.payout)}</span>` : `<span class="payout-col">${row.payout == null ? "—" : money(row.payout)}</span>`}
      ${exitCell(row)}
      ${result(row)}
    </div>`;
  }

  async function api(path, body) {
    const res = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.message || `Request failed (${res.status})`);
    return data;
  }

  async function refreshTrades(force = false) {
    if (tradeRefreshInFlight) return lastTradePayload;
    if (!force && Date.now() - lastTradeRender < 2500) return lastTradePayload;
    if (!document.querySelector(".foa-trades-card,.foa-all-trades")) return lastTradePayload;
    tradeRefreshInFlight = true;
    try {
      lastTradePayload = await api(`/me/trades/today?ts=${Date.now()}`);
      lastTradeRender = Date.now();
      renderTrades(lastTradePayload);
    } catch (_err) {
      // Logged-out users and public dashboard do not have this endpoint.
    } finally {
      tradeRefreshInFlight = false;
      hideLoaderSoon();
    }
    return lastTradePayload;
  }

  function resetButtons() {
    return `<div class="foa-reset-actions" data-final-reset-actions>
      <button type="button" data-clear-scope="today">Clear Today</button>
      <button type="button" class="danger" data-clear-scope="all">Clear All</button>
    </div>`;
  }

  function renderTrades(payload) {
    if (!payload || !Array.isArray(payload.trades)) return;
    const trades = payload.trades;
    document.querySelectorAll(".foa-trades-card,.foa-all-trades").forEach(card => {
      const isFull = card.classList.contains("foa-all-trades");
      const limit = isFull ? 5000 : 25;
      const rows = trades.slice(0, limit);
      const head = card.querySelector(".foa-card-head");
      if (head && !head.querySelector("[data-final-reset-actions]")) {
        head.insertAdjacentHTML("beforeend", resetButtons());
      }
      const table = `<div class="foa-final-trade-table ${isFull ? "" : "foa-final-recent"}">
        <div class="foa-final-trade-head">
          <span>Time</span><span>Market / Contract</span><span>Stake</span><span class="payout-col">Payout</span><span>Exit digit</span><span>Result</span>
        </div>
        ${rows.length ? rows.map(row => rowHtml(row, isFull)).join("") : `<div class="foa-final-empty">No trades have been taken on this account today.</div>`}
      </div>`;
      const oldHead = card.querySelector(".foa-trade-head,.foa-trade-head-wide");
      if (oldHead) {
        let node = oldHead;
        while (node) {
          const next = node.nextElementSibling;
          node.remove();
          node = next;
        }
      }
      card.querySelector(".foa-final-trade-table")?.remove();
      card.insertAdjacentHTML("beforeend", table);
    });
  }

  async function clearTrades(scope) {
    const label = scope === "all" ? "ALL personal trade history for this selected account" : "today's personal trades for this selected account";
    if (!confirm(`Clear ${label}? This resets this account's recovery state. It does not delete registered traders or credentials.`)) return;
    loader(scope === "all" ? "Clearing all personal trades…" : "Clearing today's trades…");
    try {
      const data = await api("/me/clear-trades", { scope });
      alert(data.message || "Trades cleared.");
      await refreshTrades(true);
      location.reload();
    } catch (err) {
      alert(String(err.message || err));
      hideLoaderSoon();
    }
  }

  function enhance() {
    if (Date.now() - lastEnhance < 350) return;
    lastEnhance = Date.now();
    ensureStyles();
    refreshTrades(false);
    const app = document.getElementById("foa-simple-app");
    if (app) app.dataset.finalUiPatch = VERSION;
  }

  document.addEventListener("click", event => {
    const clear = event.target.closest("[data-clear-scope]");
    if (clear) {
      event.preventDefault();
      clearTrades(clear.dataset.clearScope || "today");
      return;
    }
    const control = event.target.closest("[data-control]");
    if (control) {
      const action = control.dataset.control || "action";
      const text = action === "start" ? "Starting auto trading…" : action === "stop" ? "Stopping completely…" : action === "pause" ? "Pausing and preserving recovery…" : "Resuming from preserved state…";
      loader(text);
      setTimeout(() => refreshTrades(true), 1200);
      return;
    }
    const view = event.target.closest("[data-view]");
    if (view) {
      loader(`Opening ${view.dataset.view || "page"}…`);
      setTimeout(enhance, 500);
      return;
    }
    if (event.target.closest("[data-mode]")) {
      loader("Switching account mode…");
      setTimeout(() => refreshTrades(true), 1000);
      return;
    }
    if (event.target.closest("#logout")) loader("Logging out…");
  }, true);

  document.addEventListener("submit", event => {
    if (event.target.closest("#settings-form")) loader("Saving trading settings…");
    if (event.target.closest("#token-form")) loader("Saving trading credential…");
  }, true);

  const observer = new MutationObserver(() => enhance());
  document.addEventListener("DOMContentLoaded", () => {
    ensureStyles();
    observer.observe(document.body, { childList: true, subtree: true });
    enhance();
    setInterval(() => refreshTrades(true), 10000);
  }, { once: true });

  if (document.readyState !== "loading") {
    ensureStyles();
    observer.observe(document.body, { childList: true, subtree: true });
    enhance();
    setInterval(() => refreshTrades(true), 10000);
  }
})();
