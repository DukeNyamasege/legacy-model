(() => {
  "use strict";

  if (window.__FOA_RESULT_UI_FIXES__) return;
  window.__FOA_RESULT_UI_FIXES__ = true;

  const DRAFT_PREFIX = "foa-result-routing-ui-draft-v2";
  const PREDICTED = new Set(["over", "under", "matches", "differs"]);
  const CMP = {
    ">": "greater than",
    "<": "less than",
    "==": "equal to",
    ">=": "greater than or equal to",
    "<=": "less than or equal to",
    all_same: "all the same",
    all_even: "all even",
    all_odd: "all odd",
  };
  const TARGET = {
    even: "even digits",
    odd: "odd digits",
    over: "digits over",
    under: "digits under",
    digit: "exact digit",
    rise: "up ticks",
    fall: "down ticks",
    no_move: "no-move ticks",
  };
  const DIR = { rising: "up ticks", falling: "down ticks", no_move: "no-move ticks" };

  let scheduled = false;
  let restoring = false;

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const n = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const whole = (value, fallback, minimum, maximum) => Math.round(Math.max(minimum, Math.min(maximum, n(value, fallback))));

  function currentMe() {
    return window.FOA_NETLIFY_LIVE_CACHE?.me || window.FOA_BOOT_SESSION || {};
  }

  function accountKey() {
    const me = currentMe();
    const mode = String(me.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
    const account = String(
      me.account_id_masked
      || me.account_id
      || me.label
      || q(".account-pill")?.textContent
      || "account",
    ).trim();
    return `${mode}:${account}`;
  }

  function draftKey() {
    return `${DRAFT_PREFIX}:${accountKey()}`;
  }

  function readDraft() {
    try {
      const parsed = JSON.parse(localStorage.getItem(draftKey()) || "null");
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (_) {
      return null;
    }
  }

  function clearDraft() {
    try { localStorage.removeItem(draftKey()); } catch (_) {}
  }

  function captureDraft() {
    if (restoring) return;
    const toggle = q("#result-routing-enabled");
    if (!toggle) return;
    const values = {};
    qa("[data-result-route]").forEach((field) => {
      values[field.dataset.resultRoute] = field.type === "checkbox"
        ? Boolean(field.checked)
        : String(field.value ?? "");
    });
    try {
      localStorage.setItem(draftKey(), JSON.stringify({
        savedAt: Date.now(),
        enabled: Boolean(toggle.checked),
        values,
      }));
    } catch (_) {}
  }

  function emitField(field) {
    const type = field.type === "checkbox" || field.tagName === "SELECT" ? "change" : "input";
    field.dispatchEvent(new Event(type, { bubbles: true }));
  }

  function restoreDraft(forceEvents = false) {
    const draft = readDraft();
    const toggle = q("#result-routing-enabled");
    if (!draft || !toggle) return false;

    restoring = true;
    try {
      const nextChecked = Boolean(draft.enabled);
      const changedToggle = toggle.checked !== nextChecked;
      toggle.checked = nextChecked;
      if (changedToggle || forceEvents) toggle.dispatchEvent(new Event("change", { bubbles: true }));

      const values = draft.values && typeof draft.values === "object" ? draft.values : {};
      qa("[data-result-route]").forEach((field) => {
        const path = field.dataset.resultRoute;
        if (!(path in values)) return;
        const wanted = values[path];
        const changed = field.type === "checkbox"
          ? field.checked !== Boolean(wanted)
          : String(field.value) !== String(wanted);
        if (field.type === "checkbox") field.checked = Boolean(wanted);
        else field.value = String(wanted);
        if (changed || forceEvents) emitField(field);
      });

      const box = q("#result-routing-section .result-routing-recovery-box");
      if (box) box.hidden = !nextChecked;
    } finally {
      restoring = false;
    }
    return true;
  }

  function installSaveObserver() {
    if (window.__FOA_RESULT_ROUTING_SAVE_OBSERVER__) return;
    window.__FOA_RESULT_ROUTING_SAVE_OBSERVER__ = true;
    const underlyingFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const response = await underlyingFetch(input, init);
      const url = typeof input === "string" ? input : String(input?.url || "");
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      const custom = url.includes("/me/custom-strategy") || url.includes("/api/me/custom-strategy");

      if (custom && response.ok && method === "POST") {
        clearDraft();
      }
      if (custom && response.ok && method === "GET" && readDraft()) {
        // result-based-strategy.js hydrates the server response first. Re-apply the
        // active UI draft on the next task so its internal state is restored too.
        window.setTimeout(() => {
          restoreDraft(true);
          schedule();
        }, 0);
      }
      return response;
    };
  }

  function syncResultRouting() {
    const checkbox = q("#result-routing-enabled");
    const box = q("#result-routing-section .result-routing-recovery-box");
    if (!checkbox || !box) return;
    box.hidden = !checkbox.checked;
  }

  function splitCopy(value) {
    const count = Math.max(1, Math.min(3, Number(value || 1)));
    return `Total outstanding loss is recovered equally across ${count} successful recovery ${count === 1 ? "run" : "runs"}. If a recovery run loses, the outstanding loss is recalculated and the remaining recovery runs continue.`;
  }

  function syncRecoveryPlan() {
    const control = q("#recovery-spread-control");
    if (!control) return;
    const style = q("#recovery-style", control);
    const original = q("#recovery-split-count", control);
    const parts = q(".recovery-spread-parts", control);
    const note = q(".recovery-spread-note", control);
    if (!style || !parts || !original) return;

    const split = style.value === "split";
    parts.hidden = !split;

    const labelText = Array.from(parts.childNodes).find((node) => node.nodeType === Node.TEXT_NODE);
    if (labelText) labelText.textContent = "Recover loss in how many splits? ";

    original.style.display = "none";
    original.setAttribute("aria-hidden", "true");

    let input = q("#recovery-split-count-input", control);
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
        schedule();
      });
    }

    if (document.activeElement !== input) input.value = String(original.value || "2");
    if (split && note) note.textContent = splitCopy(input.value);
    if (!split && note) note.textContent = "Multiplier mode uses the Martingale multiplier configured above. Choose Martingale Spread to divide the outstanding loss equally across recovery runs.";
  }

  function builderValue(path, fallback = "") {
    const field = q(`[data-builder="${path}"]`);
    if (!field) return fallback;
    return field.type === "checkbox" ? Boolean(field.checked) : field.value;
  }

  function resultValue(path, fallback = "") {
    const field = q(`[data-result-route="${path}"]`);
    if (!field) return fallback;
    return field.type === "checkbox" ? Boolean(field.checked) : field.value;
  }

  function primaryConditions() {
    const mode = String(q("[data-strategy-mode].active")?.dataset?.strategyMode || "last_digit");
    const parts = [];
    if (mode === "last_digit" || mode === "combined") {
      const windowSize = whole(builderValue("lastRule.window", 5), 5, 1, 1000);
      const operator = String(builderValue("lastRule.operator", ">="));
      parts.push(["all_same", "all_even", "all_odd"].includes(operator)
        ? `last ${windowSize} digits are ${CMP[operator]}`
        : `last ${windowSize} digits are ${CMP[operator] || operator} ${whole(builderValue("lastRule.value", 3), 3, 0, 9)}`);
    }
    if (mode === "percentage" || mode === "combined") {
      const target = String(builderValue("percentageRule.target", "even"));
      const targetValue = ["over", "under", "digit"].includes(target)
        ? ` ${whole(builderValue("percentageRule.value", 5), 5, 0, 9)}`
        : "";
      parts.push(`${TARGET[target] || target}${targetValue} in the past ${whole(builderValue("percentageRule.window", 500), 500, 1, 1000)} ticks is ${CMP[String(builderValue("percentageRule.operator", ">="))] || builderValue("percentageRule.operator", ">=")} ${n(builderValue("percentageRule.threshold", 70), 70)}%`);
    }
    if (Boolean(builderValue("tickDirectionRule.enabled", false))) {
      const direction = String(builderValue("tickDirectionRule.direction", "rising"));
      parts.push(`last ${whole(builderValue("tickDirectionRule.window", 3), 3, 1, 1000)} tick directions are ${DIR[direction] || direction}`);
    }
    const label = mode === "combined" ? "Combined" : mode === "percentage" ? "Percentage" : "Last Digit";
    return `${label} mode — ${parts.join(" AND ") || "configured conditions"}`;
  }

  function marketSummary() {
    const mode = String(q("[data-market-mode].active")?.dataset?.marketMode || "selected");
    if (mode === "all") return "all supported markets";
    const chips = qa(".market-chips .market-chip.active")
      .map((node) => String(node.textContent || "").replace(/x\s*$/i, "").trim())
      .filter(Boolean);
    if (mode === "single") return chips[0] || String(q("[data-market-select]")?.value || "selected market");
    return chips.length ? chips.join(", ") : "selected markets";
  }

  function primaryTradeSummary() {
    const side = String(builderValue("trade.side", "over"));
    const label = side.charAt(0).toUpperCase() + side.slice(1);
    let prediction = "";
    if (PREDICTED.has(side)) {
      const dynamic = ["matches", "differs"].includes(side) ? q("[data-last-digit-prediction]") : null;
      const dynamicLabels = { last_digit: "last digit", most_appearing: "most appearing digit", second_most_appearing: "second most appearing digit" };
      prediction = dynamic
        ? (dynamicLabels[String(dynamic.value)] || String(dynamic.value))
        : String(whole(builderValue("trade.prediction", 2), 2, 0, 9));
    }
    const ticks = whole(builderValue("money.ticks", 1), 1, 1, 100);
    return `${label}${prediction ? ` ${prediction}` : ""} for ${ticks} tick${ticks === 1 ? "" : "s"}`;
  }

  function reanalysisSummary() {
    const mode = String(builderValue("reanalyze.mode", "after_every_trade"));
    const losses = whole(builderValue("reanalyze.losses", 1), 1, 1, 1000);
    const wins = whole(builderValue("reanalyze.wins", 1), 1, 1, 1000);
    if (mode === "custom") return `after ${losses} loss${losses === 1 ? "" : "es"} or ${wins} win${wins === 1 ? "" : "s"}`;
    if (mode === "after_loss") return `after ${losses} loss${losses === 1 ? "" : "es"}`;
    if (mode === "after_win") return `after ${wins} win${wins === 1 ? "" : "s"}`;
    return "after every trade";
  }

  function recoverySummary() {
    const style = String(q("#recovery-style")?.value || "multiplier");
    if (style !== "split") return `current Martingale multiplier ×${n(builderValue("money.martingale", 1), 1)}`;
    const count = whole(
      q("#recovery-split-count-input")?.value || q("#recovery-split-count")?.value || 2,
      2,
      1,
      3,
    );
    return `Martingale Spread — recover outstanding loss equally across ${count} successful recovery ${count === 1 ? "run" : "runs"}`;
  }

  function afterLossConditionSummary() {
    const mode = String(resultValue("analysisMode", "last_digit"));
    const parts = [];
    if (mode === "last_digit" || mode === "combined") {
      const windowSize = whole(resultValue("lastRule.window", 5), 5, 1, 1000);
      const operator = String(resultValue("lastRule.operator", ">="));
      parts.push(["all_same", "all_even", "all_odd"].includes(operator)
        ? `last ${windowSize} digits are ${CMP[operator]}`
        : `last ${windowSize} digits are ${CMP[operator] || operator} ${whole(resultValue("lastRule.value", 3), 3, 0, 9)}`);
    }
    if (mode === "percentage" || mode === "combined") {
      const target = String(resultValue("percentageRule.target", "even"));
      const targetValue = ["over", "under", "digit"].includes(target)
        ? ` ${whole(resultValue("percentageRule.value", 5), 5, 0, 9)}`
        : "";
      parts.push(`${TARGET[target] || target}${targetValue} in the past ${whole(resultValue("percentageRule.window", 500), 500, 1, 1000)} ticks is ${CMP[String(resultValue("percentageRule.operator", ">="))] || resultValue("percentageRule.operator", ">=")} ${n(resultValue("percentageRule.threshold", 70), 70)}%`);
    }
    if (Boolean(resultValue("tickDirectionRule.enabled", false))) {
      const direction = String(resultValue("tickDirectionRule.direction", "rising"));
      parts.push(`last ${whole(resultValue("tickDirectionRule.window", 3), 3, 1, 1000)} tick directions are ${DIR[direction] || direction}`);
    }
    return parts.join(" AND ") || "configured after-loss conditions";
  }

  function resultRoutingSummary() {
    const toggle = q("#result-routing-enabled");
    if (!toggle?.checked) return "OFF — primary strategy remains in use after losses";
    const side = String(resultValue("tradeType", "over"));
    const label = side.charAt(0).toUpperCase() + side.slice(1);
    const prediction = PREDICTED.has(side) ? ` ${whole(resultValue("prediction", 2), 2, 0, 9)}` : "";
    const ticks = whole(resultValue("durationTicks", 1), 1, 1, 100);
    return `ON — after an actual loss wait for ${afterLossConditionSummary()}, then trade ${label}${prediction} for ${ticks} tick${ticks === 1 ? "" : "s"}; keep the recovery route active while actual recovery debt remains`;
  }

  function virtualHookSummary() {
    if (!Boolean(builderValue("virtualHook.enabled", false))) return "OFF";
    const losses = whole(builderValue("virtualHook.enterAfterLosses", 2), 2, 1, 50);
    const wins = whole(builderValue("virtualHook.exitAfterConsecutiveWins", 1), 1, 1, 50);
    return `ON — enter after ${losses} actual loss${losses === 1 ? "" : "es"}, return to real after ${wins} consecutive virtual win${wins === 1 ? "" : "s"}`;
  }

  function syncFullStrategySummary() {
    const summary = q(".live-summary p");
    if (!summary) return;
    const text = [
      `Primary conditions: ${primaryConditions()}.`,
      `Markets: ${marketSummary()}.`,
      `Primary trade: ${primaryTradeSummary()}.`,
      `Re-analysis: ${reanalysisSummary()}.`,
      `Money management: stake $${n(builderValue("money.stake", 0), 0).toFixed(2)}, TP $${n(builderValue("money.takeProfit", 0), 0).toFixed(2)}, SL $${Math.abs(n(builderValue("money.stopLoss", 0), 0)).toFixed(2)}, Martingale ×${n(builderValue("money.martingale", 1), 1)}.`,
      `Recovery plan: ${recoverySummary()}.`,
      `Result-based trading: ${resultRoutingSummary()}.`,
      `Virtual Hook: ${virtualHookSummary()}.`,
    ].join(" ");
    if (summary.textContent !== text) summary.textContent = text;
  }

  function enhance() {
    scheduled = false;
    restoreDraft(false);
    syncResultRouting();
    syncRecoveryPlan();
    syncFullStrategySummary();
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(enhance);
  }

  installSaveObserver();

  document.addEventListener("change", (event) => {
    if (event.target?.matches?.("#result-routing-enabled,[data-result-route]")) captureDraft();
    if (event.target?.matches?.("#result-routing-enabled,#recovery-style,#recovery-split-count,[data-builder],[data-market-mode],[data-market-select],[data-last-digit-prediction]")) {
      window.setTimeout(schedule, 0);
    }
  }, true);

  document.addEventListener("input", (event) => {
    if (event.target?.matches?.("[data-result-route]")) captureDraft();
    if (event.target?.matches?.("[data-result-route],[data-builder],#recovery-split-count-input")) window.setTimeout(schedule, 0);
  }, true);

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-strategy-mode],[data-market-mode],[data-trade-group]")) window.setTimeout(schedule, 0);
  }, true);

  new MutationObserver(schedule).observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", schedule, { once: true })
    : schedule();

  window.FOA_RESULT_UI_FIX_VERSION = "20260813-3";
})();
