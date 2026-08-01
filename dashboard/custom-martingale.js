(() => {
  "use strict";

  if (window.__customMartingaleControlsInstalled) return;
  window.__customMartingaleControlsInstalled = true;

  const byId = (id) => document.getElementById(id);

  function injectStyles() {
    if (byId("custom-martingale-styles")) return;
    const style = document.createElement("style");
    style.id = "custom-martingale-styles";
    style.textContent = `
      .custom-martingale-card {
        grid-column: 1 / -1;
        display: grid;
        gap: 12px;
        padding: 15px;
        border: 1px solid rgba(255,255,255,.22);
        border-radius: 16px;
        background: rgba(3,22,57,.34);
      }
      .custom-martingale-card h4 {
        margin: 0;
        color: #fff;
        font-size: .88rem;
        letter-spacing: .04em;
      }
      .custom-martingale-card p {
        margin: 0;
        color: rgba(255,255,255,.72);
        font-size: .76rem;
        line-height: 1.45;
      }
      .custom-martingale-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(120px, 1fr));
        gap: 10px;
      }
      .custom-martingale-grid label,
      .custom-martingale-mode-label {
        display: grid;
        gap: 6px;
        color: rgba(255,255,255,.78);
        font-size: .75rem;
      }
      .custom-martingale-card select,
      .custom-martingale-card input {
        width: 100%;
        min-height: 42px;
        padding: 0 11px;
        border: 1px solid rgba(255,255,255,.25);
        border-radius: 10px;
        color: #fff;
        background: rgba(3,22,57,.62);
        font: inherit;
      }
      .custom-martingale-card option { color: #111; }
      .custom-martingale-status {
        padding: 10px 12px;
        border-radius: 10px;
        color: rgba(255,255,255,.82);
        background: rgba(255,255,255,.07);
        font-size: .75rem;
        line-height: 1.55;
      }
      .custom-martingale-status strong { color: #fff; }
      .custom-martingale-ladder {
        display: block;
        margin-top: 7px;
        color: #fff;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        overflow-wrap: anywhere;
      }
      .custom-martingale-fields[hidden] { display: none !important; }
      @media (max-width: 760px) {
        .custom-martingale-grid { grid-template-columns: 1fr; }
      }
    `;
    document.head.appendChild(style);
  }

  function injectControls() {
    if (byId("custom-martingale-card")) return true;
    const form = document.querySelector(".personal-settings-form");
    const saveButton = byId("btn-save-trading-settings");
    if (!form || !saveButton) return false;

    const legacyToggle = byId("personal-martingale")?.closest("label");
    if (legacyToggle) legacyToggle.style.display = "none";

    const card = document.createElement("section");
    card.id = "custom-martingale-card";
    card.className = "custom-martingale-card";
    card.innerHTML = `
      <h4>Martingale Control</h4>
      <p>System Martingale is the default and calculates the exact recovery stake from the actual loss debt and quoted payout. Custom Martingale uses your selected multiplier after your chosen number of consecutive actual losses. Virtual observations never increase the custom level.</p>
      <label class="custom-martingale-mode-label">Martingale mode
        <select id="personal-martingale-mode">
          <option value="system">System Martingale — default exact recovery</option>
          <option value="custom">Custom Martingale — multiplier based</option>
          <option value="flat">Flat stake — disable recovery escalation</option>
        </select>
      </label>
      <div id="custom-martingale-fields" class="custom-martingale-fields">
        <div class="custom-martingale-grid">
          <label>Start after consecutive losses
            <input id="personal-martingale-trigger" type="number" min="1" max="10" step="1" value="1" inputmode="numeric">
          </label>
          <label>Stake multiplier
            <input id="personal-martingale-multiplier" type="number" min="1.10" max="10" step="0.10" value="2.00" inputmode="decimal">
          </label>
          <label>Maximum levels
            <input id="personal-martingale-max-levels" type="number" min="1" max="10" step="1" value="6" inputmode="numeric">
          </label>
          <label>Maximum custom stake (USD)
            <input id="personal-martingale-max-stake" type="number" min="0.35" max="1000000" step="0.01" value="1000.00" inputmode="decimal">
          </label>
        </div>
      </div>
      <div id="custom-martingale-status" class="custom-martingale-status"></div>
    `;
    form.insertBefore(card, saveButton);

    byId("personal-martingale-mode")?.addEventListener("change", renderModeExplanation);
    [
      "personal-stake",
      "personal-martingale-trigger",
      "personal-martingale-multiplier",
      "personal-martingale-max-levels",
      "personal-martingale-max-stake",
    ].forEach((id) => byId(id)?.addEventListener("input", renderModeExplanation));
    renderModeExplanation();
    return true;
  }

  function numeric(id, fallback) {
    const value = Number(byId(id)?.value);
    return Number.isFinite(value) ? value : fallback;
  }

  function currentSettingsPayload() {
    const mode = byId("personal-martingale-mode")?.value || "system";
    return {
      martingale_enabled: mode !== "flat",
      martingale_mode: mode,
      martingale_trigger_losses: Math.max(1, Math.min(10, Math.trunc(numeric("personal-martingale-trigger", 1)))),
      martingale_multiplier: Math.max(1.10, Math.min(10, numeric("personal-martingale-multiplier", 2.0))),
      martingale_max_levels: Math.max(1, Math.min(10, Math.trunc(numeric("personal-martingale-max-levels", 6)))),
      martingale_max_stake: Math.max(0.35, Math.min(1000000, numeric("personal-martingale-max-stake", 1000))),
    };
  }

  function customStakeLadder(settings) {
    const baseStake = Math.max(0.35, numeric("personal-stake", 0.50));
    const values = [];
    for (let level = 1; level <= settings.martingale_max_levels; level += 1) {
      const calculated = baseStake * (settings.martingale_multiplier ** level);
      const capped = calculated > settings.martingale_max_stake;
      const stake = Math.ceil(Math.min(calculated, settings.martingale_max_stake) * 100 - 1e-9) / 100;
      values.push(`L${level} $${stake.toFixed(2)}${capped ? " (cap)" : ""}`);
    }
    return {
      baseStake,
      text: values.join(" → "),
    };
  }

  function renderModeExplanation() {
    const mode = byId("personal-martingale-mode")?.value || "system";
    const fields = byId("custom-martingale-fields");
    const status = byId("custom-martingale-status");
    const legacy = byId("personal-martingale");
    if (fields) fields.hidden = mode !== "custom";
    if (legacy) legacy.checked = mode !== "flat";
    if (!status) return;

    if (mode === "system") {
      status.innerHTML = "<strong>System Martingale:</strong> the built-in policy remains unchanged. It calculates the next real PUT stake from the actual unrecovered loss debt and current proposal profit ratio.";
      return;
    }
    if (mode === "flat") {
      status.innerHTML = "<strong>Flat stake:</strong> Martingale recovery escalation is disabled. Primary real trades continue at the saved base stake; no larger recovery stake is armed after a loss.";
      return;
    }

    const settings = currentSettingsPayload();
    const ladder = customStakeLadder(settings);
    status.innerHTML = `<strong>Custom Martingale:</strong> level 1 starts after actual loss ${settings.martingale_trigger_losses}. Each additional actual loss advances one level. Virtual wins and losses do not advance the level. Multiplier mode follows your chosen amounts and does not guarantee exact debt recovery; use System Martingale for exact recovery calculations.<span class="custom-martingale-ladder">Base $${ladder.baseStake.toFixed(2)} → ${ladder.text}</span>`;
  }

  function applyServerSettings(settings) {
    if (!settings || typeof settings !== "object") return;
    injectControls();
    const mode = ["system", "custom", "flat"].includes(settings.martingale_mode)
      ? settings.martingale_mode
      : settings.martingale_enabled === false
        ? "flat"
        : "system";
    if (byId("personal-martingale-mode")) byId("personal-martingale-mode").value = mode;
    if (byId("personal-martingale-trigger")) byId("personal-martingale-trigger").value = Number(settings.martingale_trigger_losses ?? 1).toFixed(0);
    if (byId("personal-martingale-multiplier")) byId("personal-martingale-multiplier").value = Number(settings.martingale_multiplier ?? 2).toFixed(2);
    if (byId("personal-martingale-max-levels")) byId("personal-martingale-max-levels").value = Number(settings.martingale_max_levels ?? 6).toFixed(0);
    if (byId("personal-martingale-max-stake")) byId("personal-martingale-max-stake").value = Number(settings.martingale_max_stake ?? 1000).toFixed(2);
    renderModeExplanation();
  }

  function endpointUrl(input) {
    if (typeof input === "string") return input;
    if (input && typeof input.url === "string") return input.url;
    return "";
  }

  function installFetchBridge() {
    if (window.__customMartingaleFetchInstalled) return;
    window.__customMartingaleFetchInstalled = true;
    const nativeFetch = window.fetch.bind(window);

    window.fetch = async (input, init = {}) => {
      const url = endpointUrl(input);
      let nextInit = init || {};
      const method = String(nextInit.method || (input && input.method) || "GET").toUpperCase();

      if (url.includes("/me/trading-settings") && method === "POST") {
        let body = {};
        try {
          body = JSON.parse(String(nextInit.body || "{}"));
        } catch (_) {
          body = {};
        }
        body = { ...body, ...currentSettingsPayload() };
        nextInit = { ...nextInit, body: JSON.stringify(body) };
      }

      const response = await nativeFetch(input, nextInit);
      if (url.endsWith("/me") || url.includes("/me?")) {
        response.clone().json().then((payload) => {
          if (payload?.authenticated) applyServerSettings(payload.settings || {});
        }).catch(() => {});
      }
      if (url.includes("/me/trading-settings") && response.ok) {
        response.clone().json().then((payload) => {
          applyServerSettings(payload?.settings || {});
        }).catch(() => {});
      }
      return response;
    };
  }

  function loadCurrentSettings() {
    fetch("/me", { credentials: "same-origin", cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => {
        if (payload?.authenticated) applyServerSettings(payload.settings || {});
      })
      .catch(() => {});
  }

  function boot() {
    injectStyles();
    installFetchBridge();
    if (!injectControls()) {
      const observer = new MutationObserver(() => {
        if (injectControls()) observer.disconnect();
      });
      observer.observe(document.documentElement, { childList: true, subtree: true });
    }
    loadCurrentSettings();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();