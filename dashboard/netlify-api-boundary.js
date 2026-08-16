(() => {
  "use strict";

  const nativeFetch = window.fetch.bind(window);
  const nativeEventSource = window.EventSource;
  const API_PREFIX = "/api";
  const FRONTEND_RUNTIME = String(
    document.querySelector('meta[name="frontend-runtime"]')?.content || "",
  ).trim().toLowerCase();
  const FULL_VPS = FRONTEND_RUNTIME.startsWith("full-vps-same-origin");

  // The old 3.2-second ceiling was designed for a remote Netlify -> VPS hop. On
  // the full VPS it caused a healthy authenticated dashboard to abort a local
  // /api/me request and render the public landing underneath live account KPIs.
  // Local reads get a realistic bounded SLA plus one safe GET-only retry. Writes
  // are never retried automatically because a repeated POST could duplicate a
  // state transition even though trading BUYs are protected elsewhere.
  const GET_TIMEOUT_MS = FULL_VPS ? 6500 : 3200;
  const WRITE_TIMEOUT_MS = FULL_VPS ? 8500 : 5200;
  const GET_RETRY_COUNT = FULL_VPS ? 1 : 0;
  const LIVE_CACHE_MAX_AGE_MS = FULL_VPS ? 30000 : 5000;

  // Keep the historical compatibility marker because older dashboard assets use
  // it as a feature flag, but expose the real hosting mode separately.
  window.FOA_NETLIFY_FRONTEND = true;
  window.FOA_FULL_VPS_FRONTEND = FULL_VPS;
  window.FOA_NATIVE_EVENT_SOURCE = nativeEventSource;
  try {
    window.EventSource = undefined;
  } catch (_) {}

  function responseJSON(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
      status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-FOA-Source": FULL_VPS ? "vps-live-cache" : "netlify-live-cache",
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
    if (!url) return "";
    return `${url.pathname}${url.search}`;
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
    if (input instanceof Request) {
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
    return next;
  }

  function cachedPayload(path) {
    const cache = window.FOA_NETLIFY_LIVE_CACHE;
    if (!cache || Date.now() - Number(cache.savedAt || 0) > LIVE_CACHE_MAX_AGE_MS) return null;
    const route = basePath(path);
    if (route === "/me") return cache.me || null;
    if (route === "/me/trading-lifecycle" || route === "/me/execution-runtime") {
      return cache.lifecycle || null;
    }
    if (route === "/me/trades/today") return cache.trades || null;
    return null;
  }

  function isLifecycleMutation(route) {
    return [
      "/me/resume-trading",
      "/me/auto-trade",
      "/me/stop-trading",
    ].includes(route);
  }

  function applyLifecycleMutation(payload) {
    if (!payload || typeof payload !== "object") return;
    const cache = window.FOA_NETLIFY_LIVE_CACHE || {};
    const runtimeState = String(payload.runtime_state || payload.state || "STOPPED").toUpperCase();
    const enabled = payload.enabled === undefined
      ? !["STOPPED", "ERROR"].includes(runtimeState)
      : Boolean(payload.enabled);
    const executionStatus = String(
      payload.execution_status
      || payload.state
      || (enabled ? "starting" : "stopped"),
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
    document.dispatchEvent(new CustomEvent("foa:backend-lifecycle", {
      detail: cache.lifecycle,
    }));
  }

  function timeoutFor(method) {
    return method === "GET" || method === "HEAD" ? GET_TIMEOUT_MS : WRITE_TIMEOUT_MS;
  }

  function safeGetMethod(method) {
    return method === "GET" || method === "HEAD";
  }

  async function oneBoundedFetch(input, options, timeoutMs, upstreamSignal) {
    const controller = new AbortController();
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
    } finally {
      window.clearTimeout(timer);
      if (upstreamSignal && upstreamAbort) {
        try { upstreamSignal.removeEventListener("abort", upstreamAbort); } catch (_) {}
      }
    }
  }

  async function boundedFetch(input, options = {}, sourcePath = "") {
    const method = String(
      options.method || (input instanceof Request ? input.method : "GET") || "GET",
    ).toUpperCase();
    const timeoutMs = timeoutFor(method);
    const upstreamSignal = options.signal || (input instanceof Request ? input.signal : null);
    const attempts = safeGetMethod(method) ? 1 + GET_RETRY_COUNT : 1;
    let lastError = null;

    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        return await oneBoundedFetch(input, options, timeoutMs, upstreamSignal);
      } catch (error) {
        if (upstreamSignal?.aborted) throw error;
        lastError = error;

        if (safeGetMethod(method)) {
          const live = cachedPayload(sourcePath);
          if (live) return responseJSON(live);
        }

        if (attempt + 1 < attempts) {
          await new Promise((resolve) => window.setTimeout(resolve, 120));
          continue;
        }
      }
    }

    if (FULL_VPS && safeGetMethod(method)) {
      throw new Error(
        `VPS backend read did not respond after ${attempts} attempts; reconnecting automatically`,
      );
    }
    if (lastError?.name === "AbortError" || String(lastError?.message || "").includes("backend timeout")) {
      throw new Error(`Backend request timed out after ${(timeoutMs / 1000).toFixed(1)}s`);
    }
    throw lastError || new Error("Backend request failed");
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
        return responseJSON({
          performance_profile: FULL_VPS
            ? "full-vps-realtime-background-summary"
            : "netlify-static-background-summary",
        });
      }
    } else if (!isLifecycleMutation(route)) {
      if (window.FOA_NETLIFY_LIVE_CACHE) {
        window.FOA_NETLIFY_LIVE_CACHE.savedAt = 0;
      }
    }

    const response = await boundedFetch(rewriteURL(input), options, path);
    if (method !== "GET" && response.ok && isLifecycleMutation(route)) {
      try {
        applyLifecycleMutation(await response.clone().json());
      } catch (_) {}
    }
    return response;
  };

  window.FOA_API_URL = (path) => {
    const value = String(path || "");
    if (value.startsWith(`${API_PREFIX}/`)) return value;
    return shouldProxy(value) ? `${API_PREFIX}${value}` : value;
  };
  window.FOA_BACKEND_PROXY_MODE = FULL_VPS
    ? "full-vps-same-origin-rest-v3-resilient"
    : "netlify-same-origin-rest-v2-optimistic-lifecycle";
})();
