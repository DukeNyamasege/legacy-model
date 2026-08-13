(() => {
  "use strict";

  if (window.__FOA_RESULT_UI_FIXES__) return;
  window.__FOA_RESULT_UI_FIXES__ = true;

  let scheduled = false;

  function syncResultRouting() {
    const checkbox = document.querySelector("#result-routing-enabled");
    const box = document.querySelector("#result-routing-section .result-routing-recovery-box");
    if (!checkbox || !box) return;
    box.hidden = !checkbox.checked;
  }

  function splitCopy(value) {
    const count = Math.max(1, Math.min(3, Number(value || 1)));
    return `Total outstanding loss is recovered equally across ${count} successful recovery ${count === 1 ? "run" : "runs"}. If a recovery run loses, the outstanding loss is recalculated and the remaining recovery runs continue.`;
  }

  function syncRecoveryPlan() {
    const control = document.querySelector("#recovery-spread-control");
    if (!control) return;
    const style = control.querySelector("#recovery-style");
    const original = control.querySelector("#recovery-split-count");
    const parts = control.querySelector(".recovery-spread-parts");
    const note = control.querySelector(".recovery-spread-note");
    if (!style || !parts || !original) return;

    const split = style.value === "split";
    parts.hidden = !split;

    const labelText = Array.from(parts.childNodes).find((node) => node.nodeType === Node.TEXT_NODE);
    if (labelText) labelText.textContent = "Recover loss in how many splits? ";

    original.style.display = "none";
    original.setAttribute("aria-hidden", "true");

    let input = control.querySelector("#recovery-split-count-input");
    if (!input) {
      input = document.createElement("input");
      input.id = "recovery-split-count-input";
      input.type = "number";
      input.min = "1";
      input.max = "3";
      input.step = "1";
      input.inputMode = "numeric";
      input.setAttribute("aria-label", "Recover loss in how many splits");
      original.after(input);
      input.addEventListener("input", () => {
        const next = Math.max(1, Math.min(3, Math.round(Number(input.value || 1))));
        input.value = String(next);
        original.value = String(next);
        original.dispatchEvent(new Event("change", { bubbles: true }));
        if (note) note.textContent = splitCopy(next);
      });
    }

    if (document.activeElement !== input) input.value = String(original.value || "2");
    if (split && note) note.textContent = splitCopy(input.value);
    if (!split && note) note.textContent = "Multiplier mode uses the Martingale multiplier configured above. Choose Martingale Spread to divide the outstanding loss equally across recovery runs.";
  }

  function enhance() {
    scheduled = false;
    syncResultRouting();
    syncRecoveryPlan();
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(enhance);
  }

  document.addEventListener("change", (event) => {
    if (event.target?.matches?.("#result-routing-enabled")) {
      const box = document.querySelector("#result-routing-section .result-routing-recovery-box");
      if (box) box.hidden = !event.target.checked;
      window.setTimeout(schedule, 0);
      return;
    }
    if (event.target?.matches?.("#recovery-style,#recovery-split-count")) {
      window.setTimeout(schedule, 0);
    }
  }, true);

  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", schedule, { once: true })
    : schedule();

  window.FOA_RESULT_UI_FIX_VERSION = "20260813-2";
})();
