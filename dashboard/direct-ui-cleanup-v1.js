(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_UI_CLEANUP_V1__) return;
  window.__DERIVADMIN_DIRECT_UI_CLEANUP_V1__ = true;

  const TIMEOUT_NOISE = /backend request timed out|backend did not answer|backend timeout|timed out after\s+\d/i;
  let queued = false;

  function removeTimeoutNoise() {
    document.querySelectorAll(".global-message,.premium-message,[role='alert']").forEach((node) => {
      if (TIMEOUT_NOISE.test(String(node.textContent || ""))) node.remove();
    });
  }

  function removeDuplicateRunControls() {
    // The fixed global Run panel is the only execution controller. Transactions
    // and strategy pages keep their content, but never a second Start/Stop state.
    document.querySelectorAll(".app-main .run-panel").forEach((page) => {
      if (!page.querySelector(".run-ledger") && !page.querySelector(".run-controls")) return;
      page.querySelector(".run-controls")?.remove();
      page.querySelector(".run-account-bar")?.remove();
      page.classList.remove("run-panel");
      page.classList.add("transactions-only-page");
    });
    document.querySelectorAll("[data-start-trading],[data-stop-trading]").forEach((button) => {
      if (!button.closest(".global-run-panel")) button.remove();
    });
  }

  function clean() {
    removeTimeoutNoise();
    removeDuplicateRunControls();
  }

  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      clean();
    });
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  const style = document.createElement("style");
  style.id = "direct-ui-cleanup-v1-style";
  style.textContent = `
    .transactions-only-page{display:flex;flex-direction:column;gap:14px}
    .transactions-only-page>.run-controls,.transactions-only-page>.run-account-bar{display:none!important}
  `;
  document.head.appendChild(style);

  clean();
})();
