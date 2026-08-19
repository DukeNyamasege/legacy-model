(() => {
  "use strict";

  if (window.__DERIVADMIN_MOBILE_LAYOUT_AUTHORITY_V1__) return;
  window.__DERIVADMIN_MOBILE_LAYOUT_AUTHORITY_V1__ = true;

  /*
   * Final responsive layout authority.
   *
   * 1. The Run statistics stay fully visible above Start/Stop.
   * 2. A collapsed Run panel always keeps a dedicated visible reopen handle on
   *    both desktop and mobile layouts.
   * 3. The Strategy Builder is width-contained on phones: no negative/off-screen
   *    placement, no fixed-width grid child can push left/right, and all controls
   *    remain reachable through the normal vertical page scroll.
   *
   * This file is presentation-only. It never starts/stops trading and never
   * changes builder values.
   */

  function ensureRunHandle() {
    const panel = document.querySelector(".global-run-panel");
    if (!panel) return;

    let handle = panel.querySelector(".run-panel-reopen-v1");
    const mobile = window.matchMedia("(max-width: 900px)").matches;

    if (!handle) {
      handle = document.createElement("button");
      handle.type = "button";
      handle.className = "run-panel-reopen-v1";
      handle.dataset.runPanelToggle = "";
      handle.setAttribute("aria-label", "Expand run panel");
      handle.addEventListener("click", (event) => {
        event.preventDefault();
        const currentPanel = handle.closest(".global-run-panel");
        const nativeToggle = currentPanel?.querySelector(".run-panel-chevron[data-run-panel-toggle]");
        if (nativeToggle && nativeToggle !== handle) nativeToggle.click();
        setTimeout(scheduleEnsure, 0);
        setTimeout(scheduleEnsure, 80);
      });
      const bar = panel.querySelector(".run-panel-bar");
      if (bar) panel.insertBefore(handle, bar);
      else panel.appendChild(handle);
    }

    const collapsed = panel.classList.contains("collapsed") && !panel.classList.contains("open");
    handle.innerHTML = mobile
      ? `<span aria-hidden="true">^</span><b>Run panel</b>`
      : `<span aria-hidden="true">&gt;</span><b>Run panel</b>`;
    handle.setAttribute("aria-label", collapsed ? "Expand run panel" : "Collapse run panel");
    handle.setAttribute("title", collapsed ? "Expand run panel" : "Collapse run panel");
  }

  function scheduleEnsure() {
    requestAnimationFrame(ensureRunHandle);
  }

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-run-panel-toggle]")) {
      setTimeout(scheduleEnsure, 0);
      setTimeout(scheduleEnsure, 80);
    }
  });
  window.addEventListener("pageshow", scheduleEnsure);
  window.addEventListener("hashchange", scheduleEnsure);
  window.addEventListener("resize", scheduleEnsure, { passive: true });
  window.addEventListener("derivadmin:direct-trade", scheduleEnsure);
  window.addEventListener("derivadmin:direct-reset-all", scheduleEnsure);

  const root = document.getElementById("derivadmin-root");
  if (root && "MutationObserver" in window) {
    // Observe only direct root replacement. This is not a subtree repaint loop and
    // does not participate in per-tick rendering.
    new MutationObserver(scheduleEnsure).observe(root, { childList: true, subtree: false });
  }

  const style = document.createElement("style");
  style.id = "mobile-layout-authority-v1-style";
  style.textContent = `
    /* --------------------------------------------------------------------- */
    /* RUN PANEL: nothing may sit behind Start/Stop.                          */
    /* --------------------------------------------------------------------- */
    .global-run-panel,
    .global-run-panel *{box-sizing:border-box}

    @media(max-width:900px){
    .global-run-panel.open{
      display:flex!important;
      flex-direction:column!important;
      overflow:hidden!important;
    }
    .global-run-panel.open .run-panel-sheet{
      flex:1 1 auto!important;
      min-height:0!important;
      height:auto!important;
      padding-bottom:0!important;
      overflow:hidden!important;
    }
    .global-run-panel.open .run-panel-body{
      flex:1 1 auto!important;
      min-height:0!important;
      overflow-y:auto!important;
      overflow-x:hidden!important;
      -webkit-overflow-scrolling:touch;
    }
    .global-run-panel.open .run-panel-stats{
      position:relative!important;
      inset:auto!important;
      flex:0 0 auto!important;
      width:100%!important;
      min-height:106px!important;
      height:auto!important;
      max-height:none!important;
      display:grid!important;
      grid-template-columns:repeat(3,minmax(0,1fr))!important;
      grid-template-rows:repeat(2,minmax(40px,auto))!important;
      align-items:center!important;
      gap:4px 8px!important;
      padding:8px 10px 10px!important;
      margin:0!important;
      overflow:visible!important;
      contain:layout paint;
      z-index:2!important;
    }
    .global-run-panel.open .run-panel-stats>*,
    .global-run-panel.open .run-stat{
      min-width:0!important;
      width:auto!important;
      max-width:100%!important;
      overflow:visible!important;
    }
    .global-run-panel.open .run-panel-bar{
      position:relative!important;
      inset:auto!important;
      left:auto!important;
      right:auto!important;
      bottom:auto!important;
      transform:none!important;
      width:100%!important;
      min-height:52px!important;
      height:calc(52px + env(safe-area-inset-bottom, 0px))!important;
      flex:0 0 calc(52px + env(safe-area-inset-bottom, 0px))!important;
      padding:0 0 env(safe-area-inset-bottom, 0px)!important;
      margin:0!important;
      z-index:20!important;
    }
    .global-run-panel.open .run-panel-run{
      min-height:52px!important;
      height:52px!important;
    }

    /* Dedicated collapsed-state handle. The sheet can disappear; this cannot. */
    .global-run-panel .run-panel-reopen-v1{
      display:none!important;
      border:0!important;
      color:#dff4ff!important;
      background:linear-gradient(180deg,#082142,#061a34)!important;
      border-top:1px solid rgba(73,174,255,.32)!important;
      border-bottom:1px solid rgba(73,174,255,.20)!important;
      font:inherit!important;
      cursor:pointer!important;
    }
    }
    @media(min-width:901px){
      .global-run-panel.collapsed .run-panel-reopen-v1{
        display:flex!important;
        position:absolute!important;
        top:0!important;
        right:0!important;
        left:auto!important;
        bottom:48px!important;
        width:48px!important;
        min-width:48px!important;
        height:auto!important;
        z-index:194!important;
        flex-direction:column!important;
        align-items:center!important;
        justify-content:center!important;
        gap:10px!important;
        padding:10px 0!important;
        color:var(--camera-text,#dff4ff)!important;
        background:var(--camera-surface-2,#082142)!important;
        border:0!important;
        border-left:1px solid var(--camera-line-strong,rgba(73,174,255,.42))!important;
        border-right:1px solid var(--camera-line-strong,rgba(73,174,255,.42))!important;
        box-shadow:8px 0 24px rgba(0,0,0,.22)!important;
      }
      .global-run-panel.collapsed .run-panel-reopen-v1 span{
        display:grid!important;
        place-items:center!important;
        width:28px!important;
        height:28px!important;
        border:1px solid var(--camera-line-strong,rgba(73,174,255,.42))!important;
        border-radius:50%!important;
        color:var(--camera-blue,#66d8ff)!important;
        font-size:22px!important;
        line-height:1!important;
      }
      .global-run-panel.collapsed .run-panel-reopen-v1 b{
        writing-mode:vertical-rl!important;
        transform:rotate(180deg)!important;
        color:var(--camera-text,#dff4ff)!important;
        font-size:12px!important;
        line-height:1!important;
        letter-spacing:0!important;
      }
      .global-run-panel.open .run-panel-reopen-v1{display:none!important}
      .global-run-panel.open .run-panel-chevron{
        width:auto!important;
        min-width:112px!important;
        padding:0 12px!important;
        display:inline-flex!important;
        align-items:center!important;
        justify-content:center!important;
        gap:7px!important;
        border:1px solid var(--camera-line-strong,rgba(73,174,255,.42))!important;
        border-radius:4px!important;
        background:var(--camera-surface,#0a2039)!important;
        color:var(--camera-text,#f4fbff)!important;
      }
      .global-run-panel.open .run-panel-chevron::after{
        content:"Collapse"!important;
        font-size:12px!important;
        font-weight:800!important;
      }
    }
    @media(max-width:900px){
    .global-run-panel.collapsed{
      position:fixed!important;
      left:0!important;
      right:0!important;
      bottom:0!important;
      top:auto!important;
      width:100%!important;
      height:calc(88px + env(safe-area-inset-bottom, 0px))!important;
      min-height:calc(88px + env(safe-area-inset-bottom, 0px))!important;
      max-height:calc(88px + env(safe-area-inset-bottom, 0px))!important;
      display:flex!important;
      flex-direction:column!important;
      overflow:visible!important;
      z-index:190!important;
      background:#03142a!important;
    }
    .global-run-panel.collapsed .run-panel-sheet{display:none!important}
    .global-run-panel.collapsed .run-panel-reopen-v1{
      display:flex!important;
      position:relative!important;
      inset:auto!important;
      width:100%!important;
      height:36px!important;
      min-height:36px!important;
      flex:0 0 36px!important;
      align-items:center!important;
      justify-content:center!important;
      gap:8px!important;
      padding:0 12px!important;
      z-index:192!important;
    }
    .global-run-panel.collapsed .run-panel-reopen-v1 span{
      display:inline-grid!important;
      place-items:center!important;
      width:20px!important;
      height:20px!important;
      font-size:20px!important;
      line-height:1!important;
      color:#66d8ff!important;
    }
    .global-run-panel.collapsed .run-panel-reopen-v1 b{
      font-size:11px!important;
      line-height:1!important;
      letter-spacing:.04em!important;
      color:#dff4ff!important;
    }
    .global-run-panel.collapsed .run-panel-bar{
      position:relative!important;
      inset:auto!important;
      left:auto!important;
      right:auto!important;
      bottom:auto!important;
      transform:none!important;
      width:100%!important;
      min-height:52px!important;
      height:calc(52px + env(safe-area-inset-bottom, 0px))!important;
      flex:0 0 calc(52px + env(safe-area-inset-bottom, 0px))!important;
      padding-bottom:env(safe-area-inset-bottom, 0px)!important;
      z-index:191!important;
    }
    .global-run-panel.collapsed .run-panel-run{
      min-height:52px!important;
      height:52px!important;
    }
    }

    /* --------------------------------------------------------------------- */
    /* BUILDER: strict phone viewport containment.                           */
    /* --------------------------------------------------------------------- */
    @media (max-width:700px){
      html,body,#derivadmin-root,.app-shell,.app-main{
        width:100%!important;
        max-width:100vw!important;
      }
      body,#derivadmin-root,.app-shell{overflow-x:hidden!important}
      .app-main{
        overflow-x:hidden!important;
        overflow-y:auto!important;
        -webkit-overflow-scrolling:touch;
      }

      .restored-builder,
      .restored-builder .builder-panel,
      .restored-builder .builder-section,
      .restored-builder .builder-template-section,
      .restored-builder .builder-market-shell,
      .restored-builder .builder-market-dropdown,
      .restored-builder .builder-market-grid,
      .restored-builder .builder-mode-grid,
      .restored-builder .rules-stack,
      .restored-builder .condition-card,
      .restored-builder .form-grid,
      .restored-builder details,
      .restored-builder summary{
        position:relative!important;
        left:auto!important;
        right:auto!important;
        transform:none!important;
        width:100%!important;
        min-width:0!important;
        max-width:100%!important;
        margin-left:0!important;
        margin-right:0!important;
        box-sizing:border-box!important;
      }

      .restored-builder,
      .restored-builder *{
        min-width:0!important;
        box-sizing:border-box!important;
      }
      .restored-builder *{
        max-width:100%;
        overflow-wrap:anywhere;
      }

      .restored-builder .form-grid,
      .restored-builder .form-grid.one,
      .restored-builder .form-grid.two,
      .restored-builder .form-grid.three,
      .restored-builder .condition-card{
        display:grid!important;
        grid-template-columns:minmax(0,1fr)!important;
        gap:9px!important;
      }
      .restored-builder .builder-mode-grid{
        display:grid!important;
        grid-template-columns:minmax(0,1fr)!important;
        gap:8px!important;
      }
      .restored-builder .builder-market-grid,
      .restored-builder .builder-market-grid.compact{
        display:grid!important;
        grid-template-columns:repeat(2,minmax(0,1fr))!important;
        gap:7px!important;
      }

      .restored-builder label,
      .restored-builder .compact-select,
      .restored-builder input,
      .restored-builder select,
      .restored-builder textarea{
        width:100%!important;
        min-width:0!important;
        max-width:100%!important;
      }
      .restored-builder input,
      .restored-builder select,
      .restored-builder textarea{
        margin-left:0!important;
        margin-right:0!important;
      }
      .restored-builder button{
        min-width:0!important;
        max-width:100%!important;
      }
      .restored-builder summary{
        white-space:normal!important;
        overflow-wrap:anywhere!important;
      }
      .restored-builder .builder-section{
        align-items:flex-start!important;
      }
      .restored-builder .builder-section>div:last-child{
        min-width:0!important;
        width:100%!important;
      }
      .restored-builder .builder-section h3,
      .restored-builder .builder-section p,
      .restored-builder .builder-section small,
      .restored-builder .mode-card small{
        white-space:normal!important;
        overflow-wrap:anywhere!important;
      }
      .restored-builder [class*="actions"],
      .restored-builder [class*="button-row"],
      .restored-builder [class*="control-row"]{
        max-width:100%!important;
        flex-wrap:wrap!important;
      }
      .restored-builder [class*="dropdown"],
      .restored-builder [class*="popover"],
      .restored-builder [class*="menu"]{
        max-width:100%!important;
      }
    }

    @media (max-width:390px){
      .restored-builder .builder-market-grid,
      .restored-builder .builder-market-grid.compact{
        grid-template-columns:minmax(0,1fr)!important;
      }
      .global-run-panel.open .run-panel-stats{
        gap:3px 5px!important;
        padding-left:6px!important;
        padding-right:6px!important;
      }
    }
  `;
  document.head.appendChild(style);

  scheduleEnsure();
  window.DERIVADMIN_MOBILE_LAYOUT_AUTHORITY_V1 = Object.freeze({
    version: "20260818-mobile-layout-v1",
    refresh: ensureRunHandle,
  });
})();
