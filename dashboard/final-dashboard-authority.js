(() => {
  "use strict";

  const SERVER_STRATEGY_PREFIX = "foa-running-server-strategy-v1";
  const LIMIT_DISMISS_PREFIX = "foa-final-risk-limit-dismiss-v1";
  const SPECIAL_COMPARATOR_PREFIX = "foa-special-last-comparator-v1";
  const BUILDER_DRAFT_KEYS = ["foa-builder-draft-v2", "foa-builder-draft-v1"];
  const SPECIAL_COMPARATORS = new Set(["all_even", "all_odd"]);
  const START_AUTHORITY_WINDOW_MS = 60000;
  const POLL_MS = 4000;
  let localStartAt = 0;
  let polling = false;
  let scheduled = false;
  let lastLifecycle = null;
  let specialComparatorHydrated = false;
  let specialComparatorTouchedAt = 0;

  function storageGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }

  function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch (_) {}
  }

  function storageRemove(key) {
    try { localStorage.removeItem(key); } catch (_) {}
  }

  function currentMe() {
    return window.FOA_NETLIFY_LIVE_CACHE?.me || window.FOA_BOOT_SESSION || null;
  }

  function accountIdentity() {
    const me = currentMe();
    const mode = String(me?.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
    const account = String(
      me?.account_id_masked || me?.account_id || me?.label || "account",
    ).trim();
    return `${mode}:${account}`;
  }

  function isAuthenticated() {
    return Boolean(currentMe()?.authenticated || document.querySelector(".builder-header #logout"));
  }

  async function getJSON(path) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`${path} returned ${response.status}`);
    return response.json();
  }

  function stableValue(value) {
    if (Array.isArray(value)) return value.map(stableValue);
    if (value && typeof value === "object") {
      return Object.keys(value).sort().reduce((result, key) => {
        result[key] = stableValue(value[key]);
        return result;
      }, {});
    }
    return value;
  }

  function strategyHash(config) {
    const text = JSON.stringify(stableValue(config || {}));
    let hash = 2166136261;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  }

  function strategyStorageKey() {
    return `${SERVER_STRATEGY_PREFIX}:${accountIdentity()}`;
  }

  function specialComparatorKey() {
    return `${SPECIAL_COMPARATOR_PREFIX}:${accountIdentity()}`;
  }

  function specialComparator() {
    const value = String(storageGet(specialComparatorKey()) || "");
    return SPECIAL_COMPARATORS.has(value) ? value : "";
  }

  function money(value, currency = "USD") {
    const amount = Number(value || 0);
    const unit = String(currency || "USD").toUpperCase();
    const prefix = unit === "USD" ? "$" : `${unit} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function cleanNavigation() {
    document.querySelectorAll(
      '.builder-header [data-view="settings"], [data-mobile-view="settings"]',
    ).forEach((node) => node.remove());

    const brandCopy = document.querySelector(
      ".builder-header .builder-brand > div:not(.builder-logo)",
    );
    if (brandCopy) brandCopy.hidden = true;

    const drawer = document.querySelector("#foa-mobile-drawer");
    if (!drawer) return;

    const head = drawer.querySelector(".foa-mobile-drawer-head");
    const headCopy = head?.querySelector(":scope > div");
    headCopy?.remove();

    const nav = drawer.querySelector(".foa-mobile-drawer-nav");
    const theme = drawer.querySelector(".foa-mobile-theme-row");
    const account = drawer.querySelector(".foa-mobile-account-card");
    const actions = drawer.querySelector(".foa-mobile-drawer-actions");

    if (nav) {
      const dashboard = nav.querySelector('[data-mobile-view="main"]');
      const trades = nav.querySelector('[data-mobile-view="trades"]');
      nav.replaceChildren(...[dashboard, trades].filter(Boolean));
    }

    /* Navigation belongs immediately below the close icon. Appearance follows it;
       the trading account and logout/risk actions stay anchored at the bottom. */
    if (head && nav && head.nextElementSibling !== nav) head.after(nav);
    if (nav && theme && nav.nextElementSibling !== theme) nav.after(theme);
    if (account && actions && account.nextElementSibling !== actions) actions.before(account);

    const active = document.querySelector(".builder-header .builder-nav [data-view].active");
    const activeView = String(active?.dataset?.view || "main");
    drawer.querySelectorAll("[data-mobile-view]").forEach((button) => {
      button.classList.toggle("active", String(button.dataset.mobileView || "main") === activeView);
    });
  }

  function readableBuilderLabels() {
    document.querySelectorAll(".field > span").forEach((node) => {
      const text = String(node.textContent || "");
      const updated = text
        .replace(/Check last N digits/gi, "Check last number of digits")
        .replace(/Last N digits/gi, "Last number of digits");
      if (updated !== text) node.textContent = updated;
    });
  }

  function ensureSpecialComparatorOptions() {
    const select = document.querySelector('select[data-builder="lastRule.operator"]');
    if (!select) return;

    const options = [
      ["all_even", "All even"],
      ["all_odd", "All odd"],
    ];
    options.forEach(([value, label]) => {
      if (select.querySelector(`option[value="${value}"]`)) return;
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    });

    const special = specialComparator();
    if (special && select.value !== special) select.value = special;

    const valueInput = document.querySelector('input[data-builder="lastRule.value"]');
    const valueField = valueInput?.closest("label.field");
    if (valueField) valueField.hidden = Boolean(special);
  }

  function hydrateSpecialComparatorFromConfig(payload, { force = false } = {}) {
    if (!force && specialComparatorHydrated) return;
    if (!force && Date.now() - specialComparatorTouchedAt < 120000) return;
    const digit = (payload?.config?.conditions || []).find((item) => item?.kind === "digit_compare");
    const operator = String(digit?.operator || "").toLowerCase();
    if (SPECIAL_COMPARATORS.has(operator)) storageSet(specialComparatorKey(), operator);
    else storageRemove(specialComparatorKey());
    specialComparatorHydrated = true;
    scheduleEnhance();
  }

  function installSpecialComparatorRequestBridge() {
    if (window.__FOA_SPECIAL_COMPARATOR_FETCH_BRIDGE__) return;
    window.__FOA_SPECIAL_COMPARATOR_FETCH_BRIDGE__ = true;
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const method = String(init?.method || "GET").toUpperCase();
      const rawUrl = typeof input === "string" ? input : String(input?.url || "");
      const isCustomSave = method === "POST"
        && (rawUrl.includes("/me/custom-strategy") || rawUrl.includes("/api/me/custom-strategy"));
      if (!isCustomSave) return originalFetch(input, init);

      const special = specialComparator();
      if (!special || typeof init.body !== "string") return originalFetch(input, init);
      try {
        const payload = JSON.parse(init.body);
        const condition = Array.isArray(payload?.conditions)
          ? payload.conditions.find((item) => item?.kind === "digit_compare")
          : null;
        if (condition) {
          condition.operator = special;
          delete condition.value;
          return originalFetch(input, { ...init, body: JSON.stringify(payload) });
        }
      } catch (_) {}
      return originalFetch(input, init);
    };
  }

  function limitDismissKey(lifecycle) {
    const status = String(lifecycle?.execution_status || "").toLowerCase();
    const achieved = Number(lifecycle?.limit_achieved ?? lifecycle?.session_profit ?? 0).toFixed(2);
    return `${LIMIT_DISMISS_PREFIX}:${accountIdentity()}:${status}:${achieved}`;
  }

  function removeLegacyLimitNotices() {
    document.querySelectorAll(".limit-notifier:not(.foa-final-limit-notifier)")
      .forEach((node) => node.remove());
  }

  function syncLimitNotice(lifecycle) {
    const status = String(lifecycle?.execution_status || "").toLowerCase();
    if (!["take_profit", "stop_loss"].includes(status)) {
      document.querySelectorAll(".foa-final-limit-notifier").forEach((node) => node.remove());
      return;
    }

    removeLegacyLimitNotices();
    const dismissKey = limitDismissKey(lifecycle);
    if (storageGet(dismissKey) === "1") {
      document.querySelectorAll(".foa-final-limit-notifier").forEach((node) => node.remove());
      return;
    }

    const me = currentMe();
    const currency = me?.currency || "USD";
    const target = Math.abs(Number(lifecycle?.limit_target || 0));
    const achieved = Number(lifecycle?.limit_achieved ?? lifecycle?.session_profit ?? 0);
    const isTp = status === "take_profit";

    let notice = document.querySelector(".foa-final-limit-notifier");
    if (!notice) {
      notice = document.createElement("aside");
      notice.className = `limit-notifier ${isTp ? "tp" : "sl"} foa-final-limit-notifier`;
      notice.setAttribute("role", "status");
      notice.setAttribute("aria-live", "polite");

      const icon = document.createElement("div");
      icon.className = "limit-icon";
      icon.textContent = isTp ? "TP" : "SL";

      const copy = document.createElement("div");
      const title = document.createElement("strong");
      const values = document.createElement("span");
      values.className = "foa-limit-values";
      const detail = document.createElement("small");
      copy.append(title, values, detail);

      const close = document.createElement("button");
      close.type = "button";
      close.textContent = "OK";
      close.dataset.finalLimitDismiss = "true";
      close.addEventListener("click", () => {
        storageSet(limitDismissKey(lastLifecycle || lifecycle), "1");
        notice.remove();
      });

      notice.append(icon, copy, close);
      (document.querySelector("#foa-simple-app") || document.body).appendChild(notice);
    }

    notice.classList.toggle("tp", isTp);
    notice.classList.toggle("sl", !isTp);
    const icon = notice.querySelector(".limit-icon");
    const title = notice.querySelector("strong");
    const values = notice.querySelector(".foa-limit-values");
    const detail = notice.querySelector("small");
    if (icon) icon.textContent = isTp ? "TP" : "SL";
    if (title) title.textContent = isTp ? "Take Profit hit — trading stopped" : "Stop Loss hit — trading stopped";
    if (values) {
      values.textContent = `Target ${money(target, currency)} · Session P/L ${money(achieved, currency)}`;
    }
    if (detail) {
      detail.textContent = String(
        lifecycle?.reason
        || `${isTp ? "Take profit" : "Stop loss"} reached. Auto trading is stopped; the next Start begins fresh.`,
      );
    }
  }

  function running(lifecycle) {
    return String(lifecycle?.lifecycle || "").toLowerCase() === "running"
      && Boolean(lifecycle?.enabled);
  }

  async function synchronizeRunningStrategy(lifecycle) {
    if (!running(lifecycle)) return;
    const custom = await getJSON("/me/custom-strategy");
    hydrateSpecialComparatorFromConfig(custom, { force: true });
    if (!custom?.config?.configured) return;

    const hash = strategyHash(custom.config);
    const key = strategyStorageKey();
    const previous = storageGet(key);
    if (previous === hash) return;

    storageSet(key, hash);
    const thisDeviceStarted = Date.now() - localStartAt <= START_AUTHORITY_WINDOW_MS;
    localStartAt = 0;
    if (thisDeviceStarted) return;

    BUILDER_DRAFT_KEYS.forEach(storageRemove);
    window.location.reload();
  }

  async function pollAuthority() {
    if (polling || document.hidden || !isAuthenticated()) return;
    polling = true;
    try {
      const lifecycle = await getJSON("/me/trading-lifecycle");
      lastLifecycle = lifecycle;
      syncLimitNotice(lifecycle);
      if (!specialComparatorHydrated) {
        try {
          hydrateSpecialComparatorFromConfig(await getJSON("/me/custom-strategy"));
        } catch (_) {}
      }
      await synchronizeRunningStrategy(lifecycle);
    } catch (_) {
      // Failed UI reads never alter trading lifecycle or local strategy state.
    } finally {
      polling = false;
    }
  }

  function enhance() {
    scheduled = false;
    cleanNavigation();
    readableBuilderLabels();
    ensureSpecialComparatorOptions();
    if (lastLifecycle) syncLimitNotice(lastLifecycle);
  }

  function scheduleEnhance() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(enhance);
  }

  installSpecialComparatorRequestBridge();

  document.addEventListener("change", (event) => {
    const select = event.target?.closest?.('select[data-builder="lastRule.operator"]');
    if (!select) return;
    const value = String(select.value || "").toLowerCase();
    specialComparatorTouchedAt = Date.now();
    specialComparatorHydrated = true;
    if (SPECIAL_COMPARATORS.has(value)) storageSet(specialComparatorKey(), value);
    else storageRemove(specialComparatorKey());
    window.setTimeout(scheduleEnhance, 0);
  }, true);

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.('[data-main-action="start"]')) {
      localStartAt = Date.now();
    }
    if (event.target?.closest?.('[data-mobile-view="main"], [data-mobile-view="trades"]')) {
      window.setTimeout(scheduleEnhance, 0);
    }
  }, true);

  const observer = new MutationObserver(() => scheduleEnhance());
  observer.observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener("focus", pollAuthority);
  window.addEventListener("pageshow", () => {
    scheduleEnhance();
    pollAuthority();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      scheduleEnhance();
      pollAuthority();
    }
  });

  window.setInterval(pollAuthority, POLL_MS);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", () => {
        scheduleEnhance();
        window.setTimeout(pollAuthority, 700);
      }, { once: true })
    : (() => {
        scheduleEnhance();
        window.setTimeout(pollAuthority, 700);
      })();

  window.FOA_FINAL_DASHBOARD_AUTHORITY_VERSION = "20260813-2";
})();
