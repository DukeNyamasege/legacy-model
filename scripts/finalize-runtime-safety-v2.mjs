import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const ledgerPath = "dist/direct-transaction-ledger-v6.js";
const runPath = "dist/direct-run-panel-authority-v6.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`runtime-safety-v2 missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}
function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}
function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`runtime-safety-v2 ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

// ---------------------------------------------------------------------------
// Browser execution: exact provider errors, indefinite recoverability, and a
// persistent 60-second no-purchase diagnosis. Nothing here creates a new stop.
// ---------------------------------------------------------------------------
let engine = read(enginePath);

engine = replaceOne(
  engine,
  `      const response = await fetchWithTimeout(apiPath("/me/direct-execution/session"), { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, 7500);\n      if (!response.ok) throw new Error("secure session unavailable");`,
  `      const response = await fetchWithTimeout(apiPath("/me/direct-execution/session"), { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, 52000);\n      if (!response.ok) {\n        let detail = "";\n        try {\n          const failure = await response.clone().json();\n          detail = String(failure?.detail || failure?.message || "").slice(0, 180);\n        } catch (_) {}\n        throw new Error(detail || ("secure session unavailable (HTTP " + response.status + ")"));\n      }`,
  "direct-session exact HTTP error",
);

const clearThroughHelper = `  function clearLocalTradesThrough(cutoffIso) {\n    const cutoff = Date.parse(String(cutoffIso || ""));\n    if (!Number.isFinite(cutoff)) { clearLocalTrades(); return; }\n    const rows = loadJournal().filter((row) => {\n      const at = Date.parse(String(row?.at || ""));\n      return Number.isFinite(at) && at > cutoff;\n    });\n    writeJournal(rows);\n    window.dispatchEvent(new CustomEvent("derivadmin:direct-clear", {\n      detail: { history_only: true, cross_device: true, cleared_through: cutoffIso, financial_state_preserved: true },\n    }));\n  }\n\n`;
engine = replaceOne(
  engine,
  `  function normalizeCondition(raw) {`,
  clearThroughHelper + `  function normalizeCondition(raw) {`,
  "cross-device local history cutoff",
);

const diagnosticsHelpers = `  function runtimeSyncSnapshot() {\n    const value = window.__DERIVADMIN_ACCOUNT_RUNTIME_SYNC_V1;\n    return value && typeof value === "object" ? value : {};\n  }\n\n  function liveConditionSnapshot() {\n    try {\n      return window.DERIVADMIN_DIRECT_RUNTIME_UX_V4?.state?.()?.latest_live\n        || window.DERIVADMIN_DIRECT_RUNTIME_UX_V3?.state?.()?.latest_live\n        || null;\n    } catch (_) { return null; }\n  }\n\n  function conditionStatusText(live) {\n    const statuses = Array.isArray(live?.statuses) ? live.statuses : [];\n    const text = statuses.map((item) => {\n      if (typeof item === "string") return item;\n      if (!item || typeof item !== "object") return "";\n      const label = String(item.label || item.name || item.kind || "condition");\n      const value = item.message || item.text || item.status;\n      if (value) return label + ": " + String(value);\n      if (Object.prototype.hasOwnProperty.call(item, "met")) return label + ": " + (item.met ? "met" : "not met");\n      return label;\n    }).filter(Boolean);\n    return text.slice(0, 4).join("; ");\n  }\n\n  function noPurchaseReason() {\n    const sync = runtimeSyncSnapshot();\n    const live = liveConditionSnapshot();\n    const financial = window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1?.state?.() || {};\n    const serverStatus = String(sync.execution_status || "").toLowerCase();\n    const serverReason = String(sync.execution_status_reason || "").trim();\n\n    if (sync.hard_stop === true || sync.enabled === false || sync.owner === "stopped") {\n      return serverReason || "Trading is stopped on this account.";\n    }\n    if (state.ownerLost) {\n      return serverReason || "Browser execution lease moved to VPS continuity; server recovery is taking over.";\n    }\n    if (!state.armed) {\n      return state.lastExecutionError || serverReason || "Execution ownership is not armed yet; retrying account lease.";\n    }\n    if (state.privateWs?.readyState !== WebSocket.OPEN) {\n      return state.lastExecutionError || serverReason || "Authenticated Deriv trading session is not connected; reconnecting automatically.";\n    }\n    if (financial.buy_allowed === false) {\n      if (Number(financial.hydrationPending || 0) > 0) {\n        return "Financial execution is waiting for live market-history hydration to complete.";\n      }\n      return "Financial execution lease is not ready for BUY yet; ownership heartbeat is recovering.";\n    }\n    if (state.inFlight) {\n      return state.lastExecutionError || "A proposal or BUY is awaiting Deriv acknowledgement; duplicate BUY is blocked until resolved.";\n    }\n    if (["reconnecting", "retrying", "credential_error", "token_required", "waiting_for_condition"].includes(serverStatus) && serverReason) {\n      return serverReason;\n    }\n    if (state.lastExecutionError) return "Last execution error: " + state.lastExecutionError;\n    if (live?.met === true) return "Strategy condition is met, but no purchase acknowledgement has been received yet.";\n    if (live?.met === false) {\n      const detail = conditionStatusText(live);\n      return detail ? "Strategy condition not met: " + detail : "Strategy condition not met yet.";\n    }\n    return state.lastBlockingReason || "Strategy condition not met yet; waiting for a qualifying live tick.";\n  }\n\n  function publishNoPurchaseDiagnostic() {\n    if (!state.running) return;\n    const anchor = Number(state.lastRealPurchaseAt || state.runStartedAt || Date.now());\n    if (Date.now() - anchor < 60000) return;\n    const reason = String(noPurchaseReason() || "Execution is waiting for a qualifying entry.").slice(0, 320);\n    if (reason === state.lastDiagnosticReason) return;\n    state.lastDiagnosticReason = reason;\n    state.noPurchaseReason = reason;\n    appendJournal({\n      mode: "system",\n      state: "DIAGNOSTIC",\n      diagnostic: true,\n      reason,\n      message: "NO TRADE PURCHASED AFTER 60 SECONDS — " + reason,\n      stake: 0,\n      profit: 0,\n    });\n    window.dispatchEvent(new CustomEvent("derivadmin:direct-no-purchase", {\n      detail: { reason, at: new Date().toISOString(), visible_in_journal: true },\n    }));\n  }\n\n  function startNoPurchaseDiagnostics() {\n    clearInterval(state.noPurchaseTimer);\n    state.runStartedAt = Date.now();\n    state.lastRealPurchaseAt = 0;\n    state.lastConditionMetAt = 0;\n    state.lastBlockingReason = "Strategy condition not met yet; waiting for a qualifying live tick.";\n    state.lastDiagnosticReason = "";\n    state.noPurchaseReason = "";\n    state.noPurchaseTimer = setInterval(publishNoPurchaseDiagnostic, 5000);\n  }\n\n`;
engine = replaceOne(
  engine,
  `  function connectPublic() {`,
  diagnosticsHelpers + `  function connectPublic() {`,
  "60-second diagnostic helpers",
);

engine = replaceOne(
  engine,
  `    const route = activeExecutionRoute();\n    if (!route || !strategyMatches(history, route)) return;`,
  `    const route = activeExecutionRoute();\n    if (!route) {\n      state.lastBlockingReason = "No executable strategy route is currently available.";\n      return;\n    }\n    if (!strategyMatches(history, route)) {\n      const live = liveConditionSnapshot();\n      const detail = conditionStatusText(live);\n      state.lastBlockingReason = detail\n        ? "Strategy condition not met: " + detail\n        : "Strategy condition not met on " + symbol + ".";\n      return;\n    }\n    state.lastConditionMetAt = Date.now();\n    state.lastBlockingReason = "Strategy condition met on " + symbol + "; preparing proposal and BUY.";`,
  "condition qualification diagnostic",
);

engine = replaceOne(
  engine,
  `      appendJournal({\n        mode: "real",\n        state: "OPEN",`,
  `      state.lastRealPurchaseAt = Date.now();\n      state.noPurchaseReason = "";\n      state.lastDiagnosticReason = "";\n      state.lastExecutionError = "";\n      state.lastBlockingReason = "Purchase confirmed by Deriv.";\n      appendJournal({\n        mode: "real",\n        state: "OPEN",`,
  "purchase resets no-purchase timer",
);

engine = replaceOne(
  engine,
  `    ownershipWatch();\n    return true;`,
  `    ownershipWatch();\n    startNoPurchaseDiagnostics();\n    return true;`,
  "start no-purchase timer",
);

engine = replaceOne(
  engine,
  `    clearInterval(state.heartbeatTimer);\n    state.heartbeatTimer = null;`,
  `    clearInterval(state.heartbeatTimer);\n    state.heartbeatTimer = null;\n    clearInterval(state.noPurchaseTimer);\n    state.noPurchaseTimer = null;\n    state.noPurchaseReason = "";`,
  "stop diagnostic timer",
);

engine = replaceOne(
  engine,
  `    clear: clearLocalTrades,`,
  `    clear: clearLocalTrades,\n    clear_through: clearLocalTradesThrough,`,
  "cross-device clear export",
);

engine = replaceOne(
  engine,
  `        open_contracts: state.openContracts.size,\n        execution_ready: executionTransportReady(),\n        last_execution_error: String(state.lastExecutionError || ""),`,
  `        open_contracts: state.openContracts.size,\n        execution_ready: executionTransportReady(),\n        last_execution_error: String(state.lastExecutionError || ""),\n        no_purchase_reason: String(state.noPurchaseReason || ""),\n        last_blocking_reason: String(state.lastBlockingReason || ""),\n        no_purchase_since_ms: state.running ? Math.max(0, Date.now() - Number(state.lastRealPurchaseAt || state.runStartedAt || Date.now())) : 0,`,
  "diagnostic state export",
);

// ---------------------------------------------------------------------------
// Transactions: one renderer owns the panel even when there are zero rows.
// System diagnostics live in Journal only and can never become financial rows.
// ---------------------------------------------------------------------------
let ledger = read(ledgerPath);
ledger = replaceOne(
  ledger,
  `    for (const raw of rows) {\n      const row = normalizeContract(raw, "browser");`,
  `    for (const raw of rows) {\n      if (raw?.diagnostic === true || String(raw?.mode || "").toLowerCase() === "system") continue;\n      const row = normalizeContract(raw, "browser");`,
  "diagnostic rows excluded from transactions",
);
ledger = replaceOne(
  ledger,
  `    const rows = contracts();\n    if (!rows.length) { lastSignature = ""; connectObserver(); return; }\n    const panel = document.querySelector(".global-run-panel");`,
  `    const rows = contracts();\n    const panel = document.querySelector(".global-run-panel");`,
  "zero-row ledger keeps ownership",
);
ledger = replaceOne(
  ledger,
  `      body.innerHTML = \`<div class="transaction-table transaction-table-v6 unified-canonical-table-v10">\n        <div class="transaction-head transaction-head-v6"><span>Time / Market</span><span>Type</span><span>Exit digit</span><span>Buy price</span><span>Profit / Loss</span></div>\n        <div class="transaction-rows">\${rows.map(rowMarkup).join("")}</div>\n      </div>\`;`,
  `      body.innerHTML = \`<div class="transaction-table transaction-table-v6 unified-canonical-table-v10">\n        <div class="transaction-head transaction-head-v6"><span>Time / Market</span><span>Type</span><span>Exit digit</span><span>Buy price</span><span>Profit / Loss</span></div>\n        <div class="transaction-rows">\${rows.length ? rows.map(rowMarkup).join("") : '<div class="transaction-empty-v10">No transactions yet.</div>'}</div>\n      </div>\`;`,
  "stable empty transaction state",
);

// ---------------------------------------------------------------------------
// Run-panel: every device always reads account-global state. A remote Stop closes
// the local BUY fence. A remote Clear revision deletes only history at/before the
// server clear time. The no-purchase reason is kept visibly pinned in Journal.
// ---------------------------------------------------------------------------
let run = read(runPath);
run = replaceOne(
  run,
  `  const STATUS_URL = "/api/me/direct-execution/status";`,
  `  const STATUS_URL = "/api/me/runtime-sync";`,
  "account-global runtime status endpoint",
);
run = replaceOne(
  run,
  `    resetUntil: 0,`,
  `    resetUntil: 0,\n    diagnosticApplying: false,`,
  "run-panel synchronization state",
);

const runHelpers = `  function clearRevisionKey(managedId) {\n    return "derivadmin-clear-revision-v1:" + String(managedId || "default");\n  }\n\n  function applyHistoryRevision(payload) {\n    const revision = String(payload?.history_revision || "");\n    const managedId = String(payload?.managed_account_id || "");\n    if (!revision || !managedId) return;\n    const key = clearRevisionKey(managedId);\n    let seen = "";\n    try { seen = String(localStorage.getItem(key) || ""); } catch (_) {}\n    if (seen === revision) return;\n    try { engine()?.clear_through?.(revision); } catch (_) {}\n    try { window.DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6?.clearSnapshot?.(); } catch (_) {}\n    try { localStorage.setItem(key, revision); } catch (_) {}\n    state.resetUntil = Date.now() + 1200;\n    window.dispatchEvent(new CustomEvent("derivadmin:direct-reset-all", {\n      detail: { cross_device: true, history_revision: revision },\n    }));\n  }\n\n  function applyRemoteStop(payload) {\n    const stopped = payload?.hard_stop === true\n      || payload?.enabled === false\n      || String(payload?.owner || "").toLowerCase() === "stopped"\n      || ["take_profit", "stop_loss", "stopped", "manual_pause"].includes(String(payload?.execution_status || "").toLowerCase());\n    if (!stopped || !browserRunning()) return;\n    try { window.DERIVADMIN_DIRECT_HARD_STOP_FENCE_V1?.hard_stop?.(); } catch (_) {}\n    try { engine()?.stop?.(String(payload?.execution_status_reason || "Stopped on another logged-in device")); } catch (_) {}\n    state.userStopLatch = true;\n    state.serverActive = false;\n    state.serverOwner = "stopped";\n  }\n\n  function ensureJournalDiagnostic() {\n    const panel = document.querySelector(".global-run-panel");\n    const active = String(panel?.querySelector("[data-run-tab].active")?.dataset?.runTab || "");\n    const body = panel?.querySelector(".run-panel-body");\n    if (!panel || !body || active !== "journal") return;\n    const reason = String(engineState().no_purchase_reason || "").trim();\n    const existing = body.querySelector(".direct-no-purchase-diagnostic");\n    if (!reason) { existing?.remove(); return; }\n    const text = "NO TRADE PURCHASED AFTER 60 SECONDS — " + reason;\n    if (existing && existing.dataset.reason === reason) return;\n    state.diagnosticApplying = true;\n    try {\n      const node = existing || document.createElement("section");\n      node.className = "direct-no-purchase-diagnostic";\n      node.dataset.reason = reason;\n      node.setAttribute("role", "alert");\n      node.innerHTML = '<strong>Execution diagnosis</strong><p></p>';\n      node.querySelector("p").textContent = text;\n      if (!existing) body.prepend(node);\n    } finally { state.diagnosticApplying = false; }\n  }\n\n`;
run = replaceOne(
  run,
  `  async function readServerStatus() {`,
  runHelpers + `  async function readServerStatus() {`,
  "cross-device synchronization helpers",
);

run = replaceOne(
  run,
  `  async function readServerStatus() {\n    if (browserRunning()) {\n      state.serverActive = false;\n      state.serverOwner = "browser";\n      queueRender();\n      return;\n    }\n    try {`,
  `  async function readServerStatus() {\n    try {`,
  "running browser must still read global status",
);

run = replaceOne(
  run,
  `      const payload = await response.json();\n      const owner = String(payload?.owner || "stopped").toLowerCase();`,
  `      const payload = await response.json();\n      window.__DERIVADMIN_ACCOUNT_RUNTIME_SYNC_V1 = payload;\n      applyHistoryRevision(payload);\n      applyRemoteStop(payload);\n      const owner = String(payload?.owner || "stopped").toLowerCase();`,
  "consume global stop and clear state",
);

run = replaceOne(
  run,
  `    } catch (_) {\n      // Status is advisory. Never show a raw backend timeout to the user and never`,
  `      ensureJournalDiagnostic();\n    } catch (_) {\n      ensureJournalDiagnostic();\n      // Status is advisory. Never show a raw backend timeout to the user and never`,
  "journal diagnostic on status reconciliation",
);

run = replaceOne(
  run,
  `  window.addEventListener("derivadmin:direct-trade", queueRender);`,
  `  window.addEventListener("derivadmin:direct-trade", () => { queueRender(); ensureJournalDiagnostic(); });\n  window.addEventListener("derivadmin:direct-no-purchase", ensureJournalDiagnostic);`,
  "journal diagnostic event",
);

run = replaceOne(
  run,
  `  window.addEventListener("pageshow", readServerStatus);`,
  `  window.addEventListener("pageshow", readServerStatus);\n  document.addEventListener("foa:vps-live", readServerStatus);`,
  "realtime account-global reconciliation",
);

run = replaceOne(
  run,
  `      setTimeout(queueRender, 0);`,
  `      setTimeout(() => { queueRender(); ensureJournalDiagnostic(); }, 0);`,
  "journal tab immediate diagnostic",
);

run = replaceOne(
  run,
  `    .global-run-panel .run-panel-reset{min-height:30px!important;height:30px!important;padding:0 14px!important;font-size:11px!important}`, 
  `    .global-run-panel .direct-no-purchase-diagnostic{margin:10px!important;padding:12px!important;border:1px solid #ffb020!important;border-left:4px solid #ffb020!important;border-radius:8px!important;background:#251a05!important;color:#fff5db!important;box-shadow:0 8px 24px rgba(0,0,0,.25)!important}\n    .global-run-panel .direct-no-purchase-diagnostic strong{display:block!important;margin-bottom:6px!important;color:#ffd166!important;font-size:13px!important;text-transform:uppercase!important;letter-spacing:.04em!important}\n    .global-run-panel .direct-no-purchase-diagnostic p{margin:0!important;font-size:12px!important;line-height:1.45!important;font-weight:700!important;color:#fff!important}\n    .global-run-panel .transaction-empty-v10{grid-column:1/-1!important;display:flex!important;align-items:center!important;justify-content:center!important;min-height:220px!important;padding:24px!important;color:#d9e7f4!important;font-size:13px!important}\n\n    .global-run-panel .run-panel-reset{min-height:30px!important;height:30px!important;padding:0 14px!important;font-size:11px!important}`,
  "diagnostic and stable empty styles",
);

// Cache-bust final assets changed here.
let index = read(indexPath);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260820-runtime-safety-v20",
);
index = index.replace(
  /\/direct-transaction-ledger-v6\.js\?v=[^"']+/g,
  "/direct-transaction-ledger-v6.js?v=20260820-single-empty-v13",
);
index = index.replace(
  /\/direct-run-panel-authority-v6\.js\?v=[^"']+/g,
  "/direct-run-panel-authority-v6.js?v=20260820-cross-device-v11",
);

for (const [source, required, label] of [
  [engine, "NO TRADE PURCHASED AFTER 60 SECONDS", "60-second Journal diagnostic"],
  [engine, "clear_through: clearLocalTradesThrough", "cross-device history cutoff"],
  [engine, "secure session unavailable (HTTP ", "exact direct-session error"],
  [ledger, "transaction-empty-v10", "stable zero-row Transactions owner"],
  [ledger, "raw?.diagnostic === true", "Journal diagnostics excluded from Transactions"],
  [run, 'const STATUS_URL = "/api/me/runtime-sync";', "account-global status polling"],
  [run, "applyRemoteStop(payload)", "cross-device Stop propagation"],
  [run, "applyHistoryRevision(payload)", "cross-device Clear propagation"],
  [run, "direct-no-purchase-diagnostic", "visible Journal diagnosis"],
]) {
  if (!source.includes(required)) throw new Error(`runtime-safety-v2 invariant missing: ${label}`);
}
for (const required of [
  "/deriv-direct-execution-v2.js?v=20260820-runtime-safety-v20",
  "/direct-transaction-ledger-v6.js?v=20260820-single-empty-v13",
  "/direct-run-panel-authority-v6.js?v=20260820-cross-device-v11",
]) {
  if (!index.includes(required)) throw new Error(`runtime-safety-v2 cache marker missing: ${required}`);
}

write(enginePath, engine);
write(ledgerPath, ledger);
write(runPath, run);
write(indexPath, index);

console.log("Runtime safety v2 finalized: TP/SL/manual-only continuity, cross-device Stop/Clear, stable Transactions, exact 60-second Journal diagnostics");
