(() => {
  "use strict";

  if (window.__FOA_TIMEZONE_SCHEDULE_ACTION4__) return;
  window.__FOA_TIMEZONE_SCHEDULE_ACTION4__ = true;

  const VERSION = "20260817-action4-2";
  const DEFAULT_TIMEZONE = "Africa/Nairobi";
  const TZ_CACHE_KEY = "foa-user-timezone-v1";
  const SCHEDULE_HANDOFF_KEY = "foa-schedule-selected-strategy-v1";
  const USER_TEMPLATE_KEY = "foa-user-strategy-templates-v1";
  const BUILDER_KEY = "foa-builder-draft-v2";
  const STAGED_SCHEDULES_KEY = "foa-staged-schedules-action4-v1";
  const ROUTE_KEY = "foa-automation-route-session-v1";
  const READY_RESULT_KEY = "foa-text-strategy-result-v1";

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const esc = (value) => String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");

  let renderQueued = false;
  let preferencesLoaded = false;
  let preferenceBusy = false;
  let timezoneState = { timezone: DEFAULT_TIMEZONE, abbreviation: "EAT", utc_offset: "UTC+03:00", configured: false };

  const FALLBACK_TIMEZONES = [
    "Africa/Nairobi", "Africa/Kampala", "Africa/Dar_es_Salaam", "Africa/Kigali", "Africa/Bujumbura",
    "Africa/Juba", "Africa/Addis_Ababa", "Africa/Mogadishu", "Africa/Lagos", "Africa/Johannesburg",
    "Africa/Cairo", "Africa/Accra", "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Madrid",
    "Asia/Dubai", "Asia/Riyadh", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "America/New_York",
    "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Toronto", "America/Sao_Paulo",
    "Australia/Sydney", "Pacific/Auckland", "UTC",
  ];

  function icon(name) {
    const c = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    const map = {
      back: `<svg ${c}><path d="m15 18-6-6 6-6"/></svg>`,
      globe: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18"/></svg>`,
      clock: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
      calendar: `<svg ${c}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18"/></svg>`,
      strategy: `<svg ${c}><path d="m12 3 4 2.2v4.6L12 12l-4-2.2V5.2zM7 12l4 2.2v4.6L7 21l-4-2.2v-4.6zM17 12l4 2.2v4.6L17 21l-4-2.2v-4.6z"/></svg>`,
      money: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="M15 8.5c-.7-.8-1.7-1.2-3-1.2-1.7 0-3 .8-3 2s1.2 1.8 3 2.2 3 .9 3 2.2-1.3 2.2-3 2.2c-1.4 0-2.6-.5-3.4-1.4M12 5v14"/></svg>`,
      target: `<svg ${c}><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/></svg>`,
      shield: `<svg ${c}><path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6z"/><path d="m9 12 2 2 4-4"/></svg>`,
      check: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg>`,
      play: `<svg ${c}><circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/></svg>`,
      search: `<svg ${c}><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>`,
      spark: `<svg ${c}><path d="m12 3 1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5zM19 3v4M17 5h4"/></svg>`,
    };
    return map[name] || map.clock;
  }

  function readJSON(storage, key, fallback = null) {
    try { const raw = storage.getItem(key); return raw ? JSON.parse(raw) : fallback; } catch (_) { return fallback; }
  }
  function writeJSON(storage, key, value) {
    try { storage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; }
  }
  function isAuthenticated() {
    return Boolean(window.FOA_NETLIFY_LIVE_CACHE?.me?.authenticated || window.FOA_BOOT_SESSION?.authenticated || q(".builder-header #logout") || q("#foa-simple-app .account-pill"));
  }
  function currentRoute() {
    return String(document.body.dataset.automationRoute || String(location.hash || "").replace(/^#\/?/, "").split(/[?&]/)[0]).toLowerCase();
  }
  function navigate(route) {
    if (typeof window.FOA_AUTOMATION_NAVIGATE === "function") return window.FOA_AUTOMATION_NAVIGATE(route);
    try { sessionStorage.setItem(ROUTE_KEY, route); } catch (_) {}
    location.hash = `#/${route}`;
  }

  function timezones() {
    try {
      if (typeof Intl.supportedValuesOf === "function") {
        const rows = Intl.supportedValuesOf("timeZone");
        if (rows?.length) return rows;
      }
    } catch (_) {}
    return FALLBACK_TIMEZONES;
  }
  function timezoneOptions(selected, filter = "") {
    const needle = String(filter || "").trim().toLowerCase();
    const preferred = [DEFAULT_TIMEZONE, "Africa/Kampala", "Africa/Dar_es_Salaam", "Europe/London", "America/New_York"];
    const matches = timezones().filter((name) => !needle || name.toLowerCase().includes(needle));
    return [...new Set([...preferred, ...matches])].slice(0, needle ? 180 : 220)
      .map((name) => `<option value="${esc(name)}" ${name === selected ? "selected" : ""}>${esc(name.replaceAll("_", " "))}</option>`).join("");
  }

  function cacheTimezone(payload) {
    timezoneState = {
      timezone: String(payload?.timezone || DEFAULT_TIMEZONE),
      abbreviation: String(payload?.abbreviation || (payload?.timezone === DEFAULT_TIMEZONE ? "EAT" : "")),
      utc_offset: String(payload?.utc_offset || "UTC+03:00"),
      configured: Boolean(payload?.configured),
    };
    try { localStorage.setItem(TZ_CACHE_KEY, timezoneState.timezone); } catch (_) {}
    window.FOA_AUTOMATION_TIMEZONE = clone(timezoneState);
    window.dispatchEvent(new CustomEvent("foa:timezone-changed", { detail: clone(timezoneState) }));
  }

  async function loadPreferences(force = false) {
    if (!isAuthenticated() || preferenceBusy || (preferencesLoaded && !force)) return timezoneState;
    preferenceBusy = true;
    try {
      const response = await fetch("/me/automation-preferences", { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) return timezoneState;
      const payload = await response.json();
      cacheTimezone(payload);
      preferencesLoaded = true;
      if (payload.requires_timezone_onboarding) showTimezoneOnboarding();
    } catch (_) {
      /* Dashboard stays usable if preference lookup is temporarily unavailable. */
    } finally { preferenceBusy = false; }
    return timezoneState;
  }

  async function saveTimezone(name, closeOnboarding = false) {
    const timezone = String(name || DEFAULT_TIMEZONE).trim() || DEFAULT_TIMEZONE;
    const response = await fetch("/me/automation-preferences/timezone", {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ timezone }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `Timezone save returned ${response.status}`);
    cacheTimezone(payload);
    preferencesLoaded = true;
    if (closeOnboarding) q(".foa-timezone-onboarding")?.remove();
    return payload;
  }

  function onboardingMarkup() {
    return `<div class="foa-timezone-onboarding" role="dialog" aria-modal="true" aria-label="Choose scheduling timezone"><div class="foa-timezone-onboarding-glow"></div><section class="foa-timezone-onboarding-card">
      <div class="foa-timezone-step">STEP 1 OF 1</div><span class="foa-timezone-hero-icon">${icon("globe")}</span><small>DERIVADMIN AUTOMATION SETUP</small><h1>Set your timezone</h1><p>Your schedules use this timezone everywhere in DerivAdmin. Nairobi / EAT is selected by default for our East African traders.</p>
      <div class="foa-timezone-default-card"><span>${icon("clock")}</span><div><small>DEFAULT TIMEZONE</small><strong>Africa/Nairobi <b>EAT</b></strong><em>Recommended for East Africa users · UTC+03:00</em></div><i>${icon("check")}</i></div>
      <label class="foa-timezone-search"><span>${icon("search")}</span><input type="search" data-timezone-search placeholder="Search city or timezone" autocomplete="off"></label>
      <label class="foa-timezone-select-label"><span>Choose timezone</span><select data-timezone-onboarding-select>${timezoneOptions(DEFAULT_TIMEZONE)}</select></label>
      <div class="foa-timezone-note">${icon("globe")}<span>Your choice becomes your global scheduling timezone across linked DOT and ROT accounts. You can change it later in Profile or Schedule Trading.</span></div>
      <button type="button" class="foa-timezone-continue" data-timezone-continue>Continue</button><button type="button" class="foa-timezone-default" data-timezone-use-default>Use Nairobi default</button><p class="foa-timezone-error" data-timezone-error hidden></p>
    </section></div>`;
  }

  function showTimezoneOnboarding() {
    if (!isAuthenticated() || q(".foa-timezone-onboarding")) return;
    document.body.insertAdjacentHTML("beforeend", onboardingMarkup());
    const root = q(".foa-timezone-onboarding");
    const select = q("[data-timezone-onboarding-select]", root);
    const search = q("[data-timezone-search]", root);
    const error = q("[data-timezone-error]", root);
    search?.addEventListener("input", () => { if (select) select.innerHTML = timezoneOptions(select.value || DEFAULT_TIMEZONE, search.value); });
    async function submit(name) {
      qa("button", root).forEach((button) => { button.disabled = true; });
      if (error) error.hidden = true;
      try { await saveTimezone(name, true); scheduleRender(); }
      catch (exc) {
        if (error) { error.textContent = String(exc?.message || exc); error.hidden = false; }
        qa("button", root).forEach((button) => { button.disabled = false; });
      }
    }
    q("[data-timezone-continue]", root)?.addEventListener("click", () => submit(select?.value || DEFAULT_TIMEZONE));
    q("[data-timezone-use-default]", root)?.addEventListener("click", () => submit(DEFAULT_TIMEZONE));
  }

  function currentBuilderMoney() {
    const builder = readJSON(localStorage, BUILDER_KEY, {}) || {};
    return { stake: Number(builder?.money?.stake ?? 0.5), takeProfit: Number(builder?.money?.takeProfit ?? 2), stopLoss: Number(builder?.money?.stopLoss ?? 3) };
  }
  function handoffStrategy() { return readJSON(sessionStorage, SCHEDULE_HANDOFF_KEY, null); }
  function userTemplates() { const rows = readJSON(localStorage, USER_TEMPLATE_KEY, []); return Array.isArray(rows) ? rows : []; }
  function builtInTemplates() { const rows = window.FOA_STRATEGY_TEMPLATE_LIBRARY?.builtIns; return Array.isArray(rows) ? rows : []; }
  function strategyCatalog() {
    const rows = [];
    const handoff = handoffStrategy();
    if (handoff?.name) rows.push({ id: `handoff:${handoff.id || "selected"}`, name: handoff.name, source: "Selected Strategy", payload: handoff });
    userTemplates().forEach((item) => rows.push({ id: `user:${item.id}`, name: item.name || "My Strategy", source: item.source === "ai" ? "AI Generated" : "My Strategies", payload: item }));
    builtInTemplates().forEach((item) => rows.push({ id: `built:${item.id}`, name: item.name, source: "Built-in", payload: item }));
    const seen = new Set();
    return rows.filter((item) => { const key = `${item.source}:${item.name}`; if (seen.has(key)) return false; seen.add(key); return true; });
  }
  function selectedStrategy(id) { return strategyCatalog().find((item) => item.id === id) || strategyCatalog()[0] || null; }
  function moneyFromStrategy(item) {
    const base = currentBuilderMoney(); const payload = item?.payload || {}; const settings = payload.settings || {}; const builder = payload.builder || {};
    return { stake: Number(settings.stake_amount ?? builder?.money?.stake ?? base.stake), takeProfit: Number(settings.take_profit ?? builder?.money?.takeProfit ?? base.takeProfit), stopLoss: Number(settings.stop_loss ?? builder?.money?.stopLoss ?? base.stopLoss) };
  }
  function strategyOptions(selectedId) {
    const rows = strategyCatalog();
    if (!rows.length) return `<option value="">No saved strategy yet</option>`;
    return ["Selected Strategy", "Built-in", "My Strategies", "AI Generated"].map((group) => {
      const groupRows = rows.filter((item) => item.source === group); if (!groupRows.length) return "";
      return `<optgroup label="${esc(group)}">${groupRows.map((item) => `<option value="${esc(item.id)}" ${item.id === selectedId ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</optgroup>`;
    }).join("");
  }
  function localDate() { const n = new Date(); return `${n.getFullYear()}-${String(n.getMonth()+1).padStart(2,"0")}-${String(n.getDate()).padStart(2,"0")}`; }
  function localTime() { const n = new Date(Date.now()+3600000); return `${String(n.getHours()).padStart(2,"0")}:00`; }
  function stagedSchedules() { const rows = readJSON(localStorage, STAGED_SCHEDULES_KEY, []); return Array.isArray(rows) ? rows : []; }
  function dateLabel(date, time) {
    try { return new Intl.DateTimeFormat(undefined, { weekday:"short", day:"numeric", month:"short", year:"numeric", hour:"numeric", minute:"2-digit" }).format(new Date(`${date}T${time}:00`)); }
    catch (_) { return `${date} · ${time}`; }
  }

  function scheduleMarkup() {
    const catalog = strategyCatalog(); const chosen = catalog[0] || null; const money = moneyFromStrategy(chosen); const prepared = stagedSchedules();
    return `<span class="foa-automation-scaffold" data-automation-scaffold="schedule" hidden aria-hidden="true"></span><section class="foa-automation-page foa-schedule-page" data-schedule-action4="${VERSION}">
      <header class="foa-schedule-header"><button type="button" data-schedule-back aria-label="Back to Home">${icon("back")}</button><div><h1>Schedule Trading</h1><p>Automate a future trading session</p></div></header>
      <section class="foa-schedule-form-card"><div class="foa-schedule-card-title"><span>${icon("calendar")}</span><div><small>AUTOMATED SESSION</small><strong>Choose what to trade and when</strong></div></div>
        <label class="foa-schedule-field full"><span>${icon("strategy")}Strategy</span><select data-schedule-strategy>${strategyOptions(chosen?.id || "")}</select><small>Built-in, My Strategies and AI Generated are available here.</small></label>
        <div class="foa-schedule-grid two"><label class="foa-schedule-field"><span>${icon("calendar")}Date</span><input type="date" data-schedule-date value="${localDate()}" min="${localDate()}"></label><label class="foa-schedule-field"><span>${icon("clock")}Time</span><input type="time" data-schedule-time value="${localTime()}"></label></div>
        <label class="foa-schedule-field full timezone"><span>${icon("globe")}Timezone</span><select data-schedule-timezone>${timezoneOptions(timezoneState.timezone)}</select><small data-schedule-timezone-copy>${esc(timezoneState.timezone)} · ${esc(timezoneState.abbreviation)} · ${esc(timezoneState.utc_offset)}</small></label>
        <div class="foa-schedule-grid three money"><label class="foa-schedule-field"><span>${icon("money")}Stake</span><input type="number" min="0.35" step="0.01" data-schedule-stake value="${money.stake.toFixed(2)}"><b>USD</b></label><label class="foa-schedule-field tp"><span>${icon("target")}Take Profit</span><input type="number" min="0" step="0.01" data-schedule-tp value="${money.takeProfit.toFixed(2)}"><b>USD</b></label><label class="foa-schedule-field sl"><span>${icon("shield")}Stop Loss</span><input type="number" min="0" step="0.01" data-schedule-sl value="${money.stopLoss.toFixed(2)}"><b>USD</b></label></div>
      </section>
      <section class="foa-schedule-overlap"><div><span>${icon("clock")}</span><div><strong>If another session is still active</strong><small>Choose how DerivAdmin should handle an overlap.</small></div></div><label><input type="radio" name="foa-overlap" value="wait" checked><span><b>Wait until previous session finishes</b><small>Recommended</small></span></label><label><input type="radio" name="foa-overlap" value="skip"><span><b>Skip this scheduled session</b><small>Leave the current session untouched</small></span></label><label><input type="radio" name="foa-overlap" value="replace"><span><b>Stop previous and start this one</b><small>Intentionally replace the active session</small></span></label></section>
      <section class="foa-schedule-preview"><div class="foa-schedule-preview-head"><span>${icon("spark")}</span><div><small>SESSION PREVIEW</small><strong data-schedule-preview-name>${esc(chosen?.name || "Choose a strategy")}</strong></div><b>READY</b></div><div class="foa-schedule-preview-grid"><span><small>Starts</small><strong data-schedule-preview-start>${esc(dateLabel(localDate(),localTime()))}</strong></span><span><small>Timezone</small><strong data-schedule-preview-timezone>${esc(timezoneState.abbreviation || "EAT")}</strong></span><span><small>Trading mode</small><strong>Automated session</strong></span><span><small>Stops when</small><strong data-schedule-preview-risk>TP $${money.takeProfit.toFixed(2)} or SL $${money.stopLoss.toFixed(2)}</strong></span></div></section>
      <div class="foa-schedule-actions"><button type="button" class="foa-schedule-primary" data-schedule-submit>${icon("calendar")}<span>Schedule Session</span></button><button type="button" class="foa-schedule-secondary" data-schedule-trade-now>${icon("play")}<span>Trade Now Instead</span></button><p data-schedule-message hidden></p></div>
      <section class="foa-schedule-upcoming"><div class="foa-schedule-section-head"><div><small>MY AUTOMATION</small><h2>Upcoming Sessions</h2></div><span>${prepared.length} prepared</span></div><div data-schedule-upcoming>${prepared.length ? prepared.slice(-3).reverse().map((item) => `<article><span>${icon("calendar")}</span><div><small>${esc(item.date)} · ${esc(item.time)} · ${esc(item.timezone_abbreviation || item.timezone)}</small><strong>${esc(item.strategy_name)}</strong><em>$${Number(item.stake).toFixed(2)} stake · TP $${Number(item.take_profit).toFixed(2)} · SL $${Number(item.stop_loss).toFixed(2)}</em></div><b>Prepared</b></article>`).join("") : `<div class="foa-schedule-empty">Your scheduled sessions will appear here.</div>`}</div></section>
    </section>`;
  }

  function updatePreview(root) {
    const item = selectedStrategy(q("[data-schedule-strategy]",root)?.value || ""); const date=q("[data-schedule-date]",root)?.value||""; const time=q("[data-schedule-time]",root)?.value||"";
    const zone=q("[data-schedule-timezone]",root)?.value||timezoneState.timezone; const tp=Number(q("[data-schedule-tp]",root)?.value||0); const sl=Number(q("[data-schedule-sl]",root)?.value||0);
    if(q("[data-schedule-preview-name]",root)) q("[data-schedule-preview-name]",root).textContent=item?.name||"Choose a strategy";
    if(q("[data-schedule-preview-start]",root)) q("[data-schedule-preview-start]",root).textContent=dateLabel(date,time);
    if(q("[data-schedule-preview-timezone]",root)) q("[data-schedule-preview-timezone]",root).textContent=zone===timezoneState.timezone?(timezoneState.abbreviation||zone):(zone.split("/").pop()?.replaceAll("_"," ")||zone);
    if(q("[data-schedule-preview-risk]",root)) q("[data-schedule-preview-risk]",root).textContent=`TP $${tp.toFixed(2)} or SL $${sl.toFixed(2)}`;
  }
  function refreshMoney(root) {
    const m=moneyFromStrategy(selectedStrategy(q("[data-schedule-strategy]",root)?.value||""));
    q("[data-schedule-stake]",root).value=m.stake.toFixed(2); q("[data-schedule-tp]",root).value=m.takeProfit.toFixed(2); q("[data-schedule-sl]",root).value=m.stopLoss.toFixed(2); updatePreview(root);
  }
  function bindSchedule(root) {
    q("[data-schedule-back]",root)?.addEventListener("click",()=>navigate("home"));
    q("[data-schedule-strategy]",root)?.addEventListener("change",()=>refreshMoney(root));
    ["[data-schedule-date]","[data-schedule-time]","[data-schedule-stake]","[data-schedule-tp]","[data-schedule-sl]"].forEach((s)=>q(s,root)?.addEventListener("input",()=>updatePreview(root)));
    q("[data-schedule-timezone]",root)?.addEventListener("change",async(event)=>{ const message=q("[data-schedule-message]",root); try { const p=await saveTimezone(event.currentTarget.value); if(q("[data-schedule-timezone-copy]",root)) q("[data-schedule-timezone-copy]",root).textContent=`${p.timezone} · ${p.abbreviation} · ${p.utc_offset}`; updatePreview(root); } catch(exc){ if(message){message.textContent=String(exc?.message||exc);message.dataset.tone="error";message.hidden=false;} } });
    q("[data-schedule-submit]",root)?.addEventListener("click",()=>{
      const strategy=selectedStrategy(q("[data-schedule-strategy]",root)?.value||""); const message=q("[data-schedule-message]",root); if(!strategy){if(message){message.textContent="Choose or create a strategy before scheduling.";message.dataset.tone="error";message.hidden=false;}return;}
      const item={id:`prepared-${Date.now()}-${Math.random().toString(36).slice(2,7)}`,strategy_id:strategy.id,strategy_name:strategy.name,strategy_source:strategy.source,strategy_snapshot:clone(strategy.payload||{}),date:q("[data-schedule-date]",root)?.value||localDate(),time:q("[data-schedule-time]",root)?.value||localTime(),timezone:q("[data-schedule-timezone]",root)?.value||timezoneState.timezone,timezone_abbreviation:timezoneState.abbreviation,stake:Number(q("[data-schedule-stake]",root)?.value||.5),take_profit:Number(q("[data-schedule-tp]",root)?.value||0),stop_loss:Number(q("[data-schedule-sl]",root)?.value||0),overlap_policy:q('input[name="foa-overlap"]:checked',root)?.value||"wait",status:"prepared_for_scheduler",created_at:new Date().toISOString()};
      const rows=stagedSchedules(); rows.push(item); writeJSON(localStorage,STAGED_SCHEDULES_KEY,rows.slice(-50)); if(message){message.textContent="Session prepared successfully. The persistent VPS scheduler is activated in Action 5.";message.dataset.tone="success";message.hidden=false;} renderSchedule(true);
    });
    q("[data-schedule-trade-now]",root)?.addEventListener("click",()=>{ const strategy=selectedStrategy(q("[data-schedule-strategy]",root)?.value||""); if(strategy?.payload?.custom_strategy){writeJSON(sessionStorage,READY_RESULT_KEY,strategy.payload);navigate("ready");return;} navigate("builder"); });
  }

  function renderSchedule(force=false) {
    if(currentRoute()!=="schedule") return; const main=q("#telegram-dashboard-snapshot > main"); if(!main) return;
    if(force || !q(`.foa-schedule-page[data-schedule-action4="${VERSION}"]`,main)){ main.innerHTML=scheduleMarkup(); bindSchedule(main); }
  }

  function profileMarkup() {
    return `<section class="foa-profile-timezone" data-profile-timezone-action4="${VERSION}"><div class="foa-profile-timezone-head"><span>${icon("globe")}</span><div><small>GLOBAL AUTOMATION TIME</small><h2>Timezone</h2><p>Used by every scheduled session across your linked Deriv accounts.</p></div></div><label><span>Current timezone</span><select data-profile-timezone-select>${timezoneOptions(timezoneState.timezone)}</select><small data-profile-timezone-copy>${esc(timezoneState.timezone)} · ${esc(timezoneState.abbreviation)} · ${esc(timezoneState.utc_offset)}</small></label><div class="foa-profile-timezone-default"><span>${icon("check")}</span><p><b>Nairobi / EAT is the default.</b> Choose any supported global timezone when needed.</p></div><p data-profile-timezone-message hidden></p></section>`;
  }
  function injectProfile() {
    if(currentRoute()!=="profile") return; const main=q("#telegram-dashboard-snapshot > main"); if(!main||q(".foa-profile-timezone",main)) return; const card=q(".foa-automation-scaffold-card",main); if(!card)return;
    card.insertAdjacentHTML("afterend",profileMarkup()); const section=q(".foa-profile-timezone",main); q("[data-profile-timezone-select]",section)?.addEventListener("change",async(event)=>{const msg=q("[data-profile-timezone-message]",section);try{const p=await saveTimezone(event.currentTarget.value);q("[data-profile-timezone-copy]",section).textContent=`${p.timezone} · ${p.abbreviation} · ${p.utc_offset}`;if(msg){msg.textContent=`Timezone saved as ${p.timezone} (${p.abbreviation}).`;msg.dataset.tone="success";msg.hidden=false;}}catch(exc){if(msg){msg.textContent=String(exc?.message||exc);msg.dataset.tone="error";msg.hidden=false;}}});
  }

  function render() {
    renderQueued=false;
    if(!isAuthenticated()){q(".foa-timezone-onboarding")?.remove();preferencesLoaded=false;return;}
    loadPreferences();
    if(currentRoute()==="schedule") renderSchedule(); else if(currentRoute()==="profile") injectProfile();
    window.FOA_TIMEZONE_SCHEDULE_ACTION4_VERSION=VERSION;
  }
  function scheduleRender(){if(renderQueued)return;renderQueued=true;requestAnimationFrame(render);}

  new MutationObserver(scheduleRender).observe(document.documentElement,{childList:true,subtree:true});
  addEventListener("hashchange",scheduleRender); addEventListener("pageshow",scheduleRender); addEventListener("focus",scheduleRender); addEventListener("foa:automation-route",scheduleRender); addEventListener("foa:timezone-changed",scheduleRender);
  document.readyState==="loading"?document.addEventListener("DOMContentLoaded",scheduleRender,{once:true}):scheduleRender();
})();
