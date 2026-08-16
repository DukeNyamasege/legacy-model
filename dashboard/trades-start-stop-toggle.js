(() => {
  "use strict";

  const FALLBACK_POLL_MS = 10000;
  const HARD_STOP_STATUSES = new Set([
    "stopped",
    "take_profit",
    "stop_loss",
    "inactive",
    "logged_out",
    "missing",
  ]);
  const PAUSED_STATUSES = new Set(["paused", "manual_pause"]);
  let lastLifecycle = null;
  let toggleInFlight = false;
  let syncInFlight = false;
  let syncQueued = false;

  function toggleButton() {
    return document.querySelector('[data-stop-trades]');
  }

  function mainActionState() {
    const main = document.querySelector('[data-main-action]');
    const action = String(main?.dataset?.mainAction || "").toLowerCase();
    if (action === "stop") return true;
    if (action === "start" || action === "resume") return false;
    return null;
  }

  function cachedLifecycle() {
    return lastLifecycle || window.FOA_NETLIFY_LIVE_CACHE?.lifecycle || null;
  }

  function isTradingActive(lifecycle) {
    const status = String(lifecycle?.execution_status || "").toLowerCase();
    const life = String(lifecycle?.lifecycle || "").toLowerCase();

    if (HARD_STOP_STATUSES.has(status) || HARD_STOP_STATUSES.has(life)) return false;
    if (PAUSED_STATUSES.has(status) || PAUSED_STATUSES.has(life)) return false;
    if (lifecycle?.enabled === true) return true;
    if (lifecycle?.enabled === false) return false;
    if (["running", "connecting", "reconnecting"].includes(life)) return true;
    if (["running", "connecting", "reconnecting"].includes(status)) return true;

    const mainState = mainActionState();
    if (mainState !== null) return mainState;
    return false;
  }

  function startMode(lifecycle) {
    const status = String(lifecycle?.execution_status || "").toLowerCase();
    const life = String(lifecycle?.lifecycle || "").toLowerCase();
    return PAUSED_STATUSES.has(status) || PAUSED_STATUSES.has(life)
      ? "continue"
      : "start_again";
  }

  function applyButtonState(lifecycle = cachedLifecycle(), busyAction = "") {
    if (lifecycle) lastLifecycle = lifecycle;
    const button = toggleButton();
    if (!button) return;

    const active = isTradingActive(lastLifecycle);
    let state = active ? "running" : "stopped";
    if (busyAction === "start") state = "starting";
    if (busyAction === "stop") state = "stopping";

    button.dataset.tradingState = state;
    button.dataset.stopped = active ? "false" : "true";
    button.disabled = toggleInFlight;

    const label = state === "running"
      ? "Stop Trading"
      : state === "starting"
        ? "Starting Trading..."
        : state === "stopping"
          ? "Stopping Trading..."
          : "Start Trading";
    button.setAttribute("aria-label", label);
    button.title = label;
  }

  async function readLifecycle() {
    const response = await fetch(`/me/trading-lifecycle?_=${Date.now()}`, {
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        Pragma: "no-cache",
      },
    });
    if (!response.ok) throw new Error(`Trading status check failed (${response.status})`);
    return response.json();
  }

  async function postLifecycle(path, body) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!response.ok) {
      let message = `Trading action failed (${response.status})`;
      try {
        const payload = await response.json();
        message = payload?.detail || payload?.message || message;
      } catch (_) {}
      throw new Error(message);
    }
    return response.json();
  }

  async function synchronizeState() {
    if (toggleInFlight || syncInFlight || !toggleButton()) return;
    syncInFlight = true;
    try {
      const lifecycle = await readLifecycle();
      lastLifecycle = lifecycle;
      applyButtonState(lifecycle);
    } catch (_) {
      if (lastLifecycle) applyButtonState(lastLifecycle);
    } finally {
      syncInFlight = false;
      if (syncQueued) {
        syncQueued = false;
        window.setTimeout(synchronizeState, 0);
      }
    }
  }

  function queueSynchronize(delay = 0) {
    if (syncInFlight) {
      syncQueued = true;
      return;
    }
    window.setTimeout(synchronizeState, delay);
  }

  async function toggleTrading() {
    if (toggleInFlight) return;
    toggleInFlight = true;
    try {
      // The signed live snapshot is authoritative while connected, so a click no
      // longer waits for a redundant pre-action HTTP status request. Only a cold
      // page with no snapshot pays that one-time fallback read.
      const lifecycle = cachedLifecycle() || await readLifecycle();
      lastLifecycle = lifecycle;
      const active = isTradingActive(lifecycle);
      applyButtonState(lifecycle, active ? "stop" : "start");

      const result = active
        ? await postLifecycle("/me/stop-trading", {})
        : await postLifecycle("/me/resume-trading", { mode: startMode(lifecycle) });

      lastLifecycle = {
        ...lifecycle,
        ...result,
        enabled: !active,
      };
      applyButtonState(lastLifecycle);
      window.dispatchEvent(new CustomEvent(
        active ? "foa:trading-stopped-from-trades" : "foa:trading-started-from-trades",
        { detail: lastLifecycle },
      ));
    } catch (error) {
      window.alert(String(error?.message || error));
    } finally {
      toggleInFlight = false;
      applyButtonState(lastLifecycle);
      // Realtime normally confirms immediately. A delayed one-shot read covers a
      // reconnecting browser without bringing back the previous 800ms poll loop.
      queueSynchronize(700);
    }
  }

  document.addEventListener("click", (event) => {
    const tradesButton = event.target?.closest?.("[data-stop-trades]");
    if (tradesButton) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void toggleTrading();
      return;
    }
    if (event.target?.closest?.("[data-main-action]")) {
      const live = window.FOA_NETLIFY_LIVE_CACHE?.lifecycle;
      if (live) applyButtonState(live);
    }
  }, true);

  window.addEventListener("foa:vps-live-snapshot", (event) => {
    const lifecycle = event.detail?.lifecycle;
    if (!lifecycle) return;
    lastLifecycle = lifecycle;
    if (!toggleInFlight) applyButtonState(lifecycle);
  });

  const observer = new MutationObserver(() => {
    if (!toggleInFlight && toggleButton()) applyButtonState(cachedLifecycle());
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener("focus", () => {
    if (cachedLifecycle()) applyButtonState(cachedLifecycle());
    else queueSynchronize(0);
  });
  window.addEventListener("pageshow", () => {
    if (cachedLifecycle()) applyButtonState(cachedLifecycle());
    else queueSynchronize(0);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !cachedLifecycle()) queueSynchronize(0);
  });

  window.addEventListener("foa:trading-stopped-from-trades", () => applyButtonState(cachedLifecycle()));
  window.addEventListener("foa:trading-started-from-trades", () => applyButtonState(cachedLifecycle()));

  // Safety fallback only. Normal UI state is event-driven from the same-origin
  // signed WebSocket and therefore incurs no recurring 800ms HTTP request loop.
  window.setInterval(() => {
    if (document.hidden || toggleInFlight) return;
    const cache = window.FOA_NETLIFY_LIVE_CACHE;
    if (cache && Date.now() - Number(cache.savedAt || 0) < FALLBACK_POLL_MS * 2) return;
    queueSynchronize(0);
  }, FALLBACK_POLL_MS);

  if (cachedLifecycle()) applyButtonState(cachedLifecycle());
  else queueSynchronize(0);

  window.FOA_TRADES_START_STOP_TOGGLE_VERSION = "20260816-vps-realtime-1";
})();
