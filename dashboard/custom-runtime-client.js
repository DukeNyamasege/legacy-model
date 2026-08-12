(() => {
  "use strict";

  const nativeFetch = window.fetch.bind(window);
  let deferredSettings = null;
  let runtimeSnapshot = null;
  const cache = new Map();
  const CACHE_TTL = new Map([
    ["/me", 30000],
    ["/metrics/summary", 60000],
    ["/me/custom-strategy", 300000],
  ]);

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
    for (const key of Array.from(cache.keys())) {
      if (key.startsWith("/me") || key.startsWith("/metrics/summary")) cache.delete(key);
    }
  }

  function cachedFetch(input, options, path) {
    const key = path;
    const ttl = CACHE_TTL.get(basePath(path));
    if (!ttl) return nativeFetch(input, options);
    const now = Date.now();
    const hit = cache.get(key);
    if (hit && now - hit.savedAt < ttl) return hit.response.clone();
    return nativeFetch(input, options).then((response) => {
      if (response.ok) cache.set(key, { savedAt: now, response: response.clone() });
      return response;
    });
  }

  function runtimeText(payload) {
    const state = String(payload?.runtime_state || "STOPPED").toUpperCase();
    const reason = String(payload?.reason || "").trim();
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
      if (text) text.textContent = `${label} - ${detail}`;
    });

    document.querySelectorAll("[data-main-action]").forEach((button) => {
      button.dataset.mainAction = enabled ? "stop" : "start";
      button.textContent = enabled ? "Stop Auto Trading" : "Start Auto Trading";
      button.classList.toggle("danger", enabled);
      button.disabled = state === "EXECUTING";
    });

    document.querySelectorAll(".trades-control-panel").forEach((panel) => {
      const title = panel.querySelector("h2");
      const paragraphs = panel.querySelectorAll("p");
      if (title) title.textContent = label;
      if (paragraphs.length > 1) paragraphs[1].textContent = detail;
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
      if (notice) notice.textContent = detail;
    } else if (notice) {
      notice.remove();
    }
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
      const response = await nativeFetch(input, next);
      if (response.ok) {
        deferredSettings = null;
        invalidateAccountCache();
      }
      await inspectRuntimeResponse(response);
      return response;
    }

    if (method !== "GET") invalidateAccountCache();
    const response = method === "GET"
      ? await cachedFetch(input, options, path)
      : await nativeFetch(input, options);
    if (route === "/me/resume-trading" || route === "/me/auto-trade") {
      await inspectRuntimeResponse(response);
    }
    return response;
  };

  async function pollRuntime() {
    if (document.hidden) return;
    try {
      const response = await nativeFetch("/me/execution-runtime", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      applyRuntime(await response.json());
    } catch (_) {}
  }

  window.addEventListener("dashboard:snapshot-ready", () => {
    if (runtimeSnapshot) applyRuntime(runtimeSnapshot);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollRuntime();
  });
  window.setInterval(pollRuntime, 4000);
  pollRuntime();

  window.FOA_CUSTOM_DIRECT_RUNTIME_CLIENT = "20260812-v1";
})();
