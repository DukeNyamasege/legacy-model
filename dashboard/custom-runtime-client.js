(() => {
  "use strict";

  const nativeFetch = window.fetch.bind(window);
  const nativeSetInterval = window.setInterval.bind(window);
  let deferredSettings = null;
  let runtimeSnapshot = null;
  let liveMe = null;
  let liveTrades = null;
  let eventSource = null;
  let liveRefreshTimer = null;
  let liveRefreshInFlight = false;
  let liveRefreshPending = false;
  let summarySnapshot = null;
  let summaryRefreshAt = 0;

  const cache = new Map();
  const CACHE_TTL = new Map([
    ["/me/custom-strategy", 300000],
  ]);
  const GET_TIMEOUT_MS = 4500;
  const POST_TIMEOUT_MS = 12000;
  const SUMMARY_BACKGROUND_TTL_MS = 60000;

  // The old renderer owns a full-screen loader whenever any dashboard request is
  // busy. Keep the rendered UI usable instead: all reads now have bounded timeouts,
  // live data arrives independently, and mutations expose their result inline.
  const style = document.createElement("style");
  style.id = "foa-nonblocking-loader-style";
  style.textContent = "#foa-simple-app #smart-loader{display:none!important;pointer-events:none!important}";
  document.head.appendChild(style);

  function urlPath(input) {
    try {
      const raw = typeof input === "string" ? input : input?.url || String(input || "");
      const url = new URL(raw, window.location.origin);
      return `${url.pathname}${url.search}`;
    } catch (_) {
      return String(input || "");
    }
  }

  function basePath(path) {
    return String(path || "").split("?", 1)[0];
  }

  function methodOf(options) {
    return String(options?.method || "GET").toUpperCase();
  }

  function jsonResponse(payload, status = 200) {
    return Promise.resolve(new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    }));
  }

  function parseBody(options) {
    try {
      if (!options?.body) return {};
      return JSON.parse(String(options.body));
    } catch (_) {
      return {};
    }
  }

  function invalidateAccountCache() {
    cache.clear();
  }

  async function fetchWithTimeout(input, options = {}, timeoutMs = GET_TIMEOUT_MS) {
    if (options?.signal || typeof AbortController === "undefined") {
      return nativeFetch(input, options);
    }
    const controller = new AbortController();
    const timer = window.setTimeout(
      () => controller.abort(),
      Math.max(250, Number(timeoutMs || GET_TIMEOUT_MS)),
    );
    try {
      return await nativeFetch(input, { ...options, signal: controller.signal });
    } catch (error) {
      if (error?.name === "AbortError") {
        throw new Error(`Dashboard request timed out after ${Math.round(timeoutMs / 1000)}s`);
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function cachedFetch(input, options, path) {
    const key = path;
    const ttl = CACHE_TTL.get(basePath(path));
    if (!ttl) return fetchWithTimeout(input, options, GET_TIMEOUT_MS);
    const now = Date.now();
    const hit = cache.get(key);
    if (hit && now - hit.savedAt < ttl) return Promise.resolve(hit.response.clone());
    return fetchWithTimeout(input, options, GET_TIMEOUT_MS).then((response) => {
      if (response.ok) cache.set(key, { savedAt: now, response: response.clone() });
      return response;
    });
  }

  function refreshSummaryInBackground(path) {
    const now = Date.now();
    if (now - summaryRefreshAt < SUMMARY_BACKGROUND_TTL_MS) return;
    summaryRefreshAt = now;
    fetchWithTimeout(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    }, 2000).then(async (response) => {
      if (!response.ok) return;
      try {
        summarySnapshot = await response.json();
      } catch (_) {}
    }).catch(() => {});
  }

  function runtimeText(payload) {
    const state = String(payload?.runtime_state || "STOPPED").toUpperCase();
    const reason = String(payload?.reason || payload?.execution_status_reason || "").trim();
    const labels = {
      STOPPED: ["Ready", reason || "Auto trading is stopped"],
      STARTING: ["Starting", reason || "Initializing account execution session"],
      WAITING_FOR_CONDITION: ["Waiting", reason || "Waiting for the configured condition"],
      EXECUTING: ["Executing", reason || "Qualified signal is being purchased"],
      RUNNING: ["Running", reason || "Contract is active and settlement is monitored"],
      ERROR: ["Error", reason || "Trading stopped because execution initialization failed"],
    };
    return labels[state] || labels.STOPPED;
  }

  function applyRuntime(payload) {
    if (!payload || !payload.authenticated) return;
    runtimeSnapshot = payload;
    const state = String(payload.runtime_state || "STOPPED").toUpperCase();
    const enabled = Boolean(payload.enabled) && !["STOPPED", "ERROR"].includes(state);
    const [label, detail] = runtimeText(payload);

    document.querySelectorAll(".builder-status-line").forEach((line) => {
      line.dataset.runtimeState = state;
      const text = line.querySelector("span");
      if (text && text.textContent !== `${label} - ${detail}`) text.textContent = `${label} - ${detail}`;
    });

    document.querySelectorAll("[data-main-action]").forEach((button) => {
      const nextAction = enabled ? "stop" : "start";
      const nextText = enabled ? "Stop Auto Trading" : "Start Auto Trading";
      button.dataset.mainAction = nextAction;
      if (button.textContent !== nextText) button.textContent = nextText;
      button.classList.toggle("danger", enabled);
      button.disabled = false;
    });

    document.querySelectorAll(".trades-control-panel").forEach((panel) => {
      const title = panel.querySelector("h2");
      const paragraphs = panel.querySelectorAll("p");
      if (title && title.textContent !== label) title.textContent = label;
      if (paragraphs.length > 1 && paragraphs[1].textContent !== detail) paragraphs[1].textContent = detail;
    });

    let notice = document.querySelector("#custom-runtime-error-notice");
    if (state === "ERROR") {
      if (!notice) {
        notice = document.createElement("div");
        notice.id = "custom-runtime-error-notice";
        notice.className = "notice error runtime-error-notice";
        const main = document.querySelector(".builder-shell main");
        if (main) main.prepend(notice);
      }
      if (notice && notice.textContent !== detail) notice.textContent = detail;
    } else if (notice) {
      notice.remove();
    }
  }

  function formatMoney(value, currency = "USD") {
    const amount = Number(value || 0);
    const prefix = String(currency || "USD").toUpperCase() === "USD" ? "$" : `${currency} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function statValue(label, value) {
    document.querySelectorAll(".builder-stat").forEach((card) => {
      const name = String(card.querySelector("span")?.textContent || "").trim();
      if (name !== label) return;
      const strong = card.querySelector("strong");
      if (strong && strong.textContent !== String(value)) strong.textContent = String(value);
    });
  }

  function patchLiveMetrics() {
    if (!liveMe && !liveTrades) return;
    const currency = liveMe?.currency || "USD";
    const summary = liveTrades?.summary || {};
    const stats = liveMe?.stats || {};
    const total = Number(summary.total ?? stats.trades ?? 0);
    const wins = Number(summary.wins ?? stats.wins ?? 0);
    const losses = Number(summary.losses ?? stats.losses ?? 0);
    const profit = Number(summary.profit ?? stats.profit ?? 0);

    if (liveMe) statValue("Balance", formatMoney(liveMe.balance || 0, currency));
    statValue("Today's P/L", formatMoney(profit, currency));
    statValue("P/L", formatMoney(profit, currency));
    statValue("Number of Runs", total.toLocaleString());
    statValue("Runs", total.toLocaleString());
    statValue("Wins", wins.toLocaleString());
    statValue("Losses", losses.toLocaleString());
  }

  function tradeTime(row) {
    const raw = row.purchase_time || row.provider_purchase_time || row.created_at || row.settlement_time;
    if (!raw) return "-";
    const date = new Date(raw);
    return Number.isNaN(date.getTime())
      ? "-"
      : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function tradeResult(row, currency) {
    const outcome = String(row.outcome || "OPEN").toUpperCase();
    if (outcome === "WIN" || outcome === "LOSS") {
      return `${outcome} - ${formatMoney(row.profit || 0, currency)}`;
    }
    return outcome;
  }

  function appendText(parent, tag, value, className = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = String(value ?? "");
    parent.appendChild(node);
    return node;
  }

  function tradeRevision(rows, summary) {
    const first = rows[0] || {};
    return [
      first.id || first.trade_id || first.virtual_trade_id || "",
      first.outcome || first.virtual_result || "",
      first.settlement_time || first.settled_at || "",
      summary?.total || 0,
      summary?.wins || 0,
      summary?.losses || 0,
      summary?.profit || 0,
      summary?.virtual_observations || 0,
    ].join("|");
  }

  function patchRecentTrades() {
    const rows = Array.isArray(liveTrades?.trades) ? liveTrades.trades : null;
    if (!rows) return;
    const currency = liveMe?.currency || "USD";
    const revision = tradeRevision(rows, liveTrades?.summary || {});

    document.querySelectorAll(".builder-recent-trades").forEach((panel) => {
      if (panel.dataset.liveRevision === revision) return;
      panel.dataset.liveRevision = revision;

      Array.from(panel.children).forEach((child) => {
        if (child.classList?.contains("trade-row") || child.classList?.contains("empty-state")) child.remove();
      });

      const limit = document.querySelector(".trades-control-panel") ? 50 : 8;
      if (!rows.length) {
        appendText(panel, "div", "No recent trades yet.", "empty-state");
        return;
      }

      rows.slice(0, limit).forEach((row) => {
        const outcome = String(row.outcome || "OPEN").toUpperCase();
        const item = document.createElement("div");
        item.className = "trade-row";
        appendText(item, "span", tradeTime(row));
        appendText(item, "strong", row.symbol || row.market || "-");
        appendText(item, "span", row.contract_type || row.type || "-");
        appendText(item, "span", formatMoney(row.buy_price ?? row.stake ?? row.amount ?? 0, currency));
        appendText(
          item,
          "b",
          tradeResult(row, currency),
          outcome === "WIN" ? "win" : outcome === "LOSS" ? "loss" : "open",
        );
        panel.appendChild(item);
      });
    });
  }

  function applyLiveSnapshot() {
    if (runtimeSnapshot) applyRuntime(runtimeSnapshot);
    patchLiveMetrics();
    patchRecentTrades();
  }

  async function inspectRuntimeResponse(response) {
    try {
      if (!response?.ok) return;
      const payload = await response.clone().json();
      if (payload?.runtime_state) applyRuntime({ ...payload, authenticated: true });
    } catch (_) {}
  }

  window.fetch = async (input, options = {}) => {
    const path = urlPath(input);
    const route = basePath(path);
    const method = methodOf(options);

    if (method === "POST" && route === "/me/trading-settings") {
      deferredSettings = parseBody(options);
      return jsonResponse({ success: true, deferred_into_custom_strategy_save: true });
    }

    if (method === "POST" && route === "/me/custom-strategy") {
      const custom = parseBody(options);
      if (deferredSettings) {
        custom.execution_settings = {
          stake_amount: Number(deferredSettings.stake_amount ?? 0.5),
          take_profit: Number(deferredSettings.take_profit ?? 0),
          stop_loss: Math.abs(Number(deferredSettings.stop_loss ?? 0)),
          martingale_enabled: deferredSettings.martingale_enabled !== false,
        };
      }
      const next = { ...options, body: JSON.stringify(custom) };
      const response = await fetchWithTimeout(input, next, POST_TIMEOUT_MS);
      if (response.ok) {
        deferredSettings = null;
        cache.clear();
      }
      await inspectRuntimeResponse(response);
      return response;
    }

    // The current builder does not render the old global model summary. Never make
    // navigation or account refresh wait for that legacy aggregate. Refresh it only
    // in the background for compatibility with any older observer still reading it.
    if (method === "GET" && route === "/metrics/summary") {
      refreshSummaryInBackground(path);
      return jsonResponse(summarySnapshot || { performance_profile: "background-summary" });
    }

    if (method !== "GET") invalidateAccountCache();
    const response = method === "GET"
      ? await cachedFetch(input, options, path)
      : await fetchWithTimeout(input, options, POST_TIMEOUT_MS);
    if (route === "/me/resume-trading" || route === "/me/auto-trade" || route === "/me/stop-trading") {
      await inspectRuntimeResponse(response);
      scheduleLiveRefresh("mutation");
    }
    return response;
  };

  async function nativeJSON(path, timeoutMs = GET_TIMEOUT_MS) {
    const response = await fetchWithTimeout(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    }, timeoutMs);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  async function refreshLiveData() {
    if (liveRefreshInFlight) {
      liveRefreshPending = true;
      return;
    }
    liveRefreshInFlight = true;
    try {
      const [me, life, trades] = await Promise.all([
        nativeJSON("/me", 3500),
        nativeJSON("/me/trading-lifecycle", 3500),
        nativeJSON("/me/trades/today?limit=100", 3500),
      ]);
      if (me?.authenticated) liveMe = me;
      if (life?.authenticated) applyRuntime(life);
      if (trades?.authenticated) liveTrades = trades;
      applyLiveSnapshot();
    } catch (_) {
      // The normal renderer refresh remains a fallback. Never cover the current
      // screen with a loader just because one live sync attempt was slow.
    } finally {
      liveRefreshInFlight = false;
      if (liveRefreshPending) {
        liveRefreshPending = false;
        scheduleLiveRefresh("coalesced", 80);
      }
    }
  }

  function scheduleLiveRefresh(_reason = "event", delay = 120) {
    window.clearTimeout(liveRefreshTimer);
    liveRefreshTimer = window.setTimeout(refreshLiveData, Math.max(0, Number(delay || 0)));
  }

  async function pollRuntime() {
    if (document.hidden) return;
    try {
      const payload = await nativeJSON("/me/execution-runtime", 3000);
      if (!payload?.authenticated) return;
      applyRuntime(payload);
    } catch (_) {}
  }

  function ensureLiveSource() {
    if (eventSource || typeof EventSource === "undefined") return;
    const booted = Boolean(window.FOA_BOOT_SESSION?.authenticated)
      || Boolean(document.querySelector(".account-pill"));
    if (!booted) return;
    try {
      eventSource = new EventSource("/me/live-events", { withCredentials: true });
      eventSource.addEventListener("snapshot", (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          if (!payload?.authenticated) return;
          applyRuntime(payload);
          scheduleLiveRefresh("sse");
        } catch (_) {}
      });
      eventSource.addEventListener("account", () => {
        eventSource?.close();
        eventSource = null;
      });
      eventSource.onerror = () => {
        // EventSource reconnects automatically. Runtime polling below remains the
        // low-frequency fallback when a proxy temporarily blocks SSE.
      };
    } catch (_) {
      eventSource = null;
    }
  }

  window.addEventListener("dashboard:snapshot-ready", () => {
    applyLiveSnapshot();
    ensureLiveSource();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      ensureLiveSource();
      pollRuntime();
      scheduleLiveRefresh("visible", 0);
    }
  });
  window.addEventListener("pageshow", () => {
    ensureLiveSource();
    scheduleLiveRefresh("pageshow", 0);
  });

  // The main renderer replaces #foa-simple-app.innerHTML. Re-apply only after that
  // direct replacement; ignore mutations caused by our own live row/text patches.
  const observer = new MutationObserver((mutations) => {
    const dashboardReplaced = mutations.some(
      (mutation) => mutation.type === "childList" && mutation.target?.id === "foa-simple-app",
    );
    if (!dashboardReplaced) return;
    window.requestAnimationFrame(applyLiveSnapshot);
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  // SSE is the primary path. This 20-second poll is only a proxy/network fallback.
  nativeSetInterval(() => {
    if (document.hidden) return;
    pollRuntime();
    scheduleLiveRefresh("fallback", 0);
  }, 20000);

  pollRuntime();
  ensureLiveSource();
  scheduleLiveRefresh("boot", 0);

  window.FOA_CUSTOM_DIRECT_RUNTIME_CLIENT = "20260812-live-sse-v4";
  window.FOA_DASHBOARD_REFRESH_MODE = "sse-primary-bounded-fallback";
})();
