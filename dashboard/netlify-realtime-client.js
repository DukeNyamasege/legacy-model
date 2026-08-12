(() => {
  "use strict";

  if (!window.FOA_NETLIFY_FRONTEND) return;

  const STREAM_BASE = String(
    document.querySelector('meta[name="stream-base-url"]')?.content || "",
  ).trim().replace(/\/+$/, "");
  const TRADE_RESET_PREFIX = "foa-trade-session-reset-v1";
  const FALLBACK_MS = 5000;
  let socket = null;
  let reconnectTimer = null;
  let fallbackTimer = null;
  let reconnectAttempt = 0;
  let lastSnapshot = null;
  let connecting = false;

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
    const raw = storageGet(`${TRADE_RESET_PREFIX}:${accountType(me)}:${accountMask(me)}`);
    if (!raw) return 0;
    const value = Date.parse(raw);
    return Number.isFinite(value) ? value : 0;
  }

  function rowTime(row) {
    const value = Date.parse(
      row?.purchase_time || row?.provider_purchase_time || row?.created_at || "",
    );
    return Number.isFinite(value) ? value : 0;
  }

  function visibleTrades(me, trades) {
    const rows = Array.isArray(trades?.trades) ? trades.trades : [];
    const cutoff = resetTime(me);
    if (!cutoff) return rows;
    return rows.filter((row) => rowTime(row) >= cutoff);
  }

  function money(value, currency = "USD") {
    const amount = Number(value || 0);
    const prefix = String(currency || "USD").toUpperCase() === "USD" ? "$" : `${currency} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function setStat(label, value) {
    document.querySelectorAll(".builder-stat").forEach((card) => {
      if (String(card.querySelector("span")?.textContent || "").trim() !== label) return;
      const strong = card.querySelector("strong");
      if (strong && strong.textContent !== String(value)) strong.textContent = String(value);
    });
  }

  function runtimeLabel(lifecycle) {
    const state = String(lifecycle?.runtime_state || "STOPPED").toUpperCase();
    const reason = String(lifecycle?.reason || lifecycle?.execution_status_reason || "").trim();
    const labels = {
      STOPPED: ["Ready", reason || "Auto trading is stopped"],
      STARTING: ["Starting", reason || "Connecting the account execution session"],
      WAITING_FOR_CONDITION: ["Waiting", reason || "Waiting for the configured condition"],
      EXECUTING: ["Executing", reason || "A qualified trade is being purchased"],
      RUNNING: ["Running", reason || "Account execution is active"],
      ERROR: ["Error", reason || "Account execution needs attention"],
    };
    return [state, ...(labels[state] || labels.STOPPED)];
  }

  function patchRuntime(lifecycle) {
    if (!lifecycle) return;
    const [state, label, detail] = runtimeLabel(lifecycle);
    const enabled = Boolean(lifecycle.enabled) && !["STOPPED", "ERROR"].includes(state);
    document.querySelectorAll(".builder-status-line").forEach((line) => {
      line.dataset.runtimeState = state;
      const span = line.querySelector("span");
      if (span) span.textContent = `${label} - ${detail}`;
    });
    document.querySelectorAll("[data-main-action]").forEach((button) => {
      button.dataset.mainAction = enabled ? "stop" : "start";
      button.textContent = enabled ? "Stop Auto Trading" : "Start Auto Trading";
      button.classList.toggle("danger", enabled);
      button.disabled = false;
    });
    document.querySelectorAll(".trades-control-panel").forEach((panel) => {
      const title = panel.querySelector("h2");
      const paragraphs = panel.querySelectorAll("p");
      if (title) title.textContent = label;
      if (paragraphs.length > 1) paragraphs[1].textContent = detail;
    });
  }

  function patchMetrics(me, trades) {
    if (!me) return;
    const rows = visibleTrades(me, trades);
    const cutoff = resetTime(me);
    const summary = trades?.summary || {};
    let total = Number(summary.total ?? me?.stats?.trades ?? 0);
    let wins = Number(summary.wins ?? me?.stats?.wins ?? 0);
    let losses = Number(summary.losses ?? me?.stats?.losses ?? 0);
    let profit = Number(summary.profit ?? me?.stats?.profit ?? 0);
    if (cutoff) {
      total = rows.length;
      wins = rows.filter((row) => String(row.outcome || "").toUpperCase() === "WIN").length;
      losses = rows.filter((row) => String(row.outcome || "").toUpperCase() === "LOSS").length;
      profit = rows.reduce((sum, row) => sum + Number(row.profit || 0), 0);
    }
    const currency = me.currency || "USD";
    setStat("Balance", money(me.balance || 0, currency));
    setStat("Today's P/L", money(profit, currency));
    setStat("P/L", money(profit, currency));
    setStat("Number of Runs", total.toLocaleString());
    setStat("Runs", total.toLocaleString());
    setStat("Wins", wins.toLocaleString());
    setStat("Losses", losses.toLocaleString());
  }

  function tradeTime(row) {
    const raw = row.purchase_time || row.provider_purchase_time || row.created_at || row.settlement_time;
    const date = raw ? new Date(raw) : null;
    return !date || Number.isNaN(date.getTime())
      ? "-"
      : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function append(parent, tag, text, className = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = String(text ?? "");
    parent.appendChild(node);
  }

  function patchTrades(me, trades) {
    if (!trades) return;
    const rows = visibleTrades(me, trades);
    const currency = me?.currency || "USD";
    const revision = `${trades?.revision || ""}|${rows[0]?.id || ""}|${rows.length}|${resetTime(me)}`;
    document.querySelectorAll(".builder-recent-trades").forEach((panel) => {
      if (panel.dataset.netlifyRevision === revision) return;
      panel.dataset.netlifyRevision = revision;
      Array.from(panel.children).forEach((child) => {
        if (child.classList?.contains("trade-row") || child.classList?.contains("empty-state")) child.remove();
      });
      const limit = document.querySelector(".trades-control-panel") ? 50 : 8;
      if (!rows.length) {
        append(panel, "div", "No recent trades yet.", "empty-state");
        return;
      }
      rows.slice(0, limit).forEach((row) => {
        const outcome = String(row.outcome || "OPEN").toUpperCase();
        const item = document.createElement("div");
        item.className = "trade-row";
        append(item, "span", tradeTime(row));
        append(item, "strong", row.symbol || row.market || "-");
        append(item, "span", row.contract_type || row.type || "-");
        append(item, "span", money(row.buy_price ?? row.stake ?? row.amount ?? 0, currency));
        const result = outcome === "WIN" || outcome === "LOSS"
          ? `${outcome} - ${money(row.profit || 0, currency)}`
          : outcome;
        append(item, "b", result, outcome === "WIN" ? "win" : outcome === "LOSS" ? "loss" : "open");
        panel.appendChild(item);
      });
    });
  }

  function applySnapshot(snapshot) {
    if (!snapshot?.me?.authenticated) return;
    lastSnapshot = snapshot;
    window.FOA_NETLIFY_LIVE_CACHE = {
      savedAt: Date.now(),
      me: snapshot.me,
      lifecycle: snapshot.lifecycle,
      trades: snapshot.trades,
    };
    patchRuntime(snapshot.lifecycle);
    patchMetrics(snapshot.me, snapshot.trades);
    patchTrades(snapshot.me, snapshot.trades);
    document.documentElement.dataset.liveTransport = "connected";
  }

  async function fallbackSnapshot() {
    try {
      const response = await fetch("/me/live-snapshot", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      applySnapshot(await response.json());
    } catch (_) {}
  }

  function scheduleFallback() {
    window.clearTimeout(fallbackTimer);
    fallbackTimer = window.setTimeout(async () => {
      if (!socket || socket.readyState !== WebSocket.OPEN) await fallbackSnapshot();
      scheduleFallback();
    }, FALLBACK_MS);
  }

  function scheduleReconnect() {
    window.clearTimeout(reconnectTimer);
    const delay = Math.min(8000, 450 * (2 ** Math.min(reconnectAttempt, 5))) + Math.random() * 250;
    reconnectAttempt += 1;
    reconnectTimer = window.setTimeout(connect, delay);
  }

  async function connect() {
    if (connecting || !STREAM_BASE || document.hidden) return;
    if (socket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(socket.readyState)) return;
    connecting = true;
    try {
      const ticketResponse = await fetch("/me/live-ticket", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!ticketResponse.ok) {
        connecting = false;
        scheduleReconnect();
        return;
      }
      const ticketPayload = await ticketResponse.json();
      const ticket = String(ticketPayload.ticket || "");
      if (!ticket) throw new Error("Realtime ticket unavailable");
      socket = new WebSocket(`${STREAM_BASE}/ws/me/live?ticket=${encodeURIComponent(ticket)}`);
      socket.onopen = () => {
        reconnectAttempt = 0;
        document.documentElement.dataset.liveTransport = "connected";
      };
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          if (payload.type === "snapshot") applySnapshot(payload);
        } catch (_) {}
      };
      socket.onerror = () => {
        document.documentElement.dataset.liveTransport = "reconnecting";
      };
      socket.onclose = () => {
        socket = null;
        document.documentElement.dataset.liveTransport = "reconnecting";
        fallbackSnapshot();
        scheduleReconnect();
      };
    } catch (_) {
      document.documentElement.dataset.liveTransport = "fallback";
      fallbackSnapshot();
      scheduleReconnect();
    } finally {
      connecting = false;
    }
  }

  const observer = new MutationObserver((mutations) => {
    if (!lastSnapshot) return;
    if (!mutations.some((item) => item.type === "childList" && item.target?.id === "foa-simple-app")) return;
    requestAnimationFrame(() => applySnapshot(lastSnapshot));
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener("click", (event) => {
    if (!event.target?.closest?.("[data-clear-local-trades]")) return;
    window.setTimeout(() => {
      if (lastSnapshot) applySnapshot(lastSnapshot);
    }, 0);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) connect();
  });
  window.addEventListener("pageshow", connect);
  window.addEventListener("online", connect);
  window.addEventListener("offline", () => {
    document.documentElement.dataset.liveTransport = "offline";
  });

  scheduleFallback();
  fallbackSnapshot();
  connect();
  window.FOA_NETLIFY_REALTIME_MODE = "direct-vps-websocket-ticket-v1";
})();
