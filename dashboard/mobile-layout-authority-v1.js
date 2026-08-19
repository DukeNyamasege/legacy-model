(() => {
  "use strict";

  const VERSION = "20260819-right-quarter-drawer-v6-right-edge";
  if (window.DERIVADMIN_MOBILE_LAYOUT_AUTHORITY_V1?.version === VERSION) return;
  window.__DERIVADMIN_MOBILE_LAYOUT_AUTHORITY_V1__ = true;

  /*
   * FINAL RESPONSIVE LAYOUT AUTHORITY
   *
   * Run panel:
   * - desktop: one fixed right-side drawer, approximately one quarter viewport wide;
   * - collapsed desktop: only the vertical `> Run panel` handle remains visible;
   * - open desktop: the native top control is a clear `Collapse` button;
   * - phone/tablet: full-width bottom/full-height sheet behavior is preserved;
   * - all surfaces inherit the camera theme variables for dark and light mode.
   *
   * IMPORTANT: the camera theme contains older high-specificity Run-panel geometry.
   * Desktop selectors here intentionally match/exceed that specificity and this
   * runtime style is appended later, so presentation colors cannot re-anchor the
   * drawer to the left side again.
   *
   * Builder:
   * - phone layout remains width-contained and vertically scrollable.
   *
   * Presentation only: this authority never starts/stops trading and never mutates
   * strategy or financial state.
   */

  const DESKTOP_QUERY = "(min-width: 901px)";

  function ensureRunHandle() {
    const panel = document.querySelector(".global-run-panel");
    if (!panel) return;

    let handle = panel.querySelector(".run-panel-reopen-v1");
    const desktop = window.matchMedia(DESKTOP_QUERY).matches;

    if (!handle) {
      handle = document.createElement("button");
      handle.type = "button";
      handle.className = "run-panel-reopen-v1";
      handle.setAttribute("aria-controls", "derivadmin-run-panel-sheet");
      handle.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const currentPanel = handle.closest(".global-run-panel");
        const nativeToggle = currentPanel?.querySelector(".run-panel-chevron[data-run-panel-toggle]");
        if (nativeToggle) nativeToggle.click();
        setTimeout(scheduleEnsure, 0);
        setTimeout(scheduleEnsure, 80);
      });
      const bar = panel.querySelector(".run-panel-bar");
      if (bar) panel.insertBefore(handle, bar);
      else panel.appendChild(handle);
    }

    const sheet = panel.querySelector(".run-panel-sheet");
    if (sheet) sheet.id = "derivadmin-run-panel-sheet";

    const collapsed = panel.classList.contains("collapsed") && !panel.classList.contains("open");
    handle.innerHTML = desktop
      ? `<span aria-hidden="true">&gt;</span><b>Run panel</b>`
      : `<span aria-hidden="true">⌃</span><b>Run panel</b>`;
    handle.hidden = !collapsed;
    handle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    handle.setAttribute("aria-label", "Expand Run panel");
    handle.setAttribute("title", "Expand Run panel");

    const nativeToggle = panel.querySelector(".run-panel-chevron[data-run-panel-toggle]");
    if (nativeToggle) {
      nativeToggle.setAttribute("aria-controls", "derivadmin-run-panel-sheet");
      nativeToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      nativeToggle.setAttribute("aria-label", collapsed ? "Expand Run panel" : "Collapse Run panel");
      nativeToggle.setAttribute("title", collapsed ? "Expand Run panel" : "Collapse Run panel");
      if (!collapsed) {
        nativeToggle.innerHTML = `<span class="run-panel-collapse-arrow" aria-hidden="true">‹</span><b>Collapse</b>`;
      }
    }
  }

  function scheduleEnsure() {
    requestAnimationFrame(ensureRunHandle);
  }

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-run-panel-toggle],.run-panel-reopen-v1")) {
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
    new MutationObserver(scheduleEnsure).observe(root, { childList: true, subtree: false });
  }

  document.getElementById("mobile-layout-authority-v1-style")?.remove();
  const style = document.createElement("style");
  style.id = "mobile-layout-authority-v1-style";
  style.textContent = `
    .global-run-panel,
    .global-run-panel *{box-sizing:border-box}

    /* ------------------------------------------------------------------ */
    /* DESKTOP: fixed right-side quarter-width drawer.                    */
    /* Double class specificity intentionally beats old theme geometry.   */
    /* ------------------------------------------------------------------ */
    @media(min-width:901px){
      .global-run-panel.global-run-panel{
        position:fixed!important;
        top:72px!important;
        right:0!important;
        bottom:0!important;
        left:auto!important;
        margin:0!important;
        width:clamp(320px,25vw,460px)!important;
        min-width:320px!important;
        max-width:calc(100vw - 72px)!important;
        height:calc(100dvh - 72px)!important;
        max-height:calc(100dvh - 72px)!important;
        display:flex!important;
        flex-direction:column!important;
        overflow:visible!important;
        z-index:190!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
        border-left:1px solid var(--camera-line-strong,var(--line2,#3b6384))!important;
        box-shadow:-14px 0 34px color-mix(in srgb,var(--camera-bg,#07111f) 30%,transparent)!important;
        transform-origin:right center!important;
        transition:transform .18s ease!important;
      }

      .global-run-panel.global-run-panel.open{
        right:0!important;
        left:auto!important;
        transform:translateX(0)!important;
      }

      .global-run-panel.global-run-panel.collapsed{
        right:0!important;
        left:auto!important;
        transform:translateX(calc(100% - 48px))!important;
      }

      .global-run-panel.global-run-panel .run-panel-sheet{
        position:relative!important;
        inset:auto!important;
        width:100%!important;
        min-width:0!important;
        height:auto!important;
        flex:1 1 auto!important;
        display:flex!important;
        flex-direction:column!important;
        min-height:0!important;
        overflow:hidden!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
        border:0!important;
        border-radius:0!important;
        transform:none!important;
      }

      .global-run-panel.global-run-panel.collapsed .run-panel-sheet,
      .global-run-panel.global-run-panel.collapsed .run-panel-bar{
        opacity:0!important;
        visibility:hidden!important;
        pointer-events:none!important;
      }

      .global-run-panel.global-run-panel .run-panel-top{
        position:relative!important;
        flex:0 0 48px!important;
        min-height:48px!important;
        height:48px!important;
        display:flex!important;
        align-items:center!important;
        justify-content:space-between!important;
        gap:8px!important;
        padding:6px 10px!important;
        background:var(--camera-surface-2,var(--panel2,#13263a))!important;
        border-bottom:1px solid var(--camera-line,var(--line,#2c4965))!important;
      }

      .global-run-panel.global-run-panel.open .run-panel-chevron{
        width:auto!important;
        min-width:118px!important;
        height:36px!important;
        padding:0 12px!important;
        display:inline-flex!important;
        align-items:center!important;
        justify-content:center!important;
        gap:7px!important;
        border:1px solid var(--camera-line-strong,var(--line2,#3b6384))!important;
        border-radius:9px!important;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        box-shadow:none!important;
        cursor:pointer!important;
      }
      .global-run-panel.global-run-panel.open .run-panel-chevron .run-panel-collapse-arrow{
        color:var(--camera-blue,var(--blue,#4b8ff7))!important;
        font-size:22px!important;
        line-height:1!important;
      }
      .global-run-panel.global-run-panel.open .run-panel-chevron b{
        color:inherit!important;
        font-size:12px!important;
        line-height:1!important;
        font-weight:800!important;
      }

      .global-run-panel.global-run-panel .run-panel-tabs{
        flex:0 0 40px!important;
        min-height:40px!important;
        height:40px!important;
        margin:0!important;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
        border-bottom:1px solid var(--camera-line,var(--line,#2c4965))!important;
      }
      .global-run-panel.global-run-panel .run-panel-tabs button{
        min-height:40px!important;
        color:var(--camera-muted,var(--muted,#a9b8c8))!important;
      }
      .global-run-panel.global-run-panel .run-panel-tabs button.active{
        color:var(--camera-blue,var(--blue,#4b8ff7))!important;
      }

      .global-run-panel.global-run-panel .run-panel-body{
        flex:1 1 auto!important;
        min-height:0!important;
        width:100%!important;
        overflow-y:auto!important;
        overflow-x:hidden!important;
        padding:0!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
        overscroll-behavior:contain;
        scrollbar-gutter:stable;
      }

      .global-run-panel.global-run-panel .run-panel-stats{
        position:relative!important;
        inset:auto!important;
        flex:0 0 auto!important;
        width:100%!important;
        min-height:96px!important;
        max-height:none!important;
        display:grid!important;
        grid-template-columns:repeat(3,minmax(0,1fr))!important;
        grid-template-rows:repeat(2,minmax(38px,auto))!important;
        gap:4px 6px!important;
        padding:8px!important;
        margin:0!important;
        overflow:visible!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        background:var(--camera-surface-2,var(--panel2,#13263a))!important;
        border-top:1px solid var(--camera-line,var(--line,#2c4965))!important;
      }
      .global-run-panel.global-run-panel .run-stat{
        min-width:0!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
      }
      .global-run-panel.global-run-panel .run-stat small{color:var(--camera-muted,var(--muted,#a9b8c8))!important}

      .global-run-panel.global-run-panel .run-panel-bar{
        position:relative!important;
        inset:auto!important;
        left:auto!important;
        right:auto!important;
        bottom:auto!important;
        transform:none!important;
        width:100%!important;
        min-height:52px!important;
        height:52px!important;
        flex:0 0 52px!important;
        margin:0!important;
        padding:0!important;
        z-index:20!important;
        background:var(--camera-surface-2,var(--panel2,#13263a))!important;
        border-top:1px solid var(--camera-line,var(--line,#2c4965))!important;
      }
      .global-run-panel.global-run-panel .run-panel-run{min-height:52px!important;height:52px!important}

      /* Only this compact vertical tab remains when the desktop drawer closes. */
      .global-run-panel.global-run-panel.collapsed .run-panel-reopen-v1{
        display:flex!important;
        visibility:visible!important;
        opacity:1!important;
        pointer-events:auto!important;
        position:absolute!important;
        top:50%!important;
        left:0!important;
        right:auto!important;
        bottom:auto!important;
        width:48px!important;
        min-width:48px!important;
        height:164px!important;
        min-height:164px!important;
        transform:translateY(-50%)!important;
        z-index:194!important;
        flex-direction:column!important;
        align-items:center!important;
        justify-content:center!important;
        gap:11px!important;
        padding:10px 0!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        background:var(--camera-surface-2,var(--panel2,#13263a))!important;
        border:1px solid var(--camera-line-strong,var(--line2,#3b6384))!important;
        border-left:0!important;
        border-radius:12px 0 0 12px!important;
        box-shadow:-7px 0 22px color-mix(in srgb,var(--camera-bg,#07111f) 24%,transparent)!important;
        cursor:pointer!important;
      }
      .global-run-panel.global-run-panel.collapsed .run-panel-reopen-v1 span{
        display:grid!important;
        place-items:center!important;
        width:30px!important;
        height:30px!important;
        border:1px solid var(--camera-line-strong,var(--line2,#3b6384))!important;
        border-radius:50%!important;
        color:var(--camera-blue,var(--blue,#4b8ff7))!important;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
        font-size:24px!important;
        font-weight:800!important;
        line-height:1!important;
      }
      .global-run-panel.global-run-panel.collapsed .run-panel-reopen-v1 b{
        writing-mode:vertical-rl!important;
        transform:rotate(180deg)!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        font-size:12px!important;
        font-weight:800!important;
        line-height:1!important;
        letter-spacing:.03em!important;
      }
      .global-run-panel.global-run-panel.open .run-panel-reopen-v1{display:none!important}
    }

    /* ------------------------------------------------------------------ */
    /* PHONE / TABLET: full-width sheet with safe-area aware controls.     */
    /* ------------------------------------------------------------------ */
    @media(max-width:900px){
      .global-run-panel.open{
        position:fixed!important;
        top:72px!important;
        left:0!important;
        right:0!important;
        bottom:0!important;
        width:100%!important;
        height:calc(100dvh - 72px)!important;
        max-height:calc(100dvh - 72px)!important;
        display:flex!important;
        flex-direction:column!important;
        overflow:hidden!important;
        z-index:190!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
      }
      .global-run-panel.open .run-panel-sheet{
        flex:1 1 auto!important;
        min-height:0!important;
        height:auto!important;
        padding-bottom:0!important;
        overflow:hidden!important;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
      }
      .global-run-panel.open .run-panel-top{
        min-height:48px!important;
        height:48px!important;
        padding:6px 10px!important;
        background:var(--camera-surface-2,var(--panel2,#13263a))!important;
        border-bottom:1px solid var(--camera-line,var(--line,#2c4965))!important;
      }
      .global-run-panel.open .run-panel-chevron{
        min-width:116px!important;
        height:36px!important;
        display:inline-flex!important;
        align-items:center!important;
        justify-content:center!important;
        gap:7px!important;
        padding:0 12px!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
        border:1px solid var(--camera-line-strong,var(--line2,#3b6384))!important;
        border-radius:9px!important;
      }
      .global-run-panel.open .run-panel-chevron .run-panel-collapse-arrow{color:var(--camera-blue,var(--blue,#4b8ff7))!important;font-size:22px!important}
      .global-run-panel.open .run-panel-body{
        flex:1 1 auto!important;
        min-height:0!important;
        overflow-y:auto!important;
        overflow-x:hidden!important;
        -webkit-overflow-scrolling:touch;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
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
        gap:4px 8px!important;
        padding:8px 10px 10px!important;
        margin:0!important;
        overflow:visible!important;
        background:var(--camera-surface-2,var(--panel2,#13263a))!important;
        border-top:1px solid var(--camera-line,var(--line,#2c4965))!important;
      }
      .global-run-panel.open .run-panel-bar{
        position:relative!important;
        inset:auto!important;
        transform:none!important;
        width:100%!important;
        min-height:52px!important;
        height:calc(52px + env(safe-area-inset-bottom,0px))!important;
        flex:0 0 calc(52px + env(safe-area-inset-bottom,0px))!important;
        padding:0 0 env(safe-area-inset-bottom,0px)!important;
        margin:0!important;
        z-index:20!important;
        background:var(--camera-surface-2,var(--panel2,#13263a))!important;
      }

      .global-run-panel.collapsed{
        position:fixed!important;
        left:0!important;
        right:0!important;
        bottom:0!important;
        top:auto!important;
        width:100%!important;
        height:calc(88px + env(safe-area-inset-bottom,0px))!important;
        min-height:calc(88px + env(safe-area-inset-bottom,0px))!important;
        max-height:calc(88px + env(safe-area-inset-bottom,0px))!important;
        display:flex!important;
        flex-direction:column!important;
        overflow:visible!important;
        z-index:190!important;
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        background:var(--camera-surface,var(--panel,#0e1d2e))!important;
        border-top:1px solid var(--camera-line-strong,var(--line2,#3b6384))!important;
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
        color:var(--camera-text,var(--text,#f1f6fb))!important;
        background:var(--camera-surface-2,var(--panel2,#13263a))!important;
        border:0!important;
        border-bottom:1px solid var(--camera-line,var(--line,#2c4965))!important;
        z-index:192!important;
      }
      .global-run-panel.collapsed .run-panel-reopen-v1 span{color:var(--camera-blue,var(--blue,#4b8ff7))!important;font-size:20px!important}
      .global-run-panel.collapsed .run-panel-reopen-v1 b{color:var(--camera-text,var(--text,#f1f6fb))!important;font-size:11px!important}
      .global-run-panel.collapsed .run-panel-bar{
        position:relative!important;
        inset:auto!important;
        transform:none!important;
        width:100%!important;
        min-height:52px!important;
        height:calc(52px + env(safe-area-inset-bottom,0px))!important;
        flex:0 0 calc(52px + env(safe-area-inset-bottom,0px))!important;
        padding-bottom:env(safe-area-inset-bottom,0px)!important;
        z-index:191!important;
      }
      .global-run-panel.collapsed .run-panel-run{min-height:52px!important;height:52px!important}
    }

    /* ------------------------------------------------------------------ */
    /* BUILDER: strict phone viewport containment.                        */
    /* ------------------------------------------------------------------ */
    @media(max-width:700px){
      html,body,#derivadmin-root,.app-shell,.app-main{width:100%!important;max-width:100vw!important}
      body,#derivadmin-root,.app-shell{overflow-x:hidden!important}
      .app-main{overflow-x:hidden!important;overflow-y:auto!important;-webkit-overflow-scrolling:touch}

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
      .restored-builder,.restored-builder *{min-width:0!important;box-sizing:border-box!important}
      .restored-builder *{max-width:100%;overflow-wrap:anywhere}
      .restored-builder .form-grid,
      .restored-builder .form-grid.one,
      .restored-builder .form-grid.two,
      .restored-builder .form-grid.three,
      .restored-builder .condition-card,
      .restored-builder .builder-mode-grid{display:grid!important;grid-template-columns:minmax(0,1fr)!important;gap:9px!important}
      .restored-builder .builder-market-grid,
      .restored-builder .builder-market-grid.compact{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:7px!important}
      .restored-builder label,
      .restored-builder .compact-select,
      .restored-builder input,
      .restored-builder select,
      .restored-builder textarea{width:100%!important;min-width:0!important;max-width:100%!important}
      .restored-builder input,.restored-builder select,.restored-builder textarea{margin-left:0!important;margin-right:0!important}
      .restored-builder button{min-width:0!important;max-width:100%!important}
      .restored-builder summary,
      .restored-builder .builder-section h3,
      .restored-builder .builder-section p,
      .restored-builder .builder-section small,
      .restored-builder .mode-card small{white-space:normal!important;overflow-wrap:anywhere!important}
      .restored-builder .builder-section{align-items:flex-start!important}
      .restored-builder .builder-section>div:last-child{min-width:0!important;width:100%!important}
      .restored-builder [class*="actions"],
      .restored-builder [class*="button-row"],
      .restored-builder [class*="control-row"]{max-width:100%!important;flex-wrap:wrap!important}
      .restored-builder [class*="dropdown"],
      .restored-builder [class*="popover"],
      .restored-builder [class*="menu"]{max-width:100%!important}
    }

    @media(max-width:390px){
      .restored-builder .builder-market-grid,
      .restored-builder .builder-market-grid.compact{grid-template-columns:minmax(0,1fr)!important}
      .global-run-panel.open .run-panel-stats{gap:3px 5px!important;padding-left:6px!important;padding-right:6px!important}
    }
  `;
  document.head.appendChild(style);

  scheduleEnsure();
  window.DERIVADMIN_MOBILE_LAYOUT_AUTHORITY_V1 = Object.freeze({
    version: VERSION,
    refresh: ensureRunHandle,
  });
})();