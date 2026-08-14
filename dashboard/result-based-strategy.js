(() => {
  "use strict";

  if (window.__FOA_RESULT_BASED_STRATEGY_INSTALLED__) return;
  window.__FOA_RESULT_BASED_STRATEGY_INSTALLED__ = true;

  const VERSION = "20260814-result-routing-v2";
  const COMPARATORS = [
    [">", "Greater than"],
    ["<", "Less than"],
    ["==", "Equal to"],
    [">=", "Greater than or equal to"],
    ["<=", "Less than or equal to"],
    ["all_same", "All same"],
    ["all_even", "All even"],
    ["all_odd", "All odd"],
  ];
  const NUMERIC_COMPARATORS = COMPARATORS.filter(([value]) => !["all_same", "all_even", "all_odd"].includes(value));
  const TRADE_TYPES = [
    ["over", "Over"],
    ["under", "Under"],
    ["matches", "Matches"],
    ["differs", "Differs"],
    ["odd", "Odd"],
    ["even", "Even"],
    ["rise", "Rise"],
    ["fall", "Fall"],
  ];
  const PERCENTAGE_TARGETS = [
    ["even", "Even"],
    ["odd", "Odd"],
    ["over", "Over digit"],
    ["under", "Under digit"],
    ["digit", "Exact digit"],
    ["rise", "Up ticks"],
    ["fall", "Down ticks"],
    ["no_move", "No-move ticks"],
  ];

  const state = {
    accountKey: "",
    routingEnabled: false,
    afterLoss: null,
    routingTouched: false,
    serverHydrated: false,
    recoveryMode: "multiplier",
    splitCount: 2,
    recoveryTouched: false,
    recoveryHydrated: false,
    scheduled: false,
    loadingServer: false,
  };

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
  const n = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  const whole = (value, fallback, minimum, maximum) => Math.round(clamp(n(value, fallback), minimum, maximum));

  function currentAccountKey() {
    const me = window.FOA_NETLIFY_LIVE_CACHE?.me || window.FOA_BOOT_SESSION || {};
    const mode = String(me.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
    const account = String(me.account_id_masked || me.account_id || me.label || q(".account-pill")?.textContent || "account").trim();
    return `${mode}:${account}`;
  }

  function optionList(items, selected) {
    return items.map(([value, label]) => `<option value="${value}" ${String(selected) === value ? "selected" : ""}>${label}</option>`).join("");
  }

  function builderValue(path, fallback = "") {
    const field = q(`[data-builder="${path}"]`);
    if (!field) return fallback;
    if (field.type === "checkbox") return field.checked;
    return field.value;
  }

  function selectedStrategyMode() {
    return q("[data-strategy-mode].active")?.dataset?.strategyMode || "last_digit";
  }

  function clonePrimaryRoute() {
    return {
      tradeType: String(builderValue("trade.side", "over")),
      prediction: whole(builderValue("trade.prediction", 2), 2, 0, 9),
      durationTicks: whole(builderValue("money.ticks", 1), 1, 1, 100),
      analysisMode: selectedStrategyMode(),
      lastRule: {
        window: whole(builderValue("lastRule.window", 5), 5, 1, 1000),
        operator: String(builderValue("lastRule.operator", ">=")),
        value: whole(builderValue("lastRule.value", 3), 3, 0, 9),
      },
      percentageRule: {
        target: String(builderValue("percentageRule.target", "even")),
        value: whole(builderValue("percentageRule.value", 5), 5, 0, 9),
        window: whole(builderValue("percentageRule.window", 500), 500, 1, 1000),
        operator: String(builderValue("percentageRule.operator", ">=")),
        threshold: clamp(n(builderValue("percentageRule.threshold", 70), 70), 0, 100),
      },
      tickDirectionRule: {
        enabled: Boolean(builderValue("tickDirectionRule.enabled", false)),
        window: whole(builderValue("tickDirectionRule.window", 3), 3, 1, 1000),
        direction: String(builderValue("tickDirectionRule.direction", "rising")),
      },
    };
  }

  function normalizeRoute(raw) {
    const source = raw && typeof raw === "object" ? raw : {};
    const base = clonePrimaryRoute();
    const route = {
      ...base,
      ...source,
      lastRule: { ...base.lastRule, ...(source.lastRule || {}) },
      percentageRule: { ...base.percentageRule, ...(source.percentageRule || {}) },
      tickDirectionRule: { ...base.tickDirectionRule, ...(source.tickDirectionRule || {}) },
    };
    route.tradeType = TRADE_TYPES.some(([value]) => value === route.tradeType) ? route.tradeType : "over";
    route.prediction = whole(route.prediction, 2, 0, 9);
    route.durationTicks = whole(route.durationTicks, 1, 1, 100);
    route.analysisMode = ["last_digit", "percentage", "combined"].includes(route.analysisMode) ? route.analysisMode : "last_digit";
    route.lastRule.window = whole(route.lastRule.window, 5, 1, 1000);
    route.lastRule.operator = COMPARATORS.some(([value]) => value === route.lastRule.operator) ? route.lastRule.operator : ">=";
    route.lastRule.value = whole(route.lastRule.value, 3, 0, 9);
    route.percentageRule.target = PERCENTAGE_TARGETS.some(([value]) => value === route.percentageRule.target) ? route.percentageRule.target : "even";
    route.percentageRule.value = whole(route.percentageRule.value, 5, 0, 9);
    route.percentageRule.window = whole(route.percentageRule.window, 500, 1, 1000);
    route.percentageRule.operator = NUMERIC_COMPARATORS.some(([value]) => value === route.percentageRule.operator) ? route.percentageRule.operator : ">=";
    route.percentageRule.threshold = clamp(n(route.percentageRule.threshold, 70), 0, 100);
    route.tickDirectionRule.enabled = Boolean(route.tickDirectionRule.enabled);
    route.tickDirectionRule.window = whole(route.tickDirectionRule.window, 3, 1, 1000);
    if (!["rising", "falling", "no_move"].includes(route.tickDirectionRule.direction)) route.tickDirectionRule.direction = "rising";
    return route;
  }

  function routeFromServer(route) {
    if (!route || typeof route !== "object") return null;
    const conditions = Array.isArray(route.conditions) ? route.conditions : [];
    const digit = conditions.find((item) => item?.kind === "digit_compare" || item?.kind === "digit_parity");
    const percentage = conditions.find((item) => item?.kind === "percentage");
    const direction = conditions.find((item) => item?.kind === "direction");
    let mode = digit && percentage ? "combined" : percentage ? "percentage" : "last_digit";
    if (!digit && !percentage && direction) mode = "last_digit";
    let lastOperator = digit?.operator || ">=";
    if (digit?.kind === "digit_parity") lastOperator = digit.parity === "odd" ? "all_odd" : "all_even";
    return normalizeRoute({
      tradeType: String(route.trade_type || "over"),
      prediction: route.prediction ?? 2,
      durationTicks: route.duration_ticks ?? 1,
      analysisMode: mode,
      lastRule: {
        window: digit?.window ?? 5,
        operator: lastOperator,
        value: digit?.value ?? 3,
      },
      percentageRule: {
        target: percentage?.target || "even",
        value: percentage?.value ?? 5,
        window: percentage?.window ?? 500,
        operator: percentage?.operator || ">=",
        threshold: percentage?.threshold ?? 70,
      },
      tickDirectionRule: {
        enabled: Boolean(direction),
        window: direction?.window ?? 3,
        direction: direction?.direction || "rising",
      },
    });
  }

  function routeConditions(route) {
    const conditions = [];
    if (route.analysisMode === "last_digit" || route.analysisMode === "combined") {
      const operator = String(route.lastRule.operator || ">=");
      conditions.push({
        kind: "digit_compare",
        window: whole(route.lastRule.window, 5, 1, 1000),
        operator,
        value: ["all_even", "all_odd"].includes(operator) ? null : whole(route.lastRule.value, 3, 0, 9),
      });
    }
    if (route.analysisMode === "percentage" || route.analysisMode === "combined") {
      const percentage = {
        kind: "percentage",
        window: whole(route.percentageRule.window, 500, 1, 1000),
        target: String(route.percentageRule.target || "even"),
        operator: String(route.percentageRule.operator || ">="),
        threshold: clamp(n(route.percentageRule.threshold, 70), 0, 100),
      };
      if (["over", "under", "digit"].includes(percentage.target)) percentage.value = whole(route.percentageRule.value, 5, 0, 9);
      conditions.push(percentage);
    }
    if (route.tickDirectionRule.enabled) {
      conditions.push({
        kind: "direction",
        window: whole(route.tickDirectionRule.window, 3, 1, 1000),
        direction: String(route.tickDirectionRule.direction || "rising"),
      });
    }
    if (!conditions.length) conditions.push({ kind: "digit_compare", window: 1, operator: ">=", value: 0 });
    return conditions;
  }

  function resultRoutingPayload() {
    if (!state.routingEnabled) return { enabled: false };
    const route = normalizeRoute(state.afterLoss || clonePrimaryRoute());
    return {
      enabled: true,
      after_loss: {
        trade_type: route.tradeType,
        prediction: ["over", "under", "matches", "differs"].includes(route.tradeType) ? route.prediction : null,
        duration_ticks: route.durationTicks,
        conditions: routeConditions(route),
        match: "all",
      },
    };
  }

  function hydrateServerPayload(payload, { force = false } = {}) {
    if (!payload || typeof payload !== "object") return;
    if (payload.result_routing && typeof payload.result_routing === "object" && (force || !state.routingTouched)) {
      state.routingEnabled = Boolean(payload.result_routing.enabled);
      state.afterLoss = routeFromServer(payload.result_routing.after_loss) || state.afterLoss;
      state.serverHydrated = true;
      state.routingTouched = false;
    }
    if (payload.martingale && typeof payload.martingale === "object" && (force || !state.recoveryTouched)) {
      const mode = String(payload.martingale.mode || "multiplier");
      state.recoveryMode = mode === "split" ? "split" : "multiplier";
      state.splitCount = whole(payload.martingale.split_count ?? 2, 2, 1, 3);
      state.recoveryHydrated = true;
      state.recoveryTouched = false;
    }
    remountControls();
  }

  function routePreview(route) {
    if (!route) return "Configure the strategy to use after an actual loss.";
    const trade = TRADE_TYPES.find(([value]) => value === route.tradeType)?.[1] || route.tradeType;
    const prediction = ["over", "under", "matches", "differs"].includes(route.tradeType) ? ` ${route.prediction}` : "";
    const mode = route.analysisMode === "combined" ? "combined analysis" : route.analysisMode === "percentage" ? "percentage analysis" : "last-digit analysis";
    return `After an actual loss, wait for this independent ${mode}, then trade ${trade}${prediction}. Keep using this recovery route while actual recovery debt remains.`;
  }

  function routeSectionHtml() {
    const route = normalizeRoute(state.afterLoss || clonePrimaryRoute());
    const predictionHidden = !["over", "under", "matches", "differs"].includes(route.tradeType);
    const lastValueHidden = ["all_same", "all_even", "all_odd"].includes(route.lastRule.operator);
    const percentageValueHidden = !["over", "under", "digit"].includes(route.percentageRule.target);
    const showLast = route.analysisMode === "last_digit" || route.analysisMode === "combined";
    const showPercentage = route.analysisMode === "percentage" || route.analysisMode === "combined";
    return `<section class="builder-section result-routing-section" id="result-routing-section">
      <div class="section-label">Result-Based Trading</div>
      <label class="result-routing-toggle">
        <span><strong>Use a different strategy after a loss</strong><small>Optional. Keep this OFF when the same strategy should handle recovery.</small></span>
        <input id="result-routing-enabled" type="checkbox" ${state.routingEnabled ? "checked" : ""}>
      </label>
      <div class="result-routing-primary-note"><span><strong><b>WIN / First trade:</b> Primary strategy above</strong><small>The normal Custom Strategy resumes after actual recovery debt is cleared.</small></span></div>
      <div class="result-routing-recovery-box" ${state.routingEnabled ? "" : "hidden"}>
        <div class="result-routing-recovery-head"><span><strong>LOSS / Recovery Strategy</strong><small>Independent contract and analysis. The selected market scope remains unchanged.</small></span><b class="result-routing-badge">AFTER LOSS</b></div>
        <div class="result-routing-grid">
          <label class="result-routing-field">Trade after loss<select data-result-route="tradeType">${optionList(TRADE_TYPES, route.tradeType)}</select></label>
          <label class="result-routing-field result-route-value-field" ${predictionHidden ? "hidden" : ""}>Prediction<input data-result-route="prediction" type="number" min="0" max="9" step="1" value="${route.prediction}"></label>
          <label class="result-routing-field">Ticks<input data-result-route="durationTicks" type="number" min="1" max="100" step="1" value="${route.durationTicks}"></label>
          <label class="result-routing-field">Analysis mode<select data-result-route="analysisMode">${optionList([["last_digit", "Last Digit"], ["percentage", "Percentage"], ["combined", "Combined"]], route.analysisMode)}</select></label>
        </div>
        <div class="result-routing-subsection result-routing-fields" ${showLast ? "" : "hidden"} data-result-block="last">
          <strong>After-loss Last Digit analysis</strong>
          <div class="result-routing-analysis-grid">
            <label class="result-routing-field">Check last<input data-result-route="lastRule.window" type="number" min="1" max="1000" step="1" value="${route.lastRule.window}"></label>
            <label class="result-routing-field">Comparison<select data-result-route="lastRule.operator">${optionList(COMPARATORS, route.lastRule.operator)}</select></label>
            <label class="result-routing-field result-route-value-field" ${lastValueHidden ? "hidden" : ""}>Value<input data-result-route="lastRule.value" type="number" min="0" max="9" step="1" value="${route.lastRule.value}"></label>
          </div>
        </div>
        <div class="result-routing-subsection result-routing-fields" ${showPercentage ? "" : "hidden"} data-result-block="percentage">
          <strong>After-loss Percentage analysis</strong>
          <div class="result-routing-analysis-grid">
            <label class="result-routing-field">Check percentage of<select data-result-route="percentageRule.target">${optionList(PERCENTAGE_TARGETS, route.percentageRule.target)}</select></label>
            <label class="result-routing-field result-route-percentage-value" ${percentageValueHidden ? "hidden" : ""}>Digit<input data-result-route="percentageRule.value" type="number" min="0" max="9" step="1" value="${route.percentageRule.value}"></label>
            <label class="result-routing-field">Past ticks<input data-result-route="percentageRule.window" type="number" min="1" max="1000" step="1" value="${route.percentageRule.window}"></label>
            <label class="result-routing-field">Comparison<select data-result-route="percentageRule.operator">${optionList(NUMERIC_COMPARATORS, route.percentageRule.operator)}</select></label>
            <label class="result-routing-field">Threshold (%)<input data-result-route="percentageRule.threshold" type="number" min="0" max="100" step="0.1" value="${route.percentageRule.threshold}"></label>
          </div>
        </div>
        <div class="result-routing-subsection">
          <strong>Optional after-loss tick direction</strong>
          <div class="result-routing-analysis-grid">
            <label class="result-routing-field result-routing-checkbox"><input data-result-route="tickDirectionRule.enabled" type="checkbox" ${route.tickDirectionRule.enabled ? "checked" : ""}><span>Require direction</span></label>
            <label class="result-routing-field">Check last<input data-result-route="tickDirectionRule.window" type="number" min="1" max="1000" step="1" value="${route.tickDirectionRule.window}"></label>
            <label class="result-routing-field">Direction<select data-result-route="tickDirectionRule.direction">${optionList([["rising", "Up ticks"], ["falling", "Down ticks"], ["no_move", "No Move"]], route.tickDirectionRule.direction)}</select></label>
          </div>
        </div>
        <p class="result-routing-preview">${routePreview(route)}</p>
      </div>
    </section>`;
  }

  function recoveryControlHtml() {
    const parts = state.splitCount;
    return `<div class="recovery-spread-control" id="recovery-spread-control">
      <span><strong>Recovery plan</strong><small>Choose normal multiplier recovery or divide exact actual loss debt across 1–3 successful recovery trades.</small></span>
      <div class="recovery-spread-grid">
        <label class="result-routing-field">Recovery style<select id="recovery-style"><option value="multiplier" ${state.recoveryMode === "multiplier" ? "selected" : ""}>Multiplier Martingale</option><option value="split" ${state.recoveryMode === "split" ? "selected" : ""}>Martingale Spread — exact debt</option></select></label>
        <label class="result-routing-field recovery-spread-parts" ${state.recoveryMode === "split" ? "" : "hidden"}>Recover loss in how many successful trades?<select id="recovery-split-count">${[1, 2, 3].map((value) => `<option value="${value}" ${parts === value ? "selected" : ""}>${value}</option>`).join("")}</select></label>
      </div>
      <p class="recovery-spread-note">${state.recoveryMode === "split" ? `Split recovery is active: exact recovery debt is spread across ${parts} successful recovery trade${parts === 1 ? "" : "s"}. A losing recovery does not consume a successful part.` : "Multiplier recovery uses the Martingale multiplier configured above. Changes remain a draft until Save Builder or Start Auto Trading."}</p>
    </div>`;
  }

  function setNestedRoute(path, rawValue, isCheckbox = false) {
    if (!state.afterLoss) state.afterLoss = clonePrimaryRoute();
    const parts = String(path).split(".");
    let target = state.afterLoss;
    for (let index = 0; index < parts.length - 1; index += 1) {
      target = target[parts[index]];
      if (!target) return;
    }
    const key = parts[parts.length - 1];
    const current = target[key];
    target[key] = isCheckbox ? Boolean(rawValue) : typeof current === "number" ? n(rawValue, current) : String(rawValue);
    state.afterLoss = normalizeRoute(state.afterLoss);
    state.routingTouched = true;
  }

  function bindRouteControls(section) {
    q("#result-routing-enabled", section)?.addEventListener("change", (event) => {
      if (event.currentTarget.checked && !state.afterLoss) state.afterLoss = clonePrimaryRoute();
      state.routingEnabled = Boolean(event.currentTarget.checked);
      state.routingTouched = true;
      remountControls();
    });
    qa("[data-result-route]", section).forEach((field) => {
      const path = field.dataset.resultRoute;
      const structural = ["tradeType", "analysisMode", "lastRule.operator", "percentageRule.target", "tickDirectionRule.enabled"].includes(path);
      const eventName = field.tagName === "SELECT" || field.type === "checkbox" ? "change" : "input";
      field.addEventListener(eventName, () => {
        setNestedRoute(path, field.type === "checkbox" ? field.checked : field.value, field.type === "checkbox");
        if (structural) remountControls();
        else {
          const preview = q(".result-routing-preview", section);
          if (preview) preview.textContent = routePreview(state.afterLoss);
        }
      });
    });
  }

  function bindRecoveryControls(control) {
    q("#recovery-style", control)?.addEventListener("change", (event) => {
      state.recoveryMode = event.currentTarget.value === "split" ? "split" : "multiplier";
      state.recoveryTouched = true;
      remountControls();
    });
    q("#recovery-split-count", control)?.addEventListener("change", (event) => {
      state.splitCount = whole(event.currentTarget.value, 2, 1, 3);
      state.recoveryTouched = true;
      remountControls();
    });
  }

  function remountControls() {
    q("#result-routing-section")?.remove();
    q("#recovery-spread-control")?.remove();
    scheduleEnhance();
  }

  function enhance() {
    state.scheduled = false;
    const accountKey = currentAccountKey();
    if (accountKey !== state.accountKey) {
      state.accountKey = accountKey;
      state.routingEnabled = false;
      state.afterLoss = null;
      state.serverHydrated = false;
      state.routingTouched = false;
      state.recoveryMode = "multiplier";
      state.splitCount = 2;
      state.recoveryHydrated = false;
      state.recoveryTouched = false;
      window.setTimeout(loadServerState, 60);
    }

    const tradeBuilder = q(".trade-builder");
    if (tradeBuilder && !q("#result-routing-section")) {
      tradeBuilder.insertAdjacentHTML("afterend", routeSectionHtml());
      bindRouteControls(q("#result-routing-section"));
    }

    const moneyBuilder = q(".money-builder");
    if (moneyBuilder && !q("#recovery-spread-control", moneyBuilder)) {
      const grid = q(".money-grid", moneyBuilder);
      if (grid) grid.insertAdjacentHTML("afterend", recoveryControlHtml());
      else moneyBuilder.insertAdjacentHTML("beforeend", recoveryControlHtml());
      bindRecoveryControls(q("#recovery-spread-control", moneyBuilder));
    }
  }

  function scheduleEnhance() {
    if (state.scheduled) return;
    state.scheduled = true;
    window.requestAnimationFrame(enhance);
  }

  function endpointUrl(input) {
    if (typeof input === "string") return input;
    return input && typeof input.url === "string" ? input.url : "";
  }

  function installFetchBridge() {
    if (window.__FOA_RESULT_BASED_FETCH_BRIDGE__) return;
    window.__FOA_RESULT_BASED_FETCH_BRIDGE__ = true;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const url = endpointUrl(input);
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      const isCustom = url.includes("/me/custom-strategy");
      let nextInit = init;

      if (isCustom && method === "POST" && typeof init.body === "string") {
        try {
          const body = JSON.parse(init.body);
          body.result_routing = resultRoutingPayload();
          const existing = body.martingale && typeof body.martingale === "object" ? body.martingale : {};
          body.martingale = state.recoveryMode === "split"
            ? { ...existing, mode: "split", split_count: whole(state.splitCount, 2, 1, 3) }
            : { ...existing, mode: "multiplier", split_count: 1 };
          nextInit = { ...init, body: JSON.stringify(body) };
        } catch (_) {}
      }

      const response = await nativeFetch(input, nextInit);
      if (isCustom && response.ok) {
        response.clone().json().then((payload) => hydrateServerPayload(payload, { force: method === "POST" })).catch(() => {});
      }
      return response;
    };
  }

  async function loadServerState() {
    if (state.loadingServer || !q("#foa-simple-app")) return;
    state.loadingServer = true;
    try {
      await fetch("/me/custom-strategy", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
    } catch (_) {
      // The builder remains fully editable while a background hydration read retries.
    } finally {
      state.loadingServer = false;
    }
  }

  function getState() {
    return {
      routingEnabled: Boolean(state.routingEnabled),
      afterLoss: clone(state.afterLoss),
      recoveryMode: state.recoveryMode,
      splitCount: state.splitCount,
    };
  }

  function applyState(raw = {}) {
    if (raw.routingEnabled !== undefined) state.routingEnabled = Boolean(raw.routingEnabled);
    if (raw.afterLoss !== undefined) state.afterLoss = raw.afterLoss ? normalizeRoute(clone(raw.afterLoss)) : null;
    if (raw.recoveryMode !== undefined) state.recoveryMode = raw.recoveryMode === "split" ? "split" : "multiplier";
    if (raw.splitCount !== undefined) state.splitCount = whole(raw.splitCount, 2, 1, 3);
    state.routingTouched = true;
    state.recoveryTouched = true;
    remountControls();
  }

  window.FOA_RESULT_BASED_API = {
    version: VERSION,
    getState,
    applyState,
    resultRoutingPayload,
  };

  installFetchBridge();
  new MutationObserver(scheduleEnhance).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("change", (event) => {
    if (event.target?.matches?.("[data-mode], [data-mobile-mode]")) {
      window.setTimeout(() => {
        state.accountKey = "";
        scheduleEnhance();
      }, 80);
    }
  }, true);

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", () => {
        scheduleEnhance();
        window.setTimeout(loadServerState, 250);
      }, { once: true })
    : (() => {
        scheduleEnhance();
        window.setTimeout(loadServerState, 250);
      })();

  window.FOA_RESULT_BASED_STRATEGY_VERSION = VERSION;
})();
