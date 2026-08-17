(() => {
  "use strict";

  const POLL_MS = 800;
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

  function isTradingActive(lifecycle) {
    const status = String(lifecycle?.execution_status || "").toLowerCase();
    const life = String(lifecycle?.lifecycle || "").toLowerCase();

    if (HARD_STOP_STATUSES.has(status) || HARD_STOP_STATUSES.has(life)) return false;
    if (PAUSED_STATUSES.has(status) || PAUSED_STATUSES.has(life)) return false;

    // `enabled` is the same server-side execution switch used by the main
    // Dashboard control. Connecting/reconnecting is still active Auto Trading,
    // so the Trades substitute must display Stop Trading during those states.
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

  function applyButtonState(lifecycle = lastLifecycle, busyAction = "") {
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
      // Keep the last confirmed state. A failed UI read must never flip the
      // visible control to Start while trading may still be active.
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

  async function toggleTrading(button) {
    if (toggleInFlight) return;
    toggleInFlight = true;
    try {
      const lifecycle = await readLifecycle();
      lastLifecycle = lifecycle;
      const active = isTradingActive(lifecycle);
      applyButtonState(lifecycle, active ? "stop" : "start");

      const result = active
        ? await postLifecycle("/me/stop-trading", {})
        : await postLifecycle("/me/resume-trading", { mode: startMode(lifecycle) });

      lastLifecycle = result;
      applyButtonState(result);
      window.dispatchEvent(new CustomEvent(
        active ? "foa:trading-stopped-from-trades" : "foa:trading-started-from-trades",
        { detail: result },
      ));
    } catch (error) {
      window.alert(String(error?.message || error));
    } finally {
      toggleInFlight = false;
      try {
        lastLifecycle = await readLifecycle();
      } catch (_) {}
      applyButtonState(lastLifecycle);
    }
  }

  document.addEventListener("click", (event) => {
    const tradesButton = event.target?.closest?.("[data-stop-trades]");
    if (tradesButton) {
      // Own this control before the older stop-only click listener reaches it.
      event.preventDefault();
      event.stopImmediatePropagation();
      toggleTrading(tradesButton);
      return;
    }

    // The Trades control mirrors the main Dashboard Start/Resume/Stop control.
    // Refresh immediately whenever the main control is used before navigation.
    if (event.target?.closest?.("[data-main-action]")) {
      queueSynchronize(100);
      queueSynchronize(450);
    }
  }, true);

  const observer = new MutationObserver(() => {
    if (!toggleInFlight && toggleButton()) queueSynchronize(0);
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener("focus", () => queueSynchronize(0));
  window.addEventListener("pageshow", () => queueSynchronize(0));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) queueSynchronize(0);
  });

  window.addEventListener("foa:trading-stopped-from-trades", () => queueSynchronize(0));
  window.addEventListener("foa:trading-started-from-trades", () => queueSynchronize(0));

  window.setInterval(() => queueSynchronize(0), POLL_MS);
  queueSynchronize(0);

  window.FOA_TRADES_START_STOP_TOGGLE_VERSION = "20260813-2";
})();
