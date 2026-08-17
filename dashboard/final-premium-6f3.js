(() => {
  "use strict";

  if (window.__DERIVADMIN_FINAL_PREMIUM_6F3__) return;
  window.__DERIVADMIN_FINAL_PREMIUM_6F3__ = true;

  const VERSION = "20260818-testing-free-3";
  const TESTING_FREE_ACCESS = true;
  const IDEMPOTENCY_KEY = "derivadmin-premium-mpesa-idempotency-6f3";
  const POLL_MS = 2500;
  const PASSIVE_MS = 15000;
  const PENDING = new Set(["initiating", "pending", "provider_uncertain"]);
  const FAILED = new Set(["failed", "verification_failed"]);
  const root = document.getElementById("derivadmin-root");
  if (!root) return;

  const state = {
    me: null,
    accounts: [],
    premium: null,
    methods: null,
    payment: null,
    renewal: null,
    history: [],
    busy: false,
    message: "",
    tone: "",
    poll: null,
    passive: null,
    expiryTimer: null,
    shellLoaded: false,
    realtimeLoaded: false,
    locked: false,
  };

  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");

  function detailMessage(payload, response) {
    const detail = payload?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string") return detail.message;
      if (typeof detail.detail === "string") return detail.detail;
    }
    return payload?.message || `Request returned ${response.status}`;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(detailMessage(payload, response));
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function formatDate(value) {
    const time = Date.parse(String(value || ""));
    if (!Number.isFinite(time)) return "Not active";
    try {
      return new Intl.DateTimeFormat(undefined, {
        day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
      }).format(new Date(time));
    } catch (_) { return new Date(time).toLocaleString(); }
  }

  function remainingSeconds() {
    if (!state.premium?.active) return 0;
    const end = Date.parse(String(state.premium?.expires_at || ""));
    return Number.isFinite(end) ? Math.max(0, Math.floor((end - Date.now()) / 1000)) : 0;
  }

  function countdown(seconds = remainingSeconds()) {
    const safe = Math.max(0, Number(seconds || 0));
    const d = Math.floor(safe / 86400);
    const h = Math.floor((safe % 86400) / 3600);
    const m = Math.floor((safe % 3600) / 60);
    const s = Math.floor(safe % 60);
    return `<div class="premium-countdown" data-premium-countdown><span><b>${d}</b><small>Days</small></span><span><b>${String(h).padStart(2,"0")}</b><small>Hours</small></span><span><b>${String(m).padStart(2,"0")}</b><small>Min</small></span><span><b>${String(s).padStart(2,"0")}</b><small>Sec</small></span></div>`;
  }

  function updateCountdowns() {
    const safe = remainingSeconds();
    document.querySelectorAll("[data-premium-countdown]").forEach((node) => {
      const d = Math.floor(safe / 86400), h = Math.floor((safe % 86400) / 3600), m = Math.floor((safe % 3600) / 60), s = safe % 60;
      const values = [String(d), String(h).padStart(2,"0"), String(m).padStart(2,"0"), String(s).padStart(2,"0")];
      node.querySelectorAll("b").forEach((item, i) => { if (item.textContent !== values[i]) item.textContent = values[i]; });
    });
    if (state.premium?.active && safe <= 0) exactExpiryReached();
  }

  function sessionGet(key) { try { return sessionStorage.getItem(key); } catch (_) { return null; } }
  function sessionSet(key, value) { try { sessionStorage.setItem(key, value); } catch (_) {} }
  function sessionRemove(key) { try { sessionStorage.removeItem(key); } catch (_) {} }
  function idempotencyKey() {
    const existing = sessionGet(IDEMPOTENCY_KEY);
    if (existing) return existing;
    const value = crypto?.randomUUID?.() || `mpesa-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    sessionSet(IDEMPOTENCY_KEY, value);
    return value;
  }

  function loadScript(src, key) {
    if (document.querySelector(`script[data-final-module="${key}"]`)) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.defer = true;
      script.async = false;
      script.dataset.finalModule = key;
      script.onload = resolve;
      script.onerror = () => reject(new Error(`Could not load ${key}`));
      document.body.appendChild(script);
    });
  }

  async function loadFinalApp({ realtime = true } = {}) {
    state.locked = false;
    document.documentElement.dataset.premiumState = state.premium?.active ? "active" : TESTING_FREE_ACCESS ? "testing-free" : "active";
    document.documentElement.dataset.premiumBoot = "ready";
    if (realtime && !state.realtimeLoaded) {
      await loadScript("/vps-realtime-client-v2.js?v=20260817-local-ui-2", "vps-realtime-v2");
      state.realtimeLoaded = true;
    }
    if (!state.shellLoaded) {
      await loadScript("/final-ui-shell-v2.js?v=20260818-local-ui-11", "final-ui-shell-v2");
      state.shellLoaded = true;
    } else if (window.FOA_FINAL_UI?.refresh) {
      await window.FOA_FINAL_UI.refresh({ quiet: true });
    }
    injectActivePremiumUi();
  }

  function linkedAccountsMarkup() {
    if (!state.accounts.length) return `<div class="premium-account-row"><span class="premium-account-dot"></span><span><b>Deriv Options</b><small>Linked account group resolved after login</small></span></div>`;
    return state.accounts.slice(0, 8).map((account) => `<div class="premium-account-row"><span class="premium-account-dot ${esc(account.account_type || "demo")}"></span><span><b>${esc(account.account_id_masked || account.label || "Deriv Options")}</b><small>${esc(String(account.account_type || "demo").toUpperCase())} · ${esc(account.currency || "USD")}</small></span>${account.selected ? `<em>Selected</em>` : ""}</div>`).join("");
  }

  function gateHeader() {
    return `<header class="premium-topbar"><div class="premium-brand"><span class="premium-brand-mark">D</span><span><b>DerivAdmin</b><small>Home of Automation</small></span></div><span class="premium-secure">Premium access</span></header>`;
  }

  function planCard() {
    const expired = String(state.premium?.status || "").toLowerCase() === "expired";
    return `<section class="premium-plan-card"><span class="premium-eyebrow">${expired ? "RENEW WEEKLY ACCESS" : "PREMIUM REQUIRED"}</span><div class="premium-plan-title"><div><h1>${expired ? "Your Premium week has ended." : "Unlock DerivAdmin automation."}</h1><p>${expired ? "Renew with a verified M-Pesa payment to start a fresh exact 7-day period." : "Login connects your Deriv accounts. Premium activates the automation service."}</p></div><span class="premium-shield">✓</span></div><div class="premium-price"><strong>KES 250</strong><span><b>7 days</b><small>from verified payment time</small></span></div><div class="premium-rule"><span>Login</span><i>→</i><span>DOT / ROT</span><i>→</i><span>M-Pesa</span><i>→</i><span>Premium</span></div></section>`;
  }

  function paymentPending() {
    const payment = state.payment || {};
    const uncertain = String(payment.status || "").toLowerCase() === "provider_uncertain";
    return `<section class="premium-payment-card"><div class="premium-phone-pulse"><span>📱</span></div><h2>${uncertain ? "Checking your existing payment" : "Approve the M-Pesa prompt"}</h2><p>${uncertain ? "Lipana's response is uncertain. Do not start another payment while we verify this request." : "Enter your M-Pesa PIN on your phone. This screen does not unlock until the server verifies payment."}</p><div class="premium-payment-facts"><span><small>Amount</small><b>KES 250</b></span><span><small>Phone</small><b>${esc(payment.phone || "M-Pesa")}</b></span><span><small>Status</small><b>${esc(String(payment.status || "pending").replaceAll("_", " "))}</b></span></div>${state.message ? `<div class="premium-message ${esc(state.tone || "error")}">${esc(state.message)}</div>` : ""}<button class="premium-secondary" data-premium-refresh>Refresh payment status</button></section>`;
  }

  function paymentForm() {
    const available = Boolean(state.methods?.mpesa?.available);
    const expired = String(state.premium?.status || "").toLowerCase() === "expired";
    return `<section class="premium-payment-card"><div class="premium-section-head"><div><span class="premium-eyebrow">M-PESA · LIPANA</span><h2>${expired ? "Renew Premium" : "Activate Premium"}</h2></div><span class="premium-method">KES</span></div><p>Enter the Kenyan M-Pesa number that should receive the STK prompt.</p><form data-premium-form><label class="premium-phone"><span>🇰🇪 +254</span><input data-premium-phone inputmode="tel" autocomplete="tel" maxlength="18" placeholder="0712 345 678" aria-label="M-Pesa phone number"></label>${state.message ? `<div class="premium-message ${esc(state.tone || "error")}">${esc(state.message)}</div>` : ""}<button class="premium-primary" type="submit" ${available && !state.busy ? "" : "disabled"}>${state.busy ? "Sending M-Pesa prompt…" : expired ? "Renew for KES 250" : "Pay KES 250 with M-Pesa"}</button></form>${available ? "" : `<div class="premium-config-note"><b>M-Pesa checkout is not configured yet.</b><span>The platform is fully wired. Add the Lipana production keys before live payment testing.</span></div>`}<p class="premium-trust">Premium activates only after a signed Lipana callback and server-side transaction verification.</p></section>`;
  }

  function paymentFailed() {
    return `<section class="premium-payment-card premium-result"><span class="premium-result-icon fail">×</span><h2>Payment was not completed.</h2><p>No Premium time was added. You can start a fresh M-Pesa request.</p>${state.message ? `<div class="premium-message error">${esc(state.message)}</div>` : ""}<button class="premium-primary" data-premium-retry>Try M-Pesa again</button></section>`;
  }

  function gate() {
    state.locked = true;
    document.documentElement.dataset.premiumState = "locked";
    document.documentElement.dataset.premiumBoot = "ready";
    const status = String(state.payment?.status || "").toLowerCase();
    const payment = PENDING.has(status) && !paymentAttemptExpired() ? paymentPending() : FAILED.has(status) ? paymentFailed() : paymentForm();
    root.innerHTML = `<div class="premium-page" data-final-premium="${VERSION}">${gateHeader()}<main class="premium-main">${planCard()}<div class="premium-columns">${payment}<section class="premium-linked-card"><div class="premium-section-head"><div><span class="premium-eyebrow">LINKED OPTIONS</span><h2>DOT & ROT access</h2></div><span class="premium-method">${Number(state.premium?.linked_account_count || state.accounts.length || 0)}</span></div><p>One verified weekly entitlement covers the linked Options account group resolved from this Deriv login.</p><div class="premium-account-list">${linkedAccountsMarkup()}</div><a class="premium-text-action" href="/oauth/start">Use another Deriv login</a></section></div></main></div>`;
  }

  function paymentAttemptExpired() {
    const end = Date.parse(String(state.payment?.expires_at || ""));
    return Boolean(Number.isFinite(end) && Date.now() >= end);
  }

  function verified(payload = {}) {
    const premium = payload.premium || state.premium;
    const payment = payload.payment || state.payment;
    return Boolean(premium?.active || payment?.activated || String(payment?.status || "").toLowerCase() === "success");
  }

  function stopPoll() { if (state.poll) clearTimeout(state.poll); state.poll = null; }
  function schedulePoll(delay = POLL_MS) { stopPoll(); if (state.payment?.id && PENDING.has(String(state.payment.status || "").toLowerCase()) && !paymentAttemptExpired()) state.poll = setTimeout(() => pollPayment().catch(() => {}), delay); }

  async function pollPayment() {
    if (!state.payment?.id) return;
    try {
      const payload = await api(`/me/premium-access/mpesa/payments/${encodeURIComponent(state.payment.id)}`);
      state.payment = payload.payment || state.payment;
      state.premium = payload.premium || state.premium;
      if (verified(payload)) {
        sessionRemove(IDEMPOTENCY_KEY);
        stopPoll();
        root.innerHTML = `<div class="premium-page"><main class="premium-success"><span class="premium-result-icon success">✓</span><h1>Payment verified.</h1><p>Premium is active for exactly 7 days from the verified payment time.</p>${countdown()}<button class="premium-primary" data-premium-enter>Enter DerivAdmin</button></main></div>`;
        scheduleExactExpiry();
        return;
      }
      gate();
      if (PENDING.has(String(state.payment?.status || "").toLowerCase()) && !paymentAttemptExpired()) schedulePoll();
    } catch (error) {
      state.message = error.message || "Could not verify payment status.";
      state.tone = "error";
      gate();
      schedulePoll(4000);
    }
  }

  async function submitPayment(form) {
    if (state.busy) return;
    const phone = String(form.querySelector("[data-premium-phone]")?.value || "").trim();
    if (!phone) { state.message = "Enter the M-Pesa number that should receive the STK prompt."; state.tone = "error"; gate(); return; }
    state.busy = true; state.message = ""; gate();
    try {
      const payload = await api("/me/premium-access/mpesa/stk-push", { method: "POST", body: JSON.stringify({ phone, idempotency_key: idempotencyKey() }) });
      state.payment = payload.payment || null;
      state.premium = payload.premium || state.premium;
      if (verified(payload)) { await pollPayment(); return; }
      schedulePoll(700);
    } catch (error) {
      const attached = error?.payload?.detail?.payment;
      if (attached) state.payment = attached;
      state.message = error.message || "M-Pesa request could not be started.";
      state.tone = "error";
      if (state.payment && PENDING.has(String(state.payment.status || "").toLowerCase())) schedulePoll(2500);
      else if (!state.payment) sessionRemove(IDEMPOTENCY_KEY);
    } finally { state.busy = false; gate(); }
  }

  async function loadPremiumData() {
    const access = await api("/me/premium-access");
    state.premium = access;
    if (access.local_dev_preview || access.active) {
      try {
        const renewal = await api("/me/premium-access/renewal-status");
        state.renewal = renewal.renewal || null;
        state.premium = renewal.premium || state.premium;
      } catch (_) {}
      return;
    }
    const [options, accounts, latest] = await Promise.allSettled([
      api("/me/premium-access/payment-options"), api("/me/accounts"), api("/me/premium-access/mpesa/payments/latest"),
    ]);
    if (options.status === "fulfilled") { state.methods = options.value.methods || {}; state.premium = options.value.premium || state.premium; }
    if (accounts.status === "fulfilled") state.accounts = accounts.value.accounts || [];
    if (latest.status === "fulfilled") { state.payment = latest.value.payment || null; state.premium = latest.value.premium || state.premium; }
  }

  async function loadHistory() {
    try {
      const payload = await api("/me/premium-access/renewal-history?limit=8");
      state.history = Array.isArray(payload.items) ? payload.items : [];
    } catch (_) { state.history = []; }
  }

  function reminderText() {
    const stage = String(state.renewal?.reminder_stage || state.renewal?.stage || "").toLowerCase();
    if (stage.includes("one_hour")) return "Premium expires in less than 1 hour.";
    if (stage.includes("six_hours")) return "Premium expires in less than 6 hours.";
    if (stage.includes("twenty_four") || stage.includes("24")) return "Premium expires within 24 hours.";
    return "";
  }

  function premiumProfileMarkup() {
    const p = state.premium || {};
    const linked = Number(p.linked_account_count || state.accounts.length || 0);
    return `<div class="premium-profile"><div class="premium-profile-head"><div><span class="premium-eyebrow">PREMIUM ACCESS</span><h3>Weekly Premium</h3></div><span class="premium-active-pill">● Active</span></div>${countdown()}<div class="premium-profile-grid"><span><small>Exact expiry</small><b>${esc(formatDate(p.expires_at))}</b></span><span><small>Linked accounts</small><b>${linked} Options</b></span><span><small>Provider</small><b>Lipana · M-Pesa</b></span><span><small>Renewal</small><b>After exact expiry</b></span></div><p>KES 250 · exactly 7 days from verified payment time.</p>${state.history.length ? `<div class="premium-history"><b>Verified periods</b>${state.history.slice(0,4).map((row) => `<span><small>${esc(formatDate(row.period_start))}</small><em>→</em><small>${esc(formatDate(row.period_end))}</small></span>`).join("")}</div>` : ""}</div>`;
  }

  function injectActivePremiumUi() {
    if (!state.premium?.active || state.locked) return;
    const profileGrid = document.querySelector(".profile-grid");
    if (profileGrid) {
      const cards = profileGrid.querySelectorAll(":scope > article.panel");
      const premiumCard = cards[2];
      if (premiumCard && !premiumCard.querySelector(".premium-profile")) premiumCard.innerHTML = premiumProfileMarkup();
    }
    const reminder = reminderText();
    const main = document.querySelector(".app-main");
    if (reminder && main && !main.querySelector(".premium-reminder")) main.insertAdjacentHTML("afterbegin", `<div class="premium-reminder"><span>⏱</span><div><b>Premium renewal reminder</b><small>${esc(reminder)}</small></div><button data-premium-open-profile>View plan</button></div>`);
  }

  function scheduleExactExpiry() {
    if (state.expiryTimer) clearTimeout(state.expiryTimer);
    const end = Date.parse(String(state.premium?.expires_at || ""));
    if (!state.premium?.active || !Number.isFinite(end)) return;
    const delay = Math.max(0, end - Date.now());
    state.expiryTimer = setTimeout(exactExpiryReached, Math.min(delay + 50, 2147483000));
  }

  async function exactExpiryReached() {
    if (!state.premium?.active) return;
    try {
      const access = await api("/me/premium-access");
      state.premium = access;
      if (access.active) { scheduleExactExpiry(); return; }
    } catch (_) {}
    document.documentElement.dataset.premiumState = "locked";
    location.reload();
  }

  async function refreshActivePremium() {
    if (!state.me?.authenticated || state.locked) return;
    try {
      const renewal = await api("/me/premium-access/renewal-status");
      state.premium = renewal.premium || state.premium;
      state.renewal = renewal.renewal || null;
      if (!state.premium?.active) return;
      scheduleExactExpiry(); injectActivePremiumUi();
    } catch (_) {}
  }

  async function boot() {
    document.documentElement.dataset.premiumBoot = "pending";
    try {
      state.me = await api("/me");
      if (!state.me?.authenticated) {
        document.documentElement.dataset.premiumState = "anonymous";
        await loadFinalApp({ realtime: false });
        return;
      }
      try { await loadPremiumData(); } catch (_) { state.premium = { active: false, testing_free_access: true }; }
      if (state.premium?.active) {
        await loadHistory();
        scheduleExactExpiry();
      }
      if (TESTING_FREE_ACCESS) {
        document.documentElement.dataset.premiumState = state.premium?.active ? "active" : "testing-free";
        await loadFinalApp({ realtime: true });
        return;
      }
      if (state.premium?.local_dev_preview || state.premium?.active) {
        await loadHistory();
        scheduleExactExpiry();
        await loadFinalApp({ realtime: true });
        return;
      }
      if (state.payment && PENDING.has(String(state.payment.status || "").toLowerCase()) && !paymentAttemptExpired()) schedulePoll(600);
      gate();
    } catch (error) {
      document.documentElement.dataset.premiumState = "error";
      document.documentElement.dataset.premiumBoot = "ready";
      root.innerHTML = `<div class="premium-page"><main class="premium-success premium-error"><span class="premium-result-icon fail">!</span><h1>Premium status could not be verified.</h1><p>DerivAdmin fails closed when entitlement cannot be verified. Your trading configuration has not been changed.</p><div class="premium-message error">${esc(error.message || "Premium status unavailable")}</div><button class="premium-secondary" data-premium-reload>Retry</button></main></div>`;
    }
  }

  document.addEventListener("submit", (event) => {
    const form = event.target?.closest?.("[data-premium-form]");
    if (!form) return;
    event.preventDefault(); submitPayment(form);
  }, true);

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-premium-refresh]")) { event.preventDefault(); pollPayment(); return; }
    if (event.target?.closest?.("[data-premium-retry]")) { event.preventDefault(); stopPoll(); state.payment = null; state.message = ""; sessionRemove(IDEMPOTENCY_KEY); gate(); return; }
    if (event.target?.closest?.("[data-premium-enter]")) { event.preventDefault(); location.reload(); return; }
    if (event.target?.closest?.("[data-premium-reload]")) { event.preventDefault(); location.reload(); return; }
    if (event.target?.closest?.("[data-premium-open-profile]")) { event.preventDefault(); location.hash = "#profile"; setTimeout(injectActivePremiumUi, 50); }
  }, true);

  let observerQueued = false;
  new MutationObserver(() => {
    if (observerQueued) return;
    observerQueued = true;
    requestAnimationFrame(() => {
      observerQueued = false;
      if (state.locked && !root.querySelector(".premium-page")) gate();
      else if (state.premium?.active) injectActivePremiumUi();
    });
  }).observe(root, { childList: true, subtree: true });

  window.addEventListener("focus", () => state.premium?.active ? refreshActivePremium() : state.payment?.id ? pollPayment() : undefined);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.premium?.active) refreshActivePremium(); });
  setInterval(updateCountdowns, 1000);
  state.passive = setInterval(() => { if (state.premium?.active) refreshActivePremium(); }, PASSIVE_MS);

  window.DERIVADMIN_FINAL_PREMIUM_6F3 = Object.freeze({ version: VERSION, refresh: refreshActivePremium });
  boot();
})();
