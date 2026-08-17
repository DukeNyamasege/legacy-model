(() => {
  "use strict";

  const nativeFetch = window.fetch.bind(window);
  const nativeEventSource = window.EventSource;
  const API_PREFIX = "/api";
  const READ_TIMEOUT_MS = 10000;
  const WRITE_TIMEOUT_MS = 8000;
  const LIVE_CACHE_MAX_AGE_MS = 5000;

  window.FOA_FULL_VPS_FRONTEND = true;
  window.FOA_NETLIFY_FRONTEND = true; // compatibility for existing dashboard layers
  window.FOA_NATIVE_EVENT_SOURCE = nativeEventSource;

  // Full VPS realtime uses the dedicated WebSocket client. Prevent older SSE
  // appenders from opening duplicate long-lived connections.
  try { window.EventSource = undefined; } catch (_) {}

  function responseJSON(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
      status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-FOA-Source": "vps-live-cache",
      },
    });
  }

  function asURL(input) {
    try {
      if (input instanceof Request) return new URL(input.url, window.location.origin);
      return new URL(String(input), window.location.origin);
    } catch (_) {
      return null;
    }
  }

  function originalPath(input) {
    const url = asURL(input);
    return url ? `${url.pathname}${url.search}` : "";
  }

  function basePath(value) {
    return String(value || "").split("?", 1)[0];
  }

  function shouldProxy(path) {
    const route = basePath(path);
    if (!route || route.startsWith(`${API_PREFIX}/`)) return false;
    return (
      route === "/health"
      || route.startsWith("/health/")
      || route === "/me"
      || route.startsWith("/me/")
      || route === "/metrics"
      || route.startsWith("/metrics/")
    );
  }

  function rewriteURL(input) {
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

  function cachedPayload(path) {
    const cache = window.FOA_NETLIFY_LIVE_CACHE;
    if (!cache || Date.now() - Number(cache.savedAt || 0) > LIVE_CACHE_MAX_AGE_MS) return null;
    const route = basePath(path);
    if (route === "/me") return cache.me || null;
    if (route === "/me/trading-lifecycle" || route === "/me/execution-runtime") return cache.lifecycle || null;
    if (route === "/me/trades/today") return cache.trades || null;
    return null;
  }

  function timeoutFor(method) {
    return method === "GET" || method === "HEAD" ? READ_TIMEOUT_MS : WRITE_TIMEOUT_MS;
  }

  async function boundedFetch(input, options = {}) {
    const method = String(
      options.method || (input instanceof Request ? input.method : "GET") || "GET",
    ).toUpperCase();
    const timeoutMs = timeoutFor(method);
    const controller = new AbortController();
    const upstreamSignal = options.signal || (input instanceof Request ? input.signal : null);
    let upstreamAbort = null;
    if (upstreamSignal) {
      upstreamAbort = () => controller.abort(upstreamSignal.reason);
      if (upstreamSignal.aborted) upstreamAbort();
      else upstreamSignal.addEventListener("abort", upstreamAbort, { once: true });
    }
    const timer = window.setTimeout(
      () => controller.abort(new Error("backend timeout")),
      timeoutMs,
    );
    try {
      return await nativeFetch(input, {
        ...options,
        credentials: options.credentials || "same-origin",
        cache: options.cache || "no-store",
        signal: controller.signal,
      });
    } catch (error) {
      if (controller.signal.aborted && !upstreamSignal?.aborted) {
        throw new Error(`Backend request timed out after ${(timeoutMs / 1000).toFixed(1)}s`);
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
      if (upstreamSignal && upstreamAbort) {
        try { upstreamSignal.removeEventListener("abort", upstreamAbort); } catch (_) {}
      }
    }
  }

  function clearStaleBrowserSession() {
    try {
      localStorage.removeItem("foa-session-v2");
      localStorage.removeItem("foa-builder-last-good-snapshot-v1");
    } catch (_) {}
  }

  function isLifecycleMutation(route) {
    return ["/me/resume-trading", "/me/auto-trade", "/me/stop-trading"].includes(route);
  }

  function applyLifecycleMutation(payload) {
    if (!payload || typeof payload !== "object") return;
    const cache = window.FOA_NETLIFY_LIVE_CACHE || {};
    const runtimeState = String(payload.runtime_state || payload.state || "STOPPED").toUpperCase();
    const enabled = payload.enabled === undefined
      ? !["STOPPED", "ERROR"].includes(runtimeState)
      : Boolean(payload.enabled);
    const executionStatus = String(
      payload.execution_status || payload.state || (enabled ? "starting" : "stopped"),
    ).toLowerCase();
    const reason = String(payload.message || payload.reason || "");
    cache.lifecycle = {
      ...(cache.lifecycle || {}),
      authenticated: true,
      enabled,
      runtime_state: runtimeState,
      execution_status: executionStatus,
      reason,
      fatal: runtimeState === "ERROR",
    };
    if (cache.me) {
      cache.me = {
        ...cache.me,
        enabled,
        execution_status: executionStatus,
        execution_status_reason: reason,
      };
    }
    cache.savedAt = Date.now();
    window.FOA_NETLIFY_LIVE_CACHE = cache;
    document.dispatchEvent(new CustomEvent("foa:backend-lifecycle", { detail: cache.lifecycle }));
  }

  window.fetch = async (input, options = {}) => {
    const path = originalPath(input);
    const route = basePath(path);
    const method = String(
      options.method || (input instanceof Request ? input.method : "GET") || "GET",
    ).toUpperCase();

    if (method === "GET") {
      const live = cachedPayload(path);
      if (live) return responseJSON(live);
      if (route === "/metrics/summary") {
        return responseJSON({ performance_profile: "full-vps-background-summary" });
      }
    } else if (window.FOA_NETLIFY_LIVE_CACHE && !isLifecycleMutation(route)) {
      window.FOA_NETLIFY_LIVE_CACHE.savedAt = 0;
    }

    const response = await boundedFetch(rewriteURL(input), options);

    if (method === "GET" && route === "/me" && response.ok) {
      try {
        const payload = await response.clone().json();
        if (!payload?.authenticated) clearStaleBrowserSession();
      } catch (_) {}
    }

    if (method !== "GET" && response.ok && isLifecycleMutation(route)) {
      try { applyLifecycleMutation(await response.clone().json()); } catch (_) {}
    }
    return response;
  };

  window.FOA_API_URL = (path) => {
    const value = String(path || "");
    if (value.startsWith(`${API_PREFIX}/`)) return value;
    return shouldProxy(value) ? `${API_PREFIX}${value}` : value;
  };
  window.FOA_BACKEND_PROXY_MODE = "full-vps-same-origin-rest-v3";
  window.FOA_VPS_API_BOUNDARY_VERSION = "20260817-1";
})();
