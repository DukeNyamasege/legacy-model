(() => {
  "use strict";

  const SPECIAL = new Set(["all_even", "all_odd"]);
  let scheduled = false;

  function syncValueField() {
    scheduled = false;
    const select = document.querySelector('select[data-builder="lastRule.operator"]');
    const input = document.querySelector('input[data-builder="lastRule.value"]');
    const field = input?.closest("label.field");
    if (!select || !input || !field) return;

    const special = SPECIAL.has(String(select.value || "").toLowerCase());
    field.hidden = special;
    field.style.display = special ? "none" : "";
    input.disabled = special;
    input.required = false;
    input.setAttribute("aria-hidden", special ? "true" : "false");
  }

  function scheduleSync() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(syncValueField);
  }

  document.addEventListener("change", (event) => {
    if (!event.target?.closest?.('select[data-builder="lastRule.operator"]')) return;
    syncValueField();
  }, true);

  const observer = new MutationObserver(scheduleSync);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener("pageshow", scheduleSync);
  window.addEventListener("focus", scheduleSync);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleSync();
  });

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleSync, { once: true })
    : scheduleSync();

  window.FOA_LAST_DIGIT_SPECIAL_UI_VERSION = "20260813-1";
})();
