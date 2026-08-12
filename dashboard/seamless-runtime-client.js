(() => {
  "use strict";

  // This file intentionally loads before custom-runtime-client.js. That client
  // captures window.fetch as its transport, so every later live refresh is routed
  // through this single-flight/cache layer instead of creating duplicate /me,
  // lifecycle and trade-history requests.
  const upstreamFetch = window.fetch.bind(window);
  const RESPONSE_TTL_MS = 1800;
  const SNAPSHOT_TTL_MS = 350;
  const RETRY_MIN_MS = 500;
  const RETRY_MAX_MS = 8000;

  const cache = new Map();
  const inflight = new Map();
  let snapshot = null;
  let snapshotSavedAt = 0;
  let snapshotRequest = null;
  let retryTimer = null;
  let retryDelay = RETRY_MIN_MS;

  function pathOf(input) {
    try {
      const raw = typeof input === "string" ? input : input?.url || String(input || "");
      return new URL(raw, window.location.origin).pathname;
    } catch (_) {
      return String(input || "").split("?", 1)[0];
    }
  }

  function methodOf(options) {
    return String(options?.method || "GET").toUpperCase();
  }

  function jsonResponse(payload, status = 200, headers = {}) {
    return new Response(JSON.stringify(payload ?? {}), {
      status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        ...headers,
      },
    });
  }

  function clonePayload(value) {
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (_) {
      return value;
    }
  }

  function bootstrapMe() {
    const boot = window.FOA_BOOT_SESSION;
    if (!boot?.authenticated) return null;
    return {
      ...clonePayload(boot),
      authenticated: true,
      balance: Number(boot.balance || 0),
      stats: { trades: 0, wins: 0, losses: 0, profit: 0 },
    };
  }

  const boot = bootstrapMe();
  if (boot) cache.set("/me", { payload: boot, savedAt: Date.now() });

  function setConnectivity(state, message = "") {
    let badge = document.getElementById("foa-live-connectivity");
    if (!badge) {
      badge = document.createElement("div");
      badge.id = "foa-live-connectivity";
      badge.setAttribute("role", "status");
      badge.style.cssText = [
        "position:fixed",
        "right:10px",
        "bottom:10px",
        "z-index:2147483000",
        "padding:6px 9px",
        "border-radius:999px",
        "font:600 11px/1.2 system-ui,sans-serif",
        "box-shadow:0 4px 18px rgba(0,0,0,.22)",
        "pointer-events:none",
      ].join(";");
      document.documentElement.appendChild(badge);
    }
    if (state === "live") {
      badge.textContent = "Live";
      badge.style.opacity = "0";
      badge.style.background = "rgba(15,23,42,.92)";
      badge.style.color = "#d1fae5";
      window.setTimeout(() => {
        if (badge.textContent === "Live") badge.style.display = "none";
      }, 900);
      retryDelay = RETRY_MIN_MS;
      return;
    }
    badge.style.display = "block";
    badge.style.opacity = "1";
    badge.style.background = "rgba(15,23,42,.96)";
    badge.style.color = "#fef3c7";
    badge.textContent = message || "Reconnecting live data…";
  }

  function cachePut(path, payload) {
    cache.set(path, { payload: clonePayload(payload), savedAt: Date.now() });
  }

  function cacheGet(path, maxAge = RESPONSE_TTL_MS) {
    const item = cache.get(path);
    if (!item || Date.now() - item.savedAt > maxAge) return null;
    return clonePayload(item.payload);
  }

  function staleGet(path) {
    const item = cache.get(path);
    return item ? clonePayload(item.payload) : null;
  }

  function scheduleReconnect() {
    if (retryTimer || document.hidden) return;
    setConnectivity("recovering");
    retryTimer = window.setTimeout(async () => {
      retryTimer = null;
      try {
        await loadSnapshot(true);
        setConnectivity("live");
      } catch (_) {
        retryDelay = Math.min(RETRY_MAX_MS, Math.max(RETRY_MIN_MS, retryDelay * 1.7));
        scheduleReconnect();
      }
    }, retryDelay);
  }

  async function fetchJSONNative(path) {
    const response = await upstreamFetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  async function loadSnapshot(force = false) {
    if (!force && snapshot && Date.now() - snapshotSavedAt < SNAPSHOT_TTL_MS) {
      return clonePayload(snapshot);
    }
    if (snapshotRequest) return snapshotRequest.then(clonePayload);
    snapshotRequest = fetchJSONNative("/me/live-snapshot")
      .then((payload) => {
        if (!payload?.authenticated) throw new Error("Personal live session is unavailable");
        snapshot = clonePayload(payload);
        snapshotSavedAt = Date.now();
        cachePut("/me/trading-lifecycle", lifecycleFromSnapshot(payload));
        cachePut("/me/execution-runtime", executionFromSnapshot(payload));
        cachePut("/me/trades/today", tradesFromSnapshot(payload));
        setConnectivity("live");
        return payload;
      })
      .finally(() => {
        snapshotRequest = null;
      });
    return snapshotRequest.then(clonePayload);
  }

  function lifecycleFromSnapshot(payload) {
    return {
      authenticated: Boolean(payload?.authenticated),
      lifecycle: payload?.lifecycle || (payload?.enabled ? "running" : "stopped"),
      execution_status: payload?.execution_status || "inactive",
      reason: payload?.reason || "",
      enabled: Boolean(payload?.enabled),
      runtime_state: payload?.runtime_state || "STOPPED",
      updated_at: payload?.updated_at || "",
    };
  }

  function executionFromSnapshot(payload) {
    return {
      authenticated: Boolean(payload?.authenticated),
      enabled: Boolean(payload?.enabled),
      runtime_state: payload?.runtime_state || "STOPPED",
      execution_status: payload?.execution_status || "inactive",
      execution_status_reason: payload?.reason || "",
      reason: payload?.reason || "",
      updated_at: payload?.updated_at || "",
    };
  }

  function tradesFromSnapshot(payload) {
    return {
      authenticated: Boolean(payload?.authenticated),
      account: payload?.account || "",
      account_type: payload?.account_type || "demo",
      timezone: payload?.timezone || "Africa/Nairobi",
      date: payload?.date || "",
      trades: Array.isArray(payload?.trades) ? payload.trades : [],
      summary: payload?.summary || {},
    };
  }

  function patchRuntimeFromMutation(payload) {
    if (!payload || typeof payload !== "object") return;
    const enabled = payload.enabled !== undefined
      ? Boolean(payload.enabled)
      : String(payload.lifecycle || "").toLowerCase() === "running";
    const runtimeState = String(
      payload.runtime_state
      || (enabled ? "STARTING" : "STOPPED"),
    ).toUpperCase();
    const status = String(
      payload.execution_status
      || payload.state
      || (enabled ? "starting" : "stopped"),
    ).toLowerCase();

    if (snapshot) {
      snapshot.enabled = enabled;
      snapshot.runtime_state = runtimeState;
      snapshot.execution_status = status;
      snapshot.lifecycle = payload.lifecycle || (enabled ? "running" : "stopped");
      snapshot.reason = payload.message || snapshot.reason || "";
      snapshot.updated_at = new Date().toISOString();
      snapshotSavedAt = Date.now();
    }

    const meItem = cache.get("/me");
    if (meItem?.payload) {
      meItem.payload.enabled = enabled;
      meItem.payload.execution_status = status;
      meItem.payload.execution_status_reason = payload.message || "";
      meItem.savedAt = Date.now();
    }

    const runtime = executionFromSnapshot(snapshot || {
      authenticated: true,
      enabled,
      runtime_state: runtimeState,
      execution_status: status,
      reason: payload.message || "",
    });
    cachePut("/me/execution-runtime", runtime);
    cachePut("/me/trading-lifecycle", {
      ...runtime,
      lifecycle: payload.lifecycle || (enabled ? "running" : "stopped"),
    });
    document.dispatchEvent(new CustomEvent("foa:runtime-mutation-confirmed", {
      detail: clonePayload(runtime),
    }));
    window.setTimeout(() => loadSnapshot(true).catch(scheduleReconnect), 80);
  }

  async function singleFlight(path, input, options) {
    const fresh = cacheGet(path);
    if (fresh) return jsonResponse(fresh, 200, { "X-FOA-Source": "memory" });
    if (inflight.has(path)) {
      const payload = await inflight.get(path);
      return jsonResponse(payload, 200, { "X-FOA-Source": "coalesced" });
    }
    const request = upstreamFetch(input, options)
      .then(async (response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        const payload = await response.clone().json();
        cachePut(path, payload);
        setConnectivity("live");
        return payload;
      })
      .finally(() => inflight.delete(path));
    inflight.set(path, request);
    try {
      const payload = await request;
      return jsonResponse(payload, 200, { "X-FOA-Source": "network" });
    } catch (error) {
      const stale = staleGet(path) || (path === "/me" ? bootstrapMe() : null);
      if (stale) {
        scheduleReconnect();
        return jsonResponse(stale, 200, { "X-FOA-Source": "stale-recovery" });
      }
      throw error;
    }
  }

  window.fetch = async (input, options = {}) => {
    const path = pathOf(input);
    const method = methodOf(options);

    if (method === "GET" && [
      "/me/trading-lifecycle",
      "/me/execution-runtime",
      "/me/trades/today",
    ].includes(path)) {
      try {
        const live = await loadSnapshot(false);
        if (path === "/me/trading-lifecycle") return jsonResponse(lifecycleFromSnapshot(live));
        if (path === "/me/execution-runtime") return jsonResponse(executionFromSnapshot(live));
        return jsonResponse(tradesFromSnapshot(live));
      } catch (_) {
        const stale = staleGet(path);
        if (stale) {
          scheduleReconnect();
          return jsonResponse(stale, 200, { "X-FOA-Source": "stale-recovery" });
        }
        // Fall through to the existing endpoint once when no last-good snapshot
        // exists yet. The outer current-runtime client still applies its timeout.
      }
    }

    if (method === "GET" && path === "/me") {
      return singleFlight(path, input, options);
    }

    try {
      const response = await upstreamFetch(input, options);
      if (method === "GET" && response.ok && [
        "/me/custom-strategy",
        "/metrics/summary",
      ].includes(path)) {
        try {
          cachePut(path, await response.clone().json());
        } catch (_) {}
      }
      if (method !== "GET" && response.ok && [
        "/me/resume-trading",
        "/me/auto-trade",
        "/me/pause-trading",
        "/me/stop-trading",
      ].includes(path)) {
        try {
          patchRuntimeFromMutation(await response.clone().json());
        } catch (_) {}
      }
      return response;
    } catch (error) {
      if (method === "GET") {
        const stale = staleGet(path);
        if (stale) {
          scheduleReconnect();
          return jsonResponse(stale, 200, { "X-FOA-Source": "stale-recovery" });
        }
      }
      throw error;
    }
  };

  document.addEventListener("foa:trades-cleared", () => {
    snapshot = null;
    snapshotSavedAt = 0;
    cache.delete("/me/trades/today");
    loadSnapshot(true).catch(scheduleReconnect);
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadSnapshot(true).catch(scheduleReconnect);
  });
  window.addEventListener("online", () => loadSnapshot(true).catch(scheduleReconnect));

  // Remove only the obsolete blocking/stale wording from older renderer state.
  // Connectivity is represented by the tiny reconnecting badge above while the
  // last rendered controls remain usable.
  const noticeObserver = new MutationObserver(() => {
    document.querySelectorAll(".notice, .status-message, .inline-warning").forEach((node) => {
      const text = String(node.textContent || "");
      if (text.includes("LIVE REFRESH DELAYED - showing last known dashboard data.")) {
        node.remove();
      }
    });
  });
  noticeObserver.observe(document.documentElement, { childList: true, subtree: true });

  window.FOA_SEAMLESS_RUNTIME_CLIENT = "20260812-seamless-v1";
})();
