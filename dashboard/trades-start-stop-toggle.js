(() => {
  "use strict";

  const POLL_MS = 2500;
  let lastLifecycle = null;
  let toggleInFlight = false;

  function isTradesView() {
    return Boolean(document.querySelector('.builder-header [data-view="trades"].active'));
  }

  function toggleButton() {
    return document.querySelector('[data-stop-trades]');
  }

  function isRunning(lifecycle) {
    return String(lifecycle?.lifecycle || "").toLowerCase() === "running"
      && Boolean(lifecycle?.enabled);
  }

  function applyButtonState(lifecycle = lastLifecycle, busyAction = "") {
    if (lifecycle) lastLifecycle = lifecycle;
    const button = toggleButton();
    if (!button) return;

    const running = isRunning(lastLifecycle);
    let state = running ? "running" : "stopped";
    if (busyAction === "start") state = "starting";
    if (busyAction === "stop") state = "stopping";

    button.dataset.tradingState = state;
    button.dataset.stopped = running ? "false" : "true";
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
    const response = await fetch("/me/trading-lifecycle", {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
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
    if (!isTradesView() || toggleInFlight) return;
    try {
      applyButtonState(await readLifecycle());
    } catch (_) {
      // A failed UI status read never changes the trading lifecycle.
    }
  }

  async function toggleTrading(button) {
    if (toggleInFlight) return;
    toggleInFlight = true;
    try {
      const lifecycle = await readLifecycle();
      lastLifecycle = lifecycle;
      const running = isRunning(lifecycle);
      applyButtonState(lifecycle, running ? "stop" : "start");

      const result = running
        ? await postLifecycle("/me/stop-trading", {})
        : await postLifecycle("/me/resume-trading", { mode: "continue" });

      lastLifecycle = result;
      window.dispatchEvent(new CustomEvent(
        running ? "foa:trading-stopped-from-trades" : "foa:trading-started-from-trades",
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
    const button = event.target?.closest?.("[data-stop-trades]");
    if (!button) return;

    // Own this control before the older stop-only click listener reaches it.
    event.preventDefault();
    event.stopImmediatePropagation();
    toggleTrading(button);
  }, true);

  const observer = new MutationObserver(() => {
    if (!toggleInFlight) applyButtonState(lastLifecycle);
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener("focus", synchronizeState);
  window.addEventListener("pageshow", synchronizeState);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) synchronizeState();
  });

  window.setInterval(synchronizeState, POLL_MS);
  window.setTimeout(synchronizeState, 450);

  window.FOA_TRADES_START_STOP_TOGGLE_VERSION = "20260813-1";
})();
