(() => {
  "use strict";

  if (window.__FOA_PREMIUM_SUBSCRIPTION_ACTION6E__) return;
  window.__FOA_PREMIUM_SUBSCRIPTION_ACTION6E__ = true;

  const VERSION = "20260817-action6e-1";
  const IDEMPOTENCY_KEY = "foa-premium-mpesa-idempotency-v1";
  const POLL_MS = 2500;
  const PASSIVE_REFRESH_MS = 30000;
  const TERMINAL_FAILURES = new Set(["failed", "verification_failed"]);
  const PENDING_STATES = new Set(["initiating", "pending", "provider_uncertain"]);

  const state = {
    premium: null,
    methods: null,
    payment: null,
    history: [],
    accountKey: "",
    loading: false,
    submitting: false,
    pollTimer: null,
    passiveTimer: null,
    tickTimer: null,
    successUntil: 0,
    message: "",
    messageTone: "",
    lastProfileSignature: "",
  };

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const esc = (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

  function svg(name) {
    const c = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    const icons = {
      logo: `<svg ${c}><path d="M5 4h6.5a7.5 7.5 0 0 1 0 15H5l5-5h1.5a2.5 2.5 0 0 0 0-5H10z"/><path d="M5 4v15"/></svg>`,
      crown: `<svg ${c}><path d="m4 17-1-9 5 4 4-7 4 7 5-4-1 9z"/><path d="M5 20h14"/></svg>`,
      phone: `<svg ${c}><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 5h4M11 18h2"/></svg>`,
      shield: `<svg ${c}><path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6z"/><path d="m9.5 12 1.7 1.7 3.6-4"/></svg>`,
      clock: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
      check: `<svg ${c}><path d="m5 12 4 4L19 6"/></svg>`,
      fail: `<svg ${c}><path d="m7 7 10 10M17 7 7 17"/></svg>`,
      link: `<svg ${c}><path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.1-1.1"/></svg>`,
      spark: `<svg ${c}><path d="m12 3 1.4 4.2L18 9l-4.6 1.8L12 15l-1.4-4.2L6 9l4.6-1.8zM19 3v4M17 5h4"/></svg>`,
    };
    return icons[name] || icons.crown;
  }

  function currentMe() {
    const live = window.FOA_NETLIFY_LIVE_CACHE?.me;
    if (live && typeof live === "object") return live;
    const boot = window.FOA_BOOT_SESSION;
    return boot && typeof boot === "object" ? boot : null;
  }

  function isAuthenticated() {
    return Boolean(
      currentMe()?.authenticated
      || q(".builder-header #logout")
      || q("#foa-simple-app .account-pill"),
    );
  }

  function accountKey() {
    const me = currentMe();
    return String(
      me?.managed_account_id
      || me?.id
      || me?.account_id_full
      || me?.account_id_masked
      || me?.account_id
      || q(".builder-header .account-pill")?.textContent
      || "authenticated",
    ).trim();
  }

  function accountLabel() {
    const me = currentMe();
    return String(
      me?.account_id_masked
      || me?.account_id
      || q(".account-pill")?.textContent
      || "Deriv Options",
    ).trim();
  }

  function errorMessage(payload, response) {
    const detail = payload?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string" && detail.message.trim()) return detail.message;
      if (typeof detail.detail === "string" && detail.detail.trim()) return detail.detail;
    }
    if (typeof payload?.message === "string" && payload.message.trim()) return payload.message;
    return `Request returned ${response.status}`;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(errorMessage(payload, response));
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function sessionGet(key) {
    try { return sessionStorage.getItem(key); } catch (_) { return null; }
  }

  function sessionSet(key, value) {
    try { sessionStorage.setItem(key, value); } catch (_) {}
  }

  function sessionRemove(key) {
    try { sessionStorage.removeItem(key); } catch (_) {}
  }

  function newIdempotencyKey() {
    const existing = sessionGet(IDEMPOTENCY_KEY);
    if (existing) return existing;
    const value = globalThis.crypto?.randomUUID?.()
      || `mpesa-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    sessionSet(IDEMPOTENCY_KEY, value);
    return value;
  }

  function clearPaymentIntent() {
    sessionRemove(IDEMPOTENCY_KEY);
  }

  function asTime(value) {
    const ms = Date.parse(String(value || ""));
    return Number.isFinite(ms) ? ms : 0;
  }

  function exactRemainingSeconds() {
    const end = asTime(state.premium?.expires_at);
    if (!state.premium?.active || !end) return 0;
    return Math.max(0, Math.floor((end - Date.now()) / 1000));
  }

  function countdownParts(seconds) {
    const safe = Math.max(0, Number(seconds || 0));
    const days = Math.floor(safe / 86400);
    const hours = Math.floor((safe % 86400) / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const secs = Math.floor(safe % 60);
    return { days, hours, minutes, seconds: secs };
  }

  function countdownMarkup(seconds, attr = "") {
    const part = countdownParts(seconds);
    return `<div class="foa-premium-countdown" ${attr}>
      <span><b>${part.days}</b>Days</span>
      <span><b>${String(part.hours).padStart(2, "0")}</b>Hours</span>
      <span><b>${String(part.minutes).padStart(2, "0")}</b>Min</span>
      <span><b>${String(part.seconds).padStart(2, "0")}</b>Sec</span>
    </div>`;
  }

  function formatDate(value) {
    const ms = asTime(value);
    if (!ms) return "Not active";
    try {
      return new Intl.DateTimeFormat(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(new Date(ms));
    } catch (_) {
      return new Date(ms).toLocaleString();
    }
  }

  function maskedReference(value) {
    const text = String(value || "").trim();
    if (!text) return "Pending";
    if (text.length <= 13) return text;
    return `${text.slice(0, 7)}…${text.slice(-5)}`;
  }

  function paymentIsSuccess(payload = null) {
    const premium = payload?.premium || state.premium;
    const payment = payload?.payment || state.payment;
    return Boolean(
      premium?.active
      || payment?.activated
      || String(payment?.status || "").toLowerCase() === "success",
    );
  }

  function paymentIsFailure(payment = state.payment) {
    return TERMINAL_FAILURES.has(String(payment?.status || "").toLowerCase());
  }

  function paymentIsPending(payment = state.payment) {
    return PENDING_STATES.has(String(payment?.status || "").toLowerCase());
  }

  function paymentExpired(payment = state.payment) {
    const expires = asTime(payment?.expires_at);
    return Boolean(expires && Date.now() >= expires);
  }

  function removeOverlay() {
    q(".foa-premium-overlay")?.remove();
    document.body.dataset.premiumGateState = "active";
  }

  function ensureOverlay() {
    let root = q(".foa-premium-overlay");
    if (root) return root;
    root = document.createElement("section");
    root.className = "foa-premium-overlay";
    root.dataset.premiumUi = VERSION;
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", "DerivAdmin Premium Access");
    document.body.appendChild(root);
    return root;
  }

  function planHero(title, eyebrow, description) {
    return `<article class="foa-premium-hero">
      <div class="foa-premium-head">
        <span class="foa-premium-icon">${svg("crown")}</span>
        <div class="foa-premium-head-copy"><small>${esc(eyebrow)}</small><h1>${esc(title)}</h1></div>
      </div>
      <p>${esc(description)}</p>
      <div class="foa-premium-price-row">
        <div class="foa-premium-price"><strong>KES 250</strong><span>M-Pesa · Lipana</span></div>
        <div class="foa-premium-period"><b>7 days</b><br>from verified payment time</div>
      </div>
    </article>`;
  }

  function benefitCard() {
    const linked = Number(state.premium?.linked_account_count || 0);
    return `<article class="foa-premium-card">
      <h2>One Premium period. Your linked Options accounts.</h2>
      <p>The same paid entitlement follows the linked DOT/ROT account group already resolved by DerivAdmin.</p>
      <div class="foa-premium-benefits">
        <div class="foa-premium-benefit"><i>✓</i><span>Strategy Builder, Text to Strategy and Strategy Ready.</span></div>
        <div class="foa-premium-benefit"><i>✓</i><span>Trade Now, automated sessions and persistent scheduling.</span></div>
        <div class="foa-premium-benefit"><i>✓</i><span>Exact 7-day server entitlement with no grace-period ambiguity.</span></div>
        <div class="foa-premium-benefit"><i>✓</i><span>${linked || "Your"} linked Options account${linked === 1 ? "" : "s"} covered by this Premium identity.</span></div>
      </div>
    </article>`;
  }

  function formCard() {
    const available = Boolean(state.methods?.mpesa?.available);
    const expired = String(state.premium?.status || "unpaid").toLowerCase() === "expired";
    const title = expired ? "Renew Premium with M-Pesa" : "Activate with M-Pesa";
    const button = expired ? "Renew for KES 250" : "Pay KES 250 with M-Pesa";
    return `<article class="foa-premium-card">
      <h2>${esc(title)}</h2>
      <p>${expired ? "Your previous period ended at the exact expiry time. A verified new payment starts a fresh 7-day period." : "Enter the Kenyan M-Pesa number that should receive the STK prompt."}</p>
      <form class="foa-premium-form" data-premium-mpesa-form>
        <label class="foa-premium-label">M-Pesa phone number
          <span class="foa-premium-phone-wrap"><span class="foa-premium-phone-prefix">🇰🇪</span><input data-premium-phone inputmode="tel" autocomplete="tel" maxlength="18" placeholder="0712 345 678" aria-label="M-Pesa phone number"></span>
        </label>
        ${state.message ? `<div class="foa-premium-message" data-tone="${esc(state.messageTone || "error")}">${esc(state.message)}</div>` : ""}
        <button type="submit" class="foa-premium-primary" data-premium-pay ${available && !state.submitting ? "" : "disabled"}>${state.submitting ? "Sending M-Pesa prompt…" : esc(button)}</button>
      </form>
      ${available ? "" : `<div class="foa-premium-message" data-tone="info" style="margin-top:12px">M-Pesa checkout is not configured on this environment yet. Premium trading remains locked until Lipana is configured.</div>`}
      <p class="foa-premium-trust">M-Pesa payment via Lipana. Premium activates only after DerivAdmin receives and re-verifies a successful provider transaction.</p>
    </article>`;
  }

  function waitingCard() {
    const payment = state.payment || {};
    const uncertain = String(payment.status || "").toLowerCase() === "provider_uncertain";
    return `<article class="foa-premium-card">
      <div class="foa-premium-waiting">
        <span class="foa-premium-waiting-orbit"><b>${svg("phone")}</b></span>
        <strong>${uncertain ? "Checking M-Pesa status" : "Approve the M-Pesa prompt"}</strong>
        <p>${uncertain ? "The provider response is uncertain. Do not send another payment yet; DerivAdmin is checking this existing request." : "Enter your M-Pesa PIN on the phone. Premium will unlock only after the payment is verified."}</p>
        <div class="foa-premium-payment-meta">
          <div><span>Amount</span><b>KES 250</b></div>
          <div><span>Phone</span><b>${esc(payment.phone || "M-Pesa number")}</b></div>
          <div><span>Reference</span><b>${esc(maskedReference(payment.provider_transaction_id || payment.merchant_reference))}</b></div>
          <div><span>Status</span><b>${esc(String(payment.status || "pending").replaceAll("_", " "))}</b></div>
        </div>
        <button type="button" class="foa-premium-secondary" style="width:100%" data-premium-refresh>Refresh payment status</button>
      </div>
    </article>`;
  }

  function successCard() {
    return `<article class="foa-premium-card">
      <div class="foa-premium-result">
        <span class="foa-premium-success-icon">${svg("check")}</span>
        <strong>Payment verified. Premium is active.</strong>
        <p>Your new entitlement runs for exactly 7 days from the verified payment time.</p>
        <div class="foa-premium-payment-meta">
          <div><span>Plan</span><b>DerivAdmin Premium Weekly</b></div>
          <div><span>Paid</span><b>KES 250 · M-Pesa</b></div>
          <div><span>Exact expiry</span><b>${esc(formatDate(state.premium?.expires_at))}</b></div>
        </div>
        ${countdownMarkup(exactRemainingSeconds(), "data-premium-overlay-countdown")}
        <button type="button" class="foa-premium-primary" style="width:100%" data-premium-continue>Continue to DerivAdmin</button>
      </div>
    </article>`;
  }

  function failedCard() {
    const expiredAttempt = paymentExpired();
    return `<article class="foa-premium-card">
      <div class="foa-premium-result">
        <span class="foa-premium-fail-icon">${svg("fail")}</span>
        <strong>${expiredAttempt ? "M-Pesa request expired" : "Payment was not completed"}</strong>
        <p>No Premium time was added. A fresh 7-day period begins only after a successful KES 250 M-Pesa payment is verified.</p>
        <div class="foa-premium-payment-meta">
          <div><span>Amount</span><b>KES 250</b></div>
          <div><span>Status</span><b>${esc(String(state.payment?.status || "not completed").replaceAll("_", " "))}</b></div>
          <div><span>Access</span><b>Not extended</b></div>
        </div>
        <button type="button" class="foa-premium-primary" style="width:100%" data-premium-try-again>Try M-Pesa again</button>
      </div>
    </article>`;
  }

  function overlayMarkup() {
    const status = String(state.premium?.status || "unpaid").toLowerCase();
    const expired = status === "expired";
    const success = state.premium?.active && state.successUntil > Date.now();
    const pending = !success && paymentIsPending() && !paymentExpired();
    const failed = !success && (paymentIsFailure() || (paymentIsPending() && paymentExpired()));
    const title = expired ? "Premium has expired" : "Premium Access Required";
    const description = expired
      ? "The exact paid period has ended, so new automated trading is paused until the next verified M-Pesa payment."
      : "DerivAdmin Premium is required for trading automation. Pay once with M-Pesa to unlock exactly seven days of access.";

    const action = success
      ? successCard()
      : pending
        ? waitingCard()
        : failed
          ? failedCard()
          : formCard();

    return `<div class="foa-premium-shell">
      <header class="foa-premium-brandbar">
        <div class="foa-premium-brand"><span class="foa-premium-brand-mark">${svg("logo")}</span><span class="foa-premium-brand-copy"><strong>DerivAdmin</strong><span>Home of Automation</span></span></div>
        <span class="foa-premium-account">${esc(accountLabel())}</span>
      </header>
      ${planHero(title, expired ? "Weekly renewal" : "Premium weekly", description)}
      ${action}
      ${success ? "" : benefitCard()}
    </div>`;
  }

  function renderOverlay() {
    if (!isAuthenticated() || !state.premium || state.premium.active && state.successUntil <= Date.now()) {
      removeOverlay();
      return;
    }
    document.body.dataset.premiumGateState = state.premium.active ? "success" : "required";
    const root = ensureOverlay();
    root.innerHTML = overlayMarkup();
    root.hidden = false;
    q("[data-premium-phone]", root)?.focus?.({ preventScroll: true });
  }

  function reminderStage() {
    const seconds = exactRemainingSeconds();
    if (!state.premium?.active) return "expired";
    if (seconds <= 3600) return "one_hour";
    if (seconds <= 6 * 3600) return "six_hours";
    if (seconds <= 24 * 3600) return "twenty_four_hours";
    return "active";
  }

  function reminderMessage() {
    const stage = reminderStage();
    const seconds = exactRemainingSeconds();
    const p = countdownParts(seconds);
    if (stage === "one_hour") return `Premium expires in ${p.minutes}m ${p.seconds}s. Renewal opens after exact expiry.`;
    if (stage === "six_hours") return `Premium expires in ${p.hours}h ${p.minutes}m. Renewal opens after exact expiry.`;
    if (stage === "twenty_four_hours") return `Premium expires in ${p.hours}h ${p.minutes}m. M-Pesa renewal becomes available after expiry.`;
    return "";
  }

  function renderReminder() {
    qa(".foa-premium-reminder").forEach((node) => node.remove());
    if (!isAuthenticated() || !state.premium?.active || reminderStage() === "active") return;
    const main = q("#telegram-dashboard-snapshot > main");
    if (!main || q(".foa-premium-overlay")) return;
    const banner = document.createElement("div");
    banner.className = "foa-premium-reminder";
    banner.innerHTML = `<div><strong>Premium renewal reminder</strong><span>${esc(reminderMessage())}</span></div><button type="button" data-premium-open-profile>View plan</button>`;
    main.prepend(banner);
  }

  function historyRows() {
    if (!state.history.length) return `<div class="foa-premium-history-empty">No verified Premium payments yet.</div>`;
    return state.history.slice(0, 6).map((row) => `<div class="foa-premium-history-row">
      <div><strong>KES ${Number(row.amount || 250).toFixed(0)} · M-Pesa</strong><small>${esc(formatDate(row.period_start))} → ${esc(formatDate(row.period_end))}</small></div>
      <b>${esc(row.status || "expired")}</b>
    </div>`).join("");
  }

  function profileMarkup() {
    const premium = state.premium || {};
    const active = Boolean(premium.active);
    const status = String(premium.status || "unpaid").toLowerCase();
    const linked = Number(premium.linked_account_count || 0);
    const renewal = premium.renewal || {};
    return `<section class="foa-premium-profile-card" data-premium-profile-version="${VERSION}">
      <div class="foa-premium-profile-head">
        <div class="foa-premium-profile-title"><span class="foa-premium-mini-icon">${svg("crown")}</span><div><strong>Premium subscription</strong><span>KES 250 · 7 days · M-Pesa</span></div></div>
        <span class="foa-premium-status" data-status="${esc(status)}">${esc(active ? "Active" : status === "expired" ? "Expired" : "Payment required")}</span>
      </div>
      ${active ? countdownMarkup(exactRemainingSeconds(), "data-premium-profile-countdown") : ""}
      <div class="foa-premium-profile-grid">
        <div class="foa-premium-profile-stat"><span>Exact expiry</span><b>${esc(formatDate(premium.expires_at))}</b></div>
        <div class="foa-premium-profile-stat"><span>Linked accounts</span><b>${linked || 0} Options account${linked === 1 ? "" : "s"}</b></div>
        <div class="foa-premium-profile-stat"><span>Renewal</span><b>Manual M-Pesa after expiry</b></div>
        <div class="foa-premium-profile-stat"><span>Provider</span><b>Lipana · M-Pesa</b></div>
      </div>
      ${active ? `<p style="margin:12px 0 0;color:#7892ac;font-size:10px;line-height:1.5">${esc(renewal.message || "Premium is active. Renewal becomes available after the exact current expiry time.")}</p>` : `<button type="button" class="foa-premium-primary" style="width:100%;margin-top:14px" data-premium-open>Pay KES 250 with M-Pesa</button>`}
      <div class="foa-premium-history"><div class="foa-premium-history-head"><strong>Premium payment history</strong><span>Verified periods only</span></div><div class="foa-premium-history-list">${historyRows()}</div></div>
    </section>`;
  }

  function renderProfile() {
    if (!state.premium || document.body.dataset.automationRoute !== "profile") return;
    const scaffold = q('.foa-automation-scaffold[data-automation-scaffold="profile"]');
    if (!scaffold) return;
    const signature = JSON.stringify({
      active: state.premium.active,
      status: state.premium.status,
      expires: state.premium.expires_at,
      linked: state.premium.linked_account_count,
      history: state.history.map((row) => [row.id, row.status]),
    });
    const existing = q(".foa-premium-profile-card", scaffold.parentElement || document);
    if (existing && signature === state.lastProfileSignature) return;
    existing?.remove();
    scaffold.insertAdjacentHTML("afterend", profileMarkup());
    state.lastProfileSignature = signature;
  }

  function updateCountdowns() {
    const seconds = exactRemainingSeconds();
    qa("[data-premium-overlay-countdown], [data-premium-profile-countdown]").forEach((node) => {
      node.outerHTML = countdownMarkup(seconds, node.hasAttribute("data-premium-overlay-countdown") ? "data-premium-overlay-countdown" : "data-premium-profile-countdown");
    });
    const reminder = q(".foa-premium-reminder span");
    if (reminder) reminder.textContent = reminderMessage();
    if (state.premium?.active && seconds <= 0 && !state.loading) refreshPremium(true);
  }

  function stopPoll() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  function schedulePoll(delay = POLL_MS) {
    stopPoll();
    if (!state.payment || paymentIsSuccess() || paymentIsFailure()) return;
    state.pollTimer = setTimeout(() => pollPayment().catch(() => {}), delay);
  }

  async function pollPayment() {
    if (!isAuthenticated() || !state.payment?.id) return;
    try {
      const payload = await api(`/me/premium-access/mpesa/payments/${encodeURIComponent(state.payment.id)}`);
      state.payment = payload.payment || state.payment;
      state.premium = payload.premium || state.premium;
      state.message = "";
      if (paymentIsSuccess(payload)) {
        clearPaymentIntent();
        state.successUntil = Date.now() + 4500;
        await refreshPremium(true, { preserveSuccess: true, skipLatest: true });
        renderOverlay();
        window.dispatchEvent(new CustomEvent("foa:premium-updated", { detail: { premium: state.premium, payment: state.payment } }));
        return;
      }
      renderOverlay();
      if (paymentIsFailure() || paymentExpired()) {
        clearPaymentIntent();
        stopPoll();
        return;
      }
      schedulePoll();
    } catch (error) {
      state.message = String(error?.message || error);
      state.messageTone = "error";
      renderOverlay();
      schedulePoll(4000);
    }
  }

  async function loadLatestPayment() {
    const payload = await api("/me/premium-access/mpesa/payments/latest");
    state.payment = payload.payment || null;
    state.premium = payload.premium || state.premium;
    if (paymentIsPending() && !paymentExpired()) schedulePoll(500);
    return payload;
  }

  async function loadHistory() {
    if (!state.premium) return;
    try {
      const payload = await api("/me/premium-access/renewal-history?limit=12");
      state.history = Array.isArray(payload.items) ? payload.items : [];
      state.lastProfileSignature = "";
      renderProfile();
    } catch (_) {
      state.history = [];
    }
  }

  async function refreshPremium(force = false, options = {}) {
    if (!isAuthenticated()) {
      state.premium = null;
      state.payment = null;
      state.methods = null;
      state.history = [];
      stopPoll();
      removeOverlay();
      document.body.removeAttribute("data-premium-gate-state");
      return;
    }
    const key = accountKey();
    if (state.accountKey && state.accountKey !== key) {
      state.premium = null;
      state.payment = null;
      state.methods = null;
      state.history = [];
      state.successUntil = 0;
      state.lastProfileSignature = "";
      clearPaymentIntent();
      stopPoll();
    }
    state.accountKey = key;
    if (state.loading && !force) return;
    state.loading = true;
    if (!state.premium) document.body.dataset.premiumGateState = "checking";
    try {
      const access = await api("/me/premium-access");
      state.premium = access;
      if (access.local_dev_preview || access.active) {
        state.methods = null;
        state.payment = null;
        if (!options.preserveSuccess) state.successUntil = 0;
        removeOverlay();
        renderReminder();
        if (document.body.dataset.automationRoute === "profile") loadHistory();
        return;
      }

      const paymentOptions = await api("/me/premium-access/payment-options");
      state.premium = paymentOptions.premium || state.premium;
      state.methods = paymentOptions.methods || {};
      document.body.dataset.premiumGateState = "required";
      if (!options.skipLatest) {
        try { await loadLatestPayment(); } catch (_) { state.payment = null; }
      }
      renderOverlay();
    } catch (error) {
      document.body.dataset.premiumGateState = "error";
      state.message = `Premium status could not be verified: ${String(error?.message || error)}`;
      state.messageTone = "error";
      if (state.premium && !state.premium.active) renderOverlay();
    } finally {
      state.loading = false;
      schedulePassiveRefresh();
    }
  }

  function schedulePassiveRefresh() {
    if (state.passiveTimer) clearTimeout(state.passiveTimer);
    state.passiveTimer = setTimeout(() => {
      refreshPremium(true).catch(() => {});
    }, PASSIVE_REFRESH_MS);
  }

  async function submitPayment(form) {
    if (state.submitting) return;
    const phone = String(q("[data-premium-phone]", form)?.value || "").trim();
    if (!phone) {
      state.message = "Enter the M-Pesa number that should receive the STK prompt.";
      state.messageTone = "error";
      renderOverlay();
      return;
    }
    state.submitting = true;
    state.message = "";
    renderOverlay();
    try {
      const payload = await api("/me/premium-access/mpesa/stk-push", {
        method: "POST",
        body: JSON.stringify({ phone, idempotency_key: newIdempotencyKey() }),
      });
      state.payment = payload.payment || null;
      state.premium = payload.premium || state.premium;
      if (paymentIsSuccess(payload)) {
        clearPaymentIntent();
        state.successUntil = Date.now() + 4500;
        await refreshPremium(true, { preserveSuccess: true, skipLatest: true });
      } else {
        schedulePoll(800);
      }
    } catch (error) {
      const payment = error?.payload?.detail?.payment;
      if (payment) state.payment = payment;
      state.message = String(error?.message || error);
      state.messageTone = "error";
      if (state.payment && PENDING_STATES.has(String(state.payment.status || ""))) schedulePoll(3000);
      else if (!state.payment) clearPaymentIntent();
    } finally {
      state.submitting = false;
      renderOverlay();
    }
  }

  function resetPaymentFlow() {
    stopPoll();
    state.payment = null;
    state.message = "";
    state.messageTone = "";
    state.successUntil = 0;
    clearPaymentIntent();
    renderOverlay();
  }

  function openProfile() {
    if (typeof window.FOA_AUTOMATION_NAVIGATE === "function") {
      window.FOA_AUTOMATION_NAVIGATE("profile");
    } else {
      location.hash = "#/profile";
    }
    setTimeout(() => { loadHistory(); renderProfile(); }, 80);
  }

  document.addEventListener("submit", (event) => {
    const form = event.target?.closest?.("[data-premium-mpesa-form]");
    if (!form) return;
    event.preventDefault();
    submitPayment(form);
  }, true);

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-premium-refresh]")) {
      event.preventDefault();
      pollPayment();
      return;
    }
    if (event.target?.closest?.("[data-premium-try-again]")) {
      event.preventDefault();
      resetPaymentFlow();
      return;
    }
    if (event.target?.closest?.("[data-premium-continue]")) {
      event.preventDefault();
      state.successUntil = 0;
      removeOverlay();
      renderReminder();
      return;
    }
    if (event.target?.closest?.("[data-premium-open]")) {
      event.preventDefault();
      if (!state.premium?.active) renderOverlay();
      return;
    }
    if (event.target?.closest?.("[data-premium-open-profile]")) {
      event.preventDefault();
      openProfile();
      return;
    }
    if (event.target?.closest?.(".foa-automation-bell") && state.premium?.active) {
      event.preventDefault();
      openProfile();
    }
  }, true);

  window.addEventListener("foa:automation-route", (event) => {
    if (event?.detail?.route === "profile") {
      loadHistory();
      setTimeout(renderProfile, 0);
    } else {
      setTimeout(renderReminder, 0);
    }
  });

  window.addEventListener("foa:premium-refresh", () => refreshPremium(true));
  window.addEventListener("pageshow", () => refreshPremium(true));
  window.addEventListener("focus", () => {
    if (paymentIsPending()) pollPayment();
    else refreshPremium(true);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && isAuthenticated()) {
      if (paymentIsPending()) pollPayment();
      else refreshPremium(true);
    }
  });

  let observerQueued = false;
  new MutationObserver(() => {
    if (observerQueued) return;
    observerQueued = true;
    requestAnimationFrame(() => {
      observerQueued = false;
      if (!isAuthenticated()) {
        if (state.premium) refreshPremium(true);
        return;
      }
      const key = accountKey();
      if (!state.premium || key !== state.accountKey) refreshPremium(true);
      else {
        if (state.premium.active) {
          renderReminder();
          renderProfile();
        } else if (!q(".foa-premium-overlay")) {
          renderOverlay();
        }
      }
    });
  }).observe(document.documentElement, { childList: true, subtree: true });

  state.tickTimer = setInterval(updateCountdowns, 1000);

  function boot() {
    window.FOA_PREMIUM_SUBSCRIPTION_ACTION6E_VERSION = VERSION;
    refreshPremium(true).catch(() => {});
  }

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", () => setTimeout(boot, 0), { once: true })
    : setTimeout(boot, 0);
})();
