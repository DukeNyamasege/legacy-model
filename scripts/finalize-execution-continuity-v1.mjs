import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const ledgerPath = "dist/direct-transaction-ledger-v6.js";
const runPath = "dist/direct-run-panel-authority-v6.js";
const guardPath = "dist/direct-interaction-guard-v3.js";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`execution-continuity missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8");
}

function replaceOnce(text, before, after, label) {
  if (text.includes(after)) return text;
  const count = text.split(before).length - 1;
  if (count !== 1) throw new Error(`execution-continuity ${label}: expected one source match, got ${count}`);
  return text.replace(before, after);
}

// ---------------------------------------------------------------------------
// 1. Browser-direct execution continuity.
// ---------------------------------------------------------------------------
let engine = read(enginePath);

engine = replaceOnce(
  engine,
  "    keepaliveTimer: null,\n    prewarmTimer: null,",
  "    keepaliveTimer: null,\n    continuityTimer: null,\n    lastPublicMessageAt: Date.now(),\n    lastPrivateMessageAt: Date.now(),\n    lastTickAt: Date.now(),\n    lastOpenContractRepairAt: 0,\n    prewarmTimer: null,",
  "continuity state",
);

engine = replaceOnce(
  engine,
  "    try { message = JSON.parse(event.data); } catch (_) { return; }\n    const map = kind === \"public\" ? state.publicPending : state.privatePending;",
  "    try { message = JSON.parse(event.data); } catch (_) { return; }\n    const receivedAt = Date.now();\n    if (kind === \"public\") state.lastPublicMessageAt = receivedAt;\n    else state.lastPrivateMessageAt = receivedAt;\n    const map = kind === \"public\" ? state.publicPending : state.privatePending;",
  "websocket activity timestamps",
);

engine = replaceOnce(
  engine,
  "    if (message.msg_type === \"tick\" && message.tick) {\n      const symbol = String(message.tick.symbol || message.echo_req?.ticks || \"\").toUpperCase();",
  "    if (message.msg_type === \"tick\" && message.tick) {\n      state.lastTickAt = Date.now();\n      const symbol = String(message.tick.symbol || message.echo_req?.ticks || \"\").toUpperCase();",
  "live tick timestamp",
);

const sendNoWaitBlock = `  function sendNoWait(kind, payload) {\n    const ws = kind === \"public\" ? state.publicWs : state.privateWs;\n    if (!ws || ws.readyState !== WebSocket.OPEN) return false;\n    const reqId = kind === \"public\" ? ++state.publicReq : ++state.privateReq;\n    try { ws.send(JSON.stringify({ ...payload, req_id: reqId })); return true; } catch (_) { return false; }\n  }`;
const continuityHelpers = `${sendNoWaitBlock}\n\n  function restoreOpenContractSubscriptions(reason = \"continuity repair\") {\n    if (!state.running || state.ownerLost || !state.openContracts.size) return false;\n    if (state.privateWs?.readyState !== WebSocket.OPEN) return false;\n    const now = Date.now();\n    if (now - Number(state.lastOpenContractRepairAt || 0) < 1200) return false;\n    state.lastOpenContractRepairAt = now;\n    let restored = 0;\n    for (const contractId of state.openContracts.keys()) {\n      const numeric = Number(contractId);\n      if (!Number.isFinite(numeric) || numeric <= 0) continue;\n      if (sendNoWait(\"private\", { proposal_open_contract: 1, contract_id: numeric, subscribe: 1 })) restored += 1;\n    }\n    if (restored) updateStatus(\`Direct • ${"${reason}"} • reconciling ${"${restored}"} open contract${"${restored === 1 ? \"\" : \"s\"}"}\`);\n    return restored > 0;\n  }\n\n  function forceSocketReconnect(kind, reason) {\n    const isPublic = kind === \"public\";\n    const ws = isPublic ? state.publicWs : state.privateWs;\n    if (!ws) return;\n    updateStatus(\`Direct • ${"${reason}"} • reconnecting automatically\`);\n    try { ws.close(); } catch (_) {}\n    setTimeout(() => {\n      if (!state.running || state.ownerLost) return;\n      if (isPublic) {\n        if (state.publicWs === ws) state.publicWs = null;\n        state.publicConnectPromise = null;\n        connectPublic().catch(() => {});\n      } else {\n        if (state.privateWs === ws) state.privateWs = null;\n        state.privateConnectPromise = null;\n        connectPrivate().catch(() => {});\n      }\n    }, 250);\n  }\n\n  function continuityRepair() {\n    if (!state.running || state.ownerLost) return;\n    const now = Date.now();\n\n    if (state.publicWs?.readyState === WebSocket.OPEN) {\n      if (now - Number(state.lastTickAt || 0) > 15000) {\n        forceSocketReconnect(\"public\", \"market stream became stale\");\n      }\n    } else {\n      if (now - Number(state.lastPublicMessageAt || 0) > 15000) state.publicConnectPromise = null;\n      connectPublic().catch(() => {});\n    }\n\n    if (state.privateWs?.readyState === WebSocket.OPEN) {\n      if (state.openContracts.size) restoreOpenContractSubscriptions(\"settlement repair\");\n      if (now - Number(state.lastPrivateMessageAt || 0) > 45000) {\n        forceSocketReconnect(\"private\", \"secure trade stream became stale\");\n      }\n    } else {\n      if (now - Number(state.lastPrivateMessageAt || 0) > 15000) state.privateConnectPromise = null;\n      connectPrivate().catch(() => {});\n    }\n  }`;
engine = replaceOnce(engine, sendNoWaitBlock, continuityHelpers, "continuity helpers");

engine = replaceOnce(
  engine,
  "        state.publicConnectPromise = null;\n        state.subscribedMarkets.clear();\n        if (state.running) subscribeMarkets();\n        resolve(ws);",
  "        state.publicConnectPromise = null;\n        state.lastPublicMessageAt = Date.now();\n        state.lastTickAt = Date.now();\n        state.subscribedMarkets.clear();\n        if (state.running) subscribeMarkets();\n        resolve(ws);",
  "public reconnect activation",
);

engine = replaceOnce(
  engine,
  "          state.privateConnectPromise = null;\n          if (state.running && !state.ownerLost) updateStatus(\"Direct • connected to Deriv • analyzing live ticks\");\n          resolve(ws);",
  "          state.privateConnectPromise = null;\n          state.lastPrivateMessageAt = Date.now();\n          if (state.running && !state.ownerLost) {\n            updateStatus(\"Direct • connected to Deriv • analyzing live ticks\");\n            restoreOpenContractSubscriptions(\"secure session restored\");\n          }\n          resolve(ws);",
  "private reconnect restores open contracts",
);

// Reset is a history action only. It must never clear TP/SL accounting, recovery
// debt, loss streak state or an active Virtual Hook while trading continues.
engine = replaceOnce(
  engine,
  `  function clearLocalTrades() {\n    try { localStorage.removeItem(journalKey()); } catch (_) {}\n    state.sessionProfit = 0;\n    state.recoveryDebt = 0;\n    state.consecutiveLosses = 0;\n    state.virtualMode = false;\n    state.virtualWins = 0;\n    state.virtualPending = null;\n    state.currentStake = baseStake();\n    const panel = document.querySelector(\".global-run-panel\");\n    panel?.querySelectorAll(\".run-panel-stats b,.run-stat b\").forEach((node) => { node.textContent = \"0\"; });\n    window.dispatchEvent(new CustomEvent(\"derivadmin:direct-clear\"));\n  }`,
  `  function clearLocalTrades() {\n    try { localStorage.removeItem(journalKey()); } catch (_) {}\n    // Financial execution state deliberately survives Reset. Start/Stop, TP/SL,\n    // recovery debt and Virtual Hook are independent from transaction visibility.\n    window.dispatchEvent(new CustomEvent(\"derivadmin:direct-clear\"));\n  }`,
  "reset history only",
);

engine = replaceOnce(
  engine,
  "      if (state.recoveryDebt <= 0.009) {\n        state.recoveryDebt = 0;",
  "      state.consecutiveLosses = 0;\n      if (state.recoveryDebt <= 0.009) {\n        state.recoveryDebt = 0;",
  "real win breaks consecutive-loss streak",
);

engine = replaceOnce(
  engine,
  "    if (win && state.virtualWins >= required) {\n      state.virtualMode = false;\n      state.virtualWins = 0;\n      updateStatus(\"Direct • virtual protection cleared • waiting for real entry\");\n    }",
  "    if (win && state.virtualWins >= required) {\n      state.virtualMode = false;\n      state.virtualWins = 0;\n      // The configured virtual confirmation deliberately breaks the protected\n      // actual-loss streak and releases exactly the next qualifying real entry.\n      state.consecutiveLosses = 0;\n      updateStatus(\"Direct • virtual protection cleared • next qualifying trade returns to real recovery\");\n    }",
  "virtual exit releases real recovery",
);

engine = replaceOnce(
  engine,
  "    updateStatus(\"Direct • virtual protection observing next result\");\n  }\n\n  function wsErrorMessage(message) {",
  "    updateStatus(\"Direct • virtual protection observing next result\");\n  }\n\n  function virtualHookShouldProtect() {\n    const hook = state.strategy?.virtual_hook;\n    if (!state.strategy?.virtual_hook_enabled || hook?.enabled === false) return false;\n    const threshold = clampInt(hook?.enter_after_losses, 1, 50, 2);\n    return Boolean(state.virtualMode || state.consecutiveLosses >= threshold);\n  }\n\n  function wsErrorMessage(message) {",
  "virtual pre-buy guard",
);

engine = replaceOnce(
  engine,
  "    const route = activeExecutionRoute();\n    if (!route || !strategyMatches(history, route)) return;\n    if (state.virtualMode) beginVirtual(symbol, history, route);\n    else executeReal(symbol, history, route);",
  "    const route = activeExecutionRoute();\n    if (!route || !strategyMatches(history, route)) return;\n    // Final browser-side financial fence: after the configured number of\n    // consecutive ACTUAL losses, another real BUY is impossible until the\n    // configured consecutive zero-cost virtual wins have completed.\n    if (virtualHookShouldProtect()) {\n      state.virtualMode = true;\n      beginVirtual(symbol, history, route);\n    } else {\n      executeReal(symbol, history, route);\n    }",
  "virtual hook financial fence",
);

engine = replaceOnce(
  engine,
  "  state.keepaliveTimer = setInterval(() => {",
  "  state.continuityTimer = setInterval(continuityRepair, 2000);\n\n  state.keepaliveTimer = setInterval(() => {",
  "continuity watchdog timer",
);

engine = replaceOnce(
  engine,
  "      open_contracts: state.openContracts.size,\n      session_profit: state.sessionProfit,",
  "      open_contracts: state.openContracts.size,\n      continuity_repair: true,\n      last_tick_age_ms: Math.max(0, Date.now() - Number(state.lastTickAt || Date.now())),\n      session_profit: state.sessionProfit,",
  "continuity diagnostics",
);

engine = engine.replace(/const VERSION = \"20260818-browser-direct-v[^\"]+\";/, 'const VERSION = "20260818-browser-direct-v7-continuity";');
fs.writeFileSync(enginePath, engine);

// ---------------------------------------------------------------------------
// 2. Unified ledger: show virtual observations but keep financial KPIs actual-only.
// ---------------------------------------------------------------------------
let ledger = read(ledgerPath);
ledger = ledger.replaceAll("DIRECT_TRANSACTION_LEDGER_V9", "DIRECT_TRANSACTION_LEDGER_V10");
ledger = ledger.replaceAll("unified-ledger-snapshot-v9:", "unified-ledger-snapshot-v10:");
ledger = ledger.replaceAll("unified-transaction-row-v9", "unified-transaction-row-v10");
ledger = ledger.replaceAll("unified-canonical-table-v9", "unified-canonical-table-v10");
ledger = ledger.replaceAll('panel.dataset.directLedgerAuthority = "v9";', 'panel.dataset.directLedgerAuthority = "v10";');

ledger = replaceOnce(
  ledger,
  "  function normalizeContract(row, source) {\n    const id = String(row?.contract_id || row?.contractId || \"\").trim();\n    if (!id) return null;\n    const outcome = String(row?.outcome || \"\").toUpperCase();",
  "  function normalizeContract(row, source) {\n    const virtual = Boolean(row?.is_virtual) || String(row?.mode || \"\").toLowerCase() === \"virtual\";\n    const rawId = String(row?.contract_id || row?.contractId || row?.virtual_trade_id || \"\").trim();\n    const synthetic = virtual ? `virtual:${source}:${String(row?.at || row?.created_at || row?.opened_at || \"unknown\")}:${String(row?.symbol || row?.market || \"\")}` : \"\";\n    const id = rawId || synthetic;\n    if (!id) return null;\n    const outcome = String(row?.outcome || row?.result || \"\").replace(/^VIRTUAL_/, \"\").toUpperCase();",
  "virtual contract normalization",
);

ledger = replaceOnce(
  ledger,
  "      mode: String(row?.mode || \"real\"),\n      source,",
  "      mode: virtual ? \"virtual\" : String(row?.mode || \"real\"),\n      source,",
  "virtual mode normalization",
);

ledger = replaceOnce(
  ledger,
  "      stake: finite(row?.stake ?? row?.buy_price ?? row?.price, 0),\n      payout: row?.payout == null ? null : finite(row.payout, 0),\n      profit: finite(row?.profit, 0),\n      entry_spot: row?.entry_spot ?? row?.entry_tick ?? row?.entrySpot ?? row?.buy_spot ?? null,\n      exit_spot: row?.exit_spot ?? row?.exit_tick ?? row?.exitSpot ?? row?.sell_spot ?? null,",
  "      stake: virtual ? 0 : finite(row?.stake ?? row?.buy_price ?? row?.price, 0),\n      payout: virtual ? 0 : (row?.payout == null ? null : finite(row.payout, 0)),\n      profit: virtual ? 0 : finite(row?.profit, 0),\n      entry_spot: row?.entry_spot ?? row?.entry_tick ?? row?.entrySpot ?? row?.buy_spot ?? row?.entry_quote ?? null,\n      exit_spot: row?.exit_spot ?? row?.exit_tick ?? row?.exitSpot ?? row?.sell_spot ?? row?.exit_quote ?? null,",
  "virtual zero-cost economics",
);

ledger = replaceOnce(
  ledger,
  "    for (const raw of rows) {\n      if (String(raw?.mode || \"\") !== \"real\") continue;\n      const row = normalizeContract(raw, \"browser\");",
  "    for (const raw of rows) {\n      const row = normalizeContract(raw, \"browser\");",
  "browser virtual rows visible",
);

ledger = replaceOnce(
  ledger,
  "    return rows\n      .filter((row) => !row?.is_virtual)\n      .map((row) => normalizeContract(row, \"server\"))",
  "    return rows\n      .map((row) => normalizeContract(row, \"server\"))",
  "server virtual rows visible",
);

ledger = replaceOnce(
  ledger,
  "  function contracts() {\n    const key = accountKey();",
  "  function contracts() {\n    if (Date.now() < Number(window.__DERIVADMIN_RESET_PENDING_UNTIL || 0)) return [];\n    const key = accountKey();",
  "reset latch hides stale server rows",
);

ledger = replaceOnce(
  ledger,
  "    const pl = settled ? `${profit >= 0 ? \"+\" : \"\"}${money(profit)}` : \"OPEN\";\n    return `<div class=\"transaction-row transaction-row-v6 direct-local-transaction-row-v6 unified-transaction-row-v10\"",
  "    const virtual = String(row?.mode || \"\").toLowerCase() === \"virtual\";\n    const pl = virtual ? `VIRTUAL ${String(row.outcome || \"OBSERVING\").toUpperCase()}` : (settled ? `${profit >= 0 ? \"+\" : \"\"}${money(profit)}` : \"OPEN\");\n    const shownType = virtual ? `VIRTUAL · ${typeLabel(row)}` : typeLabel(row);\n    return `<div class=\"transaction-row transaction-row-v6 direct-local-transaction-row-v6 unified-transaction-row-v10 ${virtual ? \"virtual-observation\" : \"\"}\"",
  "virtual row presentation prelude",
);

ledger = replaceOnce(
  ledger,
  "      <span class=\"tx-type\"><b>${esc(typeLabel(row))}</b></span>",
  "      <span class=\"tx-type\"><b>${esc(shownType)}</b></span>",
  "virtual row type label",
);

ledger = replaceOnce(
  ledger,
  "      <strong class=\"${settled ? (profit >= 0 ? \"positive\" : \"negative\") : \"muted\"}\">${esc(pl)}</strong>",
  "      <strong class=\"${virtual ? (String(row.outcome || \"\").toUpperCase() === \"WIN\" ? \"positive\" : \"negative\") : (settled ? (profit >= 0 ? \"positive\" : \"negative\") : \"muted\")}\">${esc(pl)}</strong>",
  "virtual row outcome color",
);

ledger = replaceOnce(
  ledger,
  "    for (const row of rows) {\n      const stake = Math.max(0, finite(row.stake, 0));",
  "    const actualRows = rows.filter((row) => String(row?.mode || \"real\").toLowerCase() !== \"virtual\");\n    for (const row of actualRows) {\n      const stake = Math.max(0, finite(row.stake, 0));",
  "actual-only financial KPI loop",
);

ledger = replaceOnce(
  ledger,
  "    return { totalStake, totalPayout, profit, wins, losses, runs: rows.length };",
  "    return { totalStake, totalPayout, profit, wins, losses, runs: actualRows.length };",
  "actual-only run count",
);

ledger = replaceOnce(
  ledger,
  "    if (!rows.length) { lastSignature = \"\"; connectObserver(); return; }",
  "    if (!rows.length) {\n      const panel = document.querySelector(\".global-run-panel\");\n      const body = panel?.querySelector(\".run-panel-body\");\n      const summary = panel?.querySelector(\".run-panel-stats\");\n      if (body) body.innerHTML = `<div class=\"transaction-table transaction-table-v6 unified-canonical-table-v10\"><div class=\"transaction-head transaction-head-v6\"><span>Time / Market</span><span>Type</span><span>Entry / Exit</span><span>Buy price</span><span>Profit / Loss</span></div><div class=\"transaction-rows\"></div></div>`;\n      if (summary) summary.innerHTML = statsMarkup(stats([]));\n      lastSignature = \"\";\n      connectObserver();\n      return;\n    }",
  "empty ledger clears immediately",
);

ledger += `\n/* execution-continuity-v1 */\n`;
fs.writeFileSync(ledgerPath, ledger);

// ---------------------------------------------------------------------------
// 3. Reset is truly one-click and synchronous in the UI.
// ---------------------------------------------------------------------------
let run = read(runPath);
run = replaceOnce(
  run,
  "  function resetTrades() {\n    if (!window.confirm(\"Do you want to reset all trades?\")) return;\n    state.resetUntil = Date.now() + 6000;\n    try { engine()?.clear?.(); } catch (_) {}",
  "  function resetTrades() {\n    const resetUntil = Date.now() + 15000;\n    state.resetUntil = resetUntil;\n    window.__DERIVADMIN_RESET_PENDING_UNTIL = resetUntil;\n    try { engine()?.clear?.(); } catch (_) {}",
  "one-click reset",
);
run = replaceOnce(
  run,
  "      if (xhr.status >= 200 && xhr.status < 300) {\n        state.resetUntil = 0;\n        try { window.FOA_FINAL_UI?.refresh?.({ quiet: true }); } catch (_) {}",
  "      if (xhr.status >= 200 && xhr.status < 300) {\n        state.resetUntil = 0;\n        window.__DERIVADMIN_RESET_PENDING_UNTIL = 0;\n        try { window.FOA_FINAL_UI?.refresh?.({ quiet: true }); } catch (_) {}",
  "server reset acknowledgement",
);
fs.writeFileSync(runPath, run);

// ---------------------------------------------------------------------------
// 4. Start confirmation survives shell re-render; one human start flow is enough.
// ---------------------------------------------------------------------------
let guard = read(guardPath);
guard = replaceOnce(
  guard,
  "  async function confirmStart(target) {",
  `  function replacementStartTarget(target) {\n    if (target?.matches?.(\"[data-run-start]\")) return document.querySelector(\".global-run-panel [data-run-start]\");\n    if (target?.matches?.(\"[data-builder-trade]\")) return document.querySelector(\"[data-builder-trade]\");\n    if (target?.matches?.(\"[data-ready-trade]\")) return document.querySelector(\"[data-ready-trade]\");\n    if (target?.matches?.(\"[data-trade-now-selected]\")) return document.querySelector(\"[data-trade-now-selected]\");\n    if (target?.matches?.(\"[data-start-trading]\")) return document.querySelector(\"[data-start-trading]\");\n    return null;\n  }\n\n  async function confirmStart(target) {`,
  "stable start target resolver",
);
guard = replaceOnce(
  guard,
  "    if (!ok || !target.isConnected) return;\n    approvedOnce.add(target);\n    target.click();",
  "    if (!ok) return;\n    const liveTarget = target.isConnected ? target : replacementStartTarget(target);\n    if (!liveTarget) return;\n    approvedOnce.add(liveTarget);\n    liveTarget.click();",
  "one start flow after modal rerender",
);
fs.writeFileSync(guardPath, guard);

console.log("Execution continuity v1 finalized: socket self-repair, open-contract reconciliation, immediate Reset, one-flow Start and visible Virtual Hook");
