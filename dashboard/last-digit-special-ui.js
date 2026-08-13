(() => {
  "use strict";

  const SPECIAL = new Set(["all_even", "all_odd"]);
  let scheduled = false;

  function selectedComparator() {
    const select = document.querySelector('select[data-builder="lastRule.operator"]');
    return String(select?.value || "").toLowerCase();
  }

  function syncSummary() {
    const operator = selectedComparator();
    if (!SPECIAL.has(operator)) return;

    const windowInput = document.querySelector('input[data-builder="lastRule.window"]');
    const summary = document.querySelector(".live-summary p");
    if (!windowInput || !summary) return;

    const windowSize = Math.max(1, Math.round(Number(windowInput.value || 1)));
    const parityText = operator === "all_even" ? "all even" : "all odd";
    const exactClause = `When the last ${windowSize} digits are ${parityText}`;
    const current = String(summary.textContent || "");

    if (/^When the last \d+ digits are /i.test(current)) {
      summary.textContent = current.replace(
        /^When the last \d+ digits are .*?(?= AND |, place)/i,
        exactClause,
      );
    }
  }

  function syncValueField() {
    scheduled = false;
    const select = document.querySelector('select[data-builder="lastRule.operator"]');
    const input = document.querySelector('input[data-builder="lastRule.value"]');
    const field = input?.closest("label.field");
    if (!select) return;

    const special = SPECIAL.has(String(select.value || "").toLowerCase());
    if (input && field) {
      field.hidden = special;
      field.style.display = special ? "none" : "";
      input.disabled = special;
      input.required = false;
      input.setAttribute("aria-hidden", special ? "true" : "false");
    }
    syncSummary();
  }

  function scheduleSync() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(syncValueField);
  }

  document.addEventListener("change", (event) => {
    if (!event.target?.closest?.(
      'select[data-builder="lastRule.operator"], input[data-builder="lastRule.window"]',
    )) return;
    window.setTimeout(scheduleSync, 0);
  }, true);

  document.addEventListener("input", (event) => {
    if (!event.target?.closest?.('input[data-builder="lastRule.window"]')) return;
    scheduleSync();
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

  window.FOA_LAST_DIGIT_SPECIAL_UI_VERSION = "20260813-2";
})();
