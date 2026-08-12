(() => {
  "use strict";

  const MOBILE_QUERY = "(max-width: 760px)";
  const TRADE_RESET_PREFIX = "foa-trade-session-reset-v1";
  const STAT_DEFS = [
    ["balance", "Balance"],
    ["runs", "Runs"],
    ["profit", "P/L"],
    ["wins", "Wins"],
    ["losses", "Losses"],
  ];
  let scheduled = false;

  function isMobile() {
    return window.matchMedia(MOBILE_QUERY).matches;
  }

  function storageGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }

  function accountType(me) {
    return String(me?.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
  }

  function accountMask(me) {
    return String(me?.account_id_masked || me?.account_id || "public");
  }

  function resetTime(me) {
    if (!me) return 0;
    const raw = storageGet(`${TRADE_RESET_PREFIX}:${accountType(me)}:${accountMask(me)}`);
    if (!raw) return 0;
    const value = Date.parse(raw);
    return Number.isFinite(value) ? value : 0;
  }

  function rowTime(row) {
    const raw = row?.purchase_time || row?.provider_purchase_time || row?.created_at || row?.settlement_time || "";
    const value = Date.parse(raw);
    return Number.isFinite(value) ? value : 0;
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
    const amount = Number(value || 0);
    return Number.isFinite(amount) ? Math.max(0, Math.round(amount)).toLocaleString() : "0";
  }

  function readExistingStat(labels) {
    const wanted = new Set(labels);
    for (const card of document.querySelectorAll(".builder-stats .builder-stat")) {
      const label = String(card.querySelector("span")?.textContent || "").trim();
      if (!wanted.has(label)) continue;
      const value = String(card.querySelector("strong")?.textContent || "").trim();
      if (value) return value;
    }
    return "";
  }

  function liveMetrics() {
    const cache = window.FOA_NETLIFY_LIVE_CACHE;
    const me = cache?.me;
    const trades = cache?.trades;
    const authenticated = Boolean(me?.authenticated || document.querySelector(".builder-header #logout"));

    if (!me?.authenticated) {
      const balance = readExistingStat(["Balance"]);
      const runs = readExistingStat(["Number of Runs", "Runs"]);
      const profit = readExistingStat(["Today's P/L", "P/L"]);
      const wins = readExistingStat(["Wins"]);
      const losses = readExistingStat(["Losses"]);
      return {
        authenticated,
        balance: balance || "—",
        runs: runs || "—",
        profit: profit || "—",
        wins: wins || "—",
        losses: losses || "—",
        profitTone: String(profit).trim().startsWith("-") ? "loss" : "",
      };
    }

    const currency = me.currency || "USD";
    const rows = Array.isArray(trades?.trades) ? trades.trades : [];
    const cutoff = resetTime(me);
    const visible = cutoff ? rows.filter((row) => rowTime(row) >= cutoff) : rows;
    const summary = trades?.summary || {};

    let total = Number(summary.total ?? me?.stats?.trades ?? 0);
    let wins = Number(summary.wins ?? me?.stats?.wins ?? 0);
    let losses = Number(summary.losses ?? me?.stats?.losses ?? 0);
    let profit = Number(summary.profit ?? me?.stats?.profit ?? 0);

    if (cutoff) {
      total = visible.length;
      wins = visible.filter((row) => String(row.outcome || "").toUpperCase() === "WIN").length;
      losses = visible.filter((row) => String(row.outcome || "").toUpperCase() === "LOSS").length;
      profit = visible.reduce((sum, row) => sum + Number(row.profit || 0), 0);
    }

    return {
      authenticated: true,
      balance: money(me.balance || 0, currency),
      runs: count(total),
      profit: money(profit, currency),
      wins: count(wins),
      losses: count(losses),
      profitTone: profit < 0 ? "loss" : profit > 0 ? "win" : "",
    };
  }

  function statMarkup() {
    return STAT_DEFS.map(([key, label]) => `<article class="foa-mobile-mini-stat" data-compact-stat="${key}"><span>${label}</span><strong>—</strong></article>`).join("");
  }

  function ensureCompactHeader() {
    if (!isMobile()) return;
    const launcher = document.querySelector(".foa-mobile-menu-launcher");
    if (!launcher) return;

    launcher.classList.add("foa-mobile-execution-topbar");
    let strip = launcher.querySelector(".foa-mobile-inline-stats");
    if (!strip) {
      strip = document.createElement("div");
      strip.className = "foa-mobile-inline-stats";
      strip.setAttribute("aria-label", "Live execution summary");
      strip.innerHTML = statMarkup();
      launcher.appendChild(strip);
    }

    const metrics = liveMetrics();
    launcher.dataset.authenticated = metrics.authenticated ? "true" : "false";

    const values = {
      balance: metrics.balance,
      runs: metrics.runs,
      profit: metrics.profit,
      wins: metrics.wins,
      losses: metrics.losses,
    };

    Object.entries(values).forEach(([key, value]) => {
      const cell = strip.querySelector(`[data-compact-stat="${key}"]`);
      const strong = cell?.querySelector("strong");
      if (!cell || !strong) return;
      const text = String(value ?? "—");
      if (strong.textContent !== text) strong.textContent = text;
      cell.title = `${cell.querySelector("span")?.textContent || key}: ${text}`;
      if (key === "profit") cell.dataset.tone = metrics.profitTone || "neutral";
    });
  }

  function enhance() {
    scheduled = false;
    ensureCompactHeader();
  }

  function scheduleEnhance() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(enhance);
  }

  const observer = new MutationObserver(() => scheduleEnhance());
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });

  window.addEventListener("resize", scheduleEnhance);
  window.addEventListener("pageshow", scheduleEnhance);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleEnhance();
  });

  window.setInterval(scheduleEnhance, 1000);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleEnhance, { once: true })
    : scheduleEnhance();

  window.FOA_MOBILE_COMPACT_TOPBAR_VERSION = "20260813-1";
})();
