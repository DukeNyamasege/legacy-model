(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_STRATEGY_PERSISTENCE_V1__) return;
  window.__DERIVADMIN_DIRECT_STRATEGY_PERSISTENCE_V1__ = true;

  const directFetch = window.fetch.bind(window);
  const BUILDER_DRAFT_KEY = "derivadmin-builder-draft-v2";
  const MARKET_OPEN_KEY = "derivadmin-builder-market-open-v1";
  let builderSyncTimer = 0;
  let marketObserver = null;

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

  function readJson(key, fallback = null) {
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || "null");
      return parsed == null ? fallback : parsed;
    } catch (_) { return fallback; }
  }

  function writeJson(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {}
  }

  function builderRoute() {
    return String(location.hash || "#home").replace(/^#\/?/, "").split("?", 1)[0].toLowerCase() === "builder";
  }

  function appState() {
    try { return window.FOA_FINAL_UI?.state?.() || null; }
    catch (_) { return null; }
  }

  function validBuilderSelection(value) {
    return Boolean(value && value.builder && typeof value.builder === "object");
  }

  function persistBuilderState() {
    if (!builderRoute()) return;
    const state = appState();
    const selected = state?.selectedStrategy;
    if (!validBuilderSelection(selected)) return;
    writeJson(BUILDER_DRAFT_KEY, {
      version: 2,
      saved_at: Date.now(),
      selectedStrategy: selected,
    });
  }

  function scheduleBuilderPersist() {
    clearTimeout(builderSyncTimer);
    builderSyncTimer = window.setTimeout(persistBuilderState, 0);
  }

  function marketShouldStayOpen() {
    try { return localStorage.getItem(MARKET_OPEN_KEY) === "1"; }
    catch (_) { return false; }
  }

  function saveMarketOpen(open) {
    try { localStorage.setItem(MARKET_OPEN_KEY, open ? "1" : "0"); } catch (_) {}
  }

  function bindMarketDropdown() {
    const details = document.querySelector(".restored-builder details.builder-market-dropdown");
    if (!details) return;
    if (details.dataset.persistOpenBound !== "1") {
      details.dataset.persistOpenBound = "1";
      details.addEventListener("toggle", () => saveMarketOpen(Boolean(details.open)));
    }
    if (marketShouldStayOpen() && !details.open) details.open = true;
  }

  function restoreOrPersistBuilder() {
    if (!builderRoute()) return;
    const state = appState();
    if (!state) return;
    if (validBuilderSelection(state.selectedStrategy)) {
      persistBuilderState();
      bindMarketDropdown();
      return;
    }
    const saved = readJson(BUILDER_DRAFT_KEY, null);
    if (!validBuilderSelection(saved?.selectedStrategy)) {
      bindMarketDropdown();
      return;
    }
    state.selectedStrategy = saved.selectedStrategy;
    window.setTimeout(() => {
      try { window.FOA_FINAL_UI?.refresh?.(); } catch (_) {}
      window.setTimeout(bindMarketDropdown, 0);
    }, 0);
  }

  function scheduleRestore() {
    window.setTimeout(restoreOrPersistBuilder, 0);
    window.setTimeout(bindMarketDropdown, 20);
  }

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!target?.closest?.(".restored-builder")) return;
    if (target.matches?.("[data-builder-market-mode-select]") && String(target.value || "") === "all") {
      // Selecting All Markets deliberately keeps the market list visible until
      // the trader explicitly closes the details control.
      saveMarketOpen(true);
    }
    scheduleBuilderPersist();
    window.setTimeout(bindMarketDropdown, 0);
  }, true);

  document.addEventListener("input", (event) => {
    if (!event.target?.closest?.(".restored-builder")) return;
    scheduleBuilderPersist();
  }, true);

  document.addEventListener("click", (event) => {
    if (!event.target?.closest?.(".restored-builder")) return;
    scheduleBuilderPersist();
    window.setTimeout(bindMarketDropdown, 0);
  }, true);

  window.addEventListener("hashchange", scheduleRestore);
  window.addEventListener("pageshow", scheduleRestore);
  window.addEventListener("derivadmin:direct-trade", scheduleBuilderPersist);

  const root = document.getElementById("derivadmin-root");
  if (root && "MutationObserver" in window) {
    marketObserver = new MutationObserver(() => {
      if (!builderRoute()) return;
      bindMarketDropdown();
    });
    marketObserver.observe(root, { childList: true, subtree: true });
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

  scheduleRestore();
})();
