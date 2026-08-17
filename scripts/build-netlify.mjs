import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "dist");
const rawBackend = (
  process.env.BACKEND_ORIGIN
  || process.env.DASHBOARD_API_BASE_URL
  || ""
).trim().replace(/\/+$/, "");
const rawStream = (process.env.DASHBOARD_WS_BASE_URL || "").trim().replace(/\/+$/, "");

function validateOrigin(value, label, protocols) {
  if (!value) return "";
  const url = new URL(value);
  if (!protocols.includes(url.protocol)) {
    throw new Error(`${label} must use ${protocols.join(" or ")}`);
  }
  if (url.pathname !== "/" || url.search || url.hash || url.username || url.password) {
    throw new Error(`${label} must be an origin without credentials, path, query or hash`);
  }
  return url.origin.replace(/\/+$/, "");
}

const backendOrigin = validateOrigin(rawBackend, "BACKEND_ORIGIN", ["https:", "http:"]);
if (backendOrigin && new URL(backendOrigin).protocol !== "https:" && new URL(backendOrigin).hostname !== "localhost") {
  throw new Error("BACKEND_ORIGIN must use HTTPS outside local development");
}

let streamBase = validateOrigin(rawStream, "DASHBOARD_WS_BASE_URL", ["wss:", "ws:"]);
if (!streamBase && backendOrigin) {
  const backend = new URL(backendOrigin);
  backend.protocol = backend.protocol === "https:" ? "wss:" : "ws:";
  streamBase = backend.origin;
}
if (streamBase && new URL(streamBase).protocol !== "wss:" && new URL(streamBase).hostname !== "localhost") {
  throw new Error("DASHBOARD_WS_BASE_URL must use WSS outside local development");
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(resolve(root, "dashboard"), output, { recursive: true });

const indexPath = resolve(output, "index.html");
let html = await readFile(indexPath, "utf8");
html = html.replace(
  '<meta name="api-base-url" content="">',
  `<meta name="api-base-url" content="/api">\n  <meta name="stream-base-url" content="${streamBase}">\n  <meta name="frontend-runtime" content="netlify-final-ui-6f1">`,
);

const required = [
  "/netlify-api-boundary.js",
  "/final-ui-shell-v1.css?v=20260817-6f1-1",
  "/final-ui-shell-v1.js?v=20260817-6f1-1",
  "/netlify-realtime-client.js?v=20260817-6f1-1",
];
for (const marker of required) {
  if (!html.includes(marker)) throw new Error(`Action 6F-1 frontend marker missing: ${marker}`);
}

const forbiddenLegacyUi = [
  "/ui/dashboard-v2.css",
  "/ui/dashboard-v2.js",
  "/ui/dashboard-actions-v2.js",
  "automation-home-v1",
  "text-to-strategy-v1",
  "strategy-ready-v1",
  "timezone-schedule-v1",
  "automation-scheduler-action5",
  "premium-subscription-action6e",
  "final-dashboard-authority",
  "mobile-topbar-compact",
  "tablet-navigation-fix",
];
for (const marker of forbiddenLegacyUi) {
  if (html.includes(marker)) throw new Error(`Legacy presentation authority leaked into 6F-1 build: ${marker}`);
}

await writeFile(indexPath, html, "utf8");

const redirects = [];
if (backendOrigin) {
  redirects.push(`/api/* ${backendOrigin}/:splat 200`);
  redirects.push(`/oauth/* ${backendOrigin}/oauth/:splat 200`);
  redirects.push(`/backend-health ${backendOrigin}/health/frontend-backend 200`);
}
redirects.push("/* /index.html 200");
await writeFile(resolve(output, "_redirects"), `${redirects.join("\n")}\n`, "utf8");

await writeFile(
  resolve(output, "frontend-build.json"),
  `${JSON.stringify({
    frontend_runtime: "netlify-final-ui-6f1",
    ui_authority: "final-ui-shell-v1",
    legacy_ui_loaded: false,
    backend_origin: backendOrigin || null,
    websocket_base: streamBase || null,
    generated_at: new Date().toISOString(),
  }, null, 2)}\n`,
  "utf8",
);

console.log("Netlify Action 6F-1 frontend built.");
console.log("UI authority: final-ui-shell-v1 (legacy dashboard presentation not loaded)");
console.log(`REST/OAuth backend: ${backendOrigin || "not configured (static preview only)"}`);
console.log(`Realtime backend: ${streamBase || "not configured (HTTP fallback only)"}`);
