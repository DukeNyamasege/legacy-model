import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const ledgerPath = "dist/direct-transaction-ledger-v6.js";
const runPath = "dist/direct-run-panel-authority-v6.js";
const guardPath = "dist/direct-interaction-guard-v3.js";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`execution-continuity missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}
function replaceOne(text, before, after, label) {
  const count = text.split(before).length - 1;
  if (count !== 1) throw new Error(`execution-continuity ${label}: expected 1 match, got ${count}`);
  return text.replace(before, after);
}
function replaceRe(text, pattern, replacement, label) {
  if (!pattern.test(text)) throw new Error(`execution-continuity ${label}: source shape missing`);
  pattern.lastIndex = 0;
  return text.replace(pattern, replacement);
}

// Browser runtime continuity + final financial fence.
let engine = read(enginePath);
engine = replaceRe(
  engine,
  /(\n\s*)keepaliveTimer:\s*null,\n(\s*)prewarmTimer:\s*null,/,
  (_m, nl, indent) => `${nl}keepaliveTimer: null,\n${indent}continuityTimer: null,\n${indent}lastPublicMessageAt: Date.now(),\n${indent}lastPrivateMessageAt: Date.now(),\n${indent}lastTickAt: Date.now(),\n${indent}lastOpenContractRepairAt: 0,\n${indent}prewarmTimer: null,`,
  "continuity state",
);
engine = replaceOne(
  engine,
  '    try { payload = JSON.parse(String(event.data || "{}")); } catch (_) { return; }\n    const reqId = Number(payload.req_id || 0);',
  '    try { payload = JSON.parse(String(event.data || "{}")); } catch (_) { return; }\n    const receivedAt = Date.now();\n    if (kind === "public") state.lastPublicMessageAt = receivedAt;\n    else state.lastPrivateMessageAt = receivedAt;\n    const reqId = Number(payload.req_id || 0);',
  "websocket activity timestamps",
);
engine = replaceOne(
  engine,
  '    if (kind === "public" && payload.tick) {\n      const symbol = String(payload.tick.symbol || payload.echo_req?.ticks || "").toUpperCase();',
  '    if (kind === "public" && payload.tick) {\n      state.lastTickAt = Date.now();\n      const symbol = String(payload.tick.symbol || payload.echo_req?.ticks || "").toUpperCase();',
  "live tick timestamp",
);

const helpers = `
  function restoreOpenContractSubscriptions(reason = "continuity repair") {
    if (!state.running || state.ownerLost || !state.openContracts.size || state.privateWs?.readyState !== WebSocket.OPEN) return false;
    const now = Date.now();
    if (now - Number(state.lastOpenContractRepairAt || 0) < 1200) return false;
    state.lastOpenContractRepairAt = now;
    let restored = 0;
    for (const contractId of state.openContracts.keys()) {
      const numeric = Number(contractId);
      if (!Number.isFinite(numeric) || numeric <= 0) continue;
      if (sendNoWait("private", { proposal_open_contract: 1, contract_id: numeric, subscribe: 1 })) restored += 1;
    }
    if (restored) updateStatus(\`Direct • \${reason} • reconciling \${restored} open contract\${restored === 1 ? "" : "s"}\`);
    return restored > 0;
  }

  function forceSocketReconnect(kind, reason) {
    const isPublic = kind === "public";
    const ws = isPublic ? state.publicWs : state.privateWs;
    if (!ws) return;
    updateStatus(\`Direct • \${reason} • reconnecting automatically\`);
    try { ws.close(); } catch (_) {}
    setTimeout(() => {
      if (!state.running || state.ownerLost) return;
      if (isPublic) {
        if (state.publicWs === ws) state.publicWs = null;
        state.publicConnectPromise = null;
        connectPublic().catch(() => {});
      } else {
        if (state.privateWs === ws) state.privateWs = null;
        state.privateConnectPromise = null;
        connectPrivate().catch(() => {});
      }
    }, 250);
  }

  function continuityRepair() {
    if (!state.running || state.ownerLost) return;
    const now = Date.now();
    if (state.publicWs?.readyState === WebSocket.OPEN) {
      if (now - Number(state.lastTickAt || 0) > 15000) forceSocketReconnect("public", "market stream became stale");
    } else {
      if (now - Number(state.lastPublicMessageAt || 0) > 15000) state.publicConnectPromise = null;
      connectPublic().catch(() => {});
    }
    if (state.privateWs?.readyState === WebSocket.OPEN) {
      if (state.openContracts.size) restoreOpenContractSubscriptions("settlement repair");
      if (now - Number(state.lastPrivateMessageAt || 0) > 45000) forceSocketReconnect("private", "secure trade stream became stale");
    } else {
      if (now - Number(state.lastPrivateMessageAt || 0) > 15000) state.privateConnectPromise = null;
      connectPrivate().catch(() => {});
    }
  }

`;
engine = replaceOne(engine, "  function connectPublic() {", helpers + "  function connectPublic() {", "continuity helpers");
engine = replaceOne(
  engine,
  "      ws.onopen = () => {\n        state.publicConnectPromise = null;\n        state.subscribedMarkets.clear();",
  "      ws.onopen = () => {\n        state.publicConnectPromise = null;\n        state.lastPublicMessageAt = Date.now();\n        state.lastTickAt = Date.now();\n        state.subscribedMarkets.clear();",
  "public reconnect activation",
);
engine = replaceOne(
  engine,
  '          state.privateConnectPromise = null;\n          if (state.running && !state.ownerLost) updateStatus("Direct • connected to Deriv • analyzing live ticks");',
  '          state.privateConnectPromise = null;\n          state.lastPrivateMessageAt = Date.now();\n          if (state.running && !state.ownerLost) {\n            updateStatus("Direct • connected to Deriv • analyzing live ticks");\n            restoreOpenContractSubscriptions("secure session restored");\n          }',
  "private reconnect restore",
);

// Reset is a history action only. Financial execution state deliberately survives Reset.
// reset history only
{
  const a = engine.indexOf("  function clearLocalTrades() {");
  const b = engine.indexOf("\n  function normalizeCondition", a);
  if (a < 0 || b < 0) throw new Error("execution-continuity reset history only: clearLocalTrades missing");
  const clear = engine.slice(a, b);
  for (const forbidden of ["state.sessionProfit = 0", "state.recoveryDebt = 0", "state.consecutiveLosses = 0", "state.virtualMode = false"]) {
    if (clear.includes(forbidden)) throw new Error(`execution-continuity Reset financial mutation: ${forbidden}`);
  }
}

engine = replaceOne(
  engine,
  "    } else {\n      state.recoveryDebt = Math.max(0, state.recoveryDebt - profit);",
  "    } else {\n      // Every real win breaks the consecutive ACTUAL-loss streak even while debt remains.\n      state.consecutiveLosses = 0;\n      state.recoveryDebt = Math.max(0, state.recoveryDebt - profit);",
  "real win loss-streak reset",
);
engine = replaceOne(
  engine,
  "    if (won && state.virtualWins >= needed) {\n      state.virtualMode = false;\n      state.virtualWins = 0;\n    }",
  '    if (won && state.virtualWins >= needed) {\n      state.virtualMode = false;\n      state.virtualWins = 0;\n      state.consecutiveLosses = 0;\n      updateStatus("Direct • virtual protection cleared • next qualifying trade returns to real recovery");\n    }',
  "virtual exit recovery release",
);

const hook = `  function virtualHookShouldProtect() {
    const settings = state.strategy?.virtual_hook;
    if (!state.strategy?.virtual_hook_enabled || settings?.enabled === false) return false;
    const threshold = clampInt(settings?.enter_after_losses, 1, 50, 2);
    return Boolean(state.virtualMode || state.consecutiveLosses >= threshold);
  }

`;
engine = replaceOne(engine, "  function onTick(symbol, tick) {", hook + "  function onTick(symbol, tick) {", "virtual pre-buy guard");
engine = replaceOne(
  engine,
  '    const route = activeExecutionRoute();\n    if (!route || !strategyMatches(history, route)) return;\n    if (state.virtualMode) beginVirtual(symbol, history, route);\n    else executeReal(symbol, history, route);',
  '    const route = activeExecutionRoute();\n    if (!route || !strategyMatches(history, route)) return;\n    // Final browser-side financial fence: after consecutive ACTUAL losses, another real BUY is impossible\n    // until configured zero-cost virtual wins release the next qualifying recovery entry.\n    if (virtualHookShouldProtect()) {\n      state.virtualMode = true;\n      beginVirtual(symbol, history, route);\n    } else executeReal(symbol, history, route);',
  "virtual hook financial fence",
);
engine = replaceOne(
  engine,
  '  window.addEventListener("online", () => {',
  '  state.continuityTimer = setInterval(continuityRepair, 2000);\n\n  window.addEventListener("online", () => {',
  "continuity watchdog timer",
);
engine = replaceOne(
  engine,
  "        open_contracts: state.openContracts.size,",
  "        open_contracts: state.openContracts.size,\n        continuity_repair: true,\n        last_tick_age_ms: Math.max(0, Date.now() - Number(state.lastTickAt || Date.now())),",
  "continuity diagnostics",
);
engine = replaceRe(
  engine,
  /const VERSION = "[^"]*browser-direct[^"]*";/,
  'const VERSION = "20260818-browser-direct-v7-continuity";',
  "browser runtime version",
);
for (const required of [
  "restoreOpenContractSubscriptions", "continuityRepair", "forceSocketReconnect",
  "proposal_open_contract: 1, contract_id: numeric, subscribe: 1",
  "state.continuityTimer = setInterval(continuityRepair, 2000)",
  "market stream became stale", "secure trade stream became stale", "settlement repair",
  "virtualHookShouldProtect", "last_tick_age_ms",
]) if (!engine.includes(required)) throw new Error(`execution-continuity engine invariant missing: ${required}`);
fs.writeFileSync(enginePath, engine);

// Unified transaction ledger: virtual rows visible, $0 economics, actual-only KPIs.
// VIRTUAL · ${typeLabel(row)}
// VIRTUAL ${String(row.outcome
let ledger = read(ledgerPath)
  .replaceAll("DIRECT_TRANSACTION_LEDGER_V9", "DIRECT_TRANSACTION_LEDGER_V10")
  .replaceAll("unified-ledger-snapshot-v9:", "unified-ledger-snapshot-v10:")
  .replaceAll("unified-transaction-row-v9", "unified-transaction-row-v10")
  .replaceAll("unified-canonical-table-v9", "unified-canonical-table-v10")
  .replaceAll('panel.dataset.directLedgerAuthority = "v9";', 'panel.dataset.directLedgerAuthority = "v10";');

ledger = replaceOne(
  ledger,
  '    const id = String(row?.contract_id || row?.contractId || "").trim();\n    if (!id) return null;\n    const outcome = String(row?.outcome || "").toUpperCase();',
  '    const virtual = Boolean(row?.is_virtual) || String(row?.mode || "").toLowerCase() === "virtual";\n    const rawId = String(row?.contract_id || row?.contractId || row?.virtual_trade_id || "").trim();\n    const id = rawId || (virtual ? `virtual:${source}:${String(row?.at || row?.created_at || row?.opened_at || "unknown")}:${String(row?.symbol || row?.market || "")}` : "");\n    if (!id) return null;\n    const outcome = String(row?.outcome || row?.result || "").replace(/^VIRTUAL_/, "").toUpperCase();',
  "virtual contract normalization",
);
ledger = replaceOne(ledger, '      mode: String(row?.mode || "real"),', '      mode: virtual ? "virtual" : String(row?.mode || "real"),', "virtual mode");
ledger = replaceOne(
  ledger,
  '      stake: finite(row?.stake ?? row?.buy_price ?? row?.price, 0),\n      payout: row?.payout == null ? null : finite(row.payout, 0),\n      profit: finite(row?.profit, 0),',
  '      stake: virtual ? 0 : finite(row?.stake ?? row?.buy_price ?? row?.price, 0),\n      payout: virtual ? 0 : (row?.payout == null ? null : finite(row.payout, 0)),\n      profit: virtual ? 0 : finite(row?.profit, 0),',
  "virtual zero-cost economics",
);
ledger = replaceOne(ledger, '      entry_spot: row?.entry_spot ?? row?.entry_tick ?? row?.entrySpot ?? row?.buy_spot ?? null,', '      entry_spot: row?.entry_spot ?? row?.entry_tick ?? row?.entrySpot ?? row?.buy_spot ?? row?.entry_quote ?? null,', "virtual entry spot");
ledger = replaceOne(ledger, '      exit_spot: row?.exit_spot ?? row?.exit_tick ?? row?.exitSpot ?? row?.sell_spot ?? null,', '      exit_spot: row?.exit_spot ?? row?.exit_tick ?? row?.exitSpot ?? row?.sell_spot ?? row?.exit_quote ?? null,', "virtual exit spot");
ledger = replaceOne(ledger, '      opened_at: row?.opened_at || row?.purchase_time || row?.provider_purchase_time || row?.at || "",', '      entry_digit: row?.entry_digit ?? null,\n      actual_last_digit: row?.actual_last_digit ?? row?.exit_digit ?? null,\n      opened_at: row?.opened_at || row?.purchase_time || row?.provider_purchase_time || row?.at || "",', "explicit settlement digits");
ledger = replaceOne(ledger, '    for (const raw of rows) {\n      if (String(raw?.mode || "") !== "real") continue;', '    for (const raw of rows) {', "browser virtual rows");
ledger = replaceOne(ledger, '    return rows\n      .filter((row) => !row?.is_virtual)\n      .map((row) => normalizeContract(row, "server"))', '    return rows\n      .map((row) => normalizeContract(row, "server"))', "server virtual rows");
ledger = replaceOne(ledger, "  function contracts() {\n    const key = accountKey();", '  function contracts() {\n    if (Date.now() < Number(window.__DERIVADMIN_RESET_PENDING_UNTIL || 0)) return [];\n    const key = accountKey();', "reset latch");
ledger = replaceOne(
  ledger,
  '  function money(value) { return `${finite(value, 0).toFixed(2)} USD`; }\n  function isSettled(row) { return String(row?.state || "").toUpperCase() === "SETTLED" || Boolean(row?.outcome); }',
  '  function money(value) { return `${finite(value, 0).toFixed(2)} USD`; }\n  function spotDigit(value, symbol = "", explicit = null) {\n    const explicitDigit = Number(explicit);\n    if (Number.isInteger(explicitDigit) && explicitDigit >= 0 && explicitDigit <= 9) return String(explicitDigit);\n    const precisionDigit = window.DERIVADMIN_DIRECT_PIP_PRECISION_V1?.last_digit?.(symbol, value);\n    if (Number.isInteger(precisionDigit) && precisionDigit >= 0 && precisionDigit <= 9) return String(precisionDigit);\n    if (value === null || value === undefined || value === "") return null;\n    const digits = String(value).trim().replace(/[^0-9]/g, "");\n    return digits ? digits[digits.length - 1] : null;\n  }\n  function isSettled(row) { return String(row?.state || "").toUpperCase() === "SETTLED" || Boolean(row?.outcome); }',
  "entry exit digit helper",
);
ledger = replaceOne(
  ledger,
  '    const entry = row.entry_spot ?? "—";\n    const exit = settled ? (row.exit_spot ?? "—") : "OPEN";',
  '    const entry = spotDigit(row.entry_spot) ?? "—";\n    const exit = settled ? (spotDigit(row.exit_spot) ?? "—") : "OPEN";',
  "entry exit exact digit display",
);
ledger = replaceOne(ledger, "spotDigit(row.entry_spot)", "spotDigit(row.entry_spot, row.symbol, row.entry_digit)", "entry provider digit");
ledger = replaceOne(ledger, "spotDigit(row.exit_spot)", "spotDigit(row.exit_spot, row.symbol, row.actual_last_digit)", "exit provider digit");
ledger = replaceOne(
  ledger,
  '    const pl = settled ? `${profit >= 0 ? "+" : ""}${money(profit)}` : "OPEN";',
  '    const virtual = String(row?.mode || "").toLowerCase() === "virtual";\n    const pl = virtual ? `VIRTUAL ${String(row.outcome || "OBSERVING").toUpperCase()}` : (settled ? `${profit >= 0 ? "+" : ""}${money(profit)}` : "OPEN");\n    const shownType = virtual ? `VIRTUAL · ${typeLabel(row)}` : typeLabel(row);',
  "virtual row presentation",
);
ledger = replaceOne(ledger, 'unified-transaction-row-v10" data-direct-contract-id=', 'unified-transaction-row-v10 ${virtual ? "virtual-observation" : ""}" data-direct-contract-id=', "virtual row class");
ledger = replaceOne(ledger, '<span class="tx-type"><b>${esc(typeLabel(row))}</b></span>', '<span class="tx-type"><b>${esc(shownType)}</b></span>', "virtual row type");
ledger = replaceOne(ledger, '<strong class="${settled ? (profit >= 0 ? "positive" : "negative") : "muted"}">${esc(pl)}</strong>', '<strong class="${virtual ? (String(row.outcome || "").toUpperCase() === "WIN" ? "positive" : "negative") : (settled ? (profit >= 0 ? "positive" : "negative") : "muted")}">${esc(pl)}</strong>', "virtual outcome color");
ledger = replaceOne(ledger, "    for (const row of rows) {\n      const stake = Math.max(0, finite(row.stake, 0));", '    const actualRows = rows.filter((row) => String(row?.mode || "real").toLowerCase() !== "virtual");\n    for (const row of actualRows) {\n      const stake = Math.max(0, finite(row.stake, 0));', "actual-only financial KPI loop");
ledger = replaceOne(ledger, "    return { totalStake, totalPayout, profit, wins, losses, runs: rows.length };", "    return { totalStake, totalPayout, profit, wins, losses, runs: actualRows.length };", "actual-only run count");
ledger = replaceOne(
  ledger,
  '    if (!rows.length) { lastSignature = ""; connectObserver(); return; }',
  '    if (!rows.length) {\n      const panel = document.querySelector(".global-run-panel");\n      const body = panel?.querySelector(".run-panel-body");\n      const summary = panel?.querySelector(".run-panel-stats");\n      if (body) body.innerHTML = `<div class="transaction-table transaction-table-v6 unified-canonical-table-v10"><div class="transaction-head transaction-head-v6"><span>Time / Market</span><span>Type</span><span>Entry / Exit</span><span>Buy price</span><span>Profit / Loss</span></div><div class="transaction-rows"></div></div>`;\n      if (summary) summary.innerHTML = statsMarkup(stats([]));\n      lastSignature = ""; connectObserver(); return;\n    }',
  "empty ledger clears immediately",
);
ledger += "\n/* execution-continuity-v1 */\n";
fs.writeFileSync(ledgerPath, ledger);

// One-click history Reset; execution state remains untouched.
let run = read(runPath);
run = replaceOne(
  run,
  '  function resetTrades() {\n    if (!window.confirm("Do you want to reset all trades?")) return;\n    state.resetUntil = Date.now() + 6000;\n    try { engine()?.clear?.(); } catch (_) {}',
  '  function resetTrades() {\n    const resetUntil = Date.now() + 15000;\n    state.resetUntil = resetUntil;\n    window.__DERIVADMIN_RESET_PENDING_UNTIL = resetUntil;\n    try { engine()?.clear?.(); } catch (_) {}',
  "one-click reset",
);
run = replaceOne(
  run,
  '      if (xhr.status >= 200 && xhr.status < 300) {\n        state.resetUntil = 0;\n        try { window.FOA_FINAL_UI?.refresh?.({ quiet: true }); } catch (_) {}',
  '      if (xhr.status >= 200 && xhr.status < 300) {\n        state.resetUntil = 0;\n        window.__DERIVADMIN_RESET_PENDING_UNTIL = 0;\n        try { window.FOA_FINAL_UI?.refresh?.({ quiet: true }); } catch (_) {}',
  "reset acknowledgement",
);
fs.writeFileSync(runPath, run);

// Start confirmation survives shell rerender.
let guard = read(guardPath);
guard = replaceOne(
  guard,
  "  async function confirmStart(target) {",
  '  function replacementStartTarget(target) {\n    if (target?.matches?.("[data-run-start]")) return document.querySelector(".global-run-panel [data-run-start]");\n    if (target?.matches?.("[data-builder-trade]")) return document.querySelector("[data-builder-trade]");\n    if (target?.matches?.("[data-ready-trade]")) return document.querySelector("[data-ready-trade]");\n    if (target?.matches?.("[data-trade-now-selected]")) return document.querySelector("[data-trade-now-selected]");\n    if (target?.matches?.("[data-start-trading]")) return document.querySelector("[data-start-trading]");\n    return null;\n  }\n\n  async function confirmStart(target) {',
  "stable start target resolver",
);
guard = replaceOne(
  guard,
  "    if (!ok || !target.isConnected) return;\n    approvedOnce.add(target);\n    target.click();",
  "    if (!ok) return;\n    const liveTarget = target.isConnected ? target : replacementStartTarget(target);\n    if (!liveTarget) return;\n    approvedOnce.add(liveTarget);\n    liveTarget.click();",
  "one start flow after modal rerender",
);
fs.writeFileSync(guardPath, guard);

console.log("Execution continuity v1 finalized: socket self-repair, open-contract reconciliation, immediate Reset, one-flow Start and visible Virtual Hook");
