(() => {
  "use strict";

  if (window.FOA_VIRTUAL_KPI_NEUTRALITY) return;
  window.FOA_VIRTUAL_KPI_NEUTRALITY = "20260815-2";

  const RESET_PREFIX = "foa-trade-session-reset-v1";

  function storageGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }

  function isVirtual(row) {
    if (!row || typeof row !== "object") return false;
    if (row.is_virtual === true) return true;
    if (String(row.trade_kind || "").toLowerCase() === "virtual") return true;
    if (String(row.type || "").toUpperCase().includes("VIRTUAL")) return true;
    if (String(row.contract_type || "").toUpperCase().startsWith("VIRTUAL HOOK")) return true;
    return false;
  }

  function rowTime(row) {
    const raw = row?.purchase_time || row?.provider_purchase_time || row?.created_at || row?.settlement_time || "";
    const parsed = Date.parse(raw);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function resetTime(me) {
    if (!me) return 0;
    const type = String(me.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
    const account = String(me.account_id_masked || me.account_id || "public");
    const raw = storageGet(`${RESET_PREFIX}:${type}:${account}`);
    if (!raw) return 0;
    const parsed = Date.parse(raw);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function money(value, currency = "USD") {
    const amount = Number(value || 0);
    const unit = String(currency || "USD").toUpperCase();
    const prefix = unit === "USD" ? "$" : `${unit} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function count(value) {
    return Math.max(0, Math.round(Number(value || 0))).toLocaleString();
  }

  function finiteMetric(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function summaryMetrics(payload) {
    const summary = payload?.summary;
    if (!summary || typeof summary !== "object") return null;

    const total = finiteMetric(summary.total);
    const wins = finiteMetric(summary.wins);
    const losses = finiteMetric(summary.losses);
    const profit = finiteMetric(summary.profit);
    if ([total, wins, losses, profit].some((value) => value === null)) return null;

    /*
     * IMPORTANT: `payload.trades` is intentionally a bounded Recent Activity
     * window (currently 100 rows in the realtime snapshot). `payload.summary`
     * comes from SQL aggregate COUNT/SUM queries over the complete applicable
     * trade period and is therefore the only authority for Runs/Wins/Losses/P&L.
     * Never derive KPI totals from rows.length: 101, 1,000 or 10,000 actual runs
     * must remain visible even when only a small recent-history window is sent.
     * Virtual Hook observations live outside the actual Trade aggregate, so they
     * remain visible in history while staying financially KPI-neutral.
     */
    return { total, wins, losses, profit };
  }

  function rowFallbackMetrics(me, payload) {
    const allRows = Array.isArray(payload?.trades) ? payload.trades : [];
    const cutoff = resetTime(me);
    const rows = allRows.filter((row) => {
      if (isVirtual(row)) return false;
      if (!cutoff) return true;
      return rowTime(row) >= cutoff;
    });
    const wins = rows.filter((row) => String(row.outcome || "").toUpperCase() === "WIN").length;
    const losses = rows.filter((row) => String(row.outcome || "").toUpperCase() === "LOSS").length;
    const profit = rows.reduce((sum, row) => sum + Number(row.profit || 0), 0);
    return { total: rows.length, wins, losses, profit };
  }

  function actualMetrics() {
    const cache = window.FOA_NETLIFY_LIVE_CACHE;
    const me = cache?.me;
    const payload = cache?.trades;
    if (!me?.authenticated || !payload) return null;

    // Server aggregate is intentionally independent of the bounded row window.
    const metrics = summaryMetrics(payload) || rowFallbackMetrics(me, payload);
    return {
      ...metrics,
      currency: me.currency || "USD",
    };
  }

  function setText(node, value) {
    if (!node) return;
    const text = String(value);
    if (node.textContent !== text) node.textContent = text;
  }

  function updateBuilderStats(metrics) {
    const values = new Map([
      ["Number of Runs", count(metrics.total)],
      ["Runs", count(metrics.total)],
      ["Today's P/L", money(metrics.profit, metrics.currency)],
      ["P/L", money(metrics.profit, metrics.currency)],
      ["Wins", count(metrics.wins)],
      ["Losses", count(metrics.losses)],
    ]);
    document.querySelectorAll(".builder-stats .builder-stat").forEach((card) => {
      const label = String(card.querySelector("span")?.textContent || "").trim();
      if (!values.has(label)) return;
      const strong = card.querySelector("strong");
      setText(strong, values.get(label));
      if (label === "Today's P/L" || label === "P/L") {
        card.classList.toggle("loss", metrics.profit < 0);
        card.classList.toggle("win", metrics.profit >= 0);
      }
    });
  }

  function updateCompactStats(metrics) {
    const values = {
      runs: count(metrics.total),
      profit: money(metrics.profit, metrics.currency),
      wins: count(metrics.wins),
      losses: count(metrics.losses),
    };
    Object.entries(values).forEach(([key, value]) => {
      const cell = document.querySelector(`[data-compact-stat="${key}"]`);
      if (!cell) return;
      setText(cell.querySelector("strong"), value);
      if (key === "profit") {
        cell.dataset.tone = metrics.profit < 0 ? "loss" : metrics.profit > 0 ? "win" : "neutral";
      }
    });
  }

  function refresh() {
    const metrics = actualMetrics();
    if (!metrics) return;
    updateBuilderStats(metrics);
    updateCompactStats(metrics);
  }

  window.setInterval(refresh, 750);
  window.addEventListener("pageshow", refresh);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", refresh, { once: true })
    : refresh();
})();
