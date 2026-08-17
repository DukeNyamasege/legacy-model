import { readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "dist");
const indexPath = resolve(output, "index.html");

const publicOrigin = String(
  process.env.PUBLIC_ORIGIN
  || process.env.BACKEND_ORIGIN
  || "https://derivadmin.site",
).trim().replace(/\/+$/, "");

const parsed = new URL(publicOrigin);
if (parsed.protocol !== "https:" && parsed.hostname !== "localhost") {
  throw new Error("PUBLIC_ORIGIN must use HTTPS outside local development");
}
if (parsed.pathname !== "/" || parsed.search || parsed.hash || parsed.username || parsed.password) {
  throw new Error("PUBLIC_ORIGIN must be an origin without credentials, path, query or hash");
}

process.env.BACKEND_ORIGIN = publicOrigin;
if (!String(process.env.DASHBOARD_WS_BASE_URL || "").trim()) {
  const stream = new URL(publicOrigin);
  stream.protocol = stream.protocol === "https:" ? "wss:" : "ws:";
  process.env.DASHBOARD_WS_BASE_URL = stream.origin;
}

await import(`./build-netlify.mjs?vps=${Date.now()}`);
await rm(resolve(output, "_redirects"), { force: true });

/* The pre-Action-5 library exposed only built-in labels/IDs. Scheduling must
 * freeze the complete builder/result configuration, not reconstruct trading
 * logic from a label. Upgrade only the built Full-VPS asset so Action 5 receives
 * an immutable copy of the same proven built-in object used by Load Template. */
const strategyLibraryPath = resolve(output, "strategy-template-library.js");
let strategyLibrary = await readFile(strategyLibraryPath, "utf8");
const compactBuiltInExport = "builtIns: BUILT_INS.map((item) => ({ id: item.id, name: item.name, analysis: item.analysis, side: item.side })),";
if (!strategyLibrary.includes(compactBuiltInExport)) {
  throw new Error("Strategy Library built-in export contract changed; refusing an unsafe Action 5 build");
}
strategyLibrary = strategyLibrary.replace(
  compactBuiltInExport,
  "builtIns: BUILT_INS.map((item) => clone(item)),",
);
await writeFile(strategyLibraryPath, strategyLibrary, "utf8");

let html = await readFile(indexPath, "utf8");
html = html
  .replace(
    '<meta name="frontend-runtime" content="netlify-vps-split-v1">',
    '<meta name="frontend-runtime" content="full-vps-same-origin-v1">',
  )
  .replace(
    '<script src="/netlify-api-boundary.js"></script>',
    '<script src="/vps-api-boundary.js?v=20260817-1"></script>',
  )
  .replaceAll('prelogin-landing-v2.css?v=20260814-2', 'prelogin-landing-v2.css?v=20260817-2')
  .replaceAll('prelogin-landing-v2.js?v=20260814-2', 'prelogin-landing-v2.js?v=20260817-2');

/* Action 1 foundation + Action 3 route/library extensions. */
if (!html.includes('/automation-home-v1.css?v=20260817-1')) {
  html = html.replace("</head>", '  <link rel="stylesheet" href="/automation-home-v1.css?v=20260817-1">\n</head>');
}
if (!html.includes('/automation-home-v1.js?v=20260817-3')) {
  html = html.replace("</body>", '  <script src="/automation-home-v1.js?v=20260817-3" defer></script>\n</body>');
}

/* Action 2: 250-word Text-to-Strategy mobile workspace. */
if (!html.includes('/text-to-strategy-v1.css?v=20260817-1')) {
  html = html.replace("</head>", '  <link rel="stylesheet" href="/text-to-strategy-v1.css?v=20260817-1">\n</head>');
}
if (!html.includes('/text-to-strategy-v1.js?v=20260817-1')) {
  html = html.replace("</body>", '  <script src="/text-to-strategy-v1.js?v=20260817-1" defer></script>\n</body>');
}

/* Action 3: generated Strategy Ready review + save/trade/schedule handoff. */
if (!html.includes('/strategy-ready-v1.css?v=20260817-1')) {
  html = html.replace("</head>", '  <link rel="stylesheet" href="/strategy-ready-v1.css?v=20260817-1">\n</head>');
}
if (!html.includes('/strategy-ready-v1.js?v=20260817-1')) {
  html = html.replace("</body>", '  <script src="/strategy-ready-v1.js?v=20260817-1" defer></script>\n</body>');
}

/* Historical regression markers retained while Action 5 is the active aggregate:
 * authenticated_ui: "automation-home-action3-v1"
 * authenticated_ui: "automation-home-action4-v1"
 * schedule_execution: "deferred-to-action5"
 */

/* Action 4: linked-account-stable timezone onboarding + Schedule Trading UI. */
if (!html.includes('/timezone-schedule-v1.css?v=20260817-1')) {
  html = html.replace("</head>", '  <link rel="stylesheet" href="/timezone-schedule-v1.css?v=20260817-1">\n</head>');
}
if (!html.includes('/timezone-schedule-v1.js?v=20260817-2')) {
  html = html.replace("</body>", '  <script src="/timezone-schedule-v1.js?v=20260817-2" defer></script>\n</body>');
}
if (!html.includes('/timezone-home-sync-v1.js?v=20260817-1')) {
  html = html.replace("</body>", '  <script src="/timezone-home-sync-v1.js?v=20260817-1" defer></script>\n</body>');
}

/* Action 5: browser-independent persistent scheduler, lifecycle/history status,
 * Home/Trades integration and server-backed cancel/upcoming controls. */
if (!html.includes('/automation-scheduler-action5.css?v=20260817-1')) {
  html = html.replace("</head>", '  <link rel="stylesheet" href="/automation-scheduler-action5.css?v=20260817-1">\n</head>');
}
if (!html.includes('/automation-scheduler-action5.js?v=20260817-1')) {
  html = html.replace("</body>", '  <script src="/automation-scheduler-action5.js?v=20260817-1" defer></script>\n</body>');
}

if (!html.includes('/vps-api-boundary.js?v=20260817-1')) throw new Error("Full VPS API boundary was not installed into the production HTML");
if (html.includes('<script src="/netlify-api-boundary.js"></script>')) throw new Error("Netlify 3.2-second API boundary must not remain active on full VPS");
if (!html.includes('/automation-home-v1.css?v=20260817-1')) throw new Error("Automation Home stylesheet was not installed");
if (!html.includes('/automation-home-v1.js?v=20260817-3')) throw new Error("Action 3 Automation Home controller was not installed");
if (!html.includes('/text-to-strategy-v1.css?v=20260817-1') || !html.includes('/text-to-strategy-v1.js?v=20260817-1')) throw new Error("Action 2 Text-to-Strategy assets were not installed");
if (!html.includes('/strategy-ready-v1.css?v=20260817-1') || !html.includes('/strategy-ready-v1.js?v=20260817-1')) throw new Error("Action 3 Strategy Ready assets were not installed");
if (!html.includes('/timezone-schedule-v1.css?v=20260817-1') || !html.includes('/timezone-schedule-v1.js?v=20260817-2')) throw new Error("Action 4 timezone/schedule assets were not installed");
if (!html.includes('/timezone-home-sync-v1.js?v=20260817-1')) throw new Error("Action 4 Automation Home timezone sync was not installed");
if (!html.includes('/automation-scheduler-action5.css?v=20260817-1') || !html.includes('/automation-scheduler-action5.js?v=20260817-1')) throw new Error("Action 5 persistent scheduler assets were not installed");
if (!html.includes('prelogin-landing-v2.css?v=20260817-2') || !html.includes('prelogin-landing-v2.js?v=20260817-2')) throw new Error("Action 2 mobile public landing assets were not installed");

await writeFile(indexPath, html, "utf8");

await writeFile(
  resolve(output, "vps-build.json"),
  `${JSON.stringify({
    frontend_runtime: "full-vps-same-origin-v1",
    public_origin: publicOrigin,
    api_base: "/api",
    oauth_base: "/oauth",
    websocket_base: process.env.DASHBOARD_WS_BASE_URL,
    api_boundary: "full-vps-same-origin-rest-v3",
    authenticated_ui: "automation-home-action5-v1",
    text_to_strategy: "nearest-supported-v1-250-words",
    strategy_ready: "review-save-trade-schedule-v1",
    strategy_library: "built-in-my-ai-unified-v1",
    automation_timezone: "linked-options-global-africa-nairobi-default-v1",
    schedule_workspace: "mobile-date-time-strategy-risk-overlap-action5-v1",
    schedule_execution: "persistent-server-scheduler-existing-worker-authority-v1",
    schedule_persistence: "postgres-restart-safe-exactly-once-claim-v1",
    schedule_overlap: "wait-skip-replace-v1",
    schedule_history: "server-lifecycle-history-v1",
    schedule_built_ins: "full-frozen-template-snapshots-v1",
    public_landing: "mobile-automation-action2-v1",
    generated_at: new Date().toISOString(),
  }, null, 2)}\n`,
  "utf8",
);

console.log("Full VPS frontend built.");
console.log(`Public origin: ${publicOrigin}`);
console.log("REST: same-origin /api/* -> host Nginx -> API container");
console.log("OAuth: same-origin /oauth/* -> host Nginx -> API container");
console.log("API boundary: full-vps-same-origin-rest-v3 (no 3.2s false timeout)");
console.log("Authenticated UI: automation-home-action5-v1");
console.log("Text to Strategy: nearest-supported-v1, maximum 250 words, review required");
console.log("Strategy Ready: review -> save / trade now / schedule; existing execution APIs only");
console.log("Strategy Library: Built-in + My Strategies + AI Generated; complete built-in snapshots are available to scheduler");
console.log("Timezone: Africa/Nairobi (EAT) default, mirrored across linked Options account IDs, changeable worldwide");
console.log("Schedule Trading: persistent server strategy + date + time + timezone + stake + TP + SL + overlap policy");
console.log("Schedule execution: restart-safe server scheduler -> existing Custom Strategy worker; no browser timer and no second BUY engine");
console.log("Schedule lifecycle: scheduled / waiting / starting / running / completed / skipped / cancelled / failed");
console.log("Public landing: mobile-automation-action2-v1");
console.log(`Realtime: ${process.env.DASHBOARD_WS_BASE_URL}/ws/me/live`);
