(() => {
  "use strict";

  const TRADE_RESET_PREFIX = "foa-trade-session-reset-v1";
  const FULL_REFRESH_MS = 30000;
  const START_REDIRECT_TIMEOUT_MS = 20000;
  let scheduled = false;
  let fullRows = [];
  let fetchedIdentity = "";
  let fetchInFlight = false;
  let lastFullFetchAt = 0;
  let lastRenderedSignature = "";
  let startRedirectPending = false;
  let startRedirectStartedAt = 0;
  let stopInFlight = false;

  function storageGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }

  function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch (_) {}
  }

  function currentMe() {
    return window.FOA_NETLIFY_LIVE_CACHE?.me || null;
  }

  function currentTradesPayload() {
    return window.FOA_NETLIFY_LIVE_CACHE?.trades || null;
  }

  function isAuthenticated() {
    return Boolean(currentMe()?.authenticated || document.querySelector(".builder-header #logout"));
  }

  function accountType(me = currentMe()) {
    return String(me?.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
  }

  function accountMask(me = currentMe()) {
    return String(me?.account_id_masked || me?.account_id || "public");
  }

  function historyIdentity() {
    const me = currentMe();
    const date = String(currentTradesPayload()?.date || "current");
    return `${accountType(me)}:${accountMask(me)}:${date}`;
  }

  function resetKey() {
    return `${TRADE_RESET_PREFIX}:${accountType()}:${accountMask()}`;
  }

  function resetTime() {
    const raw = storageGet(resetKey());
    if (!raw) return 0;
    const parsed = Date.parse(raw);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function rowTime(row) {
    const raw = row?.purchase_time
      || row?.provider_purchase_time
      || row?.created_at
      || row?.settlement_time
      || "";
    const parsed = Date.parse(raw);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function rowKey(row) {
    return String(
      row?.id
      ?? row?.trade_id
      ?? row?.contract_id
      ?? row?.virtual_trade_id
      ?? `${rowTime(row)}:${row?.symbol || row?.market || ""}:${row?.buy_price ?? row?.stake ?? ""}`
    );
  }

  function mergeRows(...collections) {
    const map = new Map();
    collections.flat().forEach((row) => {
      if (!row || typeof row !== "object") return;
      const key = rowKey(row);
      const existing = map.get(key);
      map.set(key, existing ? { ...existing, ...row } : row);
    });
    return Array.from(map.values()).sort((a, b) => rowTime(b) - rowTime(a));
  }

  function visibleRows(rows) {
    const cutoff = resetTime();
    return cutoff ? rows.filter((row) => rowTime(row) >= cutoff) : rows;
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

  function tradeTime(row) {
    const raw = row?.purchase_time || row?.provider_purchase_time || row?.created_at || row?.settlement_time;
    const date = raw ? new Date(raw) : null;
    return !date || Number.isNaN(date.getTime())
      ? "-"
      : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function contractType(row) {
    const type = String(row?.contract_type || row?.type || "TRADE").toUpperCase();
    const barrier = String(row?.barrier || row?.prediction || "").trim();
    return barrier && ["DIGITOVER", "DIGITUNDER", "DIGITMATCH", "DIGITDIFF"].includes(type)
      ? `${type} ${barrier}`
      : type;
  }

  function exitSpotDisplay(row) {
    const digit = row?.exit_digit ?? row?.actual_last_digit;
    if (digit !== null && digit !== undefined && String(digit) !== "") return String(digit);
    const spot = row?.exit_spot ?? row?.exit_tick;
    if (spot !== null && spot !== undefined && String(spot) !== "") {
      const text = String(spot);
      const digits = text.match(/\d/g);
      return digits?.length ? digits[digits.length - 1] : text;
    }
    return String(row?.outcome || "OPEN").toUpperCase() === "OPEN" ? "Open" : "-";
  }

  function resultClass(row) {
    const outcome = String(row?.outcome || "OPEN").toUpperCase();
    return outcome === "WIN" ? "win" : outcome === "LOSS" ? "loss" : "neutral";
  }

  function resultText(row, currency) {
    const outcome = String(row?.outcome || "OPEN").toUpperCase();
    return outcome === "WIN" || outcome === "LOSS"
      ? `${outcome} - ${money(row?.profit || 0, currency)}`
      : outcome;
  }

  function append(parent, tag, text, className = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = String(text ?? "");
    parent.appendChild(node);
    return node;
  }

  function rowSignature(rows) {
    const head = rows.slice(0, 20).map((row) => [
      rowKey(row),
      row?.outcome,
      row?.profit,
      row?.exit_digit ?? row?.actual_last_digit ?? row?.exit_spot ?? row?.exit_tick,
    ].join(":"));
    return `${historyIdentity()}|${resetTime()}|${rows.length}|${head.join("|")}`;
  }

  function renderTradeTable(panel) {
    const live = Array.isArray(currentTradesPayload()?.trades) ? currentTradesPayload().trades : [];
    const rows = visibleRows(mergeRows(fullRows, live));
    const signature = rowSignature(rows);
    if (panel.dataset.minimalSignature === signature && panel.querySelector(".trade-head")) return;
    panel.dataset.minimalSignature = signature;

    const currency = currentMe()?.currency || "USD";
    panel.replaceChildren();

    const head = document.createElement("div");
    head.className = "trade-head";
    ["Time", "Market", "Trade type", "Stake", "Exit spot", "Result"].forEach((label) => append(head, "span", label));
    panel.appendChild(head);

    rows.forEach((row) => {
      const item = document.createElement("div");
      item.className = "trade-row";
      append(item, "span", tradeTime(row));
      append(item, "strong", row?.symbol || row?.market || "-");
      append(item, "span", contractType(row));
      append(item, "span", money(row?.buy_price ?? row?.stake ?? row?.amount ?? 0, currency));
      append(item, "span", exitSpotDisplay(row), "trade-exit-spot");
      append(item, "b", resultText(row, currency), resultClass(row));
      panel.appendChild(item);
    });
  }

  function ensureBalanceStat(stats) {
    if (!stats) return;
    const hasBalance = Array.from(stats.querySelectorAll(".builder-stat > span"))
      .some((node) => String(node.textContent || "").trim() === "Balance");
    if (hasBalance) return;
    const me = currentMe();
    if (!me?.authenticated) return;
    const article = document.createElement("article");
    article.className = "builder-stat";
    append(article, "span", "Balance");
    append(article, "strong", money(me.balance || 0, me.currency || "USD"));
    append(article, "small", `${accountType(me)} account`);
    stats.prepend(article);
  }

  function resetIconMarkup() {
    return `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7h7V3l5 5-5 5V9H7a4 4 0 0 0-4 4 7 7 0 0 0 12 4"/><path d="m15 17 2 2 4-4"/></svg>`;
  }

  function stopIconMarkup() {
    return `<svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>`;
  }

  function fallbackClearButton() {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.clearLocalTrades = "true";
    button.addEventListener("click", () => {
      if (!window.confirm("Reset the trades currently shown on this device?")) return;
      storageSet(resetKey(), new Date().toISOString());
      lastRenderedSignature = "";
      scheduleEnhance();
    });
    return button;
  }

  async function stopTradingFromTrades(button) {
    if (stopInFlight) return;
    stopInFlight = true;
    button.disabled = true;
    button.dataset.busy = "true";
    button.title = "Stopping auto trading...";
    try {
      const response = await fetch("/me/stop-trading", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: "{}",
      });
      if (!response.ok) {
        let message = `Stop trading failed (${response.status})`;
        try {
          const payload = await response.json();
          message = payload?.detail || payload?.message || message;
        } catch (_) {}
        throw new Error(message);
      }
      button.dataset.stopped = "true";
      button.title = "Auto trading stopped";
      window.dispatchEvent(new CustomEvent("foa:trading-stopped-from-trades"));
    } catch (error) {
      window.alert(String(error?.message || error));
    } finally {
      stopInFlight = false;
      button.disabled = false;
      button.dataset.busy = "false";
      scheduleEnhance();
    }
  }

  function makeStopButton() {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "foa-stop-trades-icon";
    button.dataset.stopTrades = "true";
    button.innerHTML = stopIconMarkup();
    button.setAttribute("aria-label", "Stop auto trading");
    button.title = "Stop auto trading";
    button.addEventListener("click", () => stopTradingFromTrades(button));
    return button;
  }

  function ensureTradesControls(main) {
    const candidates = Array.from(main.querySelectorAll(
      "#foa-trades-controls-footer, .foa-trades-controls-footer, .foa-clear-trades-footer, .session-actions-panel"
    ));
    let footer = main.querySelector("#foa-trades-controls-footer") || candidates[0] || null;

    candidates.forEach((node) => {
      if (footer && node !== footer) node.remove();
    });

    if (!footer) {
      footer = document.createElement("section");
      main.appendChild(footer);
    }

    const needsInitialization = footer.id !== "foa-trades-controls-footer";
    footer.id = "foa-trades-controls-footer";
    footer.className = "foa-trades-controls-footer";

    if (needsInitialization) footer.replaceChildren();

    let resetButton = footer.querySelector("[data-clear-local-trades]");
    if (!resetButton) {
      resetButton = fallbackClearButton();
      footer.appendChild(resetButton);
    }
    resetButton.className = "foa-reset-trades-icon";
    resetButton.innerHTML = resetIconMarkup();
    resetButton.setAttribute("aria-label", "Reset trade view");
    resetButton.title = "Reset trade view";

    let stopButton = footer.querySelector("[data-stop-trades]");
    if (!stopButton) {
      stopButton = makeStopButton();
      footer.appendChild(stopButton);
    }
    stopButton.className = "foa-stop-trades-icon";
    if (!stopInFlight) {
      stopButton.disabled = false;
      if (stopButton.dataset.stopped !== "true") stopButton.title = "Stop auto trading";
    }

    Array.from(footer.children).forEach((node) => {
      if (node !== resetButton && node !== stopButton) node.remove();
    });
    return footer;
  }

  function minimalizeTradesView() {
    const main = document.querySelector("#foa-simple-app main");
    if (!main) return;

    document.body.classList.add("foa-trades-page-active");
    main.querySelector(".trades-control-panel")?.remove();

    const stats = main.querySelector(".builder-stats.compact");
    ensureBalanceStat(stats);

    const controls = ensureTradesControls(main);

    let panel = main.querySelector(".foa-trades-table") || main.querySelector(".builder-recent-trades");
    if (!panel) {
      panel = document.createElement("section");
      main.appendChild(panel);
    }
    panel.className = "builder-panel foa-trades-table";
    renderTradeTable(panel);

    if (stats && stats.nextElementSibling !== panel) main.insertBefore(panel, stats.nextElementSibling);
    if (main.lastElementChild !== controls) main.appendChild(controls);

    fetchAllTrades();
  }

  function isTradesView() {
    return Boolean(document.querySelector('.builder-header [data-view="trades"].active'));
  }

  function removeSettingsNavigation() {
    const settings = document.querySelector('.builder-header [data-view="settings"]');
    if (settings?.classList.contains("active")) {
      const dashboard = document.querySelector('.builder-header [data-view="main"]');
      if (dashboard) {
        dashboard.click();
        return true;
      }
    }
    settings?.remove();
    document.querySelectorAll('[data-mobile-view="settings"]').forEach((button) => button.remove());
    document.querySelectorAll('.credential-notice [data-view="settings"]').forEach((button) => button.remove());
    return false;
  }

  function enforceLoginLogoutToggle() {
    const authenticated = isAuthenticated();
    document.querySelectorAll("[data-mobile-logout]").forEach((node) => { node.hidden = !authenticated; });
    document.querySelectorAll("[data-mobile-login]").forEach((node) => { node.hidden = authenticated; });
  }

  async function fetchAllTrades(force = false) {
    if (!isTradesView() || !isAuthenticated() || fetchInFlight) return;
    const identity = historyIdentity();
    if (identity !== fetchedIdentity) {
      fetchedIdentity = identity;
      fullRows = [];
      lastFullFetchAt = 0;
      lastRenderedSignature = "";
    }
    if (!force && Date.now() - lastFullFetchAt < FULL_REFRESH_MS) return;
    fetchInFlight = true;
    try {
      const response = await fetch("/me/trades/today", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json();
      const rows = Array.isArray(payload?.trades) ? payload.trades : [];
      fullRows = mergeRows(fullRows, rows);
      lastFullFetchAt = Date.now();
      lastRenderedSignature = "";
      scheduleEnhance();
    } catch (_) {
      // Keep the last known rows; realtime snapshots continue to merge below.
    } finally {
      fetchInFlight = false;
    }
  }

  function maybeRedirectAfterStart() {
    if (!startRedirectPending) return;
    if (Date.now() - startRedirectStartedAt > START_REDIRECT_TIMEOUT_MS) {
      startRedirectPending = false;
      return;
    }
    const mainAction = document.querySelector('[data-main-action="stop"]');
    const status = String(document.querySelector(".builder-status-line span")?.textContent || "").toLowerCase();
    const running = Boolean(mainAction || status.startsWith("running"));
    if (!running) return;
    const trades = document.querySelector('.builder-header [data-view="trades"]');
    if (!trades) return;
    startRedirectPending = false;
    trades.click();
  }

  function enhance() {
    scheduled = false;
    if (removeSettingsNavigation()) return;
    enforceLoginLogoutToggle();
    maybeRedirectAfterStart();

    if (isTradesView()) {
      minimalizeTradesView();
    } else {
      document.body.classList.remove("foa-trades-page-active");
    }
  }

  function scheduleEnhance() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(enhance);
  }

  document.addEventListener("click", (event) => {
    const start = event.target?.closest?.('[data-main-action="start"]');
    if (start) {
      startRedirectPending = true;
      startRedirectStartedAt = Date.now();
    }
  }, true);

  const observer = new MutationObserver(() => scheduleEnhance());
  observer.observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener("pageshow", scheduleEnhance);
  window.addEventListener("resize", scheduleEnhance);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      fetchAllTrades(true);
      scheduleEnhance();
    }
  });

  window.setInterval(() => {
    const live = Array.isArray(currentTradesPayload()?.trades) ? currentTradesPayload().trades : [];
    if (live.length) fullRows = mergeRows(fullRows, live);
    if (isTradesView()) {
      const panel = document.querySelector(".foa-trades-table");
      if (panel) renderTradeTable(panel);
      fetchAllTrades();
    }
    maybeRedirectAfterStart();
    enforceLoginLogoutToggle();
  }, 700);

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleEnhance, { once: true })
    : scheduleEnhance();

  window.FOA_MINIMAL_TRADES_PAGE_VERSION = "20260813-2";
})();