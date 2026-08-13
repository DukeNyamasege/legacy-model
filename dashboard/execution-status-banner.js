(() => {
  "use strict";
  if (window.__FOA_EXECUTION_STATUS_BANNER__) return;
  window.__FOA_EXECUTION_STATUS_BANNER__ = true;

  function update() {
    const cache = window.FOA_NETLIFY_LIVE_CACHE || {};
    const me = cache.me || window.FOA_BOOT_SESSION || {};
    const life = cache.lifecycle || {};
    const status = String(life.execution_status || me.execution_status || "").toLowerCase();
    const isError = /(error|invalid|failed|reject|insufficient|unavailable|expired|revoked|duplicate)/.test(status);
    let box = document.querySelector(".foa-execution-status-banner");
    if (!isError || !me.authenticated) {
      box?.remove();
      return;
    }
    if (!box) {
      box = document.createElement("div");
      box.className = "foa-execution-status-banner notice error";
      box.setAttribute("role", "alert");
      const card = document.querySelector(".strategy-builder-card");
      if (card?.parentNode) card.parentNode.insertBefore(box, card);
    }
    if (!box) return;
    const reason = String(life.reason || me.execution_status_reason || "Execution error");
    box.textContent = `Execution error: ${reason}`;
  }

  new MutationObserver(update).observe(document.documentElement, { childList: true, subtree: true });
  setInterval(update, 1000);
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", update, { once: true }) : update();
})();
