import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const fencePath = "dist/direct-financial-fence-v1.js";
const checkpointPath = "dist/direct-continuity-checkpoint-v1.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`browser-deriv-direct-v3 missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}
function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}
function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`browser-deriv-direct-v3 ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}
function replaceBetween(source, start, end, replacement, label) {
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`browser-deriv-direct-v3 ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

let engine = read(enginePath);

// ---------------------------------------------------------------------------
// 1. Browser memory owns the short-lived OAuth access credential. Refresh tokens
// never enter JavaScript. There is one lightweight VPS bootstrap only when the
// current page/start needs a token; OTP itself is requested browser -> Deriv.
// ---------------------------------------------------------------------------
if (!engine.includes("directAccessToken: null,")) {
  engine = replaceOne(
    engine,
    `    privateConnectPromise: null,\n`,
    `    privateConnectPromise: null,\n    directAccessToken: null,\n    directAccountId: "",\n    directDerivAppId: "",\n    directTokenExpiresAt: 0,\n    directPrivateRetryMs: 1000,\n`,
    "browser OAuth state",
  );
}

const directPrivateTransport = `  function clearDirectBrowserCredential() {\n    state.directAccessToken = null;\n    state.directAccountId = "";\n    state.directDerivAppId = "";\n    state.directTokenExpiresAt = 0;\n  }\n\n  async function directBrowserBootstrap(forceRefresh = false) {\n    const now = Date.now();\n    if (\n      !forceRefresh\n      && state.directAccessToken\n      && state.directAccountId\n      && state.directDerivAppId\n      && Number(state.directTokenExpiresAt || 0) - now > 90000\n    ) {\n      return {\n        accessToken: state.directAccessToken,\n        accountId: state.directAccountId,\n        derivAppId: state.directDerivAppId,\n      };\n    }\n\n    const response = await fetchWithTimeout(\n      apiPath("/me/direct-execution/bootstrap"),\n      {\n        method: "POST",\n        credentials: "include",\n        cache: "no-store",\n        headers: { "Content-Type": "application/json", Accept: "application/json" },\n        body: JSON.stringify({ force_refresh: Boolean(forceRefresh) }),\n      },\n      9000,\n    );\n    let payload = {};\n    try { payload = await response.clone().json(); } catch (_) {}\n    if (!response.ok) {\n      const detail = String(payload?.detail || payload?.message || ("browser OAuth bootstrap HTTP " + response.status)).slice(0, 180);\n      throw new Error(detail);\n    }\n    const token = String(payload?.access_token || "").trim();\n    const accountId = String(payload?.account_id || "").trim();\n    const derivAppId = String(payload?.deriv_app_id || "").trim();\n    if (!token || !accountId || !derivAppId) throw new Error("Browser-direct Deriv authorization bootstrap is incomplete");\n\n    state.directAccessToken = token;\n    state.directAccountId = accountId;\n    state.directDerivAppId = derivAppId;\n    const expiresAt = Date.parse(String(payload?.expires_at || ""));\n    state.directTokenExpiresAt = Number.isFinite(expiresAt) ? expiresAt : Date.now() + 45 * 60 * 1000;\n    return { accessToken: token, accountId, derivAppId };\n  }\n\n  function directDerivError(payload, fallback) {\n    const errors = Array.isArray(payload?.errors) ? payload.errors : [];\n    const first = errors[0] && typeof errors[0] === "object" ? errors[0] : {};\n    return String(first.message || first.code || payload?.message || fallback || "Deriv request failed").slice(0, 180);\n  }\n\n  async function requestDirectDerivOtp(forceRefresh = false) {\n    const auth = await directBrowserBootstrap(forceRefresh);\n    const otpUrl = \`https://api.derivws.com/trading/v1/options/accounts/\${encodeURIComponent(auth.accountId)}/otp\`;\n    let response;\n    try {\n      response = await originalFetch(otpUrl, {\n        method: "POST",\n        mode: "cors",\n        credentials: "omit",\n        cache: "no-store",\n        headers: {\n          Authorization: \`Bearer \${auth.accessToken}\`,\n          "Deriv-App-ID": auth.derivAppId,\n          Accept: "application/json",\n        },\n      });\n    } catch (error) {\n      throw new Error("Direct Deriv OTP request failed in browser: " + String(error?.message || error || "network error").slice(0, 130));\n    }\n\n    let payload = {};\n    try { payload = await response.clone().json(); } catch (_) {}\n    if (response.status === 401 && !forceRefresh) {\n      clearDirectBrowserCredential();\n      return requestDirectDerivOtp(true);\n    }\n    if (!response.ok) {\n      throw new Error(directDerivError(payload, "Deriv OTP HTTP " + response.status));\n    }\n    const wsUrl = String(payload?.data?.url || "").trim();\n    let parsedWsUrl = null;\n    try { parsedWsUrl = new URL(wsUrl); } catch (_) {}\n    const allowedWsPath = Boolean(\n      parsedWsUrl\n      && ["/trading/v1/options/ws/demo", "/trading/v1/options/ws/real"].includes(parsedWsUrl.pathname)\n    );\n    if (\n      !parsedWsUrl\n      || parsedWsUrl.protocol !== "wss:"\n      || parsedWsUrl.hostname !== "api.derivws.com"\n      || !allowedWsPath\n    ) {\n      throw new Error("Deriv did not return a valid authenticated Options WebSocket URL");\n    }\n    return wsUrl;\n  }\n\n  function connectPrivate() {\n    // Do not prewarm private trading while idle. One Start creates one direct Deriv\n    // authorization path; page load itself creates no OTP/server-session traffic.\n    if (!state.running) return Promise.resolve(null);\n    if (state.privateWs?.readyState === WebSocket.OPEN) return Promise.resolve(state.privateWs);\n    if (state.privateConnectPromise) return state.privateConnectPromise;\n\n    state.privateConnectPromise = (async () => {\n      const wsUrl = await requestDirectDerivOtp(false);\n      return await new Promise((resolve, reject) => {\n        const ws = new WebSocket(wsUrl);\n        state.privateWs = ws;\n        let opened = false;\n        const timer = setTimeout(() => {\n          try { ws.close(); } catch (_) {}\n          if (!opened) reject(new Error("Direct Deriv authenticated WebSocket opening handshake exceeded 15 seconds"));\n        }, 15000);\n\n        ws.onopen = () => {\n          opened = true;\n          clearTimeout(timer);\n          state.privateConnectPromise = null;\n          state.lastPrivateMessageAt = Date.now();\n          state.lastExecutionError = "";\n          state.directPrivateRetryMs = 1000;\n          if (state.running && !state.ownerLost) {\n            updateStatus("Direct • browser connected straight to Deriv • execution channel ready");\n            try { restoreOpenContractSubscriptions("direct Deriv session restored"); } catch (_) {}\n            try { subscribeMarkets(); } catch (_) {}\n          }\n          resolve(ws);\n        };\n        ws.onmessage = (event) => handleWsMessage("private", event);\n        ws.onerror = () => {\n          state.lastExecutionError = "Direct Deriv authenticated WebSocket reported a connection error";\n        };\n        ws.onclose = (event) => {\n          clearTimeout(timer);\n          if (state.privateWs === ws) state.privateWs = null;\n          state.privateConnectPromise = null;\n          const code = Number(event?.code || 0);\n          const reason = String(event?.reason || "").replace(/otp=[^&\\s]+/gi, "otp=[redacted]").slice(0, 120);\n          const message = "Direct Deriv WebSocket closed" + (code ? " code " + code : "") + (reason ? ": " + reason : "");\n          state.lastExecutionError = message;\n          rejectPending("private", new Error(message));\n\n          if (!opened) {\n            reject(new Error(message));\n            return;\n          }\n          if (state.running && !state.ownerLost) {\n            updateStatus("Direct • Deriv trade channel disconnected • browser reconnecting directly");\n            const delay = Math.max(1000, Math.min(15000, Number(state.directPrivateRetryMs || 1000)));\n            state.directPrivateRetryMs = Math.min(15000, Math.round(delay * 1.7));\n            setTimeout(() => {\n              if (state.running && !state.ownerLost) connectPrivate().catch(() => {});\n            }, delay);\n          }\n        };\n      });\n    })().catch((error) => {\n      state.privateConnectPromise = null;\n      const message = String(error?.message || error || "Direct Deriv trade channel unavailable")\n        .replace(/otp=[^&\\s]+/gi, "otp=[redacted]")\n        .replace(/bearer\\s+[^\\s]+/gi, "Bearer [redacted]")\n        .slice(0, 180);\n      state.lastExecutionError = message;\n      if (state.running && !state.ownerLost) {\n        updateStatus("Direct • browser restoring Deriv trade channel • " + message);\n        const delay = Math.max(1000, Math.min(15000, Number(state.directPrivateRetryMs || 1000)));\n        state.directPrivateRetryMs = Math.min(15000, Math.round(delay * 1.7));\n        setTimeout(() => {\n          if (state.running && !state.ownerLost) connectPrivate().catch(() => {});\n        }, delay);\n      }\n      throw error;\n    });\n    return state.privateConnectPromise;\n  }\n\n`;

engine = replaceBetween(
  engine,
  "  function connectPrivate() {",
  "  function schedulePrewarm() {",
  directPrivateTransport,
  "direct browser Deriv private transport",
);

// ---------------------------------------------------------------------------
// 2. Start is one control write, never a 5-second financial heartbeat. Browser
// execution remains browser-owned until TP, SL or explicit Stop changes lifecycle.
// ---------------------------------------------------------------------------
const directOwnership = `  async function armOnce(epoch, strategy) {\n    const response = await fetchWithTimeout(\n      apiPath("/me/direct-execution/arm"),\n      {\n        method: "POST",\n        credentials: "include",\n        headers: { "Content-Type": "application/json" },\n        body: JSON.stringify({ epoch, strategy }),\n      },\n      9000,\n    );\n    if (!response.ok) {\n      let detail = "";\n      try {\n        const failure = await response.clone().json();\n        detail = String(failure?.detail || failure?.message || "").slice(0, 180);\n      } catch (_) {}\n      throw new Error(detail || ("Start control unavailable HTTP " + response.status));\n    }\n    const payload = await response.json();\n    if (!state.running || state.epoch !== epoch) return false;\n    state.armed = true;\n    state.ownerLost = false;\n    state.lastLeaseAckAt = Date.now();\n    state.leaseMs = Number.MAX_SAFE_INTEGER;\n    return true;\n  }\n\n  function armInBackground(epoch, strategy) {\n    const attempt = async () => {\n      if (!state.running || state.epoch !== epoch || state.ownerLost || state.armed) return;\n      try {\n        if (await armOnce(epoch, strategy)) {\n          updateStatus("Direct • browser owns execution • connecting straight to Deriv");\n          connectPrivate().catch(() => {});\n          subscribeMarkets();\n          return;\n        }\n      } catch (error) {\n        state.lastExecutionError = String(error?.message || error || "Start control unavailable").slice(0, 180);\n      }\n      // Control-plane retry is intentionally slow. It is not part of the tick or\n      // purchase hot path and cannot create a request storm on the VPS.\n      if (state.running && state.epoch === epoch && !state.ownerLost && !state.armed) {\n        setTimeout(attempt, 15000);\n      }\n    };\n    attempt();\n  }\n\n  async function heartbeatOnce(_epoch) {\n    return true;\n  }\n\n  function ownershipWatch() {\n    clearInterval(state.heartbeatTimer);\n    state.heartbeatTimer = null;\n    state.ownerLost = false;\n  }\n\n`;

engine = replaceBetween(
  engine,
  "  async function armOnce(epoch, strategy) {",
  "  async function ensureStrategy() {",
  directOwnership,
  "remove periodic VPS ownership heartbeat",
);

// ---------------------------------------------------------------------------
// 3. Server receives only contract receipts from the financial hot path. The
// local journal remains immediate; receipt delivery is asynchronous and cannot
// delay proposal, BUY or settlement handling.
// ---------------------------------------------------------------------------
const receiptJournal = `  function sendTradeReceipt(row) {\n    if (!row || String(row.mode || "").toLowerCase() !== "real") return;\n    const event = String(row.state || "").toUpperCase();\n    const contractId = String(row.contract_id || "").trim();\n    if (!contractId || !["OPEN", "SETTLED"].includes(event)) return;\n    originalFetch(apiPath("/me/direct-execution/receipt"), {\n      method: "POST",\n      credentials: "include",\n      keepalive: true,\n      headers: { "Content-Type": "application/json", Accept: "application/json" },\n      body: JSON.stringify({ event, contract_id: contractId, payload: row }),\n    }).catch(() => {});\n  }\n\n  function appendJournal(row) {\n    const rows = loadJournal();\n    const next = { at: new Date().toISOString(), ...row };\n    rows.push(next);\n    writeJournal(rows);\n    window.dispatchEvent(new CustomEvent("derivadmin:direct-trade", { detail: next }));\n    if (String(next.mode || "").toLowerCase() === "real" && ["OPEN", "SETTLED"].includes(String(next.state || "").toUpperCase())) {\n      queueMicrotask(() => sendTradeReceipt(next));\n    }\n  }\n\n`;
engine = replaceBetween(
  engine,
  "  function appendJournal(row) {",
  "  function clearLocalTrades() {",
  receiptJournal,
  "trade receipt journal",
);

// Browser faults never create server takeover in v3.
engine = engine.replaceAll(
  "Browser execution lease moved to VPS continuity; server recovery is taking over.",
  "Browser execution remains direct to Deriv; reconnecting the browser trade channel.",
);
engine = engine.replaceAll(
  "Authenticated Deriv trading session is not connected; reconnecting automatically.",
  "Direct Deriv trade channel is not connected; browser is reconnecting directly to Deriv.",
);
engine = engine.replaceAll(
  "Server continuity • browser closing • VPS takeover after lease timeout",
  "Direct • browser closing • live execution pauses on this device; no VPS trade transport",
);
engine = engine.replaceAll(
  'owner: state.ownerLost ? "server" : "browser"',
  'owner: "browser"',
);

// A browser that loses its direct Deriv socket keeps retrying Deriv. It never
// surrenders financial transport to the VPS.
engine = engine.replaceAll("state.ownerLost = true;", "state.ownerLost = false;");

for (const required of [
  "/me/direct-execution/bootstrap",
  "https://api.derivws.com/trading/v1/options/accounts/",
  'Authorization: `Bearer ${auth.accessToken}`',
  '"Deriv-App-ID": auth.derivAppId',
  'credentials: "omit"',
  "/me/direct-execution/receipt",
  "sendTradeReceipt",
  "heartbeatOnce(_epoch)",
  "browser reconnecting directly",
  'parsedWsUrl.hostname !== "api.derivws.com"',
  '[/"/trading/v1/options/ws/demo", "/trading/v1/options/ws/real"/]'.replaceAll("/", ""),
]) {
  if (!engine.includes(required)) throw new Error(`browser-deriv-direct-v3 engine invariant missing: ${required}`);
}
for (const forbidden of [
  'apiPath("/me/direct-execution/session")',
  'apiPath("/me/direct-execution/heartbeat")',
  'apiPath("/me/direct-execution/yield")',
  "VPS continuity takeover activated automatically",
]) {
  if (engine.includes(forbidden)) throw new Error(`browser-deriv-direct-v3 server hot-path survived: ${forbidden}`);
}
write(enginePath, engine);

// ---------------------------------------------------------------------------
// 4. Financial fence is event-owned, not lease-time-owned. The one successful Arm
// authorizes this browser; Stop or another same-origin owner clears it. There is no
// heartbeat deadline that can disable BUY while the Deriv channel is healthy.
// ---------------------------------------------------------------------------
let fence = read(fencePath);
fence = replaceBetween(
  fence,
  "  function leaseAllowsBuy() {",
  "  function GuardedWebSocket(url, protocols) {",
  `  function leaseAllowsBuy() {\n    return Boolean(state.armed && state.epoch && state.hydrationPending <= 0);\n  }\n\n`,
  "event-owned browser financial fence",
);
fence = fence.replaceAll(
  'version: "20260818-direct-financial-fence-v2"',
  'version: "20260820-browser-deriv-direct-financial-fence-v3"',
);
if (fence.includes("state.leaseMs - 8000")) throw new Error("browser-deriv-direct-v3 timed BUY lease survived");
if (!fence.includes("state.armed && state.epoch && state.hydrationPending <= 0")) {
  throw new Error("browser-deriv-direct-v3 financial fence invariant missing");
}
write(fencePath, fence);

// ---------------------------------------------------------------------------
// 5. The checkpoint asset must be a zero-write compatibility shim. OPEN/SETTLED
// receipts replace the former five-second VPS continuity checkpoint.
// ---------------------------------------------------------------------------
let checkpoint = read(checkpointPath);
for (const forbidden of [
  "/api/me/direct-execution/checkpoint",
  "setInterval(checkpoint, 5000)",
  "XMLHttpRequest",
]) {
  if (checkpoint.includes(forbidden)) throw new Error(`browser-deriv-direct-v3 periodic checkpoint survived: ${forbidden}`);
}
if (!checkpoint.includes("trade_receipts_only: true")) {
  throw new Error("browser-deriv-direct-v3 no-checkpoint marker missing");
}

let index = read(indexPath);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260820-browser-deriv-direct-v3",
);
index = index.replace(
  /\/direct-financial-fence-v1\.js\?v=[^"']+/g,
  "/direct-financial-fence-v1.js?v=20260820-browser-deriv-direct-v3",
);
index = index.replace(
  /\/direct-continuity-checkpoint-v1\.js\?v=[^"']+/g,
  "/direct-continuity-checkpoint-v1.js?v=20260820-browser-deriv-direct-v3",
);
for (const marker of [
  "/deriv-direct-execution-v2.js?v=20260820-browser-deriv-direct-v3",
  "/direct-financial-fence-v1.js?v=20260820-browser-deriv-direct-v3",
  "/direct-continuity-checkpoint-v1.js?v=20260820-browser-deriv-direct-v3",
]) {
  if (!index.includes(marker)) throw new Error(`browser-deriv-direct-v3 cache-bust missing: ${marker}`);
}
write(indexPath, index);

console.log(
  "Browser-direct Deriv v3 finalized: public ticks + OTP + authenticated WSS + proposal + BUY + contract updates stay browser<->Deriv; VPS receives control events and OPEN/SETTLED receipts only",
);
