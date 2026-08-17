import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const dashboard = resolve(root, "dashboard");
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

const stream = new URL(publicOrigin);
stream.protocol = stream.protocol === "https:" ? "wss:" : "ws:";
const streamBase = String(process.env.DASHBOARD_WS_BASE_URL || stream.origin).trim().replace(/\/+$/, "");
const parsedStream = new URL(streamBase);
if (!["wss:", "ws:"].includes(parsedStream.protocol)) {
  throw new Error("DASHBOARD_WS_BASE_URL must use WSS or WS");
}
if (parsedStream.protocol !== "wss:" && parsedStream.hostname !== "localhost") {
  throw new Error("DASHBOARD_WS_BASE_URL must use WSS outside local development");
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });

// 6F-1 is a clean replacement, not a legacy-dashboard overlay. Only assets owned
// by the new direct-VPS shell are copied into the production image. Historical UI
// sources stay in Git temporarily for migration/regression reference but are not
// served by the product.
const productionAssets = [
  "index.html",
  "final-ui-shell-v1.css",
  "final-ui-shell-v1.js",
  "vps-api-boundary.js",
  "vps-realtime-client.js",
];
for (const asset of productionAssets) {
  await copyFile(resolve(dashboard, asset), resolve(output, asset));
}

let html = await readFile(indexPath, "utf8");
html = html
  .replace(
    '<meta name="stream-base-url" content="">',
    `<meta name="stream-base-url" content="${streamBase}">`,
  )
  .replace(
    '<meta name="frontend-runtime" content="direct-vps-final-ui-6f1">',
    '<meta name="frontend-runtime" content="full-vps-final-ui-6f1">',
  );

const required = [
  '<meta name="frontend-runtime" content="full-vps-final-ui-6f1">',
  'meta name="stream-base-url"',
  '/vps-api-boundary.js?v=20260817-1',
  '/vps-realtime-client.js?v=20260817-6f1-2',
  '/final-ui-shell-v1.css?v=20260817-6f1-1',
  '/final-ui-shell-v1.js?v=20260817-6f1-1',
];
for (const marker of required) {
  if (!html.includes(marker)) throw new Error(`Action 6F-1 VPS marker missing: ${marker}`);
}

const forbiddenProductionUi = [
  '/netlify-api-boundary.js',
  '/netlify-realtime-client.js',
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
for (const marker of forbiddenProductionUi) {
  if (html.includes(marker)) throw new Error(`Legacy/Netlify presentation leaked into direct VPS UI: ${marker}`);
}

await writeFile(indexPath, html, "utf8");

await writeFile(
  resolve(output, "vps-build.json"),
  `${JSON.stringify({
    frontend_runtime: "full-vps-final-ui-6f1",
    deployment_topology: "direct-vps-only",
    ui_authority: "final-ui-shell-v1",
    production_asset_policy: "new-shell-whitelist-only",
    legacy_ui_loaded: false,
    legacy_ui_shipped: false,
    netlify_runtime_loaded: false,
    mockup_contract: "six-approved-mobile-screens-authoritative",
    public_origin: publicOrigin,
    api_base: "/api",
    oauth_base: "/oauth",
    websocket_base: streamBase,
    api_boundary: "full-vps-same-origin-rest-v3",
    realtime_client: "vps-realtime-client-v1",
    account_switching: "demo-real-post-me-switch-account",
    schedule_execution: "persistent-server-scheduler-existing-worker-authority-v1",
    schedule_persistence: "postgres-restart-safe-exactly-once-claim-v1",
    schedule_ui_and_library: "reconstructed-in-6f2",
    premium_access: "weekly-linked-options-server-gate-action6a-v1",
    premium_period: "exact-7-days-no-grace-v1",
    premium_prices: "KES250-mpesa-only-v1",
    premium_payment: "lipana-stk-verified-webhook-v1",
    premium_renewal: "manual-mpesa-after-exact-expiry-v1",
    generated_at: new Date().toISOString(),
  }, null, 2)}\n`,
  "utf8",
);

console.log("Direct VPS Action 6F-1 frontend built.");
console.log(`Public origin: ${publicOrigin}`);
console.log(`Realtime: ${streamBase}/ws/me/live`);
console.log("UI authority: final-ui-shell-v1; production artifact contains only new-shell assets");
console.log("REST/OAuth: same-origin /api/* and /oauth/* through the VPS edge");
console.log("Demo/Real switching: /me/switch-account retained in the new shell");
console.log("Backend strategy, scheduler, premium and Lipana authorities remain unchanged");
