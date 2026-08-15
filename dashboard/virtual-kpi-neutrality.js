(() => {
  "use strict";

  if (window.FOA_VIRTUAL_KPI_NEUTRALITY) return;
  window.FOA_VIRTUAL_KPI_NEUTRALITY = "20260815-3";

  const RESET_PREFIX = "foa-trade-session-reset-v1";
  let scheduled = false;

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

  function payloadCutoffTime(payload) {
    const raw = payload?.history_cleared_at || payload?.session_started_at || "";
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

  function zeroMetrics() {
    return { total: 0, wins: 0, losses: 0, profit: 0 };
  }

  function summaryMetrics(me, payload) {
    const summary = payload?.summary;
    const localCutoff = resetTime(me);

    /*
     * A Clear Trades response is stored locally immediately. During a rolling
     * Netlify/VPS deployment an older realtime snapshot can still arrive with the
     * pre-clear day aggregate. Never repaint that stale aggregate. Hold the KPI
     * cards at zero until the server payload proves that it is using the same (or
     * newer) durable cutoff. This removes the 0 <-> old-total flicker completely.
     */
    if (localCutoff) {
      const serverCutoff = payloadCutoffTime(payload);
      if (!serverCutoff || serverCutoff + 1000 < localCutoff) return zeroMetrics();
    }

    if (!summary || typeof summary !== "object") return null;

    const total = finiteMetric(summary.total);
    const wins = finiteMetric(summary.wins);
    const losses = finiteMetric(summary.losses);
    const profit = finiteMetric(summary.profit);
    if ([total, wins, losses, profit].some((value) => value === null)) return null;

    /*
     * IMPORTANT: `payload.trades` is intentionally only Recent Activity. It may
     * contain 8, 50 or 100 rows. `payload.summary` is the unbounded PostgreSQL
     * COUNT/SUM aggregate for the visible post-clear session and is the ONLY KPI
     * authority. Never derive Runs/Wins/Losses/P&L from rows.length: 101, 1,000 or
     * 10,000 actual runs must remain visible. Virtual observations live outside
     * the actual Trade aggregate and therefore stay financially KPI-neutral.
     */
    return { total, wins, losses, profit };
  }

  function rowFallbackMetrics(me, payload) {
    const allRows = Array.isArray(payload?.trades) ? payload.trades : [];
    const rows = allRows.filter((row) => !isVirtual(row));
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

    const localCutoff = resetTime(me);
    const metrics = summaryMetrics(me, payload)
      || (localCutoff ? zeroMetrics() : rowFallbackMetrics(me, payload));
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
    scheduled = false;
    const metrics = actualMetrics();
    if (!metrics) return;
    updateBuilderStats(metrics);
    updateCompactStats(metrics);
  }

  function scheduleRefresh() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(refresh);
  }

  /*
   * dashboard-v2 still rebuilds the shell during its slow 15-second compatibility
   * refresh. Re-apply the single KPI authority in the same render turn, before a
   * user can see the row-derived placeholders. setText() is change-aware, so this
   * observer settles after one correction instead of creating a mutation loop.
   */
  new MutationObserver((mutations) => {
    if (!mutations.some((item) => item.type === "childList" || item.type === "characterData")) return;
    scheduleRefresh();
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });

  window.setInterval(scheduleRefresh, 750);
  window.addEventListener("pageshow", scheduleRefresh);
  window.addEventListener("foa:global-trades-cleared", scheduleRefresh);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleRefresh();
  });
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleRefresh, { once: true })
    : scheduleRefresh();
})();
