(() => {
  "use strict";
  if (window.__FOA_TIMEZONE_HOME_SYNC_ACTION4__) return;
  window.__FOA_TIMEZONE_HOME_SYNC_ACTION4__ = true;

  const TZ_KEY = "foa-user-timezone-v1";
  const STAGED_KEY = "foa-staged-schedules-action4-v1";
  let queued = false;

  function timezoneLabel() {
    const state = window.FOA_AUTOMATION_TIMEZONE;
    if (state?.abbreviation) return String(state.abbreviation);
    try {
      const name = localStorage.getItem(TZ_KEY) || "Africa/Nairobi";
      if (name === "Africa/Nairobi" || name === "Africa/Kampala" || name === "Africa/Dar_es_Salaam") return "EAT";
      return name.split("/").pop()?.replaceAll("_", " ") || "EAT";
    } catch (_) { return "EAT"; }
  }

  function nextPrepared() {
    try {
      const rows = JSON.parse(localStorage.getItem(STAGED_KEY) || "[]");
      if (!Array.isArray(rows) || !rows.length) return null;
      return rows
        .filter((item) => item?.date && item?.time)
        .sort((a, b) => `${a.date}T${a.time}`.localeCompare(`${b.date}T${b.time}`))[0] || null;
    } catch (_) { return null; }
  }

  function sync() {
    queued = false;
    const home = document.querySelector(".foa-automation-home");
    if (!home) return;
    const chip = Array.from(home.querySelectorAll(".foa-automation-chip")).find((node) => String(node.textContent || "").trim().startsWith("Timezone"));
    const value = chip?.querySelector("b");
    if (value) value.textContent = timezoneLabel();

    const prepared = nextPrepared();
    if (!prepared) return;
    const next = home.querySelector(".foa-automation-next");
    const line = next?.querySelector(".foa-automation-next-copy > span b");
    const title = next?.querySelector(".foa-automation-next-copy > strong");
    const status = next?.querySelector(".foa-automation-status");
    if (line) line.textContent = `${prepared.date} · ${prepared.time}`;
    if (title) title.textContent = prepared.strategy_name || "Scheduled Strategy";
    if (status) status.textContent = "Prepared";
  }

  function queue() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(sync);
  }

  new MutationObserver(queue).observe(document.documentElement, { childList: true, subtree: true });
  addEventListener("foa:timezone-changed", queue);
  addEventListener("hashchange", queue);
  addEventListener("pageshow", queue);
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", queue, { once: true }) : queue();
})();
