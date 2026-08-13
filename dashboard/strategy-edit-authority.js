(() => {
  "use strict";

  if (window.__FOA_STRATEGY_EDIT_AUTHORITY__) return;
  window.__FOA_STRATEGY_EDIT_AUTHORITY__ = true;

  const PREFIX = "foa-strategy-edit-authority-v1";
  let restoring = false;
  let scheduled = false;
  let suspendedUntil = 0;

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function currentMe() {
    return window.FOA_NETLIFY_LIVE_CACHE?.me || window.FOA_BOOT_SESSION || {};
  }

  function accountKey() {
    const me = currentMe();
    const mode = String(me.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
    const account = String(
      me.account_id_masked || me.account_id || me.label || q(".account-pill")?.textContent || "account",
    ).trim();
    return `${mode}:${account}`;
  }

  function storageKey() {
    return `${PREFIX}:${accountKey()}`;
  }

  function readSnapshot() {
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey()) || "null");
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (_) {
      return null;
    }
  }

  function writeSnapshot(snapshot) {
    try { localStorage.setItem(storageKey(), JSON.stringify(snapshot)); } catch (_) {}
  }

  function clearSnapshot() {
    try { localStorage.removeItem(storageKey()); } catch (_) {}
  }

  function activeValue(selector, attribute) {
    const node = q(`${selector}.active`);
    return node ? String(node.getAttribute(attribute) || "") : "";
  }

  function fieldValues(selector) {
    const values = {};
    qa(selector).forEach((field) => {
      const name = field.getAttribute("data-builder")
        || field.getAttribute("data-result-route")
        || field.id
        || field.getAttribute("data-final-prediction")
        || field.getAttribute("data-after-loss-prediction");
      if (!name) return;
      values[name] = field.type === "checkbox" ? Boolean(field.checked) : String(field.value ?? "");
    });
    return values;
  }

  function selectedMarkets() {
    return qa("[data-market].active,[data-market-symbol].active,.market-chip.active")
      .map((node) => String(
        node.dataset.market || node.dataset.marketSymbol || node.dataset.symbol || node.textContent || "",
      ).trim())
      .filter(Boolean);
  }

  function capture() {
    if (restoring || Date.now() < suspendedUntil) return;
    const root = q("#foa-simple-app");
    if (!root) return;
    const snapshot = {
      savedAt: Date.now(),
      builder: fieldValues("[data-builder]"),
      result: fieldValues("[data-result-route]"),
      strategyMode: activeValue("[data-strategy-mode]", "data-strategy-mode"),
      marketMode: activeValue("[data-market-mode]", "data-market-mode"),
      markets: selectedMarkets(),
      resultEnabled: Boolean(q("#result-routing-enabled")?.checked),
      recoveryStyle: String(q("#recovery-style")?.value || ""),
      recoverySplits: String(
        q("#recovery-split-count-input")?.value || q("#recovery-split-count")?.value || "",
      ),
      primaryPrediction: String(q("[data-final-prediction]")?.value || ""),
      recoveryPrediction: String(q("[data-after-loss-prediction]")?.value || ""),
    };
    writeSnapshot(snapshot);
  }

  function emit(field) {
    const type = field.type === "checkbox" || field.tagName === "SELECT" ? "change" : "input";
    field.dispatchEvent(new Event(type, { bubbles: true }));
  }

  function restoreFields(values, selector, keyAttribute) {
    if (!values || typeof values !== "object") return;
    qa(selector).forEach((field) => {
      const key = field.getAttribute(keyAttribute);
      if (!key || !(key in values)) return;
      const wanted = values[key];
      const changed = field.type === "checkbox"
        ? field.checked !== Boolean(wanted)
        : String(field.value) !== String(wanted);
      if (!changed) return;
      if (field.type === "checkbox") field.checked = Boolean(wanted);
      else field.value = String(wanted);
      emit(field);
    });
  }

  function activate(selector, dataName, wanted) {
    if (!wanted) return;
    const node = qa(selector).find((item) => String(item.dataset[dataName] || "") === String(wanted));
    if (node && !node.classList.contains("active")) node.click();
  }

  function restore() {
    scheduled = false;
    if (Date.now() < suspendedUntil) return;
    const snapshot = readSnapshot();
    if (!snapshot || !q("#foa-simple-app")) return;

    restoring = true;
    try {
      activate("[data-strategy-mode]", "strategyMode", snapshot.strategyMode);
      activate("[data-market-mode]", "marketMode", snapshot.marketMode);

      // IMPORTANT: trade.group belongs exclusively to dashboard-v2.js. Restoring
      // it here caused the helper to click the previously captured Odd/Even tab
      // immediately after the user selected Over/Under, Matches/Differs or
      // Rise/Fall. The canonical builder already marks trade-group changes dirty,
      // saves them to the account-scoped builder draft and protects them from
      // silent server hydration, so this authority must never re-select a group.
      restoreFields(snapshot.builder, "[data-builder]", "data-builder");
      restoreFields(snapshot.result, "[data-result-route]", "data-result-route");

      const toggle = q("#result-routing-enabled");
      if (toggle && toggle.checked !== Boolean(snapshot.resultEnabled)) {
        toggle.checked = Boolean(snapshot.resultEnabled);
        toggle.dispatchEvent(new Event("change", { bubbles: true }));
      }

      const recoveryStyle = q("#recovery-style");
      if (recoveryStyle && snapshot.recoveryStyle && recoveryStyle.value !== snapshot.recoveryStyle) {
        recoveryStyle.value = snapshot.recoveryStyle;
        recoveryStyle.dispatchEvent(new Event("change", { bubbles: true }));
      }
      const recoverySplits = q("#recovery-split-count-input") || q("#recovery-split-count");
      if (recoverySplits && snapshot.recoverySplits && String(recoverySplits.value) !== snapshot.recoverySplits) {
        recoverySplits.value = snapshot.recoverySplits;
        recoverySplits.dispatchEvent(new Event(recoverySplits.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
      }

      const primaryPrediction = q("[data-final-prediction]");
      if (primaryPrediction && snapshot.primaryPrediction && primaryPrediction.value !== snapshot.primaryPrediction) {
        primaryPrediction.value = snapshot.primaryPrediction;
        primaryPrediction.dispatchEvent(new Event("change", { bubbles: true }));
      }
      const recoveryPrediction = q("[data-after-loss-prediction]");
      if (recoveryPrediction && snapshot.recoveryPrediction && recoveryPrediction.value !== snapshot.recoveryPrediction) {
        recoveryPrediction.value = snapshot.recoveryPrediction;
        recoveryPrediction.dispatchEvent(new Event("change", { bubbles: true }));
      }
    } finally {
      restoring = false;
    }
  }

  function schedule() {
    if (scheduled || Date.now() < suspendedUntil) return;
    scheduled = true;
    requestAnimationFrame(restore);
  }

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-reset-strategy]")) {
      clearSnapshot();
      suspendedUntil = Date.now() + 2500;
      return;
    }
    if (event.target?.closest?.(
      "[data-strategy-mode],[data-market-mode],[data-market],[data-market-symbol],.market-chip,[data-trade-group]",
    )) {
      window.setTimeout(capture, 0);
    }
  }, true);

  document.addEventListener("change", (event) => {
    if (!event.isTrusted || restoring) return;
    if (event.target?.matches?.(
      "[data-builder],[data-result-route],#result-routing-enabled,#recovery-style,#recovery-split-count,[data-final-prediction],[data-after-loss-prediction]",
    )) {
      window.setTimeout(capture, 0);
    }
  }, true);

  document.addEventListener("input", (event) => {
    if (!event.isTrusted || restoring) return;
    if (event.target?.matches?.(
      "[data-builder],[data-result-route],#recovery-split-count-input",
    )) {
      window.setTimeout(capture, 0);
    }
  }, true);

  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("pageshow", schedule);
  window.addEventListener("focus", schedule);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) schedule();
  });

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", schedule, { once: true })
    : schedule();

  window.FOA_STRATEGY_EDIT_AUTHORITY_VERSION = "20260813-2";
})();
