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

let html = await readFile(indexPath, "utf8");
html = html
  .replace(
    '<meta name="frontend-runtime" content="netlify-final-ui-6f1">',
    '<meta name="frontend-runtime" content="full-vps-final-ui-6f1">',
  )
  .replace(
    '<script src="/netlify-api-boundary.js"></script>',
    '<script src="/vps-api-boundary.js?v=20260817-1"></script>',
  );

const required = [
  '<meta name="frontend-runtime" content="full-vps-final-ui-6f1">',
  '/vps-api-boundary.js?v=20260817-1',
  '/final-ui-shell-v1.css?v=20260817-6f1-1',
  '/final-ui-shell-v1.js?v=20260817-6f1-1',
  '/netlify-realtime-client.js?v=20260817-6f1-1',
];
for (const marker of required) {
  if (!html.includes(marker)) throw new Error(`Action 6F-1 VPS marker missing: ${marker}`);
}

const forbiddenLegacyUi = [
  '/ui/dashboard-v2.css',
  '/ui/dashboard-v2.js',
  '/ui/dashboard-actions-v2.js',
  'automation-home-v1',
  'text-to-strategy-v1',
  'strategy-ready-v1',
  'timezone-schedule-v1',
  'automation-scheduler-action5',
  'premium-subscription-action6e',
  'final-dashboard-authority',
  'mobile-topbar-compact',
  'tablet-navigation-fix',
];
for (const marker of forbiddenLegacyUi) {
  if (html.includes(marker)) throw new Error(`Legacy presentation authority leaked into 6F-1 VPS build: ${marker}`);
}
if (html.includes('<script src="/netlify-api-boundary.js"></script>')) {
  throw new Error("Netlify API boundary must not remain active on the full VPS build");
}

await writeFile(indexPath, html, "utf8");

await writeFile(
  resolve(output, "vps-build.json"),
  `${JSON.stringify({
    frontend_runtime: "full-vps-final-ui-6f1",
    ui_authority: "final-ui-shell-v1",
    legacy_ui_loaded: false,
    mockup_contract: "six-approved-mobile-screens-authoritative",
    public_origin: publicOrigin,
    api_base: "/api",
    oauth_base: "/oauth",
    websocket_base: process.env.DASHBOARD_WS_BASE_URL,
    api_boundary: "full-vps-same-origin-rest-v3",
    account_switching: "demo-real-post-me-switch-account",
    premium_access: "weekly-linked-options-server-gate-action6a-v1",
    premium_period: "exact-7-days-no-grace-v1",
    premium_prices: "KES250-mpesa-only-v1",
    premium_payment: "lipana-stk-verified-webhook-v1",
    premium_renewal: "manual-mpesa-after-exact-expiry-v1",
    schedule_execution: "persistent-server-scheduler-existing-worker-authority-v1",
    generated_at: new Date().toISOString(),
  }, null, 2)}\n`,
  "utf8",
);

console.log("Full VPS Action 6F-1 frontend built.");
console.log(`Public origin: ${publicOrigin}`);
console.log("UI authority: final-ui-shell-v1; legacy dashboard presentation is not loaded");
console.log("REST/OAuth: same-origin /api/* and /oauth/* through the VPS edge");
console.log("Realtime: existing signed WebSocket snapshot client retained as data transport only");
console.log("Demo/Real switching: /me/switch-account retained in the new shell");
console.log("Backend strategy, scheduler, premium and Lipana authorities remain unchanged");
