(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1__) return;
  window.__DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1__ = true;

  const DOT_ID = "DOT93427967";
  const DOT_MASK = "DOT***967";
  const ROT_ID = "ROT92069206";
  const DOT_SHARE = 0.75;
  const ROT_SHARE = 0.25;
  const VIEW_KEY = "derivadmin-marketing-demo-view-v4";
  const LEDGER_PREFIX = "derivadmin-marketing-demo-ui-ledger-v4:";
  const EPSILON = 0.000001;

  function runtimeState() {
    try { return window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.state?.() || {}; }
    catch (_) { return {}; }
  }

  function accountId(account) {
    return String(
      account?.account_id_full
      || account?.account_id
      || account?.loginid
      || account?.account_id_masked
      || "",
    ).trim().toUpperCase();
  }

  function isDotAccount(account) {
    const id = accountId(account);
    return id === DOT_ID || id === DOT_MASK || (id.startsWith("DOT") && id.endsWith("967"));
  }

  function providerAccount() {
    const state = runtimeState();
    const accounts = Array.isArray(state.accounts) ? state.accounts : [];
    const selectedId = Number(state.selected_managed_id || 0);
    const selected = accounts.find((item) => Number(item?.managed_account_id || 0) === selectedId)
      || accounts.find((item) => item?.selected)
      || null;
    return isDotAccount(selected) ? selected : null;
  }

  function active() {
    return Boolean(providerAccount());
  }

  function view() {
    try { return localStorage.getItem(VIEW_KEY) === "rot" ? "rot" : "dot"; }
    catch (_) { return "dot"; }
  }

  function setView(next) {
    const normalized = next === "rot" ? "rot" : "dot";
    try { localStorage.setItem(VIEW_KEY, normalized); } catch (_) {}
    renderSoon();
  }

  function managedId() {
    return Number(providerAccount()?.managed_account_id || 0);
  }

  function ledgerKey() {
    return `${LEDGER_PREFIX}${managedId() || "default"}`;
  }

  function finite(value, fallback = NaN) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function roundMoney(value) {
    return Math.round(Math.max(0, finite(value, 0)) * 100000000) / 100000000;
  }

  function initialProviderBalance() {
    const account = providerAccount();
    return Math.max(0, finite(account?.balance, 0));
  }

  function newLedger(providerBalance = initialProviderBalance()) {
    const provider = roundMoney(providerBalance);
    const rot = roundMoney(provider * ROT_SHARE);
    const dot = roundMoney(provider - rot);
    return { version: 4, provider, dot, rot, updated_at: Date.now() };
  }

  function validLedger(value) {
    return Boolean(
      value
      && Number(value.version) === 4
      && Number.isFinite(Number(value.provider))
      && Number.isFinite(Number(value.dot))
      && Number.isFinite(Number(value.rot))
    );
  }

  function readLedger() {
    const key = ledgerKey();
    let parsed = null;
    try { parsed = JSON.parse(localStorage.getItem(key) || "null"); } catch (_) {}
    if (!validLedger(parsed)) {
      parsed = newLedger();
      writeLedger(parsed);
    }
    return {
      version: 4,
      provider: roundMoney(parsed.provider),
      dot: roundMoney(parsed.dot),
      rot: roundMoney(parsed.rot),
      updated_at: Number(parsed.updated_at || Date.now()),
    };
  }

  function writeLedger(ledger) {
    const normalized = {
      version: 4,
      provider: roundMoney(ledger.provider),
      dot: roundMoney(ledger.dot),
      rot: roundMoney(ledger.rot),
      updated_at: Date.now(),
    };
    try { localStorage.setItem(ledgerKey(), JSON.stringify(normalized)); } catch (_) {}
    return normalized;
  }

  function visibleBalance(ledger = readLedger(), selectedView = view()) {
    return roundMoney(selectedView === "rot" ? ledger.rot : ledger.dot);
  }

  function applyProviderAbsolute(absolute) {
    const provider = finite(absolute, NaN);
    const ledger = readLedger();
    if (!Number.isFinite(provider)) return ledger;
    const delta = provider - ledger.provider;
    if (Math.abs(delta) > EPSILON) {
      if (view() === "rot") ledger.rot = roundMoney(ledger.rot + delta);
      else ledger.dot = roundMoney(ledger.dot + delta);
      ledger.provider = roundMoney(provider);
      return writeLedger(ledger);
    }
    return ledger;
  }

  function applyProviderDelta(deltaValue) {
    const delta = finite(deltaValue, NaN);
    const ledger = readLedger();
    if (!Number.isFinite(delta) || Math.abs(delta) <= EPSILON) return ledger;
    if (view() === "rot") ledger.rot = roundMoney(ledger.rot + delta);
    else ledger.dot = roundMoney(ledger.dot + delta);
    ledger.provider = roundMoney(ledger.provider + delta);
    return writeLedger(ledger);
  }

  function splitReset(providerBalance) {
    const ledger = newLedger(providerBalance);
    writeLedger(ledger);
    return ledger;
  }

  function projectBalanceEvent(event) {
    if (!active()) return;
    const detail = event?.detail;
    if (!detail || typeof detail !== "object" || detail.__marketing_ui_projection_applied) return;

    let ledger;
    const absolute = finite(detail.balance, NaN);
    const delta = finite(detail.delta, NaN);
    if (Number.isFinite(absolute)) ledger = applyProviderAbsolute(absolute);
    else if (Number.isFinite(delta)) ledger = applyProviderDelta(delta);
    else ledger = readLedger();

    // The backend and Deriv still receive/use the real provider balance. Only the
    // already-created browser UI event is projected before the normal UI listener.
    detail.balance = visibleBalance(ledger);
    if (Object.prototype.hasOwnProperty.call(detail, "delta")) delete detail.delta;
    if (view() === "rot" && Object.prototype.hasOwnProperty.call(detail, "loginid")) delete detail.loginid;
    detail.__marketing_ui_projection_applied = true;
    renderSoon();
  }

  window.addEventListener("derivadmin:direct-balance", projectBalanceEvent, true);
  window.addEventListener("derivadmin:direct-balance-live", projectBalanceEvent, true);

  window.addEventListener("derivadmin:demo-balance-reset", (event) => {
    if (!active()) return;
    const detail = event?.detail || {};
    const provider = finite(detail.balance, 10000);
    const ledger = splitReset(provider);
    detail.balance = visibleBalance(ledger);
    detail.__marketing_ui_projection_applied = true;
    renderSoon();
  }, true);

  function money(value) {
    const currency = String(providerAccount()?.currency || "USD").toUpperCase();
    const numeric = roundMoney(value);
    return `${currency === "USD" ? "$" : ""}${numeric.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
  }

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function providerRows() {
    const id = managedId();
    if (!id) return [];
    return Array.from(document.querySelectorAll(`[data-account-id="${CSS.escape(String(id))}"]`))
      .filter((row) => !row.classList.contains("marketing-synthetic-rot"));
  }

  function removeRealRotRows() {
    document.querySelectorAll("[data-account-id]").forEach((row) => {
      if (row.classList.contains("marketing-synthetic-rot")) return;
      const text = String(row.textContent || "").toUpperCase();
      if (text.includes(ROT_ID) || (text.includes("ROT") && text.includes("206"))) row.remove();
    });
  }

  function decorateRow(row, selectedView, ledger) {
    if (!row) return;
    const isRot = selectedView === "rot";
    row.dataset.marketingView = selectedView;
    row.classList.toggle("marketing-dot-view", !isRot);
    row.classList.toggle("marketing-rot-view", isRot);
    row.classList.toggle("selected", view() === selectedView);

    const label = row.querySelector("span b,b");
    const small = row.querySelector("span small,small");
    const moneyNode = row.querySelector("strong,.account-money b,.direct-demo-balance");
    setText(label, isRot ? "Real account" : "Demo account");
    setText(small, isRot ? ROT_ID : DOT_ID);
    setText(moneyNode, money(isRot ? ledger.rot : ledger.dot));

    const symbol = row.querySelector(".direct-account-symbol");
    if (isRot && symbol) {
      const flag = document.createElement("span");
      flag.className = "deriv-real-flag";
      flag.setAttribute("aria-hidden", "true");
      symbol.replaceWith(flag);
    }
    if (!isRot) {
      const flag = row.querySelector(".deriv-real-flag");
      if (flag) {
        const demo = document.createElement("span");
        demo.className = "direct-account-symbol";
        demo.textContent = "D";
        flag.replaceWith(demo);
      }
    }
    if (isRot) row.querySelectorAll("[data-demo-reset],.direct-demo-reset").forEach((node) => node.remove());
  }

  function ensureRotRows(ledger) {
    for (const dotRow of providerRows()) {
      if (dotRow.closest(".global-run-panel")) continue;
      const parent = dotRow.parentElement;
      if (!parent) continue;
      let rotRow = parent.querySelector(`.marketing-synthetic-rot[data-marketing-owner="${managedId()}"]`);
      if (!rotRow) {
        rotRow = dotRow.cloneNode(true);
        rotRow.classList.add("marketing-synthetic-rot");
        rotRow.dataset.marketingOwner = String(managedId());
        rotRow.dataset.accountId = String(managedId());
        dotRow.insertAdjacentElement("afterend", rotRow);
      }
      decorateRow(dotRow, "dot", ledger);
      decorateRow(rotRow, "rot", ledger);
    }
  }

  function renderTopAccount(ledger) {
    const selectedView = view();
    const visible = visibleBalance(ledger, selectedView);
    document.querySelectorAll(".top-account-switch .account-switch-summary strong,.balance-pill b").forEach((node) => setText(node, money(visible)));

    document.querySelectorAll(".top-account-switch .account-switch-summary small,.balance-pill small").forEach((node) => {
      const text = String(node.textContent || "").toUpperCase();
      if (text.includes("DOT") || text.includes("ROT") || /\*{2,}/.test(text)) setText(node, selectedView === "rot" ? ROT_ID : DOT_ID);
    });
  }

  function renderBadge() {
    const panel = document.querySelector(".global-run-panel");
    if (!panel) return;
    let badge = panel.querySelector(".marketing-tutorial-runtime-badge");
    if (!badge) {
      badge = document.createElement("div");
      badge.className = "marketing-tutorial-runtime-badge";
      badge.style.cssText = "margin:7px 12px 0;padding:6px 9px;border-radius:9px;border:1px solid rgba(84,200,255,.2);background:rgba(8,24,40,.72);display:flex;align-items:center;gap:7px;font-size:8px;line-height:1.2;letter-spacing:.02em";
      (panel.querySelector(".run-panel-sheet") || panel).prepend(badge);
    }
    const signature = "ui-only-v4";
    if (badge.dataset.signature !== signature) {
      badge.dataset.signature = signature;
      badge.innerHTML = '<span style="font-weight:900;text-transform:uppercase">Tutorial</span><b>One Deriv demo account</b><small style="opacity:.7">UI split · DOT 75% · ROT 25%</small>';
    }
  }

  function renderMarketingUi() {
    if (!active()) {
      document.querySelectorAll(".marketing-synthetic-rot,.marketing-tutorial-runtime-badge").forEach((node) => node.remove());
      return;
    }
    const ledger = readLedger();
    removeRealRotRows();
    ensureRotRows(ledger);
    renderTopAccount(ledger);
    renderBadge();
  }

  let renderTimer = 0;
  function renderSoon() {
    clearTimeout(renderTimer);
    renderTimer = window.setTimeout(renderMarketingUi, 0);
  }

  // UI-only switching. Both visible rows keep the same managed account ID and no
  // /me/switch-account request is sent for DOT <-> ROT presentation changes.
  window.addEventListener("click", (event) => {
    const row = event.target?.closest?.("[data-marketing-view]");
    if (!row || !active()) return;
    if (event.target?.closest?.("[data-demo-reset]") && row.dataset.marketingView === "dot") return;
    event.preventDefault();
    event.stopImmediatePropagation();
    setView(row.dataset.marketingView === "rot" ? "rot" : "dot");
  }, true);

  window.addEventListener("pageshow", renderSoon);
  window.addEventListener("derivadmin:direct-run-state", renderSoon);
  window.addEventListener("derivadmin:direct-trade", renderSoon);
  window.addEventListener("derivadmin:direct-clear", renderSoon);
  document.addEventListener("foa:vps-live", renderSoon);

  const observer = new MutationObserver(renderSoon);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  renderSoon();

  window.DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1 = Object.freeze({
    version: "20260821-marketing-dot-rot-v4-ui-only",
    marketing_ui_active: active,
    selected_view: view,
    provider_account_id: DOT_ID,
    display_rot_id: ROT_ID,
    partition_share: () => view() === "rot" ? ROT_SHARE : DOT_SHARE,
    available_balance: () => visibleBalance(readLedger()),
    reset_projection: splitReset,
  });
})();