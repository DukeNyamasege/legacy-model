(() => {
  "use strict";

  if (window.__FOA_BUILDER_EDIT_STABILITY__) return;
  window.__FOA_BUILDER_EDIT_STABILITY__ = true;

  const VERSION = "20260814-builder-edit-stability-v1";
  const nativeRemove = Element.prototype.remove;
  let scheduled = false;
  let recoveryChangeGuardUntil = 0;
  let editingScrollX = 0;
  let editingScrollY = 0;
  let editingActive = false;

  const q = (selector, root = document) => root.querySelector(selector);

  function isBuilderEditor(node = document.activeElement) {
    return Boolean(
      node
      && node.matches?.("input, select, textarea, [contenteditable='true']")
      && node.closest?.(".strategy-builder-card"),
    );
  }

  function rememberEditingViewport() {
    if (!isBuilderEditor()) return;
    editingActive = true;
    editingScrollX = window.scrollX;
    editingScrollY = window.scrollY;
  }

  function restoreEditingViewport() {
    if (!editingActive || !isBuilderEditor()) return;
    if (Math.abs(window.scrollY - editingScrollY) < 2 && Math.abs(window.scrollX - editingScrollX) < 2) return;
    window.scrollTo(editingScrollX, editingScrollY);
  }

  // result-based-strategy.js historically removes and recreates this recovery
  // control after each /me/custom-strategy hydration. During editing that destroys
  // the focused input and turns a background refresh into a visible page jump.
  // Keep the mounted recovery editor stable while any Builder field has focus.
  Element.prototype.remove = function (...args) {
    if (this?.id === "recovery-spread-control" && (isBuilderEditor() || Date.now() < recoveryChangeGuardUntil)) {
      return undefined;
    }

    if (this?.id === "result-routing-section" || this?.id === "recovery-spread-control") {
      const x = window.scrollX;
      const y = window.scrollY;
      const result = nativeRemove.apply(this, args);
      window.requestAnimationFrame(() => {
        if (!isBuilderEditor()) window.scrollTo(x, y);
      });
      return result;
    }

    return nativeRemove.apply(this, args);
  };

  function splitCopy(value) {
    const count = Math.max(1, Math.min(3, Math.round(Number(value || 1))));
    return `Total outstanding loss is recovered equally across ${count} successful recovery ${count === 1 ? "run" : "runs"}. If a recovery run loses, the outstanding loss is recalculated and the remaining recovery runs continue.`;
  }

  function syncRecoveryPresentation(control) {
    if (!control) return;
    const style = q("#recovery-style", control);
    const original = q("#recovery-split-count", control);
    const input = q("#recovery-split-count-input", control);
    const parts = q(".recovery-spread-parts", control);
    const note = q(".recovery-spread-note", control);
    if (!style || !original || !parts) return;

    const split = style.value === "split";
    parts.hidden = !split;
    if (!split) {
      if (note) note.textContent = "Multiplier mode uses the Martingale multiplier configured above. Choose Martingale Spread to divide the outstanding loss equally across recovery runs.";
      return;
    }

    const raw = input ? String(input.value || "").trim() : String(original.value || "2");
    if (note) {
      if (!raw) note.textContent = "Enter how many successful recovery runs should share the outstanding loss (1–3).";
      else note.textContent = splitCopy(raw);
    }
  }

  function commitSplitInput(input, original, control) {
    const raw = String(input.value || "").trim();
    if (!raw) {
      input.value = String(original.value || "2");
      syncRecoveryPresentation(control);
      return;
    }

    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) {
      input.value = String(original.value || "2");
      syncRecoveryPresentation(control);
      return;
    }

    const next = Math.max(1, Math.min(3, Math.round(parsed)));
    input.value = String(next);
    const changed = String(original.value || "") !== String(next);
    original.value = String(next);

    if (changed) {
      // The original hidden selector owns the canonical result-based state. Let it
      // receive one committed change, but suppress its legacy remove/recreate pass.
      recoveryChangeGuardUntil = Date.now() + 120;
      original.dispatchEvent(new Event("change", { bubbles: true }));
    }
    syncRecoveryPresentation(control);
  }

  function installSafeSplitInput(control) {
    if (!control) return;
    const original = q("#recovery-split-count", control);
    let input = q("#recovery-split-count-input", control);
    if (!original || !input) return;
    if (input.dataset.stableSplitInput === "true") return;

    // Clone once to remove the old per-keystroke clamping listener installed by
    // result-ui-fixes.js. Keeping the same id means its presentation synchronizer
    // continues to recognize the field but cannot reattach that old listener.
    const safe = input.cloneNode(true);
    safe.dataset.stableSplitInput = "true";
    input.replaceWith(safe);
    input = safe;

    input.addEventListener("focus", () => {
      rememberEditingViewport();
      recoveryChangeGuardUntil = Date.now() + 120;
    });

    input.addEventListener("input", () => {
      // Deliberately do not clamp, rewrite or dispatch change here. A number input
      // must be allowed to be temporarily empty while the trader replaces a value.
      rememberEditingViewport();
      syncRecoveryPresentation(control);
    });

    input.addEventListener("change", () => commitSplitInput(input, original, control));
    input.addEventListener("blur", () => {
      commitSplitInput(input, original, control);
      window.requestAnimationFrame(() => window.scrollTo(editingScrollX, editingScrollY));
    });

    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      commitSplitInput(input, original, control);
      input.blur();
    });
  }

  function enhance() {
    scheduled = false;
    const control = q("#recovery-spread-control");
    if (control) {
      installSafeSplitInput(control);
      syncRecoveryPresentation(control);
    }
    restoreEditingViewport();
  }

  function scheduleEnhance() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(enhance);
  }

  document.addEventListener("focusin", (event) => {
    if (!isBuilderEditor(event.target)) return;
    editingActive = true;
    editingScrollX = window.scrollX;
    editingScrollY = window.scrollY;
    if (event.target.closest?.("#recovery-spread-control")) {
      recoveryChangeGuardUntil = Date.now() + 120;
    }
  }, true);

  document.addEventListener("focusout", (event) => {
    if (!event.target?.closest?.(".strategy-builder-card")) return;
    window.setTimeout(() => {
      if (!isBuilderEditor()) editingActive = false;
    }, 0);
  }, true);

  document.addEventListener("change", (event) => {
    if (event.target?.id !== "recovery-style") return;
    // Recovery style changes should update visibility/copy in place. The old
    // result-based handler may request a remount; suppress that one remount.
    recoveryChangeGuardUntil = Date.now() + 120;
    window.setTimeout(() => {
      syncRecoveryPresentation(q("#recovery-spread-control"));
      scheduleEnhance();
    }, 0);
  }, true);

  document.addEventListener("input", (event) => {
    if (isBuilderEditor(event.target)) rememberEditingViewport();
  }, true);

  new MutationObserver(scheduleEnhance).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });

  window.setInterval(() => {
    if (isBuilderEditor()) {
      restoreEditingViewport();
      scheduleEnhance();
    }
  }, 500);

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleEnhance, { once: true })
    : scheduleEnhance();

  window.FOA_BUILDER_EDIT_STABILITY_VERSION = VERSION;
})();
