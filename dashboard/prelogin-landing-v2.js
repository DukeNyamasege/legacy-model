(() => {
  "use strict";

  if (window.__FOA_PRELOGIN_LANDING_V2__) return;
  window.__FOA_PRELOGIN_LANDING_V2__ = true;

  const REGISTER_URL = "https://t.deriv.link?t=CZXDLJPXM38M";
  let scheduled = false;

  const brainSvg = `
    <svg class="foa-brain-svg" viewBox="0 0 760 610" role="img" aria-label="Blue and red neural trading network">
      <defs>
        <linearGradient id="foaBrainStroke" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#22d3ee"/>
          <stop offset=".48" stop-color="#2563eb"/>
          <stop offset="1" stop-color="#ef4444"/>
        </linearGradient>
        <radialGradient id="foaBrainGlow" cx="48%" cy="45%" r="62%">
          <stop offset="0" stop-color="#2563eb" stop-opacity=".22"/>
          <stop offset=".7" stop-color="#0ea5e9" stop-opacity=".06"/>
          <stop offset="1" stop-color="#020617" stop-opacity="0"/>
        </radialGradient>
        <filter id="foaGlow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="4" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <ellipse cx="384" cy="300" rx="325" ry="255" fill="url(#foaBrainGlow)"/>
      <g class="foa-brain-outline" fill="none" stroke="url(#foaBrainStroke)" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" filter="url(#foaGlow)">
        <path d="M380 88c-41-48-122-55-174-18-35 25-47 58-43 91-55 7-92 43-96 92-4 41 19 72 51 88-19 41-10 85 24 111 25 20 59 25 89 15 17 40 55 64 99 62 22-1 40-8 55-21"/>
        <path d="M384 88c41-48 122-55 174-18 35 25 47 58 43 91 55 7 92 43 96 92 4 41-19 72-51 88 19 41 10 85-24 111-25 20-59 25-89 15-17 40-55 64-99 62-22-1-40-8-55-21"/>
        <path d="M384 97c-17 45-18 93-4 142 12 43 10 92-6 137-13 36-12 78 10 121" opacity=".82"/>
        <path d="M170 172c49-13 94-2 128 32 26 26 44 65 48 105M594 172c-49-13-94-2-128 32-26 26-44 65-48 105" opacity=".68"/>
        <path d="M128 334c54-25 105-18 144 18 30 28 55 60 80 93M636 334c-54-25-105-18-144 18-30 28-55 60-80 93" opacity=".62"/>
        <path d="M225 105c17 39 49 65 96 78M539 105c-17 39-49 65-96 78" opacity=".7"/>
      </g>
      <g class="foa-neural-lines" fill="none" stroke-width="1.4">
        <path d="M137 250L232 195 315 250 390 177 475 236 601 188" stroke="#38bdf8"/>
        <path d="M126 342L220 315 307 363 390 301 478 349 637 317" stroke="#2563eb"/>
        <path d="M180 428L273 395 356 451 443 387 553 429" stroke="#ef4444"/>
        <path d="M206 139L276 217 352 132 430 213 517 142" stroke="#60a5fa"/>
        <path d="M249 491L332 421 410 484 490 413" stroke="#f43f5e"/>
      </g>
      <g class="foa-nodes" filter="url(#foaGlow)">
        ${[[137,250,"#22d3ee"],[232,195,"#60a5fa"],[315,250,"#2563eb"],[390,177,"#22d3ee"],[475,236,"#60a5fa"],[601,188,"#ef4444"],[126,342,"#38bdf8"],[220,315,"#2563eb"],[307,363,"#22d3ee"],[390,301,"#60a5fa"],[478,349,"#ef4444"],[637,317,"#f43f5e"],[180,428,"#2563eb"],[273,395,"#22d3ee"],[356,451,"#60a5fa"],[443,387,"#ef4444"],[553,429,"#f43f5e"],[206,139,"#38bdf8"],[352,132,"#2563eb"],[517,142,"#ef4444"],[249,491,"#60a5fa"],[410,484,"#ef4444"]].map(([x,y,c]) => `<circle cx="${x}" cy="${y}" r="4.5" fill="${c}"/>`).join("")}
      </g>
      <g class="foa-candles" opacity=".95">
        <line x1="300" y1="275" x2="300" y2="380" stroke="#22c55e" stroke-width="3"/><rect x="290" y="303" width="20" height="48" rx="2" fill="#22c55e"/>
        <line x1="330" y1="250" x2="330" y2="348" stroke="#ef4444" stroke-width="3"/><rect x="320" y="271" width="20" height="45" rx="2" fill="#ef4444"/>
        <line x1="360" y1="229" x2="360" y2="330" stroke="#22c55e" stroke-width="3"/><rect x="350" y="249" width="20" height="54" rx="2" fill="#22c55e"/>
        <line x1="390" y1="206" x2="390" y2="309" stroke="#22c55e" stroke-width="3"/><rect x="380" y="225" width="20" height="52" rx="2" fill="#22c55e"/>
        <line x1="420" y1="187" x2="420" y2="296" stroke="#ef4444" stroke-width="3"/><rect x="410" y="212" width="20" height="49" rx="2" fill="#ef4444"/>
        <line x1="450" y1="172" x2="450" y2="270" stroke="#22c55e" stroke-width="3"/><rect x="440" y="193" width="20" height="48" rx="2" fill="#22c55e"/>
        <line x1="480" y1="151" x2="480" y2="254" stroke="#ef4444" stroke-width="3"/><rect x="470" y="175" width="20" height="44" rx="2" fill="#ef4444"/>
        <line x1="510" y1="135" x2="510" y2="233" stroke="#22c55e" stroke-width="3"/><rect x="500" y="157" width="20" height="51" rx="2" fill="#22c55e"/>
      </g>
    </svg>`;

  function marketCard(code, name, detail, tone = "blue") {
    return `<article class="foa-market-card ${tone}">
      <div class="foa-market-card-head"><span>${name}</span><b>${code}</b></div>
      <strong>${detail}</strong>
      <div class="foa-spark" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
      <small>Deriv Volatility market</small>
    </article>`;
  }

  function landingMarkup() {
    return `<section class="foa-landing-v2" aria-label="Derivadmin strategy automation">
      <div class="foa-landing-grid" aria-hidden="true"></div>
      <div class="foa-landing-glow foa-landing-glow-blue" aria-hidden="true"></div>
      <div class="foa-landing-glow foa-landing-glow-red" aria-hidden="true"></div>

      <div class="foa-landing-main">
        <div class="foa-hero-copy">
          <span class="foa-hero-kicker"><i></i> DERIV STRATEGY AUTOMATION</span>
          <h1><span>BUILD.</span><span class="accent-blue">AUTOMATE.</span><span class="accent-mix">TRADE.</span></h1>
          <h2>Create your strategy with <strong>Mr Duke</strong> on <em>Derivadmin</em>.</h2>
          <p>Turn your trading rules into automated Custom Strategies, choose your Deriv Volatility markets, and control execution from one dashboard.</p>

          <div class="foa-landing-actions">
            <a class="foa-landing-login" href="/oauth/start"><span>Login</span><small>Open your dashboard</small></a>
            <a class="foa-landing-register" href="${REGISTER_URL}" target="_blank" rel="noopener noreferrer"><span>Register</span><small>Create a Deriv account</small></a>
          </div>

          <a class="foa-register-link" href="${REGISTER_URL}" target="_blank" rel="noopener noreferrer" aria-label="Register with Deriv">
            <span class="foa-link-icon" aria-hidden="true">↗</span>
            <span><small>REGISTER WITH DERIV</small><strong>t.deriv.link?t=CZXDLJPXM38M</strong></span>
          </a>

          <div class="foa-feature-row" aria-label="Platform capabilities">
            <span><b>01</b><strong>Build Rules</strong><small>Custom conditions</small></span>
            <span><b>02</b><strong>Choose Markets</strong><small>Volatility indices</small></span>
            <span><b>03</b><strong>Automate</strong><small>Account execution</small></span>
          </div>
        </div>

        <div class="foa-hero-visual">
          <div class="foa-brain-wrap">${brainSvg}</div>
          <div class="foa-floating-tag tag-one"><i></i> Pattern detected</div>
          <div class="foa-floating-tag tag-two"><i></i> Automation ready</div>

          <div class="foa-market-stack">
            ${marketCard("V10", "Volatility 10 Index", "1s · Digits", "blue")}
            <div class="foa-market-mini-grid">
              ${marketCard("V25", "Volatility 25", "Digits", "cyan")}
              ${marketCard("V75", "Volatility 75", "Digits", "red")}
            </div>
          </div>

          <article class="foa-signal-card">
            <div class="foa-signal-orb" aria-hidden="true"><i></i></div>
            <div><small>STRATEGY ENGINE</small><strong>READY TO AUTOMATE</strong><span>Custom conditions · Account scoped · Risk controls</span></div>
          </article>
        </div>
      </div>

      <div class="foa-trust-strip">
        <span><b>◇</b><strong>Custom Strategies</strong></span>
        <span><b>⌁</b><strong>Volatility Markets</strong></span>
        <span><b>◎</b><strong>Account Scoped</strong></span>
        <span><b>⚡</b><strong>Fast Execution</strong></span>
      </div>
    </section>`;
  }

  function enhanceHeader(root) {
    const brand = root.querySelector(".builder-brand > div:last-child");
    if (brand) {
      const strong = brand.querySelector("strong");
      const span = brand.querySelector("span");
      if (strong) strong.textContent = "Derivadmin";
      if (span) span.textContent = "Mr Duke · Strategy Automation";
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
    const current = root.querySelector(".foa-landing-v2");
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

  window.FOA_PRELOGIN_LANDING_VERSION = "20260814-1";
})();
