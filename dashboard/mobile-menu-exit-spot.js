(() => {
  "use strict";

  const MOBILE_QUERY = "(max-width: 760px)";
  const TRADE_RESET_PREFIX = "foa-trade-session-reset-v1";
  let scheduled = false;

  function storageGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }

  function isMobile() {
    return window.matchMedia(MOBILE_QUERY).matches;
  }

  function accountType(me) {
    return String(me?.account_type || "demo").toLowerCase() === "real" ? "real" : "demo";
  }

  function accountMask(me) {
    return String(me?.account_id_masked || me?.account_id || "public");
  }

  function resetTime(me) {
    if (!me) return 0;
    const raw = storageGet(`${TRADE_RESET_PREFIX}:${accountType(me)}:${accountMask(me)}`);
    if (!raw) return 0;
    const value = Date.parse(raw);
    return Number.isFinite(value) ? value : 0;
  }

  function rowTime(row) {
    const value = Date.parse(
      row?.purchase_time || row?.provider_purchase_time || row?.created_at || "",
    );
    return Number.isFinite(value) ? value : 0;
  }

  function liveRows() {
    const cache = window.FOA_NETLIFY_LIVE_CACHE;
    const me = cache?.me;
    const rows = Array.isArray(cache?.trades?.trades) ? cache.trades.trades : [];
    const cutoff = resetTime(me);
    return cutoff ? rows.filter((row) => rowTime(row) >= cutoff) : rows;
  }

  function exitSpotDisplay(row) {
    if (!row) return "-";
    const outcome = String(row.outcome || "OPEN").toUpperCase();
    const digit = row.exit_digit ?? row.actual_last_digit;
    if (digit !== null && digit !== undefined && String(digit) !== "") {
      return String(digit);
    }
    const spot = row.exit_spot ?? row.exit_tick;
    if (spot !== null && spot !== undefined && String(spot) !== "") {
      const text = String(spot);
      const numericDigits = text.match(/\d/g);
      return numericDigits?.length ? numericDigits[numericDigits.length - 1] : text;
    }
    return outcome === "OPEN" ? "Open" : "-";
  }

  function exitSpotTitle(row) {
    if (!row) return "Exit spot unavailable";
    const spot = row.exit_spot ?? row.exit_tick;
    const digit = row.exit_digit ?? row.actual_last_digit;
    if (spot !== null && spot !== undefined && String(spot) !== "") {
      return digit !== null && digit !== undefined
        ? `Exit spot ${spot} · final digit ${digit}`
        : `Exit spot ${spot}`;
    }
    return digit !== null && digit !== undefined ? `Final digit ${digit}` : "Exit spot unavailable";
  }

  function patchTradeColumns() {
    const dataRows = liveRows();
    document.querySelectorAll(".builder-recent-trades").forEach((panel) => {
      const head = panel.querySelector(".trade-head");
      if (head && !head.querySelector("[data-exit-spot-head]")) {
        const label = document.createElement("span");
        label.dataset.exitSpotHead = "true";
        label.textContent = "Exit spot";
        head.insertBefore(label, head.lastElementChild || null);
      }

      const rowNodes = Array.from(panel.querySelectorAll(":scope > .trade-row"));
      rowNodes.forEach((node, index) => {
        let cell = node.querySelector(".trade-exit-spot");
        if (!cell) {
          cell = document.createElement("span");
          cell.className = "trade-exit-spot";
          node.insertBefore(cell, node.lastElementChild || null);
        }
        const source = dataRows[index];
        const text = exitSpotDisplay(source);
        if (cell.textContent !== text) cell.textContent = text;
        cell.title = exitSpotTitle(source);
      });
    });
  }

  function currentThemeIsLight() {
    const app = document.querySelector("#foa-simple-app");
    return String(app?.dataset?.theme || document.documentElement.dataset.theme || "dark") === "light";
  }

  function currentAccountLabel() {
    const me = window.FOA_NETLIFY_LIVE_CACHE?.me;
    if (me?.authenticated) {
      const mode = accountType(me) === "real" ? "Real" : "Demo";
      const id = me.display_account_id || me.account_id_full || me.account_id_masked || me.account_id || "Account";
      return `${mode} · ${id}`;
    }
    return String(document.querySelector(".builder-header .account-pill")?.textContent || "Not logged in").trim();
  }

  function activeView() {
    const active = document.querySelector(".builder-header .builder-nav [data-view].active");
    return String(active?.dataset?.view || "main");
  }

  function drawerMarkup() {
    return `<aside id="foa-mobile-drawer" class="foa-mobile-drawer" aria-hidden="true" aria-label="Mobile navigation">
      <div class="foa-mobile-drawer-head">
        <div><strong>Custom Strategy Builder</strong><small>Navigation & account</small></div>
        <button type="button" class="foa-mobile-drawer-close" data-mobile-drawer-close aria-label="Close menu">×</button>
      </div>
      <div class="foa-mobile-account-card"><span>Trading account</span><strong data-mobile-account-label>Not logged in</strong></div>
      <nav class="foa-mobile-drawer-nav" aria-label="Builder navigation">
        <button type="button" class="foa-mobile-nav-button" data-mobile-view="main">Dashboard</button>
        <button type="button" class="foa-mobile-nav-button" data-mobile-view="settings">Settings</button>
        <button type="button" class="foa-mobile-nav-button" data-mobile-view="trades">Trades</button>
      </nav>
      <div class="foa-mobile-theme-row">
        <div class="foa-mobile-theme-copy"><strong>Appearance</strong><small data-mobile-theme-label>Dark mode</small></div>
        <button type="button" class="foa-mobile-theme-toggle" data-mobile-theme-toggle role="switch" aria-checked="false" aria-label="Toggle light and dark mode"></button>
      </div>
      <div class="foa-mobile-drawer-actions">
        <button type="button" class="foa-mobile-risk-button" data-mobile-risk>Risk Disclaimer</button>
        <button type="button" class="foa-mobile-logout-button" data-mobile-logout>Logout</button>
        <a class="foa-mobile-login-button" data-mobile-login href="/oauth/start">Login with Deriv</a>
      </div>
    </aside><div class="foa-mobile-drawer-backdrop" data-mobile-drawer-close aria-hidden="true"></div>`;
  }

  function ensureDrawer() {
    if (!document.querySelector("#foa-simple-app")) return;
    if (!document.querySelector("#foa-mobile-drawer")) {
      const shell = document.createElement("div");
      shell.innerHTML = drawerMarkup();
      while (shell.firstChild) document.body.appendChild(shell.firstChild);
    }

    const host = document.querySelector("#telegram-dashboard-snapshot");
    if (host && !host.querySelector(".foa-mobile-menu-launcher")) {
      const launcher = document.createElement("div");
      launcher.className = "foa-mobile-menu-launcher";
      launcher.innerHTML = `<button type="button" class="foa-mobile-menu-button" data-mobile-drawer-open aria-controls="foa-mobile-drawer" aria-expanded="false" aria-label="Open menu"><span></span></button>`;
      host.insertBefore(launcher, host.firstChild);
    }

    const account = document.querySelector("[data-mobile-account-label]");
    if (account) account.textContent = currentAccountLabel();

    const view = activeView();
    document.querySelectorAll("[data-mobile-view]").forEach((button) => {
      button.classList.toggle("active", button.dataset.mobileView === view);
    });

    const light = currentThemeIsLight();
    const toggle = document.querySelector("[data-mobile-theme-toggle]");
    if (toggle) toggle.setAttribute("aria-checked", light ? "true" : "false");
    const themeLabel = document.querySelector("[data-mobile-theme-label]");
    if (themeLabel) themeLabel.textContent = light ? "Light mode" : "Dark mode";

    const authenticated = Boolean(
      window.FOA_NETLIFY_LIVE_CACHE?.me?.authenticated
      || document.querySelector(".builder-header #logout"),
    );
    const logout = document.querySelector("[data-mobile-logout]");
    const login = document.querySelector("[data-mobile-login]");
    if (logout) logout.hidden = !authenticated;
    if (login) login.hidden = authenticated;
  }

  function setDrawer(open) {
    const drawer = document.querySelector("#foa-mobile-drawer");
    if (!drawer) return;
    const next = Boolean(open && isMobile());
    document.body.classList.toggle("foa-mobile-drawer-open", next);
    drawer.setAttribute("aria-hidden", next ? "false" : "true");
    document.querySelectorAll("[data-mobile-drawer-open]").forEach((button) => {
      button.setAttribute("aria-expanded", next ? "true" : "false");
    });
  }

  function clickOriginal(selector) {
    const target = document.querySelector(selector);
    if (!target) return false;
    target.click();
    return true;
  }

  function reorderTradeStats() {
    const stats = document.querySelector("#foa-simple-app main > .builder-stats.compact");
    const controls = document.querySelector("#foa-simple-app main > .trades-control-panel");
    if (!stats || !controls || stats.parentElement !== controls.parentElement) return;
    const parent = stats.parentElement;
    if (isMobile()) {
      if (controls.previousElementSibling !== stats) parent.insertBefore(stats, controls);
    } else if (stats.previousElementSibling !== controls) {
      parent.insertBefore(controls, stats);
    }
  }

  function enhance() {
    scheduled = false;
    ensureDrawer();
    reorderTradeStats();
    patchTradeColumns();
  }

  function scheduleEnhance() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(enhance);
  }

  document.addEventListener("click", (event) => {
    const openButton = event.target?.closest?.("[data-mobile-drawer-open]");
    if (openButton) {
      setDrawer(true);
      return;
    }
    if (event.target?.closest?.("[data-mobile-drawer-close]")) {
      setDrawer(false);
      return;
    }

    const viewButton = event.target?.closest?.("[data-mobile-view]");
    if (viewButton) {
      const view = String(viewButton.dataset.mobileView || "main");
      setDrawer(false);
      clickOriginal(`.builder-header [data-view="${view}"]`);
      return;
    }

    if (event.target?.closest?.("[data-mobile-theme-toggle]")) {
      const next = currentThemeIsLight() ? "dark" : "light";
      setDrawer(false);
      clickOriginal(`.builder-header [data-theme-value="${next}"]`);
      return;
    }

    if (event.target?.closest?.("[data-mobile-risk]")) {
      setDrawer(false);
      clickOriginal(".builder-header #risk-disclaimer-toggle");
      return;
    }

    if (event.target?.closest?.("[data-mobile-logout]")) {
      setDrawer(false);
      clickOriginal(".builder-header #logout");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setDrawer(false);
  });

  window.addEventListener("resize", () => {
    if (!isMobile()) setDrawer(false);
    scheduleEnhance();
  });

  const observer = new MutationObserver(() => scheduleEnhance());
  observer.observe(document.documentElement, { childList: true, subtree: true });

  window.setInterval(scheduleEnhance, 1200);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scheduleEnhance, { once: true })
    : scheduleEnhance();

  window.FOA_MOBILE_DRAWER_EXIT_SPOT_VERSION = "20260813-1";
})();
