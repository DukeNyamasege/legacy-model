(() => {
  "use strict";

  if (window.__DERIVADMIN_VPS_API_BOUNDARY_V2__) return;
  window.__DERIVADMIN_VPS_API_BOUNDARY_V2__ = true;

  const nativeFetch = window.fetch.bind(window);
  const API_PREFIX = "/api";
  const READ_TIMEOUT_MS = 10000;
  const WRITE_TIMEOUT_MS = 8000;
  const LIVE_CACHE_MAX_AGE_MS = 5000;
  let lastMe = null;

  window.FOA_FULL_VPS_FRONTEND = true;

  try { window.EventSource = undefined; } catch (_) {}

  function asURL(input) {
    try {
      if (input instanceof Request) return new URL(input.url, window.location.origin);
      return new URL(String(input), window.location.origin);
    } catch (_) { return null; }
  }

  function pathOf(input) {
    const url = asURL(input);
    return url ? `${url.pathname}${url.search}` : "";
  }

  function routeOf(path) { return String(path || "").split("?", 1)[0]; }

  function shouldProxy(path) {
    const route = routeOf(path);
    if (!route || route.startsWith(`${API_PREFIX}/`)) return false;
    return route === "/health" || route.startsWith("/health/") || route === "/me" || route.startsWith("/me/") || route === "/metrics" || route.startsWith("/metrics/");
  }

  function rewrittenURL(input) {
    const url = asURL(input);
    if (!url || url.origin !== window.location.origin) return input;
    const path = `${url.pathname}${url.search}`;
    if (!shouldProxy(path)) return input;
    const next = `${API_PREFIX}${url.pathname}${url.search}`;
    if (!(input instanceof Request)) return next;
    return new Request(next, {
      method: input.method,
      headers: input.headers,
      body: ["GET", "HEAD"].includes(input.method.toUpperCase()) ? undefined : input.body,
      mode: input.mode,
      credentials: input.credentials,
      cache: input.cache,
      redirect: input.redirect,
      referrer: input.referrer,
      referrerPolicy: input.referrerPolicy,
      integrity: input.integrity,
      keepalive: input.keepalive,
      signal: input.signal,
      duplex: input.duplex,
    });
  }

  function responseJSON(payload, status = 200, headers = {}) {
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers },
    });
  }

  function livePayload(path) {
    const cache = window.FOA_VPS_LIVE_CACHE;
    if (!cache || Date.now() - Number(cache.savedAt || 0) > LIVE_CACHE_MAX_AGE_MS) return null;
    const route = routeOf(path);
    if (route === "/me") return cache.me || null;
    if (route === "/me/trading-lifecycle" || route === "/me/execution-runtime") return cache.lifecycle || null;
    if (route === "/me/trades/today") return cache.trades || null;
    return null;
  }

  function transformedBody(route, options) {
    if (!options?.body || typeof options.body !== "string") return options;
    let payload;
    try { payload = JSON.parse(options.body); } catch (_) { return options; }

    // The 6F-2 Builder intentionally has no martingale control. Preserve whatever
    // the account already has instead of silently changing recovery configuration.
    if (route === "/me/custom-strategy" && payload?.execution_settings) {
      const current = lastMe?.settings?.martingale_enabled;
      if (typeof current === "boolean") payload.execution_settings.martingale_enabled = current;
    }

    // Action 5 accepts a frozen custom_strategy wrapper. 6F-2 may hand it the
    // canonical Custom Strategy directly, so normalize only that browser payload.
    if (route === "/me/automation-schedules" && payload?.strategy_snapshot?.market_mode && Array.isArray(payload.strategy_snapshot.conditions)) {
      payload.strategy_snapshot = { custom_strategy: payload.strategy_snapshot };
    }

    return { ...options, body: JSON.stringify(payload) };
  }

  function timeoutFor(method) {
    return method === "GET" || method === "HEAD" ? READ_TIMEOUT_MS : WRITE_TIMEOUT_MS;
  }

  async function boundedFetch(input, options = {}) {
    const method = String(options.method || (input instanceof Request ? input.method : "GET") || "GET").toUpperCase();
    const controller = new AbortController();
    const upstreamSignal = options.signal || (input instanceof Request ? input.signal : null);
    let detach = null;
    if (upstreamSignal) {
      detach = () => controller.abort(upstreamSignal.reason);
      if (upstreamSignal.aborted) detach();
      else upstreamSignal.addEventListener("abort", detach, { once: true });
    }
    const timeoutMs = timeoutFor(method);
    const timer = window.setTimeout(() => controller.abort(new Error("backend timeout")), timeoutMs);
    try {
      return await nativeFetch(input, {
        ...options,
        credentials: options.credentials || "same-origin",
        cache: options.cache || "no-store",
        signal: controller.signal,
      });
    } catch (error) {
      if (controller.signal.aborted && !upstreamSignal?.aborted) throw new Error(`Backend request timed out after ${(timeoutMs / 1000).toFixed(1)}s`);
      throw error;
    } finally {
      window.clearTimeout(timer);
      if (upstreamSignal && detach) {
        try { upstreamSignal.removeEventListener("abort", detach); } catch (_) {}
      }
    }
  }

  async function transformResponse(route, response) {
    if (!response.ok) return response;
    if (!["/me", "/me/text-to-strategy/compile", "/me/automation-schedules"].includes(route)) return response;
    let payload;
    try { payload = await response.clone().json(); } catch (_) { return response; }

    if (route === "/me") {
      lastMe = payload;
      if (!payload?.authenticated) {
        try { localStorage.removeItem("foa-session-v2"); } catch (_) {}
      }
    }
    if (route === "/me/text-to-strategy/compile" && payload?.custom_strategy) {
      payload.canonical = payload.custom_strategy;
      payload.best_possible_interpretation = payload.rules?.length
        ? `${payload.market_label || "Selected market"} · ${payload.contract_label || "Selected contract"} · ${payload.rules.join("; ")}`
        : "Compiled to the nearest supported deterministic strategy.";
      payload.unsupported_or_adjusted_items = Array.isArray(payload.adjustments) ? payload.adjustments : [];
    }
    if (route === "/me/automation-schedules") {
      const items = Array.isArray(payload.items) ? payload.items : [];
      payload.schedules = items.map((item) => ({ ...item, scheduled_local: item.scheduled_local || item.date_time_local || "" }));
    }
    return responseJSON(payload, response.status, { "X-DerivAdmin-Boundary": "6F-2" });
  }

  window.fetch = async (input, options = {}) => {
    const path = pathOf(input);
    const route = routeOf(path);
    const method = String(options.method || (input instanceof Request ? input.method : "GET") || "GET").toUpperCase();

    if (method === "GET") {
      const live = livePayload(path);
      if (live) {
        if (route === "/me") lastMe = live;
        return responseJSON(live, 200, { "X-DerivAdmin-Source": "vps-live-cache" });
      }
      if (route === "/metrics/summary") return responseJSON({ performance_profile: "full-vps-background-summary" });
    } else if (window.FOA_VPS_LIVE_CACHE) {
      window.FOA_VPS_LIVE_CACHE.savedAt = 0;
    }

    const nextOptions = transformedBody(route, options);
    const response = await boundedFetch(rewrittenURL(input), nextOptions);
    return transformResponse(route, response);
  };

  window.FOA_API_URL = (path) => {
    const value = String(path || "");
    if (value.startsWith(`${API_PREFIX}/`)) return value;
    return shouldProxy(value) ? `${API_PREFIX}${value}` : value;
  };
  window.FOA_BACKEND_PROXY_MODE = "direct-vps-same-origin-rest-6f2";
  window.FOA_VPS_API_BOUNDARY_VERSION = "20260817-6f2-1";
})();
