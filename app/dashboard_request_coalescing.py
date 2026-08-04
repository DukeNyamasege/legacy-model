from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from app.dashboard_stability_fix import _remove_route
from app.multi_strategy_ui import (
    _append as multi_strategy_append,
    _headers as multi_strategy_headers,
    base_compat_script,
    base_dashboard_script,
)

_INSTALLED = False
UI_VERSION = "20260804-request-coalescing-2"

_REQUEST_BROKER_JS = r'''

/* FOA_REQUEST_COALESCING_VERSION:20260804-2
   Several historical UI layers poll the same personal routes. Keep those visual
   layers compatible while one broker coalesces identical requests, caches short
   reads, and aborts stale Demo responses before switching to Real (and vice versa). */
(() => {
  "use strict";
  const VERSION = "20260804-2";
  if (window.FOA_REQUEST_COALESCING_VERSION) return;

  const nativeFetch = window.fetch.bind(window);
  const cache = new Map();
  const inFlight = new Map();
  const controllers = new Set();
  let generation = 0;

  const managedTTL = new Map([
    ["/me", 4000],
    ["/me/trades/today", 4000],
    ["/me/trading-lifecycle", 4000],
    ["/me/aidr-status", 5000],
    ["/me/strategy-settings", 10000],
    ["/metrics/public-traders", 15000],
    ["/metrics/summary", 5000],
  ]);
  const ignoredQueryKeys = new Set([
    "t", "ts", "identity", "live_metrics", "reset_controls", "reset_refresh",
  ]);

  function requestParts(input, init = {}) {
    const raw = typeof input === "string" ? input : input?.url;
    if (!raw) return null;
    let url;
    try { url = new URL(raw, window.location.href); }
    catch (_) { return null; }
    if (url.origin !== window.location.origin) return null;
    const method = String(init.method || (typeof input !== "string" && input?.method) || "GET").toUpperCase();
    return { url, method };
  }

  function ttlFor(pathname) {
    return managedTTL.get(pathname) || 0;
  }

  function cacheKey(parts) {
    const params = new URLSearchParams(parts.url.search);
    ignoredQueryKeys.forEach(key => params.delete(key));
    const query = params.toString();
    return `${parts.method}:${parts.url.pathname}${query ? `?${query}` : ""}:g${generation}`;
  }

  function responseFrom(entry) {
    return new Response(entry.body, {
      status: entry.status,
      statusText: entry.statusText,
      headers: new Headers(entry.headers),
    });
  }

  function abortManagedReads() {
    generation += 1;
    for (const controller of Array.from(controllers)) {
      try { controller.abort("account-generation-changed"); } catch (_) {}
    }
    controllers.clear();
    cache.clear();
    inFlight.clear();
    document.body.dataset.foaAccountRequestGeneration = String(generation);
  }

  async function fetchAndStore(input, init, key, ttl) {
    const controller = new AbortController();
    controllers.add(controller);
    const externalSignal = init?.signal;
    const abortFromExternal = () => {
      try { controller.abort(externalSignal?.reason || "external-abort"); } catch (_) {}
    };
    if (externalSignal) {
      if (externalSignal.aborted) abortFromExternal();
      else externalSignal.addEventListener("abort", abortFromExternal, { once: true });
    }
    const requestGeneration = generation;
    try {
      const response = await nativeFetch(input, { ...init, signal: controller.signal });
      const body = await response.clone().text();
      const entry = {
        body,
        status: response.status,
        statusText: response.statusText,
        headers: Array.from(response.headers.entries()),
        expiresAt: Date.now() + ttl,
        generation: requestGeneration,
      };
      if (response.ok && requestGeneration === generation) cache.set(key, entry);
      return entry;
    } finally {
      controllers.delete(controller);
      if (externalSignal) externalSignal.removeEventListener("abort", abortFromExternal);
    }
  }

  window.fetch = function coalescedFetch(input, init = {}) {
    const parts = requestParts(input, init);
    if (!parts) return nativeFetch(input, init);

    const isSwitch = parts.method === "POST" && parts.url.pathname === "/me/switch-account";
    const isPersonalMutation = parts.method !== "GET" && parts.url.pathname.startsWith("/me/");
    if (isSwitch) abortManagedReads();

    if (parts.method !== "GET") {
      return nativeFetch(input, init).then(response => {
        if (response.ok && isPersonalMutation) {
          cache.clear();
          inFlight.clear();
        }
        return response;
      });
    }

    const ttl = ttlFor(parts.url.pathname);
    if (!ttl) return nativeFetch(input, init);
    const key = cacheKey(parts);
    const now = Date.now();
    const existing = cache.get(key);
    if (existing && (existing.expiresAt > now || (document.hidden && now - existing.expiresAt < 60000))) {
      return Promise.resolve(responseFrom(existing));
    }
    const pending = inFlight.get(key);
    if (pending) return pending.then(responseFrom);

    const promise = fetchAndStore(input, init, key, ttl)
      .finally(() => inFlight.delete(key));
    inFlight.set(key, promise);
    return promise.then(responseFrom);
  };

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      for (const [key, entry] of cache.entries()) {
        if (entry.expiresAt <= Date.now()) cache.delete(key);
      }
    }
  });
  window.addEventListener("pageshow", event => {
    if (event.persisted) abortManagedReads();
  });

  window.FOA_ABORT_STALE_ACCOUNT_REQUESTS = abortManagedReads;
  window.FOA_REQUEST_COALESCING_VERSION = VERSION;
  document.documentElement.dataset.foaRequestCoalescingVersion = VERSION;
})();
'''


def _script(*, compatibility: bool = False) -> str:
    source = multi_strategy_append(
        base_compat_script() if compatibility else base_dashboard_script()
    )
    if "FOA_REQUEST_COALESCING_VERSION" in source:
        return source
    # The broker must execute before any historical dashboard IIFE can call boot()
    # and start its polling interval. Appending it was too late when the script was
    # loaded after DOMContentLoaded.
    return _REQUEST_BROKER_JS + "\n" + source


def _headers() -> dict[str, str]:
    return {
        **multi_strategy_headers(),
        "X-FOA-UI-Version": UI_VERSION,
        "X-FOA-Request-Coalescing": "1",
    }


def install_dashboard_request_coalescing(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    @app.get("/ui/dashboard-v2.js", include_in_schema=False)
    def coalesced_dashboard_script() -> Response:
        return Response(
            _script(),
            media_type="application/javascript",
            headers=_headers(),
        )

    @app.get("/ui/simplified-dashboard.js", include_in_schema=False)
    def coalesced_compat_script() -> Response:
        return Response(
            _script(compatibility=True),
            media_type="application/javascript",
            headers=_headers(),
        )

    app.state.dashboard_request_coalescing_installed = True
    app.state.dashboard_request_coalescing_version = UI_VERSION
    _INSTALLED = True
