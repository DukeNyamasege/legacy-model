(() => {
  "use strict";

  if (window.__FOA_BUILDER_EDIT_STABILITY__) return;
  window.__FOA_BUILDER_EDIT_STABILITY__ = true;

  const VERSION = "20260814-builder-edit-stability-v3";
  const nativeRemove = Element.prototype.remove;
  let scheduled = false;
  let recoveryChangeGuardUntil = 0;
  let allowResultRemountUntil = 0;
  let preserveViewportUntil = 0;
  let editingScrollX = 0;
  let editingScrollY = 0;
  let editingActive = false;
  let programmaticScroll = false;

  const q = (selector, root = document) => root.querySelector(selector);

  function isBuilderEditor(node = document.activeElement) {
    return Boolean(
      node
      && node.matches?.("input, select, textarea, [contenteditable='true']")
      && node.closest?.(".strategy-builder-card"),
    );
  }

  function setText(node, value) {
    if (!node) return;
    const next = String(value ?? "");
    if (node.textContent !== next) node.textContent = next;
  }

  function rememberEditingViewport() {
    if (!isBuilderEditor()) return;
    editingActive = true;
    editingScrollX = window.scrollX;
    editingScrollY = window.scrollY;
  }

  function scrollBackToEditingViewport() {
    if (Math.abs(window.scrollY - editingScrollY) < 2 && Math.abs(window.scrollX - editingScrollX) < 2) return;
    programmaticScroll = true;
    window.scrollTo(editingScrollX, editingScrollY);
    window.requestAnimationFrame(() => { programmaticScroll = false; });
  }

  function restoreEditingViewport() {
    const protectedWindow = editingActive || Date.now() < preserveViewportUntil;
    if (!protectedWindow) return;
    scrollBackToEditingViewport();
  }

  function releasePostEditViewportProtection() {
    if (programmaticScroll || isBuilderEditor()) return;
    preserveViewportUntil = 0;
    editingActive = false;
  }

  function isResultStructuralField(node) {
    const path = String(node?.dataset?.resultRoute || "");
    return [
      "tradeType",
      "analysisMode",
      "lastRule.operator",
      "percentageRule.target",
      "tickDirectionRule.enabled",
    ].includes(path);
  }

  // Older result-routing code removes and recreates these surfaces after every
  // /me/custom-strategy hydration. A background refresh must never destroy a
  // focused Builder control. Intentional structural result-routing changes are
  // allowed through a short explicit-remount window below.
  Element.prototype.remove = function (...args) {
    const isRecovery = this?.id === "recovery-spread-control";
    const isRouting = this?.id === "result-routing-section";
    const now = Date.now();
    const protectedEdit = isBuilderEditor() || now < preserveViewportUntil;

    if (isRecovery && (protectedEdit || now < recoveryChangeGuardUntil)) {
      return undefined;
    }
    if (isRouting && protectedEdit && now >= allowResultRemountUntil) {
      return undefined;
    }

    if (isRouting || isRecovery) {
      const result = nativeRemove.apply(this, args);
      if (protectedEdit) {
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(restoreEditingViewport);
        });
      }
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
      setText(note, "Multiplier mode uses the Martingale multiplier configured above. Choose Martingale Spread to divide the outstanding loss equally across recovery runs.");
      return;
    }

    const raw = input ? String(input.value || "").trim() : String(original.value || "2");
    if (!raw) {
      setText(note, "Enter how many successful recovery runs should share the outstanding loss (1–3).");
    } else {
      setText(note, splitCopy(raw));
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
      // The hidden selector still owns the canonical result-based state. Send one
      // committed event only after editing is complete; never on each keystroke.
      recoveryChangeGuardUntil = Date.now() + 220;
      preserveViewportUntil = Date.now() + 1500;
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
      recoveryChangeGuardUntil = Date.now() + 220;
    });

    input.addEventListener("input", () => {
      // Deliberately do not clamp, rewrite or dispatch change here. The field may
      // be temporarily empty while the trader deletes 1 and types 2 or 3.
      rememberEditingViewport();
      syncRecoveryPresentation(control);
    });

    input.addEventListener("change", () => commitSplitInput(input, original, control));
    input.addEventListener("blur", () => {
      commitSplitInput(input, original, control);
      preserveViewportUntil = Date.now() + 1500;
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
    preserveViewportUntil = 0;
    if (event.target.closest?.("#recovery-spread-control")) {
      recoveryChangeGuardUntil = Date.now() + 220;
    }
  }, true);

  document.addEventListener("focusout", (event) => {
    if (!event.target?.closest?.(".strategy-builder-card")) return;
    preserveViewportUntil = Date.now() + 1500;
    window.setTimeout(() => {
      if (!isBuilderEditor()) editingActive = false;
    }, 0);
  }, true);

  document.addEventListener("change", (event) => {
    if (event.target?.id === "recovery-style") {
      // Recovery style changes update visibility/copy in place. Suppress the
      // legacy remove/recreate request from the older recovery component.
      recoveryChangeGuardUntil = Date.now() + 220;
      preserveViewportUntil = Date.now() + 1500;
      window.setTimeout(() => {
        syncRecoveryPresentation(q("#recovery-spread-control"));
        scheduleEnhance();
      }, 0);
      return;
    }

    if (event.target?.closest?.("#result-routing-section") && isResultStructuralField(event.target)) {
      // These user-driven changes genuinely alter the result-routing layout. Let
      // that one remount occur, but preserve the viewport around it.
      allowResultRemountUntil = Date.now() + 220;
      rememberEditingViewport();
      preserveViewportUntil = Date.now() + 1500;
    }
  }, true);

  document.addEventListener("input", (event) => {
    if (isBuilderEditor(event.target)) rememberEditingViewport();
  }, true);

  document.addEventListener("pointerdown", (event) => {
    if (!event.target?.closest?.("input, select, textarea, [contenteditable='true']")) {
      releasePostEditViewportProtection();
    }
  }, true);

  window.addEventListener("wheel", releasePostEditViewportProtection, { passive: true });
  window.addEventListener("touchmove", releasePostEditViewportProtection, { passive: true });
  window.addEventListener("scroll", () => {
    if (programmaticScroll) return;
    if (isBuilderEditor()) {
      editingScrollX = window.scrollX;
      editingScrollY = window.scrollY;
      return;
    }
    releasePostEditViewportProtection();
  }, { passive: true });

  new MutationObserver(scheduleEnhance).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleEnhance, { once: true })
    : scheduleEnhance();

  window.FOA_BUILDER_EDIT_STABILITY_VERSION = VERSION;
})();
