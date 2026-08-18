(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_BUILDER_LOADED_V2__) return;
  window.__DERIVADMIN_DIRECT_BUILDER_LOADED_V2__ = true;

  const KEY = "derivadmin-builder-loaded-label-v2";

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function render() {
    const builder = document.querySelector(".restored-builder");
    if (!builder) return;
    const label = String(sessionStorage.getItem(KEY) || "").trim();
    let note = builder.querySelector(".direct-builder-loaded-note-v2");
    if (!label) {
      note?.remove();
      return;
    }
    if (!note) {
      note = document.createElement("div");
      note.className = "direct-builder-loaded-note-v2";
      builder.prepend(note);
    }
    const signature = label;
    if (note.dataset.signature === signature) return;
    note.dataset.signature = signature;
    note.innerHTML = `<span>LOADED INTO BUILDER</span><b>${esc(label)}</b><small>Edit it if needed. Save or Trade Now makes this the active execution strategy.</small>`;
  }

  function renderSoon() {
    [0, 40, 140, 320].forEach((delay) => setTimeout(render, delay));
  }

  document.addEventListener("click", (event) => {
    const cardButton = event.target?.closest?.("[data-load-bot-id]");
    if (cardButton) {
      const card = cardButton.closest(".dashboard-bot-card");
      const label = String(card?.querySelector("b")?.textContent || cardButton.dataset.loadBotId || "Strategy").trim();
      sessionStorage.setItem(KEY, label);
      renderSoon();
      return;
    }

    const selectedButton = event.target?.closest?.("[data-load-selected-bot]");
    if (selectedButton) {
      const select = document.querySelector("[data-dashboard-bot-select]");
      const label = String(select?.selectedOptions?.[0]?.textContent || "Selected strategy").trim();
      sessionStorage.setItem(KEY, label);
      renderSoon();
    }
  }, true);

  window.addEventListener("pageshow", renderSoon);

  const style = document.createElement("style");
  style.id = "direct-builder-loaded-v2-style";
  style.textContent = `
    .direct-builder-loaded-note-v2{margin:0 0 14px;padding:11px 13px;border:1px solid rgba(72,216,255,.18);border-radius:14px;background:rgba(7,27,48,.86);display:grid;grid-template-columns:auto 1fr;gap:3px 9px;align-items:center}
    .direct-builder-loaded-note-v2 span{font-size:7px;letter-spacing:.13em;color:#55cfee;font-weight:900}.direct-builder-loaded-note-v2 b{font-size:10px;color:#edf9ff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.direct-builder-loaded-note-v2 small{grid-column:1/-1;font-size:8px;color:#7894aa}
  `;
  document.head.appendChild(style);

  renderSoon();
  window.DERIVADMIN_DIRECT_BUILDER_LOADED_V2 = Object.freeze({ version: "20260818-builder-loaded-v2", render });
})();
