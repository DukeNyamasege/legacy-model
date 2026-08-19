import fs from "node:fs";

const shellPath = "dist/final-ui-shell-v2.js";
const runPath = "dist/direct-run-panel-authority-v6.js";
const premiumPath = "dist/final-premium-6f3.js";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`scheduler-v2 missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function replaceOnce(text, oldValue, newValue, label) {
  if (text.includes(newValue)) return text;
  const count = text.split(oldValue).length - 1;
  if (count !== 1) throw new Error(`scheduler-v2 ${label}: expected exactly one source match, got ${count}`);
  return text.replace(oldValue, newValue);
}

let shell = read(shellPath);

const scheduleHelpers = `
  function normalizedScheduleTime(value) {
    const text = String(value || "").trim();
    if (/^\\d{2}:\\d{2}:\\d{2}$/.test(text)) return text;
    if (/^\\d{2}:\\d{2}$/.test(text)) return \`${"${text}"}:00\`;
    return "";
  }

  function exactScheduleTime(value, seconds) {
    const normalized = normalizedScheduleTime(value);
    if (!normalized) return "";
    const second = Math.max(0, Math.min(59, Math.trunc(Number(seconds || 0))));
    return \`${"${normalized.slice(0, 5)}"}:${"${String(second).padStart(2, \"0\")}"}\`;
  }

  function zonedWallClock(date, timezone) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date);
    const value = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
    return \`${"${value.year}"}-${"${value.month}"}-${"${value.day}"}T${"${value.hour}"}:${"${value.minute}"}:${"${value.second}"}\`;
  }

  function scheduleWallClockIsFuture(dateText, timeText, timezone) {
    const normalizedTime = normalizedScheduleTime(timeText);
    if (!/^\\d{4}-\\d{2}-\\d{2}$/.test(String(dateText || "")) || !normalizedTime) return false;
    const minimum = zonedWallClock(new Date(Date.now() + 5000), timezone || DEFAULT_TZ);
    return \`${"${dateText}"}T${"${normalizedTime}"}\` > minimum;
  }

`;

shell = replaceOnce(shell, "  function schedulePage() {", `${scheduleHelpers}  function schedulePage() {`, "schedule helpers");
shell = replaceOnce(
  shell,
  'const localTime = state.scheduleDraft?.time || new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false }).format(now);',
  'const localClock = normalizedScheduleTime(state.scheduleDraft?.time || new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, hourCycle: "h23" }).format(now));\n    const localTime = localClock.slice(0, 5);\n    const localSecond = localClock.slice(6, 8) || "00";',
  "seconds default",
);
shell = replaceOnce(
  shell,
  'const active = (state.schedules?.schedules || []).filter((item) => !["completed", "cancelled", "skipped", "failed"].includes(String(item.status || "").toLowerCase()));',
  'const schedules = state.schedules?.items || state.schedules?.schedules || [];\n    const active = schedules.filter((item) => !["completed", "cancelled", "skipped", "failed"].includes(String(item.status || "").toLowerCase()));\n    const history = schedules.filter((item) => ["completed", "cancelled", "skipped", "failed"].includes(String(item.status || "").toLowerCase()));',
  "schedule API items/history",
);

const oldTimeGrid = '<div class="form-grid three"><label><span>Date</span><input id="s-date" type="date" value="${esc(localDate)}"></label><label><span>Time</span><input id="s-time" type="time" value="${esc(localTime)}"></label><label><span>Timezone</span><select id="s-timezone">${TIMEZONES.map(([zone, city]) => `<option value="${esc(zone)}" ${zone === (state.scheduleDraft?.timezone || tz) ? "selected" : ""}>${esc(city)}</option>`).join("")}</select></label></div>';
const newTimeGrid = '<div class="form-grid schedule-clock-grid"><label><span>Date</span><input id="s-date" type="date" value="${esc(localDate)}"></label><label><span>Time</span><input id="s-time" type="time" step="60" value="${esc(localTime)}"></label><label><span>Seconds</span><input id="s-second" type="number" min="0" max="59" step="1" inputmode="numeric" value="${esc(localSecond)}"></label><label><span>Timezone</span><select id="s-timezone">${TIMEZONES.map(([zone, city]) => `<option value="${esc(zone)}" ${zone === (state.scheduleDraft?.timezone || tz) ? "selected" : ""}>${esc(city)}</option>`).join("")}</select></label></div>';
shell = replaceOnce(shell, oldTimeGrid, newTimeGrid, "explicit seconds input");

const oldAside = '<aside class="panel upcoming compact-schedules"><div class="panel-title"><div><span class="eyebrow">SCHEDULED TRADES</span><h3>${active.length}</h3></div></div>${active.length ? active.slice(0, 12).map((item) => scheduleRow(item)).join("") : `<div class="empty-mini compact"><p>No scheduled trades.</p></div>`}</aside>';
const newAside = '<aside class="panel upcoming compact-schedules"><div class="panel-title"><div><span class="eyebrow">UPCOMING / ACTIVE</span><h3>${active.length}</h3></div></div>${active.length ? active.slice(0, 12).map((item) => scheduleRow(item)).join("") : `<div class="empty-mini compact"><p>No upcoming scheduled trades.</p></div>`}<div class="panel-title schedule-history-title"><div><span class="eyebrow">SCHEDULE HISTORY</span><h3>${history.length}</h3></div></div>${history.length ? history.slice(0, 12).map((item) => scheduleRow(item)).join("") : `<div class="empty-mini compact"><p>No completed scheduled sessions yet.</p></div>`}</aside>';
shell = replaceOnce(shell, oldAside, newAside, "history panel");

const oldRow = `  function scheduleRow(item) {
    const status = String(item.status || "scheduled").toLowerCase();
    const editable = ["scheduled", "waiting", "starting"].includes(status);
    return \`<div class="schedule-row compact">
      <div class="schedule-row-actions">\${editable ? \`<button data-delete-schedule="\${esc(item.id)}">Delete</button><button data-edit-schedule="\${esc(item.id)}">Edit</button>\` : ""}</div>
      <span><b>\${esc(item.strategy_name || "Strategy")}</b><small>\${esc(item.scheduled_local || item.scheduled_for_utc || "")}</small></span>
      <em>\${esc(status)}</em>
    </div>\`;
  }`;
const newRow = `  function scheduleRow(item) {
    const status = String(item.status || "scheduled").toLowerCase();
    const editable = ["scheduled", "waiting", "starting"].includes(status);
    const numericProfit = Number(item.result_profit);
    const hasResult = Number.isFinite(numericProfit) && item.result_profit !== null;
    const result = hasResult
      ? \`<span class="schedule-result \${numericProfit >= 0 ? "positive" : "negative"}"><b>\${numericProfit >= 0 ? "+" : ""}\${numericProfit.toFixed(2)} USD</b><small>\${esc(item.result_label || "Session finished")} · \${Number(item.result_runs || 0)} runs · \${Number(item.result_wins || 0)}W/\${Number(item.result_losses || 0)}L</small></span>\`
      : "";
    const reason = item.status_reason
      ? \`<small class="schedule-reason">\${esc(item.status_reason)}</small>\`
      : "";
    return \`<div class="schedule-row compact \${status}">
      <div class="schedule-row-actions">\${editable ? \`<button data-delete-schedule="\${esc(item.id)}">Delete</button><button data-edit-schedule="\${esc(item.id)}">Edit</button>\` : ""}</div>
      <span><b>\${esc(item.strategy_name || "Strategy")}</b><small>\${esc(item.date_time_local || item.scheduled_local || item.scheduled_for_utc || "")}</small></span>
      <em>\${esc(status.replaceAll("_", " "))}</em>
      \${result}
      \${reason}
    </div>\`;
  }`;
shell = replaceOnce(shell, oldRow, newRow, "schedule result card");

shell = replaceOnce(
  shell,
  'const dateTime = String(item?.scheduled_local || item?.date_time_local || "").match(/(\\d{4}-\\d{2}-\\d{2}).*?(\\d{2}:\\d{2})/);',
  'const dateTime = String(item?.date_time_local || item?.scheduled_local || "").match(/(\\d{4}-\\d{2}-\\d{2}).*?(\\d{2}:\\d{2}(?::\\d{2})?)/);',
  "edit seconds",
);
shell = replaceOnce(
  shell,
  'const overlap = document.querySelector(\'input[name="overlap"]:checked\')?.value || "wait";\n      const payload = {',
  'const overlap = document.querySelector(\'input[name="overlap"]:checked\')?.value || "wait";\n      const scheduleDate = document.getElementById("s-date")?.value || "";\n      const scheduleTime = exactScheduleTime(document.getElementById("s-time")?.value || "", document.getElementById("s-second")?.value || 0);\n      const scheduleZone = document.getElementById("s-timezone")?.value || DEFAULT_TZ;\n      if (!scheduleWallClockIsFuture(scheduleDate, scheduleTime, scheduleZone)) throw new Error("Choose an exact schedule time in the future.");\n      const payload = {',
  "future-only UI guard",
);
shell = replaceOnce(shell, 'date: document.getElementById("s-date")?.value,', 'date: scheduleDate,', "schedule date payload");
shell = replaceOnce(shell, 'time: document.getElementById("s-time")?.value,', 'time: scheduleTime,', "schedule time payload");
shell = replaceOnce(shell, 'timezone: document.getElementById("s-timezone")?.value || DEFAULT_TZ,', 'timezone: scheduleZone,', "schedule timezone payload");
shell = replaceOnce(
  shell,
  'const item = (state.schedules?.schedules || []).find((row) => String(row.id) === String(button.dataset.editSchedule));',
  'const item = (state.schedules?.items || state.schedules?.schedules || []).find((row) => String(row.id) === String(button.dataset.editSchedule));',
  "edit API items",
);
shell = replaceOnce(
  shell,
  'state.loaded = true;\n      if (shouldHoldRender(quiet)) {',
  'state.loaded = true;\n      if (state.schedules?.active) window.dispatchEvent(new CustomEvent("derivadmin:scheduled-runtime", { detail: state.schedules.active }));\n      if (shouldHoldRender(quiet)) {',
  "scheduled runtime event",
);
shell = replaceOnce(
  shell,
  'window.FOA_FINAL_UI = Object.freeze({ version: "20260818-local-ui-12", refresh, go });',
  'window.FOA_FINAL_UI = Object.freeze({ version: "20260818-scheduler-v2", refresh, go, state: () => state });',
  "expose scheduler/live ledger state",
);
fs.writeFileSync(shellPath, shell);

let run = read(runPath);
run = replaceOnce(
  run,
  'state.serverOwner = stopped ? "stopped" : owner;\n      state.serverActive = !stopped;\n      if (stopped) state.userStopLatch = true;\n      queueRender();',
  'state.serverOwner = stopped ? "stopped" : owner;\n      state.serverActive = !stopped;\n      if (stopped) state.userStopLatch = true;\n      else if (owner === "server" || owner === "server_takeover") state.userStopLatch = false;\n      queueRender();',
  "scheduled server start clears visual stop latch",
);
run = replaceOnce(run, '  }, 4000);', '  }, 1000);', "scheduled start status cadence");
fs.writeFileSync(runPath, run);

let premium = read(premiumPath);
premium = replaceOnce(
  premium,
  'await loadScript("/final-ui-shell-v2.js?v=20260818-production-v6", "final-ui-shell-v2");',
  'await loadScript("/final-ui-shell-v2.js?v=20260818-scheduler-v2", "final-ui-shell-v2");',
  "final shell cache bust after production-v6",
);
fs.writeFileSync(premiumPath, premium);

console.log("scheduler-v2 finalizer applied: explicit seconds/future-only/history/server start/run ledger state");
