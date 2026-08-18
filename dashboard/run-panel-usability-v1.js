(() => {
  "use strict";

  if (window.__DERIVADMIN_RUN_PANEL_USABILITY_V2__) return;
  window.__DERIVADMIN_RUN_PANEL_USABILITY_V2__ = true;

  /* Presentation/safety only. Trading ownership stays with the direct engine. */
  let syncQueued = false;
  let panelObserver = null;
  let observedPanel = null;

  function running() {
    return document.documentElement.dataset.finalRunState === "running";
  }

  function connectPanelObserver(panel) {
    if (!("MutationObserver" in window) || panel === observedPanel) return;
    try { panelObserver?.disconnect(); } catch (_) {}
    observedPanel = panel || null;
    if (!panel) return;
    panelObserver = new MutationObserver(queueSync);
    panelObserver.observe(panel, { attributes: true, attributeFilter: ["class"] });
  }

  function syncUi() {
    syncQueued = false;
    const isRunning = running();
    const panel = document.querySelector(".global-run-panel");
    connectPanelObserver(panel);

    document.querySelectorAll(".global-run-panel [data-run-reset]").forEach((button) => {
      button.disabled = isRunning;
      button.setAttribute("aria-disabled", isRunning ? "true" : "false");
      button.setAttribute("title", isRunning ? "Stop the bot before resetting trades" : "Reset trades");
      button.dataset.resetLocked = isRunning ? "true" : "false";
    });

    let panelState = "none";
    if (panel?.classList.contains("open")) panelState = "open";
    else if (panel?.classList.contains("collapsed")) panelState = "collapsed";
    document.documentElement.dataset.runPanelVisibility = panelState;
  }

  function queueSync() {
    if (syncQueued) return;
    syncQueued = true;
    requestAnimationFrame(syncUi);
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
  window.addEventListener("resize", queueSync, { passive: true });
  window.addEventListener("derivadmin:hard-stop", queueSync);
  window.addEventListener("derivadmin:hard-stop-cleared", queueSync);
  window.addEventListener("derivadmin:direct-trade", queueSync);

  const style = document.createElement("style");
  style.id = "run-panel-usability-v2-style";
  style.textContent = `
    /* Reset is history-only and is deliberately unavailable during execution. */
    html[data-final-run-state="running"] .global-run-panel [data-run-reset],
    .global-run-panel [data-run-reset][data-reset-locked="true"]{
      opacity:.38!important;
      filter:saturate(.35)!important;
      cursor:not-allowed!important;
      pointer-events:none!important;
    }

    /* Keep primary navigation visible and clickable above the Run panel. */
    html[data-run-panel-visibility="open"] .bottom-nav,
    html[data-run-panel-visibility="collapsed"] .bottom-nav{
      position:fixed!important;
      left:0!important;
      right:0!important;
      width:100%!important;
      max-width:100vw!important;
      z-index:420!important;
      pointer-events:auto!important;
      transform:none!important;
    }
    html[data-run-panel-visibility="open"] .bottom-nav{
      bottom:calc(52px + env(safe-area-inset-bottom, 0px))!important;
    }
    html[data-run-panel-visibility="collapsed"] .bottom-nav{
      bottom:calc(88px + env(safe-area-inset-bottom, 0px))!important;
    }
    html[data-run-panel-visibility="open"] .global-run-panel .run-panel-stats{
      margin-bottom:64px!important;
    }
    .bottom-nav .nav-item{
      min-width:0!important;
      pointer-events:auto!important;
      touch-action:manipulation;
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
    .global-run-panel .run-help{font-size:10px!important}
    .global-run-panel .run-panel-run{font-size:20px!important;font-weight:950!important}
    .global-run-panel .run-panel-chevron{min-width:40px!important;min-height:34px!important}
    .global-run-panel .run-panel-reopen-v1 b{font-size:13px!important}

    /* Full balance stays centered inside the account control instead of flowing
       into the currency/icon/caret columns. No ellipsis is used. */
    @media(max-width:700px){
      .top-account-switch{
        flex:1 1 auto!important;
        min-width:0!important;
        max-width:min(100%,calc(100vw - 104px))!important;
        overflow:visible!important;
      }
      .top-account-switch .account-switch-summary{
        display:grid!important;
        grid-template-columns:auto auto minmax(0,1fr) auto!important;
        align-items:center!important;
        justify-items:center!important;
        column-gap:6px!important;
        width:100%!important;
        min-width:0!important;
        max-width:100%!important;
        padding-left:8px!important;
        padding-right:8px!important;
        overflow:visible!important;
      }
      .top-account-switch .account-switch-summary>strong{
        justify-self:stretch!important;
        width:100%!important;
        min-width:0!important;
        max-width:none!important;
        text-align:center!important;
        white-space:nowrap!important;
        overflow:visible!important;
        text-overflow:clip!important;
        font-size:clamp(12px,3.55vw,18px)!important;
        letter-spacing:-.02em!important;
      }
      .top-account-switch .currency-pill,
      .top-account-switch .switch-caret{
        flex:0 0 auto!important;
        min-width:0!important;
      }
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
      .top-account-switch .account-switch-summary{column-gap:4px!important;padding-left:5px!important;padding-right:5px!important}
      .top-account-switch .account-switch-summary>strong{font-size:clamp(11px,3.35vw,15px)!important}
    }
  `;
  document.head.appendChild(style);

  queueSync();
  const api = Object.freeze({ version: "20260818-run-panel-usability-v2", refresh: queueSync });
  window.DERIVADMIN_RUN_PANEL_USABILITY_V1 = api;
  window.DERIVADMIN_RUN_PANEL_USABILITY_V2 = api;
})();
