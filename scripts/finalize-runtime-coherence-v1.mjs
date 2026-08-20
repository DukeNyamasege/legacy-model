import fs from "node:fs";

const shellPath = "dist/final-ui-shell-v2.js";
const premiumPath = "dist/final-premium-6f3.js";
const enginePath = "dist/deriv-direct-execution-v2.js";
const runPath = "dist/direct-run-panel-authority-v6.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`runtime-coherence missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) {
    throw new Error(`runtime-coherence ${label}: expected 1 source match or installed shape, got ${count}`);
  }
  return source.replace(before, after);
}

// ---------------------------------------------------------------------------
// 1. Exactly one Transactions renderer.
// The final shell may own Summary/Journal, but when the direct ledger is loaded it
// must never rewrite the same Transactions body/stats that the ledger owns.
// ---------------------------------------------------------------------------
let shell = read(shellPath);
const shellBefore = `    const body = panel.querySelector(".run-panel-body");\n    if (body) body.innerHTML = runPanelContent(activeTab, stats, currency, running);\n    const summary = panel.querySelector(".run-panel-stats");\n    if (summary) summary.innerHTML = runPanelStatsMarkup(stats, currency);`;
const shellAfter = `    const body = panel.querySelector(".run-panel-body");\n    const summary = panel.querySelector(".run-panel-stats");\n    const directLedgerOwnsTransactions = activeTab === "transactions"\n      && Boolean(window.DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6);\n    if (!directLedgerOwnsTransactions) {\n      if (body) body.innerHTML = runPanelContent(activeTab, stats, currency, running);\n      if (summary) summary.innerHTML = runPanelStatsMarkup(stats, currency);\n    } else {\n      queueMicrotask(() => {\n        try { window.DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6?.refresh?.(); } catch (_) {}\n      });\n    }`;
shell = replaceOne(shell, shellBefore, shellAfter, "single Transactions DOM authority");
if (!shell.includes("directLedgerOwnsTransactions")) {
  throw new Error("runtime-coherence single Transactions renderer invariant missing");
}
if (shell.includes(shellBefore)) {
  throw new Error("runtime-coherence unconditional Transactions renderer survived finalization");
}
write(shellPath, shell);

// ---------------------------------------------------------------------------
// 2. Authenticated Deriv socket recovery and observable purchase failures.
// Six seconds was short enough to close a still-establishing provider socket. The
// browser heartbeat is independent, so a 15s connect window is safe while the
// fresh browser lease remains authoritative.
// ---------------------------------------------------------------------------
let engine = read(enginePath);
engine = replaceOne(
  engine,
  `        const timer = setTimeout(() => { try { ws.close(); } catch (_) {} reject(new Error("secure session connection delayed")); }, 6000);`,
  `        const timer = setTimeout(() => { try { ws.close(); } catch (_) {} reject(new Error("secure session connection delayed after 15s")); }, 15000);`,
  "private WebSocket establishment window",
);

engine = replaceOne(
  engine,
  `        ws.onmessage = (event) => handleWsMessage("private", event);\n        ws.onerror = () => {};\n        ws.onclose = () => {\n          clearTimeout(timer);`,
  `        ws.onmessage = (event) => handleWsMessage("private", event);\n        ws.onerror = () => {\n          state.lastExecutionError = "Authenticated Deriv WebSocket reported a connection error";\n        };\n        ws.onclose = (event) => {\n          clearTimeout(timer);\n          const code = Number(event?.code || 0);\n          const reason = String(event?.reason || "").replace(/otp=[^&\\s]+/gi, "otp=[redacted]").slice(0, 120);\n          state.lastExecutionError = "Authenticated Deriv WebSocket closed" + (code ? " code " + code : "") + (reason ? ": " + reason : "");`,
  "private WebSocket close diagnostics",
);

engine = replaceOne(
  engine,
  `    } catch (error) {\n      if (state.running && !state.ownerLost) updateStatus("Direct • analyzing • last entry was not purchased");\n    } finally {`,
  `    } catch (error) {\n      const message = String(error?.message || error || "Deriv purchase failed")\n        .replace(/otp=[^&\\s]+/gi, "otp=[redacted]")\n        .replace(/bearer\\s+[^\\s]+/gi, "Bearer [redacted]")\n        .slice(0, 180);\n      state.lastExecutionError = message;\n      window.dispatchEvent(new CustomEvent("derivadmin:direct-execution-error", {\n        detail: { message, symbol, at: new Date().toISOString() },\n      }));\n      if (state.running && !state.ownerLost) updateStatus("Direct • purchase not completed • " + message);\n    } finally {`,
  "provider purchase error visibility",
);

// ---------------------------------------------------------------------------
// 3. One public-history owner and one financial-ready gate.
// direct-financial-fence owns the 1,001-tick hydration for every live subscription.
// The execution engine must not react to that provider history response by sending
// another subscribe request, otherwise the fence starts another hydration cycle and
// BUY remains blocked by hydrationPending. Synthetic hydration ticks fill history
// only; they are never entry signals. Live market subscriptions begin only after
// server arm + authenticated private WSS + browser financial lease are all ready.
// ---------------------------------------------------------------------------
engine = replaceOne(
  engine,
  `    if (kind === "public" && payload.history && payload.echo_req?.ticks_history) {\n      const symbol = String(payload.echo_req.ticks_history || "").toUpperCase();\n      seedHistory(symbol, payload.history.prices || [], payload.history.times || []);\n      sendNoWait("public", { ticks: symbol, subscribe: 1 });\n      state.subscribedMarkets.add(symbol);\n    }`,
  `    if (kind === "public" && payload.history && payload.echo_req?.ticks_history\n        && !window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1) {\n      const symbol = String(payload.echo_req.ticks_history || "").toUpperCase();\n      seedHistory(symbol, payload.history.prices || [], payload.history.times || []);\n      sendNoWait("public", { ticks: symbol, subscribe: 1 });\n      state.subscribedMarkets.add(symbol);\n    }`,
  "single public history hydration owner",
);

engine = replaceOne(
  engine,
  `  function onTick(symbol, tick) {\n    const history = recordTick(symbol, tick);\n    if (!history || !state.running || !state.strategy || state.ownerLost) return;`,
  `  function onTick(symbol, tick) {\n    const history = recordTick(symbol, tick);\n    if (!history) return;\n    // History hydration builds the statistical window only. It is not a live entry\n    // boundary and may occur while the financial fence intentionally blocks BUY.\n    if (Boolean(tick?.__history_hydration)) return;\n    if (!state.running || !state.strategy || state.ownerLost) return;`,
  "history ticks cannot trigger purchases",
);

const readinessHelper = `  function executionTransportReady() {\n    const financial = window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1?.state?.();\n    const financialReady = !financial || financial.buy_allowed !== false;\n    return Boolean(\n      state.running\n      && !state.ownerLost\n      && state.armed\n      && state.privateWs?.readyState === WebSocket.OPEN\n      && financialReady\n    );\n  }\n\n`;
engine = replaceOne(
  engine,
  `  function subscribeMarkets() {`,
  readinessHelper + `  function subscribeMarkets() {`,
  "execution-ready helper",
);
engine = replaceOne(
  engine,
  `    if (!state.running || !state.strategy || state.publicWs?.readyState !== WebSocket.OPEN) return;`,
  `    if (!executionTransportReady() || !state.strategy || state.publicWs?.readyState !== WebSocket.OPEN) return;`,
  "market subscription financial readiness gate",
);

engine = replaceOne(
  engine,
  `        if (await armOnce(epoch, strategy)) {\n          updateStatus("Direct • browser owns execution • offline continuation armed");\n          return;\n        }`,
  `        if (await armOnce(epoch, strategy)) {\n          updateStatus("Direct • browser owns execution • secure trade channel arming");\n          subscribeMarkets();\n          return;\n        }`,
  "arm readiness activates market stream",
);

engine = replaceOne(
  engine,
  `          if (state.running && !state.ownerLost) {\n            updateStatus("Direct • connected to Deriv • analyzing live ticks");\n            restoreOpenContractSubscriptions("secure session restored");\n          }`,
  `          if (state.running && !state.ownerLost) {\n            updateStatus("Direct • connected to Deriv • execution channel ready");\n            restoreOpenContractSubscriptions("secure session restored");\n            subscribeMarkets();\n          }`,
  "private socket readiness activates market stream",
);

engine = replaceOne(
  engine,
  `    })().catch((error) => {\n      state.privateConnectPromise = null;\n      if (!state.running) schedulePrewarm();\n      throw error;\n    });`,
  `    })().catch((error) => {\n      state.privateConnectPromise = null;\n      const message = String(error?.message || error || "secure session unavailable").slice(0, 180);\n      state.lastExecutionError = message;\n      if (state.running && !state.ownerLost) {\n        updateStatus("Direct • restoring secure trade session • " + message);\n        setTimeout(() => {\n          if (state.running && !state.ownerLost) connectPrivate().catch(() => {});\n        }, 900);\n      } else if (!state.running) schedulePrewarm();\n      throw error;\n    });`,
  "running private socket automatic retry",
);

engine = replaceOne(
  engine,
  `  async function executeReal(symbol, history, route = activeExecutionRoute()) {\n    if (!route || !state.running || state.ownerLost || state.inFlight || state.openContracts.size) return;`,
  `  async function executeReal(symbol, history, route = activeExecutionRoute()) {\n    if (!route || !state.running || state.ownerLost || state.inFlight || state.openContracts.size) return;\n    if (!executionTransportReady()) {\n      updateStatus("Direct • condition qualified • securing execution channel before BUY");\n      connectPrivate().catch(() => {});\n      return;\n    }`,
  "qualified signal financial readiness fence",
);

engine = replaceOne(
  engine,
  `    updateStatus("Direct • Run active • analyzing Deriv ticks now");`,
  `    updateStatus("Direct • Run starting • securing Deriv execution channel");`,
  "start status reflects financial readiness",
);

engine = replaceOne(
  engine,
  `        open_contracts: state.openContracts.size,\n        last_execution_error: String(state.lastExecutionError || ""),`,
  `        open_contracts: state.openContracts.size,\n        execution_ready: executionTransportReady(),\n        last_execution_error: String(state.lastExecutionError || ""),`,
  "execution readiness state export",
);

for (const required of [
  "secure session connection delayed after 15s",
  "derivadmin:direct-execution-error",
  "last_execution_error",
  "Authenticated Deriv WebSocket closed",
  "executionTransportReady",
  "execution_ready: executionTransportReady()",
  "Boolean(tick?.__history_hydration)",
  "!window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1",
  "secure trade channel arming",
  "execution channel ready",
]) {
  if (!engine.includes(required)) throw new Error(`runtime-coherence engine invariant missing: ${required}`);
}
write(enginePath, engine);

// ---------------------------------------------------------------------------
// 4. Run-panel ownership label must not claim browser ownership after surrender.
// ---------------------------------------------------------------------------
let run = read(runPath);
run = replaceOne(
  run,
  `  function browserRunning() {\n    return Boolean(engineState().running);\n  }`,
  `  function browserRunning() {\n    const snapshot = engineState();\n    return Boolean(snapshot.running && String(snapshot.owner || "").toLowerCase() === "browser");\n  }`,
  "run panel browser owner truth",
);
if (!run.includes('snapshot.owner || ""')) {
  throw new Error("runtime-coherence run-panel owner invariant missing");
}
write(runPath, run);

// ---------------------------------------------------------------------------
// 5. Cache-bust every asset changed by this finalizer, including the dynamically
// loaded shell. This prevents an old browser cache from preserving either bug.
// ---------------------------------------------------------------------------
let premium = read(premiumPath);
premium = premium.replace(
  /\/final-ui-shell-v2\.js\?v=[^"']+/g,
  "/final-ui-shell-v2.js?v=20260820-single-ledger-v16",
);
if (!premium.includes("/final-ui-shell-v2.js?v=20260820-single-ledger-v16")) {
  throw new Error("runtime-coherence shell cache-bust missing");
}
write(premiumPath, premium);

let index = read(indexPath);
index = index.replace(
  /\/final-premium-6f3\.js\?v=[^"']+/g,
  "/final-premium-6f3.js?v=20260820-runtime-coherence-v17",
);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260820-execution-ready-v12",
);
index = index.replace(
  /\/direct-run-panel-authority-v6\.js\?v=[^"']+/g,
  "/direct-run-panel-authority-v6.js?v=20260820-owner-truth-v8",
);
for (const required of [
  "/final-premium-6f3.js?v=20260820-runtime-coherence-v17",
  "/deriv-direct-execution-v2.js?v=20260820-execution-ready-v12",
  "/direct-run-panel-authority-v6.js?v=20260820-owner-truth-v8",
]) {
  if (!index.includes(required)) throw new Error(`runtime-coherence index cache-bust missing: ${required}`);
}
write(indexPath, index);

console.log("Runtime coherence v1 finalized: one Transactions renderer, one history hydrator, purchase-ready market subscriptions, persistent private-session recovery");
