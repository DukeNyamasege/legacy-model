(() => {
  "use strict";

  if (window.__FOA_AUTOMATION_SCHEDULER_ACTION5__) return;
  window.__FOA_AUTOMATION_SCHEDULER_ACTION5__ = true;

  const VERSION = "20260817-action5-1";
  const HANDOFF_KEY = "foa-schedule-selected-strategy-v1";
  const USER_TEMPLATE_KEY = "foa-user-strategy-templates-v1";
  let renderQueued = false;
  let requestBusy = false;
  let lastPayload = null;
  let lastFetchAt = 0;

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const esc = (value) => String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");

  function readJSON(storage, key, fallback = null) {
    try { const raw = storage.getItem(key); return raw ? JSON.parse(raw) : fallback; }
    catch (_) { return fallback; }
  }

  function currentRoute() {
    return String(document.body.dataset.automationRoute || String(location.hash || "").replace(/^#\/?/, "").split(/[?&]/)[0]).toLowerCase();
  }

  function isAuthenticated() {
    return Boolean(
      window.FOA_NETLIFY_LIVE_CACHE?.me?.authenticated
      || window.FOA_BOOT_SESSION?.authenticated
      || q(".builder-header #logout")
      || q("#foa-simple-app .account-pill"),
    );
  }

  function strategyBySelectValue(value) {
    const raw = String(value || "");
    const handoff = readJSON(sessionStorage, HANDOFF_KEY, null);
    if (raw.startsWith("handoff:") && handoff) {
      return { id: raw, name: handoff.name || "Selected Strategy", source: "Selected Strategy", payload: handoff };
    }
    if (raw.startsWith("user:")) {
      const id = raw.slice(5);
      const users = readJSON(localStorage, USER_TEMPLATE_KEY, []);
      const item = Array.isArray(users) ? users.find((row) => String(row?.id) === id) : null;
      if (item) return { id: raw, name: item.name || "My Strategy", source: item.source === "ai" ? "AI Generated" : "My Strategies", payload: item };
    }
    if (raw.startsWith("built:")) {
      const id = raw.slice(6);
      const rows = window.FOA_STRATEGY_TEMPLATE_LIBRARY?.builtIns;
      const item = Array.isArray(rows) ? rows.find((row) => String(row?.id) === id) : null;
      if (item) return { id: raw, name: item.name || "Built-in Strategy", source: "Built-in", payload: item };
    }
    if (handoff) return { id: `handoff:${handoff.id || "selected"}`, name: handoff.name || "Selected Strategy", source: "Selected Strategy", payload: handoff };
    return null;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.message || `Request returned ${response.status}`);
    return payload;
  }

  async function loadSchedules(force = false) {
    if (!isAuthenticated()) return null;
    if (!force && lastPayload && Date.now() - lastFetchAt < 2500) return lastPayload;
    const payload = await api("/me/automation-schedules?limit=80");
    lastPayload = payload;
    lastFetchAt = Date.now();
    window.FOA_AUTOMATION_SCHEDULES = payload;
    window.dispatchEvent(new CustomEvent("foa:schedules-updated", { detail: payload }));
    return payload;
  }

  function statusLabel(status) {
    const value = String(status || "scheduled").toLowerCase();
    return {
      scheduled: "Scheduled",
      waiting: "Waiting",
      starting: "Starting",
      running: "Running",
      completed: "Completed",
      skipped: "Skipped",
      cancelled: "Cancelled",
      failed: "Failed",
    }[value] || value;
  }

  function displayLocal(item) {
    const raw = String(item?.date_time_local || "");
    try {
      const parsed = new Date(raw);
      if (Number.isNaN(parsed.getTime())) return raw.replace("T", " ");
      return new Intl.DateTimeFormat(undefined, {
        weekday: "short", day: "numeric", month: "short", year: "numeric",
        hour: "numeric", minute: "2-digit",
        timeZone: String(item.timezone || "Africa/Nairobi"),
      }).format(parsed);
    } catch (_) { return raw.replace("T", " "); }
  }

  function scheduleCard(item) {
    const cancellable = ["scheduled", "waiting", "starting"].includes(String(item.status || ""));
    return `<article class="foa-action5-schedule-row" data-status="${esc(item.status)}" data-action5-schedule-id="${esc(item.id)}">
      <span class="foa-action5-schedule-icon" aria-hidden="true">◷</span>
      <div class="foa-action5-schedule-copy">
        <small>${esc(displayLocal(item))} · ${esc(item.timezone)}</small>
        <strong>${esc(item.strategy_name)}</strong>
        <em>$${Number(item.stake || 0).toFixed(2)} stake · TP $${Number(item.take_profit || 0).toFixed(2)} · SL $${Number(item.stop_loss || 0).toFixed(2)}</em>
        ${item.status_reason && ["failed", "skipped"].includes(String(item.status)) ? `<p>${esc(item.status_reason)}</p>` : ""}
      </div>
      <div class="foa-action5-schedule-end"><b>${esc(statusLabel(item.status))}</b>${cancellable ? `<button type="button" data-action5-cancel="${esc(item.id)}">Cancel</button>` : ""}</div>
    </article>`;
  }

  async function renderScheduleList(force = false) {
    if (currentRoute() !== "schedule") return;
    const target = q("[data-schedule-upcoming]");
    if (!target) return;
    try {
      const payload = await loadSchedules(force);
      const rows = [
        ...(payload?.active ? [payload.active] : []),
        ...(Array.isArray(payload?.upcoming) ? payload.upcoming : []),
        ...(Array.isArray(payload?.history) ? payload.history.slice(0, 5) : []),
      ].filter((item, index, all) => all.findIndex((other) => other.id === item.id) === index);
      target.innerHTML = rows.length
        ? rows.slice(0, 10).map(scheduleCard).join("")
        : `<div class="foa-schedule-empty">Your server-scheduled sessions will appear here and remain available after browser or VPS restarts.</div>`;
      const count = q(".foa-schedule-section-head > span");
      if (count) count.textContent = `${(payload?.upcoming?.length || 0) + (payload?.active ? 1 : 0)} upcoming`;
    } catch (error) {
      target.innerHTML = `<div class="foa-schedule-empty error">${esc(error?.message || error)}</div>`;
    }
  }

  function setScheduleMessage(text, tone = "success") {
    const node = q("[data-schedule-message]");
    if (!node) return;
    node.textContent = text;
    node.dataset.tone = tone;
    node.hidden = false;
  }

  async function createFromVisibleForm() {
    if (requestBusy) return;
    const root = q(".foa-schedule-page");
    if (!root) return;
    const select = q("[data-schedule-strategy]", root);
    const strategy = strategyBySelectValue(select?.value || "");
    if (!strategy) {
      setScheduleMessage("Choose or create a strategy before scheduling.", "error");
      return;
    }
    const submit = q("[data-schedule-submit]", root);
    requestBusy = true;
    if (submit) submit.disabled = true;
    try {
      const body = {
        strategy_name: strategy.name,
        strategy_source: strategy.source,
        strategy_snapshot: strategy.payload,
        date: q("[data-schedule-date]", root)?.value || "",
        time: q("[data-schedule-time]", root)?.value || "",
        timezone: q("[data-schedule-timezone]", root)?.value || "Africa/Nairobi",
        stake: Number(q("[data-schedule-stake]", root)?.value || 0.5),
        take_profit: Number(q("[data-schedule-tp]", root)?.value || 0),
        stop_loss: Number(q("[data-schedule-sl]", root)?.value || 0),
        overlap_policy: q('input[name="foa-overlap"]:checked', root)?.value || "wait",
      };
      const payload = await api("/me/automation-schedules", { method: "POST", body: JSON.stringify(body) });
      setScheduleMessage(`Session scheduled on the server for ${displayLocal(payload.schedule)}. It will run even if this browser is closed.`, "success");
      lastFetchAt = 0;
      await renderScheduleList(true);
      await renderHomeSchedule(true);
    } catch (error) {
      setScheduleMessage(String(error?.message || error), "error");
    } finally {
      requestBusy = false;
      if (submit) submit.disabled = false;
    }
  }

  async function cancelSchedule(id) {
    if (!id || requestBusy) return;
    requestBusy = true;
    try {
      await api(`/me/automation-schedules/${encodeURIComponent(id)}/cancel`, { method: "POST" });
      setScheduleMessage("Scheduled session cancelled.", "success");
      lastFetchAt = 0;
      await renderScheduleList(true);
      await renderHomeSchedule(true);
    } catch (error) {
      setScheduleMessage(String(error?.message || error), "error");
    } finally { requestBusy = false; }
  }

  async function renderHomeSchedule(force = false) {
    if (currentRoute() !== "home") return;
    const card = q(".foa-automation-next");
    if (!card) return;
    try {
      const payload = await loadSchedules(force);
      const next = payload?.active || payload?.upcoming?.slice()?.sort((a, b) => String(a.scheduled_for_utc).localeCompare(String(b.scheduled_for_utc)))[0];
      if (!next) return;
      const copy = q(".foa-automation-next-copy", card);
      if (copy) copy.innerHTML = `<span>${payload.active ? "Active session" : "Next session"}: <b>${esc(displayLocal(next))}</b></span><strong>${esc(next.strategy_name)}</strong>`;
      const status = q(".foa-automation-status", card);
      if (status) { status.textContent = statusLabel(next.status); status.dataset.status = next.status; }
      qa(".foa-automation-chip", card).forEach((chip) => {
        const text = String(chip.textContent || "").trim().toLowerCase();
        if (text.startsWith("stake")) chip.innerHTML = `Stake <b>$${Number(next.stake || 0).toFixed(2)}</b>`;
        if (text.startsWith("tp")) chip.innerHTML = `TP <b>$${Number(next.take_profit || 0).toFixed(2)}</b>`;
        if (text.startsWith("sl")) chip.innerHTML = `SL <b>$${Number(next.stop_loss || 0).toFixed(2)}</b>`;
        if (text.startsWith("timezone")) chip.innerHTML = `Timezone <b>${esc(next.timezone)}</b>`;
      });
    } catch (_) {}
  }

  async function renderTradesSchedule(force = false) {
    if (currentRoute() !== "trades") return;
    const main = q("#telegram-dashboard-snapshot > main");
    if (!main) return;
    try {
      const payload = await loadSchedules(force);
      const active = payload?.active;
      const next = payload?.upcoming?.slice()?.sort((a, b) => String(a.scheduled_for_utc).localeCompare(String(b.scheduled_for_utc)))[0];
      let strip = q(".foa-action5-trades-session", main);
      if (!strip) {
        main.insertAdjacentHTML("afterbegin", `<section class="foa-action5-trades-session" data-action5-version="${VERSION}"></section>`);
        strip = q(".foa-action5-trades-session", main);
      }
      const item = active || next;
      strip.innerHTML = item
        ? `<span class="foa-action5-live-dot" data-live="${active ? "true" : "false"}"></span><div><small>${active ? "SCHEDULED SESSION ACTIVE" : "NEXT SCHEDULED SESSION"}</small><strong>${esc(item.strategy_name)}</strong><p>${esc(active ? statusLabel(item.status) : displayLocal(item))} · ${esc(item.timezone)} · Stake $${Number(item.stake || 0).toFixed(2)}</p></div><button type="button" data-action5-open-schedule>Schedule Trading</button>`
        : `<span class="foa-action5-live-dot"></span><div><small>AUTOMATION SCHEDULER</small><strong>No scheduled session</strong><p>Create a server-owned session from Schedule Trading.</p></div><button type="button" data-action5-open-schedule>Schedule Trading</button>`;
    } catch (_) {}
  }

  async function render(force = false) {
    renderQueued = false;
    if (!isAuthenticated()) { lastPayload = null; return; }
    await Promise.all([
      renderScheduleList(force),
      renderHomeSchedule(force),
      renderTradesSchedule(force),
    ]);
    window.FOA_AUTOMATION_SCHEDULER_ACTION5_VERSION = VERSION;
  }

  function scheduleRender(force = false) {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => render(force));
  }

  document.addEventListener("click", (event) => {
    const submit = event.target.closest?.("[data-schedule-submit]");
    if (submit && currentRoute() === "schedule") {
      // Capture before Action 4's localStorage-only staging handler. Action 5
      // owns persistence and execution from this point forward.
      event.preventDefault();
      event.stopImmediatePropagation();
      createFromVisibleForm();
      return;
    }
    const cancel = event.target.closest?.("[data-action5-cancel]");
    if (cancel) {
      event.preventDefault();
      cancelSchedule(cancel.dataset.action5Cancel);
      return;
    }
    if (event.target.closest?.("[data-action5-open-schedule]")) {
      event.preventDefault();
      if (typeof window.FOA_AUTOMATION_NAVIGATE === "function") window.FOA_AUTOMATION_NAVIGATE("schedule");
      else location.hash = "#/schedule";
    }
  }, true);

  new MutationObserver(() => scheduleRender(false)).observe(document.documentElement, { childList: true, subtree: true });
  addEventListener("hashchange", () => scheduleRender(true));
  addEventListener("pageshow", () => scheduleRender(true));
  addEventListener("focus", () => scheduleRender(true));
  addEventListener("foa:automation-route", () => scheduleRender(true));
  addEventListener("foa:timezone-changed", () => scheduleRender(false));
  window.setInterval(() => { if (isAuthenticated()) scheduleRender(true); }, 5000);
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", () => scheduleRender(true), { once: true }) : scheduleRender(true);
})();
