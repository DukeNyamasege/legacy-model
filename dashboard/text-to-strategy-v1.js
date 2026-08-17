(() => {
  "use strict";

  if (window.__FOA_TEXT_TO_STRATEGY_ACTION2__) return;
  window.__FOA_TEXT_TO_STRATEGY_ACTION2__ = true;

  const VERSION = "20260817-action2-1";
  const MAX_WORDS = 250;
  const RESULT_KEY = "foa-text-strategy-result-v1";
  const DRAFT_KEY = "foa-text-strategy-draft-v1";
  let scheduled = false;
  let compiling = false;
  let lastResult = null;

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const icon = (name) => {
    const c = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    const map = {
      back: `<svg ${c}><path d="m15 18-6-6 6-6"/></svg>`,
      ai: `<svg ${c}><path d="M5 17a4 4 0 0 1-2-3.5V8a4 4 0 0 1 4-4h7a4 4 0 0 1 4 4v5.5a4 4 0 0 1-4 4H9l-4 3z"/><path d="M8 13l2-5 2 5M8.8 11h2.4M15 8v5M21 4v4M19 6h4"/></svg>`,
      arrowUp: `<svg ${c}><path d="m5 14 5-5 4 4 5-6"/><path d="M15 7h4v4"/></svg>`,
      arrowDown: `<svg ${c}><path d="m5 10 5 5 4-4 5 6"/><path d="M15 17h4v-4"/></svg>`,
      split: `<svg ${c}><circle cx="7" cy="7" r="2"/><circle cx="17" cy="7" r="2"/><circle cx="7" cy="17" r="2"/><circle cx="17" cy="17" r="2"/><path d="M9 7h6M7 9v6M17 9v6M9 17h6"/></svg>`,
      digits: `<svg ${c}><path d="M6 4v16M12 4v16M18 4v16M3 8h18M3 16h18"/></svg>`,
      clock: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
      target: `<svg ${c}><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><path d="m15 9 5-5M16 4h4v4"/></svg>`,
      shield: `<svg ${c}><path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6z"/><path d="M9 12h6"/></svg>`,
      info: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></svg>`,
      star: `<svg ${c}><path d="m12 3 1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5zM19 3v4M17 5h4"/></svg>`,
      trophy: `<svg ${c}><path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H4v2a4 4 0 0 0 4 4M16 6h4v2a4 4 0 0 1-4 4M12 13v5M8 21h8"/></svg>`,
      trend: `<svg ${c}><path d="m4 17 5-5 4 3 7-8"/><path d="M16 7h4v4"/></svg>`,
      check: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg>`,
      edit: `<svg ${c}><path d="M4 20h4l11-11-4-4L4 16z"/><path d="m13 7 4 4"/></svg>`,
    };
    return map[name] || map.ai;
  };

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function words(value) {
    const text = String(value || "").trim();
    return text ? text.split(/\s+/).filter(Boolean) : [];
  }

  function currentRoute() {
    return String(document.body.dataset.automationRoute || "").toLowerCase();
  }

  function isAuthenticated() {
    return Boolean(
      window.FOA_NETLIFY_LIVE_CACHE?.me?.authenticated
      || window.FOA_BOOT_SESSION?.authenticated
      || q(".builder-header #logout")
      || q("#foa-simple-app .account-pill"),
    );
  }

  function savedDraft() {
    try { return sessionStorage.getItem(DRAFT_KEY) || ""; } catch (_) { return ""; }
  }

  function saveDraft(value) {
    try { sessionStorage.setItem(DRAFT_KEY, String(value || "")); } catch (_) {}
  }

  function saveResult(value) {
    lastResult = value;
    try { sessionStorage.setItem(RESULT_KEY, JSON.stringify(value)); } catch (_) {}
  }

  const EXAMPLE = "Create a strategy called Risk Managers. Trade Over 3 on Volatility 100 1s when the last 3 digits are less than or equal to 3 and the Over 3 percentage over the last 1000 ticks is above 78%. Stake 0.50 and use a take profit of 2 with a stop loss of 3.";

  const TEMPLATES = [
    {
      id: "risk-managers",
      name: "Risk Managers",
      subtitle: "Volatility 100 · Over 3",
      icon: "shield",
      tone: "green",
      prompt: EXAMPLE,
    },
    {
      id: "over1-golden",
      name: "Over 1 Golden",
      subtitle: "All markets · Over 1",
      icon: "trophy",
      tone: "blue",
      prompt: "Create a strategy called Over 1 Golden. Trade Over 1 on all markets when the Over 1 percentage over the last 1000 digits is above 80%. Stake 0.50, take profit 5 and stop loss 10. Re-analyze after every trade.",
    },
    {
      id: "mean-reverter",
      name: "Mean Reverter",
      subtitle: "Volatility 10 · Under 6",
      icon: "trend",
      tone: "purple",
      prompt: "Create a strategy called Mean Reverter. Trade Under 6 on Volatility 10 when the last 4 digits are greater than or equal to 6 and the Under 6 percentage over the last 500 digits is above 60%. Stake 0.50, take profit 3 and stop loss 5.",
    },
  ];

  function chip(iconName, label, insert) {
    return `<button type="button" class="foa-ai-chip" data-ai-insert="${esc(insert)}">${icon(iconName)}<span>${esc(label)}</span></button>`;
  }

  function templateCard(item) {
    const path = item.tone === "green"
      ? "M2 22 14 17 24 19 35 12 46 14 58 7 70 9"
      : item.tone === "blue"
      ? "M2 20 12 15 25 16 35 9 48 13 60 7 70 10"
      : "M2 21 13 16 24 18 36 10 47 14 58 8 70 5";
    return `<button type="button" class="foa-ai-template ${item.tone}" data-ai-template="${esc(item.id)}">
      <span class="foa-ai-template-top"><span class="foa-ai-template-icon">${icon(item.icon)}</span><svg viewBox="0 0 72 26" fill="none" aria-hidden="true"><path d="${path}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="foa-ai-template-star">☆</span></span>
      <strong>${esc(item.name)}</strong><small>${esc(item.subtitle)}</small><span class="foa-ai-popular">● Popular</span>
    </button>`;
  }

  function resultMarkup(result) {
    if (!result) return "";
    const adjustments = Array.isArray(result.adjustments) ? result.adjustments : [];
    const rules = Array.isArray(result.rules) ? result.rules : [];
    return `<section class="foa-ai-result" data-ai-result>
      <div class="foa-ai-result-head"><span>${icon("check")}</span><div><small>INTERPRETATION READY</small><strong>${esc(result.name || "AI Strategy")}</strong></div><b>Review next</b></div>
      <div class="foa-ai-result-grid"><span><small>Market</small><strong>${esc(result.market_label || "Volatility 100 (1s)")}</strong></span><span><small>Contract</small><strong>${esc(result.contract_label || "Digit Over 3")}</strong></span></div>
      <div class="foa-ai-result-rules"><small>Rules understood</small>${rules.map((rule) => `<span>${icon("check")}<b>${esc(rule)}</b></span>`).join("")}</div>
      ${adjustments.length ? `<div class="foa-ai-adjustments"><small>Best possible interpretation</small><p>${esc(adjustments.join(" "))}</p></div>` : `<div class="foa-ai-adjustments exact"><small>Best possible interpretation</small><p>Your description mapped directly to supported DerivAdmin strategy rules.</p></div>`}
      <p class="foa-ai-review-note">The draft is saved for the Strategy Ready review screen in Action 3. Text never purchases a trade directly.</p>
    </section>`;
  }

  function pageMarkup() {
    const draft = savedDraft() || EXAMPLE;
    const count = words(draft).length;
    return `<section class="foa-automation-page foa-automation-scaffold foa-text-strategy-page" data-automation-scaffold="ai" data-text-strategy-version="${VERSION}">
      <header class="foa-ai-header"><button type="button" class="foa-ai-back" data-automation-route="home" aria-label="Back to Home">${icon("back")}</button><div><h1>Text to Strategy</h1><p>Describe what you want to trade</p></div></header>

      <section class="foa-ai-prompt-card">
        <div class="foa-ai-card-title"><span>${icon("ai")}</span><strong>Describe your strategy in plain English</strong></div>
        <div class="foa-ai-input-wrap"><textarea id="foa-ai-strategy-text" rows="8" spellcheck="true" autocapitalize="sentences" placeholder="Explain your strategy here...">${esc(draft)}</textarea><span id="foa-ai-word-count" class="${count > MAX_WORDS ? "over" : ""}">${count}/${MAX_WORDS} words</span></div>
        <div class="foa-ai-chips">${chip("arrowUp", "Over 3", "Trade Over 3")}${chip("arrowDown", "Under 6", "Trade Under 6")}${chip("split", "Even/Odd", "Trade Even when")}${chip("digits", "Last 3 Digits", "when the last 3 digits are")}${chip("clock", "1000 Ticks", "over the last 1000 ticks")}${chip("target", "TP", "take profit 2")}${chip("shield", "SL", "stop loss 3")}</div>
        <div class="foa-ai-helper">${icon("info")}<span>Use plain language. You have up to 250 words. DerivAdmin converts supported rules into the nearest workable strategy and tells you what it adjusted.</span></div>
      </section>

      <section class="foa-ai-templates"><div class="foa-ai-section-head"><h2>Templates you can modify</h2><button type="button" data-automation-route="builder">View all ›</button></div><div class="foa-ai-template-grid">${TEMPLATES.map(templateCard).join("")}</div></section>

      <section class="foa-ai-actions"><button type="button" class="foa-ai-generate" data-ai-generate ${count > MAX_WORDS ? "disabled" : ""}>${icon("star")}<span>${compiling ? "Generating Strategy..." : "Generate Strategy"}</span></button><button type="button" class="foa-ai-use-template" data-ai-focus-template>Use a Template</button><div id="foa-ai-error" class="foa-ai-error" hidden></div></section>

      ${resultMarkup(lastResult)}

      <section class="foa-ai-how"><h2>How it works</h2><div class="foa-ai-how-grid"><article><span class="foa-ai-step-icon">${icon("ai")}<b>1</b></span><strong>Describe</strong><small>Tell us your idea in plain English.</small></article><i></i><article><span class="foa-ai-step-icon">${icon("shield")}<b>2</b></span><strong>Validate</strong><small>We convert and validate supported rules.</small></article><i></i><article><span class="foa-ai-step-icon">${icon("clock")}<b>3</b></span><strong>Trade or Schedule</strong><small>Review it, then run now or schedule later.</small></article></div></section>
    </section>`;
  }

  function updateCounter() {
    const textarea = q("#foa-ai-strategy-text");
    const counter = q("#foa-ai-word-count");
    const generate = q("[data-ai-generate]");
    if (!textarea || !counter) return;
    const count = words(textarea.value).length;
    counter.textContent = `${count}/${MAX_WORDS} words`;
    counter.classList.toggle("over", count > MAX_WORDS);
    if (generate) generate.disabled = compiling || count === 0 || count > MAX_WORDS;
    saveDraft(textarea.value);
  }

  function insertPhrase(phrase) {
    const textarea = q("#foa-ai-strategy-text");
    if (!textarea) return;
    const value = textarea.value.trim();
    const joiner = value && !/[.!?]$/.test(value) ? ". " : value ? " " : "";
    textarea.value = `${value}${joiner}${phrase}`.trim();
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    updateCounter();
  }

  async function compileStrategy() {
    if (compiling) return;
    const textarea = q("#foa-ai-strategy-text");
    const errorBox = q("#foa-ai-error");
    if (!textarea) return;
    const text = textarea.value.trim();
    const count = words(text).length;
    if (!text || count > MAX_WORDS) {
      updateCounter();
      return;
    }

    compiling = true;
    if (errorBox) errorBox.hidden = true;
    render();
    try {
      const response = await fetch("/me/text-to-strategy/compile", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Accept": "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || body.message || `Strategy compiler returned ${response.status}`);
      saveResult(body);
    } catch (error) {
      const message = String(error?.message || error || "Strategy generation failed.");
      if (errorBox) {
        errorBox.textContent = message;
        errorBox.hidden = false;
      }
    } finally {
      compiling = false;
      render();
      q("[data-ai-result]")?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function bind(root) {
    const textarea = q("#foa-ai-strategy-text", root);
    textarea?.addEventListener("input", updateCounter);

    qa("[data-ai-insert]", root).forEach((button) => {
      button.addEventListener("click", () => insertPhrase(button.dataset.aiInsert || ""));
    });

    qa("[data-ai-template]", root).forEach((button) => {
      button.addEventListener("click", () => {
        const item = TEMPLATES.find((entry) => entry.id === button.dataset.aiTemplate);
        if (!item || !textarea) return;
        textarea.value = item.prompt;
        saveDraft(item.prompt);
        lastResult = null;
        updateCounter();
        textarea.scrollIntoView({ behavior: "smooth", block: "center" });
        textarea.focus();
      });
    });

    q("[data-ai-generate]", root)?.addEventListener("click", compileStrategy);
    q("[data-ai-focus-template]", root)?.addEventListener("click", () => {
      q(".foa-ai-templates", root)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function render() {
    scheduled = false;
    if (!isAuthenticated() || currentRoute() !== "ai") return;
    const main = q("#telegram-dashboard-snapshot > main");
    if (!main) return;
    const existing = q(`.foa-text-strategy-page[data-text-strategy-version="${VERSION}"]`, main);
    if (!existing) {
      main.innerHTML = pageMarkup();
      bind(main);
    } else {
      const result = q("[data-ai-result]", existing);
      if (lastResult && !result) {
        existing.outerHTML = pageMarkup();
        bind(main);
      }
    }
    window.FOA_TEXT_TO_STRATEGY_ACTION2_VERSION = VERSION;
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(render);
  }

  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  addEventListener("hashchange", schedule);
  addEventListener("pageshow", schedule);
  addEventListener("focus", schedule);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", schedule, { once: true })
    : schedule();
})();
