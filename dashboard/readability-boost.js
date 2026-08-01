(() => {
  "use strict";

  if (window.__readabilityBoostInstalled) return;
  window.__readabilityBoostInstalled = true;

  const byId = (id) => document.getElementById(id);
  let standardToday = null;
  let standardSystem = null;

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
      #reference-policy-card,
      .reference-policy-card {
        display: none !important;
      }
      @media (max-width: 760px) {
        body { font-size: 15.5px !important; }
        .gbs-money { font-size: 2rem !important; }
      }
    `;
    document.head.appendChild(style);
  }

  function removeReferencePolicyCard() {
    const card = byId("reference-policy-card") || document.querySelector(".reference-policy-card");
    if (card) card.remove();
  }

  function number(value, digits = 2) {
    const parsed = Number(value || 0);
    return parsed.toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function signedMoney(value) {
    const amount = Number(value || 0);
    const sign = amount >= 0 ? "+" : "-";
    return `${sign}$${number(Math.abs(amount), 2)}`;
  }

  function setMoney(id, value, positiveColor, negativeColor) {
    const element = byId(id);
    if (!element) return;
    const amount = Number(value || 0);
    element.textContent = signedMoney(amount);
    element.classList.toggle("negative", amount < 0);
    element.style.color = amount < 0 ? negativeColor : positiveColor;
  }

  function setPlainMoney(id, value) {
    const element = byId(id);
    if (element) element.textContent = `$${number(value || 0, 2)}`;
  }

  function todayFromSummary(summary) {
    return summary?.system_performance?.today || null;
  }

  function applyStandardModelCards() {
    const today = standardToday;
    if (!today) return;

    const withMartingale = today.with_martingale_pnl ?? today.martingale_pnl ?? today.simulated_martingale_pnl ?? 0;
    const withoutMartingale = today.without_martingale_pnl ?? today.fixed_pnl ?? today.simulated_fixed_pnl ?? 0;
    const maximumStake = today.maximum_martingale_stake ?? today.simulated_maximum_martingale_stake ?? 0.50;
    const flatStake = today.flat_stake ?? today.simulated_base_stake ?? today.reference_base_stake ?? 0.50;

    setMoney("model-pl-martingale", withMartingale, "var(--gbs-green)", "var(--gbs-red)");
    setMoney("model-pl-fixed", withoutMartingale, "var(--gbs-blue)", "var(--gbs-blue)");
    setPlainMoney("model-maximum-stake", maximumStake);
    setPlainMoney("model-flat-stake", flatStake);
  }

  function captureStandardSummary(summary) {
    const today = todayFromSummary(summary);
    if (!today) return;
    standardToday = today;
    standardSystem = summary?.system_performance || standardSystem;
    applyStandardModelCards();
  }

  async function fetchStandardSummary() {
    try {
      const response = await fetch("/metrics/summary?mode=demo", {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) return;
      captureStandardSummary(await response.json());
    } catch (_) {}
  }

  function endpointUrl(input) {
    if (typeof input === "string") return input;
    if (input && typeof input.url === "string") return input.url;
    return "";
  }

  function installFetchBridge() {
    if (window.__standardModelPnlFetchBridgeInstalled) return;
    window.__standardModelPnlFetchBridgeInstalled = true;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const response = await nativeFetch(input, init);
      const url = endpointUrl(input);
      if (response.ok && url.includes("/metrics/summary")) {
        response.clone().json().then(captureStandardSummary).catch(() => {});
      }
      if (url.includes("/metrics/system-performance")) {
        window.setTimeout(applyStandardModelCards, 80);
        window.setTimeout(applyStandardModelCards, 260);
      }
      return response;
    };
  }

  function boot() {
    injectStyles();
    removeReferencePolicyCard();
    installFetchBridge();
    fetchStandardSummary();
    const observer = new MutationObserver(() => {
      removeReferencePolicyCard();
      applyStandardModelCards();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    window.setInterval(applyStandardModelCards, 2000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
