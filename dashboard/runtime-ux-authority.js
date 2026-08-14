(() => {
  "use strict";

  if (window.__FOA_RUNTIME_UX_AUTHORITY__) return;
  window.__FOA_RUNTIME_UX_AUTHORITY__ = true;

  const VERSION = "20260814-runtime-ux-v2";
  let pendingMode = "";
  let switchSnapshot = null;
  let switchLockUntil = 0;
  let scheduled = false;

  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function parseMoney(text) {
    const normalized = String(text || "").replace(/[^0-9+\-.]/g, "");
    const value = Number(normalized);
    return Number.isFinite(value) ? value : 0;
  }

  function setPnlTone() {
    qa(".builder-stat").forEach((card) => {
      const label = String(card.querySelector("span")?.textContent || "").trim();
      if (!["Today's P/L", "P/L"].includes(label)) return;
      const strong = card.querySelector("strong");
      if (!strong || String(strong.textContent || "").includes("Syncing")) return;
      const value = parseMoney(strong.textContent || "0");
      card.classList.remove("win", "loss", "foa-pnl-positive", "foa-pnl-negative", "foa-pnl-zero");
      if (value > 0) card.classList.add("win", "foa-pnl-positive");
      else if (value < 0) card.classList.add("loss", "foa-pnl-negative");
      else card.classList.add("foa-pnl-zero");
    });
  }

  function money(value, currency = "USD") {
    const amount = Number(value || 0);
    const prefix = String(currency || "USD").toUpperCase() === "USD" ? "$" : `${currency} `;
    return `${amount < 0 ? "-" : ""}${prefix}${Math.abs(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function statCard(label) {
    return qa(".builder-stat").find((card) => String(card.querySelector("span")?.textContent || "").trim() === label) || null;
  }

  function showSwitchPending(type) {
    const mode = type === "real" ? "real" : "demo";
    const balance = statCard("Balance");
    if (balance) {
      const strong = balance.querySelector("strong");
      const small = balance.querySelector("small");
      if (strong) strong.textContent = "Syncing…";
      if (small) small.textContent = `${mode} account`;
    }
    const pnl = statCard("Today's P/L") || statCard("P/L");
    if (pnl) {
      const strong = pnl.querySelector("strong");
      if (strong) strong.textContent = "Syncing…";
      pnl.classList.remove("win", "loss", "foa-pnl-positive", "foa-pnl-negative", "foa-pnl-zero");
    }
    qa(".account-pill").forEach((pill) => { pill.textContent = `${mode} · syncing account…`; });
    qa("[data-mode]").forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
  }

  function patchSwitchSnapshot() {
    if (!switchSnapshot || Date.now() > switchLockUntil) return;
    const me = switchSnapshot;
    const type = String(me.account_type || pendingMode || "demo").toLowerCase() === "real" ? "real" : "demo";
    const balance = statCard("Balance");
    if (balance) {
      const strong = balance.querySelector("strong");
      const small = balance.querySelector("small");
      if (strong) strong.textContent = money(me.balance || 0, me.currency || "USD");
      if (small) small.textContent = `${type} account`;
    }
    qa(".account-pill").forEach((pill) => {
      const id = me.account_id || me.account_id_full || me.account_id_masked || me.display_account_id || "Account";
      pill.textContent = `${type} ${id}`;
    });
    qa("[data-mode]").forEach((button) => button.classList.toggle("active", button.dataset.mode === type));

    const stats = me.stats || {};
    const pnl = Number(stats.profit || 0);
    const pnlCard = statCard("Today's P/L") || statCard("P/L");
    if (pnlCard && stats.profit !== undefined) {
      const strong = pnlCard.querySelector("strong");
      if (strong) strong.textContent = money(pnl, me.currency || "USD");
    }
    delete document.documentElement.dataset.accountSwitching;
    setPnlTone();
  }

  function normalizeStartingCopy() {
    qa(".builder-status-line").forEach((line) => {
      const state = String(line.dataset.runtimeState || "").toUpperCase();
      const span = line.querySelector("span");
      if (!span) return;
      const text = String(span.textContent || "").toLowerCase();
      if (state === "STARTING" || text.includes("initializing authenticated") || text.includes("validating authenticated") || text.includes("connecting private") || text.includes("resynchronizing automatically")) {
        span.textContent = "Starting - Connecting execution stream and preparing strategy watcher...";
      }
    });
    qa(".trades-control-panel").forEach((panel) => {
      const title = panel.querySelector("h2");
      const paragraphs = panel.querySelectorAll("p");
      const text = Array.from(paragraphs).map((node) => String(node.textContent || "").toLowerCase()).join(" ");
      if (text.includes("initializing authenticated") || text.includes("validating authenticated") || text.includes("connecting private") || text.includes("resynchronizing automatically")) {
        if (title) title.textContent = "Starting";
        if (paragraphs.length > 1) paragraphs[1].textContent = "Connecting execution stream and preparing strategy watcher...";
      }
    });
  }

  async function warmLiveSnapshot() {
    try {
      const response = await fetch("/me/live-snapshot", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json();
      if (payload?.me?.authenticated && pendingMode && String(payload.me.account_type || "").toLowerCase() === pendingMode) {
        switchSnapshot = payload.me;
        switchLockUntil = Date.now() + 3500;
        window.FOA_NETLIFY_LIVE_CACHE = {
          ...(window.FOA_NETLIFY_LIVE_CACHE || {}),
          savedAt: Date.now(),
          me: payload.me,
          lifecycle: payload.lifecycle,
          trades: payload.trades,
        };
        patchSwitchSnapshot();
      }
    } catch (_) {}
  }

  function scheduleFastWarmup() {
    [80, 260, 650, 1400].forEach((delay) => window.setTimeout(warmLiveSnapshot, delay));
  }

  function installFetchBridge() {
    const previousFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const url = typeof input === "string" ? input : String(input?.url || "");
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      const response = await previousFetch(input, init);
      if (response.ok && method === "POST" && url.includes("/me/switch-account")) {
        response.clone().json().then((payload) => {
          if (!payload?.me?.authenticated) return;
          switchSnapshot = payload.me;
          pendingMode = String(payload.me.account_type || pendingMode || "demo").toLowerCase() === "real" ? "real" : "demo";
          switchLockUntil = Date.now() + 4500;
          window.FOA_NETLIFY_LIVE_CACHE = {
            ...(window.FOA_NETLIFY_LIVE_CACHE || {}),
            savedAt: Date.now(),
            me: payload.me,
          };
          patchSwitchSnapshot();
          scheduleFastWarmup();
          window.setTimeout(() => window.dispatchEvent(new Event("pageshow")), 2300);
          window.dispatchEvent(new CustomEvent("foa:account-switched", { detail: { me: payload.me } }));
        }).catch(() => {});
      }
      return response;
    };
  }

  function enhance() {
    scheduled = false;
    setPnlTone();
    patchSwitchSnapshot();
    normalizeStartingCopy();
  }

  function scheduleEnhance() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(enhance);
  }

  document.addEventListener("click", (event) => {
    const mode = event.target?.closest?.("[data-mode]");
    if (mode && !mode.disabled) {
      pendingMode = String(mode.dataset.mode || "demo").toLowerCase() === "real" ? "real" : "demo";
      switchSnapshot = null;
      switchLockUntil = Date.now() + 6000;
      document.documentElement.dataset.accountSwitching = pendingMode;
      showSwitchPending(pendingMode);
    }

    const main = event.target?.closest?.("[data-main-action]");
    if (main && main.dataset.mainAction === "start") {
      document.documentElement.dataset.autoTradingStarting = "true";
      scheduleFastWarmup();
      [200, 700, 1600, 3200].forEach((delay) => window.setTimeout(() => {
        scheduleEnhance();
        warmLiveSnapshot();
      }, delay));
      window.setTimeout(() => delete document.documentElement.dataset.autoTradingStarting, 15000);
    }
  }, true);

  new MutationObserver(scheduleEnhance).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  installFetchBridge();
  window.setInterval(scheduleEnhance, 700);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleEnhance, { once: true })
    : scheduleEnhance();

  window.FOA_RUNTIME_UX_VERSION = VERSION;
})();
