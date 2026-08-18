(() => {
  "use strict";

  if (window.__DERIVADMIN_RUN_PANEL_USABILITY_V1__) return;
  window.__DERIVADMIN_RUN_PANEL_USABILITY_V1__ = true;

  /* Presentation/safety only. Trading ownership stays with the direct engine. */
  let syncQueued = false;

  function running() {
    return document.documentElement.dataset.finalRunState === "running";
  }

  function syncReset() {
    syncQueued = false;
    const isRunning = running();
    document.querySelectorAll(".global-run-panel [data-run-reset]").forEach((button) => {
      button.disabled = isRunning;
      button.setAttribute("aria-disabled", isRunning ? "true" : "false");
      button.setAttribute("title", isRunning ? "Stop the bot before resetting trades" : "Reset trades");
      button.dataset.resetLocked = isRunning ? "true" : "false";
    });
  }

  function queueSync() {
    if (syncQueued) return;
    syncQueued = true;
    requestAnimationFrame(syncReset);
  }

  if ("MutationObserver" in window) {
    new MutationObserver(queueSync).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-final-run-state"],
    });
    const root = document.getElementById("derivadmin-root");
    if (root) new MutationObserver(queueSync).observe(root, { childList: true, subtree: false });
  }

  window.addEventListener("pageshow", queueSync);
  window.addEventListener("derivadmin:hard-stop", queueSync);
  window.addEventListener("derivadmin:hard-stop-cleared", queueSync);
  window.addEventListener("derivadmin:direct-trade", queueSync);

  const style = document.createElement("style");
  style.id = "run-panel-usability-v1-style";
  style.textContent = `
    /* Reset is history-only and is deliberately unavailable during execution. */
    html[data-final-run-state="running"] .global-run-panel [data-run-reset],
    .global-run-panel [data-run-reset][data-reset-locked="true"]{
      opacity:.38!important;
      filter:saturate(.35)!important;
      cursor:not-allowed!important;
      pointer-events:none!important;
    }

    /* About 20-30% larger than the compact v6 typography. */
    .global-run-panel .run-panel-tabs button{
      font-size:15px!important;
      font-weight:800!important;
    }
    .global-run-panel .run-panel-reset{
      font-size:14px!important;
      font-weight:900!important;
      min-height:34px!important;
      height:34px!important;
    }
    .global-run-panel .transaction-head-v6{
      font-size:10px!important;
      font-weight:900!important;
      line-height:1.2!important;
    }
    .global-run-panel .transaction-row-v6{
      font-size:11px!important;
      line-height:1.28!important;
      min-height:58px!important;
    }
    .global-run-panel .transaction-row-v6 b,
    .global-run-panel .transaction-row-v6 strong{
      font-size:11.5px!important;
      line-height:1.25!important;
    }
    .global-run-panel .transaction-row-v6 small{
      font-size:9.5px!important;
      line-height:1.25!important;
    }
    .global-run-panel .run-panel-stats b,
    .global-run-panel .run-stat b{
      font-size:13px!important;
      line-height:1.2!important;
    }
    .global-run-panel .run-panel-stats span,
    .global-run-panel .run-panel-stats small,
    .global-run-panel .run-stat span,
    .global-run-panel .run-stat small{
      font-size:10.5px!important;
      line-height:1.2!important;
    }
    .global-run-panel .run-help{
      font-size:10px!important;
    }
    .global-run-panel .run-panel-run{
      font-size:20px!important;
      font-weight:950!important;
    }
    .global-run-panel .run-panel-chevron{
      min-width:40px!important;
      min-height:34px!important;
    }
    .global-run-panel .run-panel-reopen-v1 b{
      font-size:13px!important;
    }

    @media(max-width:390px){
      .global-run-panel .run-panel-tabs button{font-size:13px!important}
      .global-run-panel .transaction-head-v6{font-size:9px!important}
      .global-run-panel .transaction-row-v6{font-size:10px!important;min-height:55px!important}
      .global-run-panel .transaction-row-v6 b,
      .global-run-panel .transaction-row-v6 strong{font-size:10.5px!important}
      .global-run-panel .transaction-row-v6 small{font-size:9px!important}
      .global-run-panel .run-panel-stats b,
      .global-run-panel .run-stat b{font-size:12px!important}
      .global-run-panel .run-panel-stats span,
      .global-run-panel .run-panel-stats small,
      .global-run-panel .run-stat span,
      .global-run-panel .run-stat small{font-size:10px!important}
    }
  `;
  document.head.appendChild(style);

  queueSync();
  window.DERIVADMIN_RUN_PANEL_USABILITY_V1 = Object.freeze({
    version: "20260818-run-panel-usability-v1",
    refresh: queueSync,
  });
})();
