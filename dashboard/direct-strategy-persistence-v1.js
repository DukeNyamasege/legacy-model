(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_STRATEGY_PERSISTENCE_V1__) return;
  window.__DERIVADMIN_DIRECT_STRATEGY_PERSISTENCE_V1__ = true;

  const directFetch = window.fetch.bind(window);

  function pathFor(input) {
    try {
      const raw = typeof input === "string" ? input : input?.url;
      return new URL(String(raw || ""), location.origin).pathname.replace(/^\/api(?=\/)/, "");
    } catch (_) {
      return "";
    }
  }

  function methodFor(input, init) {
    return String(init?.method || (typeof input !== "string" ? input?.method : "") || "GET").toUpperCase();
  }

  async function bodyText(input, init) {
    if (typeof init?.body === "string") return init.body;
    if (init?.body && typeof init.body === "object" && !(init.body instanceof FormData) && !(init.body instanceof Blob)) {
      try { return JSON.stringify(init.body); } catch (_) { return ""; }
    }
    if (typeof input !== "string" && input?.clone) {
      try { return await input.clone().text(); } catch (_) {}
    }
    return "";
  }

  function persistInBackground(body) {
    if (!body) return;
    try {
      const request = new XMLHttpRequest();
      request.open("POST", "/api/me/custom-strategy", true);
      request.withCredentials = true;
      request.timeout = 9000;
      request.setRequestHeader("Content-Type", "application/json");
      request.setRequestHeader("Accept", "application/json");
      // Saving must feel local/instant. Slow persistence is deliberately detached
      // from the UI response and will be written again by direct-execution/arm at
      // Run time if this background synchronization fails.
      request.send(body);
    } catch (_) {}
  }

  window.fetch = async function directStrategyPersistenceFetch(input, init) {
    const path = pathFor(input);
    const method = methodFor(input, init);
    const body = path === "/me/custom-strategy" && method === "POST"
      ? await bodyText(input, init)
      : "";
    const response = await directFetch(input, init);
    if (body && response?.ok) persistInBackground(body);
    return response;
  };
})();
