(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_UI_CLEANUP_V1__) return;
  window.__DERIVADMIN_DIRECT_UI_CLEANUP_V1__ = true;

  const TIMEOUT_NOISE = /backend request timed out|backend did not answer|backend timeout|timed out after\s+\d/i;
  let queued = false;
  let lastNonTradesRoute = "home";
  let redirectingTradesRoute = false;

  function routeFromHash() {
    return String(location.hash || "#home")
      .replace(/^#\/?/, "")
      .split("?", 1)[0]
      .toLowerCase() || "home";
  }

  function rememberVisibleRoute() {
    const route = routeFromHash();
    if (route && route !== "trades") lastNonTradesRoute = route;
  }

  function removeTimeoutNoise() {
    document.querySelectorAll(".global-message,.premium-message,[role='alert']").forEach((node) => {
      if (TIMEOUT_NOISE.test(String(node.textContent || ""))) node.remove();
    });
  }

  function removeDuplicateRunPanel() {
    // The fixed .global-run-panel is the one and only Run panel. The retired
    // page-level tradesPage() panel must not remain as a second ledger/status UI.
    document.querySelectorAll(".app-main .run-panel").forEach((page) => page.remove());

    // Retired page-level execution controls may never survive outside the one
    // global Run panel, even if an older shell briefly renders them.
    document.querySelectorAll(
      "[data-start-trading],[data-pause-trading],[data-stop-trading],[data-clear-trades]"
    ).forEach((button) => {
      if (!button.closest(".global-run-panel")) button.remove();
    });
  }

  function retireTradesRoute() {
    if (routeFromHash() !== "trades" || redirectingTradesRoute) return;
    redirectingTradesRoute = true;
    const target = lastNonTradesRoute && lastNonTradesRoute !== "trades" ? lastNonTradesRoute : "home";

    try {
      if (typeof window.FOA_FINAL_UI?.go === "function") {
        window.FOA_FINAL_UI.go(target);
      } else {
        history.replaceState(history.state, "", `#${target}`);
      }
    } finally {
      redirectingTradesRoute = false;
    }
  }

  function clean() {
    rememberVisibleRoute();
    removeTimeoutNoise();
    removeDuplicateRunPanel();
    retireTradesRoute();
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

  window.addEventListener("hashchange", () => {
    rememberVisibleRoute();
    clean();
  });

  // Prevent even a single painted frame of the retired page-level Run panel while
  // the shell is transitioning away from an old #trades navigation.
  const style = document.createElement("style");
  style.id = "direct-ui-cleanup-v1-style";
  style.textContent = `
    .app-main .run-panel{display:none!important}
  `;
  document.head.appendChild(style);

  rememberVisibleRoute();
  clean();
})();
