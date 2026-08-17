import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { build as esbuild } from "esbuild";

const root = resolve(import.meta.dirname, "..");
const dashboard = resolve(root, "dashboard");
const output = resolve(root, "dist");
const indexPath = resolve(output, "index.html");
const bundledIconExporter = resolve(root, "scripts", ".quill-export-bundle-6f2.mjs");

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
await rm(bundledIconExporter, { force: true });
await mkdir(output, { recursive: true });

// 6F-2 remains a direct-VPS clean shell. Historical dashboard sources can stay in
// Git for regression reference, but the production image receives only this list.
const productionAssets = [
  "index.html",
  "final-ui-shell-v2.css",
  "final-ui-shell-v2.js",
  "vps-api-boundary-v2.js",
  "vps-realtime-client-v2.js",
];
for (const asset of productionAssets) {
  await copyFile(resolve(dashboard, asset), resolve(output, asset));
}

// @deriv/quill-icons is the official ESM package. Its generated category barrels
// contain extensionless internal specifiers which Node 24 does not execute
// directly. Bundle those official Quill modules so esbuild resolves their internal
// imports, but leave React/ReactDOM and Node built-ins native. This avoids the CJS
// react-dom/server dynamic-require shim while still using the exact Deriv exports.
await esbuild({
  entryPoints: [resolve(root, "scripts", "export-deriv-quill-icons-v2.mjs")],
  outfile: bundledIconExporter,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node24",
  packages: "bundle",
  external: [
    "react",
    "react-dom",
    "react-dom/*",
    "node:*",
  ],
  logLevel: "warning",
});
try {
  await import(`${new URL(`file://${bundledIconExporter}`).href}?build=${Date.now()}`);
} finally {
  await rm(bundledIconExporter, { force: true });
}

let html = await readFile(indexPath, "utf8");
html = html
  .replace(
    '<meta name="stream-base-url" content="">',
    `<meta name="stream-base-url" content="${streamBase}">`,
  )
  .replace(
    '<meta name="frontend-runtime" content="direct-vps-final-ui-6f2">',
    '<meta name="frontend-runtime" content="full-vps-final-ui-6f2">',
  );

const required = [
  '<meta name="frontend-runtime" content="full-vps-final-ui-6f2">',
  'meta name="stream-base-url"',
  '/vps-api-boundary-v2.js?v=20260817-6f2-1',
  '/deriv-quill-icons-v2.js?v=2.4.18',
  '/vps-realtime-client-v2.js?v=20260817-6f2-1',
  '/final-ui-shell-v2.css?v=20260817-6f2-1',
  '/final-ui-shell-v2.js?v=20260817-6f2-1',
];
for (const marker of required) {
  if (!html.includes(marker)) throw new Error(`Action 6F-2 VPS marker missing: ${marker}`);
}

const forbiddenProductionUi = [
  '/netlify-api-boundary.js',
  '/netlify-realtime-client.js',
  '/vps-api-boundary.js?v=20260817-1',
  '/vps-realtime-client.js?v=20260817-6f1-2',
  '/final-ui-shell-v1.css',
  '/final-ui-shell-v1.js',
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
  if (html.includes(marker)) throw new Error(`Retired presentation leaked into direct VPS 6F-2 UI: ${marker}`);
}

const iconManifest = JSON.parse(await readFile(resolve(output, "deriv-quill-icons-v2.json"), "utf8"));
for (const key of ["over", "under", "matches", "differs", "even", "odd", "rise", "fall", "demoAccount", "realAccount", "usd", "volatility"]) {
  if (!iconManifest.exports?.[key]) throw new Error(`Official Deriv Quill icon provenance missing: ${key}`);
}

await writeFile(indexPath, html, "utf8");
await writeFile(
  resolve(output, "vps-build.json"),
  `${JSON.stringify({
    frontend_runtime: "full-vps-final-ui-6f2",
    deployment_topology: "direct-vps-only",
    ui_authority: "final-ui-shell-v2",
    authenticated_ui: "six-screen-reconstruction-6f2-v1",
    production_asset_policy: "new-shell-whitelist-only",
    legacy_ui_loaded: false,
    legacy_ui_shipped: false,
    netlify_runtime_loaded: false,
    mockup_contract: "six-approved-mobile-screens-authoritative",
    run_panel: "deriv-transaction-ledger-v1",
    run_panel_source: "me-trades-today-real-and-virtual-stream",
    deriv_icons: "official-quill-icons-2.4.18-build-time-static-svg",
    deriv_icon_repository: "deriv-com/quill-icons",
    deriv_icon_build_resolution: "esbuild-quill-only-react-native-externals-v1",
    linked_account_selector: "specific-linked-options-account-v1",
    public_origin: publicOrigin,
    api_base: "/api",
    oauth_base: "/oauth",
    websocket_base: streamBase,
    api_boundary: "direct-vps-same-origin-rest-6f2",
    realtime_client: "vps-realtime-client-v2",
    account_switching: "specific-linked-account-post-me-switch-account",
    text_to_strategy: "250-word-nearest-supported-review-first-v1",
    builder_execution: "canonical-custom-strategy-existing-worker-authority",
    schedule_execution: "persistent-server-scheduler-existing-worker-authority-v1",
    schedule_persistence: "postgres-restart-safe-exactly-once-claim-v1",
    schedule_ui_and_library: "reconstructed-6f2-v1",
    premium_access: "weekly-linked-options-server-gate-action6a-v1",
    premium_period: "exact-7-days-no-grace-v1",
    premium_prices: "KES250-mpesa-only-v1",
    premium_payment: "lipana-stk-verified-webhook-v1",
    premium_renewal: "manual-mpesa-after-exact-expiry-v1",
    generated_at: new Date().toISOString(),
  }, null, 2)}\n`,
  "utf8",
);

console.log("Direct VPS Action 6F-2 frontend built.");
console.log(`Public origin: ${publicOrigin}`);
console.log(`Realtime: ${streamBase}/ws/me/live`);
console.log("UI authority: final-ui-shell-v2; six-screen reconstruction plus live run ledger");
console.log("Official Deriv icons: @deriv/quill-icons 2.4.18 exported to local static SVGs");
console.log("Run ledger: /me/trades/today; specific linked account selector: /me/accounts + /me/switch-account");
console.log("No Netlify or retired 6F-1 presentation is shipped in the production artifact");
