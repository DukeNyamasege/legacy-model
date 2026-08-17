(() => {
  "use strict";

  if (window.__FOA_PRELOGIN_LANDING_V2__) return;
  window.__FOA_PRELOGIN_LANDING_V2__ = true;

  const VERSION = "20260817-mobile-automation-1";
  const REGISTER_URL = "https://t.deriv.link?t=CZXDLJPXM38M";
  let scheduled = false;

  const svg = (name) => {
    const c = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    const map = {
      logo: `<svg ${c}><path d="M5 4h6.5a7.5 7.5 0 0 1 0 15H5l5-5h1.5a2.5 2.5 0 0 0 0-5H10z"/><path d="M5 4v15"/></svg>`,
      builder: `<svg ${c}><path d="m12 3 4 2.2v4.6L12 12l-4-2.2V5.2zM7 12l4 2.2v4.6L7 21l-4-2.2v-4.6zM17 12l4 2.2v4.6L17 21l-4-2.2v-4.6zM12 12V7.2"/></svg>`,
      ai: `<svg ${c}><path d="M5 17a4 4 0 0 1-2-3.5V8a4 4 0 0 1 4-4h7a4 4 0 0 1 4 4v5.5a4 4 0 0 1-4 4H9l-4 3zM8 13l2-5 2 5M8.8 11h2.4M15 8v5M21 4v4M19 6h4"/></svg>`,
      calendar: `<svg ${c}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18"/><circle cx="17" cy="17" r="3"/><path d="M17 15.5V17l1 1"/></svg>`,
      check: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg>`,
      arrow: `<svg ${c}><path d="M5 12h14M14 7l5 5-5 5"/></svg>`,
      shield: `<svg ${c}><path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6z"/><path d="m9 12 2 2 4-4"/></svg>`,
      pulse: `<svg ${c}><path d="M2 12h4l2-6 4 12 3-8 2 2h5"/></svg>`,
      phone: `<svg ${c}><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4"/></svg>`,
    };
    return map[name] || map.logo;
  };

  function productCard(icon, eyebrow, title, copy, tone) {
    return `<article class="foa-public-product ${tone}"><span class="foa-public-product-icon">${svg(icon)}</span><div><small>${eyebrow}</small><strong>${title}</strong><p>${copy}</p></div><span class="foa-public-product-arrow">${svg("arrow")}</span></article>`;
  }

  function automationPreview() {
    return `<div class="foa-public-preview" aria-hidden="true">
      <div class="foa-public-preview-top"><span><i></i><i></i></span><b>DERIVADMIN</b><em>LIVE</em></div>
      <div class="foa-public-preview-balance"><small>AUTOMATION BALANCE</small><strong>$8,630.78</strong><span>Home of Automation</span></div>
      <div class="foa-public-preview-chart"><svg viewBox="0 0 300 100" preserveAspectRatio="none"><defs><linearGradient id="foaLandingLine" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#28d6ff"/><stop offset="1" stop-color="#316cff"/></linearGradient><linearGradient id="foaLandingFill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#1ca9ff" stop-opacity=".28"/><stop offset="1" stop-color="#1ca9ff" stop-opacity="0"/></linearGradient></defs><path d="M0 91 28 75 53 81 78 55 105 62 132 39 158 50 189 24 217 35 247 12 274 20 300 5V100H0Z" fill="url(#foaLandingFill)"/><path d="M0 91 28 75 53 81 78 55 105 62 132 39 158 50 189 24 217 35 247 12 274 20 300 5" fill="none" stroke="url(#foaLandingLine)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
      <div class="foa-public-preview-tools"><span>${svg("builder")}<b>Build</b></span><span>${svg("ai")}<b>Describe</b></span><span>${svg("calendar")}<b>Schedule</b></span></div>
      <div class="foa-public-preview-session"><span>${svg("check")}</span><div><small>NEXT AUTOMATION</small><strong>Risk Managers · 7:00 PM EAT</strong></div></div>
    </div>`;
  }

  function landingMarkup() {
    return `<section class="foa-landing-v2 foa-public-automation" data-public-automation-version="${VERSION}" aria-label="DerivAdmin Home of Automation">
      <div class="foa-public-grid" aria-hidden="true"></div><div class="foa-public-glow one" aria-hidden="true"></div><div class="foa-public-glow two" aria-hidden="true"></div>

      <div class="foa-public-mobile-brand"><span>${svg("logo")}</span><div><strong>DerivAdmin</strong><small>Home of Automation</small></div><b>Powered by Deriv</b></div>

      <div class="foa-public-hero">
        <div class="foa-public-hero-copy">
          <span class="foa-public-kicker"><i></i> AUTOMATION FOR DERIV TRADERS</span>
          <h1><span>Build it.</span><span>Describe it.</span><span>Schedule it.</span></h1>
          <p class="foa-public-lead">Turn the way you think about trading into automation. Build advanced rules, describe a strategy in plain English, or schedule trading sessions from one mobile-first platform.</p>

          <div class="foa-public-actions">
            <a class="foa-public-login" href="/oauth/start"><span>Login with Deriv</span><small>Open Home of Automation</small>${svg("arrow")}</a>
            <a class="foa-public-register" href="${REGISTER_URL}" target="_blank" rel="noopener noreferrer"><span>Register</span><small>Create a Deriv account</small>${svg("arrow")}</a>
          </div>

          <div class="foa-public-micro-trust"><span>${svg("check")}No coding required</span><span>${svg("phone")}Built for mobile</span><span>${svg("shield")}Account scoped</span></div>
        </div>

        <div class="foa-public-hero-visual">${automationPreview()}</div>
      </div>

      <section class="foa-public-products"><div class="foa-public-section-copy"><small>HOME OF AUTOMATION</small><h2>Three ways to automate.</h2><p>Choose how much control you want. Every path feeds the same validated DerivAdmin strategy engine.</p></div><div class="foa-public-product-grid">
        ${productCard("builder", "ADVANCED", "Strategy Builder", "Create precise conditions with the full rule Builder.", "blue")}
        ${productCard("ai", "EASIEST", "Text to Strategy", "Explain your idea naturally. DerivAdmin converts it into supported rules.", "cyan")}
        ${productCard("calendar", "AUTOMATIC", "Schedule Trading", "Choose a strategy, date, time, stake, TP and SL, then let the VPS run the session.", "purple")}
      </div></section>

      <section class="foa-public-mobile-first"><span class="foa-public-mobile-icon">${svg("phone")}</span><div><small>MOBILE FIRST</small><h2>Your automation desk fits in your hand.</h2><p>Designed around the traders who use DerivAdmin from their phones every day: large controls, clear actions, fast navigation and a consistent dark-blue automation interface.</p><div><span>EAT ready</span><span>Global timezone support</span><span>Demo & Real</span></div></div></section>

      <div class="foa-public-trust"><span>${svg("pulse")}<b>Live trading controls</b></span><span>${svg("shield")}<b>Deterministic execution</b></span><span>${svg("ai")}<b>Plain-language strategies</b></span><span>${svg("calendar")}<b>Scheduled sessions</b></span></div>
      <p class="foa-public-risk">Automated trading involves financial risk. Strategy templates, generated rules and displayed statistics do not guarantee profit.</p>
    </section>`;
  }

  function enhanceHeader(root) {
    const brand = root.querySelector(".builder-brand > div:last-child");
    if (brand) {
      const strong = brand.querySelector("strong");
      const span = brand.querySelector("span");
      if (strong) strong.textContent = "DerivAdmin";
      if (span) span.textContent = "Home of Automation";
    }
    const actions = root.querySelector(".builder-head-actions");
    if (actions && !actions.querySelector(".foa-header-register")) {
      const register = document.createElement("a");
      register.className = "foa-header-register";
      register.href = REGISTER_URL;
      register.target = "_blank";
      register.rel = "noopener noreferrer";
      register.textContent = "Register";
      actions.appendChild(register);
    }
  }

  function applyLanding() {
    scheduled = false;
    const root = document.getElementById("foa-simple-app");
    if (!root) return;
    const old = root.querySelector(".public-builder");
    const current = root.querySelector(".foa-public-automation");
    if (!old && !current) {
      document.body.classList.remove("foa-prelogin-landing-v2");
      return;
    }
    document.body.classList.add("foa-prelogin-landing-v2");
    enhanceHeader(root);
    if (old) old.outerHTML = landingMarkup();
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(applyLanding);
  }

  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("pageshow", schedule);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", schedule, { once: true })
    : schedule();

  window.FOA_PRELOGIN_LANDING_VERSION = VERSION;
})();
