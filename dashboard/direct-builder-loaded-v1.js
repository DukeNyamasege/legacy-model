(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_BUILDER_LOADED_V1__) return;
  window.__DERIVADMIN_DIRECT_BUILDER_LOADED_V1__ = true;

  const KEY = "derivadmin-builder-loaded-label-v1";
  let queued = false;

  function currentLabel() {
    return String(sessionStorage.getItem(KEY) || "").trim();
  }

  function render() {
    queued = false;
    const builder = document.querySelector(".restored-builder");
    if (!builder) return;
    const label = currentLabel();
    let note = builder.querySelector(".direct-builder-loaded-note");
    if (!label) {
      note?.remove();
      return;
    }
    if (!note) {
      note = document.createElement("div");
      note.className = "direct-builder-loaded-note";
      builder.prepend(note);
    }
    note.innerHTML = `<span>LOADED INTO BUILDER</span><b>${label.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")}</b><small>Save or Trade Now to make this the active execution strategy.</small>`;
  }

  function queueRender() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(render);
  }

  document.addEventListener("click", (event) => {
    const cardLoad = event.target?.closest?.("[data-load-bot-id]");
    if (cardLoad) {
      const card = cardLoad.closest(".dashboard-bot-card");
      const label = String(card?.querySelector("b")?.textContent || cardLoad.dataset.loadBotId || "Strategy").trim();
      sessionStorage.setItem(KEY, label);
      setTimeout(queueRender, 0);
      return;
    }
    const selectLoad = event.target?.closest?.("[data-load-selected-bot]");
    if (selectLoad) {
      const select = document.querySelector("[data-dashboard-bot-select]");
      const label = String(select?.selectedOptions?.[0]?.textContent || "Selected strategy").trim();
      sessionStorage.setItem(KEY, label);
      setTimeout(queueRender, 0);
    }
  });

  const observer = new MutationObserver(queueRender);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  const style = document.createElement("style");
  style.textContent = `.direct-builder-loaded-note{margin:0 0 14px;padding:11px 13px;border:1px solid rgba(72,216,255,.18);border-radius:14px;background:rgba(7,27,48,.86);display:grid;grid-template-columns:auto 1fr;gap:3px 9px;align-items:center}.direct-builder-loaded-note span{font-size:7px;letter-spacing:.13em;color:#55cfee;font-weight:900}.direct-builder-loaded-note b{font-size:10px;color:#edf9ff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.direct-builder-loaded-note small{grid-column:1/-1;font-size:8px;color:#7894aa}`;
  document.head.appendChild(style);

  queueRender();
})();
