(() => {
  "use strict";
  if (window.__FOA_EXECUTION_STATUS_BANNER__) return;
  window.__FOA_EXECUTION_STATUS_BANNER__ = true;

  const FATAL = new Set([
    "error",
    "credential_error",
    "invalid_account",
    "token_required",
    "bulk_execution_pat_required",
    "contract_unavailable",
    "purchase_registration_error",
    "duplicate",
  ]);
  const REASONED_TERMINAL = new Set([
    ...FATAL,
    "stopped",
    "disabled",
    "real_disabled",
    "take_profit",
    "stop_loss",
    "manual_pause",
    "insufficient_balance",
    "purchase_insufficient_balance",
  ]);

  function runtimeDomState() {
    const line = document.querySelector(".builder-status-line[data-runtime-state]");
    if (!line) return { state: "", reason: "" };
    const state = String(line.dataset.runtimeState || "").toUpperCase();
    const text = String(line.querySelector("span")?.textContent || "").trim();
    const separator = text.indexOf(" - ");
    return {
      state,
      reason: separator >= 0 ? text.slice(separator + 3).trim() : "",
    };
  }

  function update() {
    const cache = window.FOA_NETLIFY_LIVE_CACHE || {};
    const me = cache.me || window.FOA_BOOT_SESSION || {};
    const life = cache.lifecycle || {};
    const dom = runtimeDomState();
    const status = String(life.execution_status || me.execution_status || "").trim().toLowerCase();
    const runtimeState = dom.state;
    const authenticated = Boolean(me.authenticated || document.querySelector(".strategy-builder-card"));
    const terminal = runtimeState === "ERROR"
      || runtimeState === "STOPPED"
      || REASONED_TERMINAL.has(status);

    let box = document.querySelector(".foa-execution-status-banner");
    if (!authenticated || !terminal || status === "inactive") {
      box?.remove();
      return;
    }

    const reason = String(
      life.reason
      || me.execution_status_reason
      || dom.reason
      || "Auto trading stopped. Press Start Auto Trading to retry.",
    ).trim();

    // custom-runtime-client already owns the primary ERROR notice. Do not show a
    // duplicate red box for the same failure, but keep this authority for every
    // other reasoned terminal state that previously disappeared silently.
    const runtimeError = document.querySelector("#custom-runtime-error-notice");
    if ((runtimeState === "ERROR" || FATAL.has(status)) && runtimeError) {
      box?.remove();
      runtimeError.setAttribute("role", "alert");
      runtimeError.setAttribute("aria-live", "assertive");
      if (runtimeError.textContent !== reason) runtimeError.textContent = reason;
      return;
    }

    if (!box) {
      box = document.createElement("div");
      box.className = "foa-execution-status-banner notice";
      box.setAttribute("role", "alert");
      box.setAttribute("aria-live", "assertive");
      box.setAttribute("aria-atomic", "true");
      const card = document.querySelector(".strategy-builder-card");
      if (card?.parentNode) card.parentNode.insertBefore(box, card);
    }
    if (!box) return;

    const fatal = runtimeState === "ERROR" || FATAL.has(status);
    box.classList.toggle("error", fatal);
    box.dataset.executionStatus = status || runtimeState.toLowerCase();

    let prefix = "Auto trading stopped";
    if (status === "manual_pause") prefix = "Auto trading paused";
    if (status === "take_profit") prefix = "Take profit stop";
    if (status === "stop_loss") prefix = "Stop loss stop";
    box.textContent = `${prefix}: ${reason}`;
  }

  new MutationObserver(update).observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  setInterval(update, 750);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", update, { once: true })
    : update();
})();