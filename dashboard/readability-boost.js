(() => {
  "use strict";

  if (window.__readabilityBoostInstalled) return;
  window.__readabilityBoostInstalled = true;

  const byId = (id) => document.getElementById(id);

  function injectStyles() {
    if (byId("readability-boost-styles")) return;
    const style = document.createElement("style");
    style.id = "readability-boost-styles";
    style.textContent = `
      :root {
        --readable-text: #f5f9ff;
        --readable-muted: #d3e4ff;
        --readable-soft: #b7cbee;
        --readable-line: rgba(108, 159, 255, .42);
      }
      body {
        font-size: 16px !important;
        line-height: 1.55 !important;
        color: var(--readable-text) !important;
        text-rendering: geometricPrecision;
        -webkit-font-smoothing: antialiased;
      }
      .global-dashboard-snapshot,
      .panel,
      .personal-accent,
      .custom-martingale-card,
      .contract-history,
      .gbs-panel,
      .gbs-platform-card,
      .gbs-period-card,
      .gbs-risk-card {
        color: var(--readable-text) !important;
      }
      .gbs-card-label,
      .gbs-card-note,
      .gbs-money-label,
      .gbs-section-subtle,
      .gbs-period-caption,
      .gbs-sim-copy,
      .custom-martingale-card p,
      .custom-martingale-grid label,
      .custom-martingale-mode-label,
      .settings-message,
      .api-token-message,
      .contract-table th,
      .contract-table td,
      .personal-execution-alert span {
        color: var(--readable-muted) !important;
        font-size: .93rem !important;
        line-height: 1.5 !important;
      }
      .gbs-card-label,
      .gbs-section-title,
      .gbs-period-head,
      .gbs-risk-card span,
      .custom-martingale-card h4,
      .personal-settings-toggle,
      .gbs-sim-title {
        font-size: .98rem !important;
        letter-spacing: .035em !important;
        color: #ffffff !important;
        font-weight: 800 !important;
      }
      .gbs-platform-card strong,
      .gbs-risk-card strong,
      .gbs-period-money,
      .gbs-stake-detail {
        color: #ffffff !important;
        text-shadow: 0 1px 16px rgba(70,140,255,.22);
      }
      .gbs-money {
        font-size: clamp(2rem, 4.5vw, 3.05rem) !important;
        line-height: 1.05 !important;
        font-weight: 900 !important;
      }
      .custom-martingale-card {
        border-color: var(--readable-line) !important;
        background: rgba(5, 22, 58, .72) !important;
      }
      .custom-martingale-card select,
      .custom-martingale-card input,
      .personal-settings-form input,
      .api-token-form input,
      .gbs-stake-input {
        min-height: 46px !important;
        font-size: 1rem !important;
        color: #ffffff !important;
        background: rgba(2, 16, 43, .86) !important;
        border-color: rgba(125, 174, 255, .5) !important;
      }
      button,
      .btn-login,
      .btn-logout,
      .gbs-filter,
      .account-mode-switch button {
        font-size: .95rem !important;
        font-weight: 800 !important;
      }
      .contract-table {
        font-size: .94rem !important;
      }
      .reference-policy-card {
        grid-column: 1 / -1;
        margin-top: 12px;
        padding: 14px 16px;
        border: 1px solid rgba(61, 240, 187, .42);
        border-radius: 14px;
        color: var(--readable-muted);
        background: rgba(4, 30, 45, .58);
      }
      .reference-policy-card strong { color: #ffffff; }
      .reference-policy-card h4 {
        margin: 0 0 8px;
        color: #3df0bb;
        font-size: 1rem;
        letter-spacing: .06em;
        text-transform: uppercase;
      }
      .reference-profile-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(150px, 1fr));
        gap: 10px;
        margin-top: 10px;
      }
      .reference-profile {
        padding: 10px 12px;
        border: 1px solid rgba(108, 159, 255, .28);
        border-radius: 12px;
        background: rgba(6, 23, 57, .62);
      }
      .reference-profile span,
      .reference-profile small { display: block; color: var(--readable-soft); }
      .reference-profile strong { display: block; margin-top: 5px; font-size: 1.1rem; }
      @media (max-width: 760px) {
        body { font-size: 15.5px !important; }
        .reference-profile-grid { grid-template-columns: 1fr; }
        .gbs-money { font-size: 2rem !important; }
      }
    `;
    document.head.appendChild(style);
  }

  function money(value) {
    const amount = Number(value || 0);
    const sign = amount >= 0 ? "+" : "-";
    return `${sign}$${Math.abs(amount).toFixed(2)}`;
  }

  function renderReferencePolicy(summary) {
    const today = summary?.system_performance?.today || {};
    const policy = today.global_reference_policy || {};
    if (!Object.keys(today).length) return;
    const anchor = byId("model-pl-fixed")?.closest(".gbs-pnl-wrap") || byId("model-pl-fixed")?.closest("section") || byId("global-dashboard-snapshot");
    if (!anchor) return;
    let card = byId("reference-policy-card");
    if (!card) {
      card = document.createElement("section");
      card.id = "reference-policy-card";
      card.className = "reference-policy-card";
      anchor.appendChild(card);
    }
    const profiles = Array.isArray(today.custom_martingale_profiles) ? today.custom_martingale_profiles : [];
    const profileHtml = profiles.slice(0, 3).map((profile) => `
      <div class="reference-profile">
        <span>${profile.label || "Custom profile"}</span>
        <strong>${money(profile.pnl)}</strong>
        <small>Max stake: $${Number(profile.maximum_stake || 0).toFixed(2)}</small>
      </div>
    `).join("");
    card.innerHTML = `
      <h4>Global P/L Reference</h4>
      <div><strong>Public Global Statistics use a standard $0.50 model replay.</strong> They do not sum or copy a trader's $1,000, $3,000 or custom stake size.</div>
      <div style="margin-top:6px;">${policy.with_martingale || "System Martingale follows the built-in recovery and virtual-guard sequence."}</div>
      ${profileHtml ? `<div class="reference-profile-grid">${profileHtml}</div>` : ""}
    `;
  }

  function endpointUrl(input) {
    if (typeof input === "string") return input;
    if (input && typeof input.url === "string") return input.url;
    return "";
  }

  function installFetchBridge() {
    if (window.__readabilityBoostFetchInstalled) return;
    window.__readabilityBoostFetchInstalled = true;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const response = await nativeFetch(input, init);
      const url = endpointUrl(input);
      if (response.ok && url.includes("/metrics/summary")) {
        response.clone().json().then(renderReferencePolicy).catch(() => {});
      }
      return response;
    };
  }

  function boot() {
    injectStyles();
    installFetchBridge();
    fetch("/metrics/summary?mode=demo", { credentials: "same-origin", cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => payload && renderReferencePolicy(payload))
      .catch(() => {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
