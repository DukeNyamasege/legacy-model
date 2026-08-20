import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const shellPath = "dist/final-ui-shell-v2.js";
const premiumPath = "dist/final-premium-6f3.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`ws1006-runpanel missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}
function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}
function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`ws1006-runpanel ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

// ---------------------------------------------------------------------------
// 1. Private Deriv WebSocket keepalive.
// Deriv recommends ping every 30-60 seconds because proxies/firewalls may close
// idle WebSockets. The continuity watchdog also treats 45s without a private
// message as stale, so a 30s ping keeps a healthy authenticated channel alive
// while the strategy is simply waiting for a qualifying entry.
// ---------------------------------------------------------------------------
let engine = read(enginePath);

const keepaliveHelpers = `  function stopPrivateKeepalive() {\n    clearInterval(state.keepaliveTimer);\n    state.keepaliveTimer = null;\n  }\n\n  function sendPrivateKeepalive(ws) {\n    if (!ws || state.privateWs !== ws || ws.readyState !== WebSocket.OPEN) return false;\n    try {\n      state.privateReq += 1;\n      ws.send(JSON.stringify({ ping: 1, req_id: state.privateReq }));\n      return true;\n    } catch (error) {\n      state.lastExecutionError = "Authenticated Deriv WebSocket keepalive failed: " + String(error?.message || error || "send failed").slice(0, 120);\n      return false;\n    }\n  }\n\n  function startPrivateKeepalive(ws) {\n    stopPrivateKeepalive();\n    sendPrivateKeepalive(ws);\n    state.keepaliveTimer = setInterval(() => {\n      if (!sendPrivateKeepalive(ws)) {\n        stopPrivateKeepalive();\n        if (state.running && !state.ownerLost) connectPrivate().catch(() => {});\n      }\n    }, 30000);\n  }\n\n`;

engine = replaceOne(
  engine,
  `  function connectPrivate() {`,
  keepaliveHelpers + `  function connectPrivate() {`,
  "private keepalive helpers",
);

engine = replaceOne(
  engine,
  `          state.lastPrivateMessageAt = Date.now();\n          if (state.running && !state.ownerLost) {`,
  `          state.lastPrivateMessageAt = Date.now();\n          state.lastExecutionError = "";\n          startPrivateKeepalive(ws);\n          if (state.running && !state.ownerLost) {`,
  "private socket open starts keepalive",
);

engine = replaceOne(
  engine,
  `        ws.onclose = (event) => {\n          clearTimeout(timer);`,
  `        ws.onclose = (event) => {\n          clearTimeout(timer);\n          stopPrivateKeepalive();`,
  "private socket close stops keepalive",
);

for (const required of [
  "function startPrivateKeepalive(ws)",
  "ws.send(JSON.stringify({ ping: 1, req_id: state.privateReq }))",
  "setInterval(() =>",
  "}, 30000)",
  "state.lastExecutionError = \"\";",
  "stopPrivateKeepalive();",
]) {
  if (!engine.includes(required)) throw new Error(`ws1006-runpanel engine invariant missing: ${required}`);
}
write(enginePath, engine);

// ---------------------------------------------------------------------------
// 2. Preserve the already-live Run panel across full shell rerenders.
// updateRunPanelDom is no longer the only writer: render() replaces root.innerHTML,
// which destroys and recreates the whole .global-run-panel. Preserve the live node
// after binding the freshly rendered page so the direct ledger remains the one
// Transactions DOM authority with no visible shell/table interchange.
// ---------------------------------------------------------------------------
let shell = read(shellPath);
const renderBefore = `    const pages = { home, builder: builderPage, ai: aiPage, ready: readyPage, schedule: schedulePage, profile: profilePage, trades: tradesPage, timezone: timezonePage };\n    root.innerHTML = pages[state.route]();\n    bind();\n    const nextRoute = state.route;`;
const renderAfter = `    const pages = { home, builder: builderPage, ai: aiPage, ready: readyPage, schedule: schedulePage, profile: profilePage, trades: tradesPage, timezone: timezonePage };\n    const preservedRunPanel = root.querySelector(".global-run-panel");\n    const preserveDirectRunPanel = Boolean(preservedRunPanel && window.DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6);\n    root.innerHTML = pages[state.route]();\n    bind();\n    if (preserveDirectRunPanel) {\n      const replacementRunPanel = root.querySelector(".global-run-panel");\n      if (replacementRunPanel && preservedRunPanel) replacementRunPanel.replaceWith(preservedRunPanel);\n      queueMicrotask(() => {\n        try { window.DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6?.refresh?.(); } catch (_) {}\n      });\n    }\n    const nextRoute = state.route;`;
shell = replaceOne(shell, renderBefore, renderAfter, "preserve live Run panel across shell render");

for (const required of [
  "const preservedRunPanel = root.querySelector(\".global-run-panel\")",
  "const preserveDirectRunPanel = Boolean(preservedRunPanel && window.DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V6)",
  "replacementRunPanel.replaceWith(preservedRunPanel)",
]) {
  if (!shell.includes(required)) throw new Error(`ws1006-runpanel shell invariant missing: ${required}`);
}
write(shellPath, shell);

// Cache bust every changed runtime so browsers cannot continue running the old
// 1006/no-ping engine or the full-shell Run-panel renderer from cache.
let premium = read(premiumPath);
premium = premium.replace(
  /\/final-ui-shell-v2\.js\?v=[^"']+/g,
  "/final-ui-shell-v2.js?v=20260820-single-node-v19",
);
if (!premium.includes("/final-ui-shell-v2.js?v=20260820-single-node-v19")) {
  throw new Error("ws1006-runpanel shell cache marker missing");
}
write(premiumPath, premium);

let index = read(indexPath);
index = index.replace(
  /\/final-premium-6f3\.js\?v=[^"']+/g,
  "/final-premium-6f3.js?v=20260820-runpanel-owner-v18",
);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260820-private-ping-v22",
);
for (const required of [
  "/final-premium-6f3.js?v=20260820-runpanel-owner-v18",
  "/deriv-direct-execution-v2.js?v=20260820-private-ping-v22",
]) {
  if (!index.includes(required)) throw new Error(`ws1006-runpanel index cache marker missing: ${required}`);
}
write(indexPath, index);

console.log("WS1006/Run-panel v1 finalized: authenticated Deriv ping keepalive and one persistent Run-panel DOM node");
