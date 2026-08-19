import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { build as esbuild } from "esbuild";

const root = resolve(import.meta.dirname, "..");
const dashboard = resolve(root, "dashboard");
const output = resolve(root, "dist");
const indexPath = resolve(output, "index.html");
const premiumPath = resolve(output, "final-premium-6f3.js");
const bundledIconExporter = resolve(root, "scripts", ".quill-export-bundle-6f3.mjs");

const publicOrigin = String(
  process.env.PUBLIC_ORIGIN
  || process.env.BACKEND_ORIGIN
  || "https://derivadmin.site",
).trim().replace(/\/+$/, "");
const publicTestingFreeAccess = !["0", "false", "no", "off"].includes(
  String(process.env.PUBLIC_TESTING_FREE_ACCESS ?? "true").trim().toLowerCase(),
);

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

const productionAssets = [
  "index.html",
  "final-ui-shell-v2.css",
  "mobile-reference-ui.css",
  "real-account-flag.webp",
  "final-ui-shell-v2.js",
  "final-premium-6f3.css",
  "final-premium-6f3.js",
  "public-testing-runtime-v1.js",
  "vps-api-boundary-v2.js",
  "vps-realtime-client-v2.js",
];
for (const asset of productionAssets) {
  await copyFile(resolve(dashboard, asset), resolve(output, asset));
}

let premiumSource = await readFile(premiumPath, "utf8");
const testingFlagMarker = "const TESTING_FREE_ACCESS = true;";
if (!premiumSource.includes(testingFlagMarker)) {
  throw new Error("6F-3 testing access marker is missing from premium bootstrap");
}
premiumSource = premiumSource.replace(
  testingFlagMarker,
  `const TESTING_FREE_ACCESS = ${publicTestingFreeAccess ? "true" : "false"};`,
);
await writeFile(premiumPath, premiumSource, "utf8");

await esbuild({
  entryPoints: [resolve(root, "scripts", "export-deriv-quill-icons-v2.mjs")],
  outfile: bundledIconExporter,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node24",
  packages: "bundle",
  external: ["react", "react-dom", "react-dom/*", "node:*"],
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
    '<meta name="frontend-runtime" content="direct-vps-final-ui-6f3">',
    '<meta name="frontend-runtime" content="full-vps-final-ui-6f3">\n  <meta name="frontend-release-20260819-live-fix-v1" content="present">',
  );

const required = [
  '<meta name="frontend-runtime" content="full-vps-final-ui-6f3">',
  'meta name="frontend-release-20260819-live-fix-v1" content="present"',
  '<meta name="frontend-authority" content="final-ui-shell-v2">',
  'meta name="stream-base-url"',
  '/vps-api-boundary-v2.js?v=20260818-local-ui-12',
  '/deriv-quill-icons-v2.js?v=2.4.18',
  '/final-ui-shell-v2.css?v=20260817-6f2-1',
  '/final-premium-6f3.css?v=20260817-6f3-1',
  '/mobile-reference-ui.css?v=20260818-local-ui-12',
  '/final-premium-6f3.js?v=20260818-local-ui-12',
  '/public-testing-runtime-v1.js?v=20260818-public-testing-run-v5',
];
for (const marker of required) {
  if (!html.includes(marker)) throw new Error(`Action 6F-3 VPS marker missing: ${marker}`);
}

for (const marker of [
  '<script src="/vps-realtime-client-v2.js?v=20260817-6f2-1" defer>',
  '<script src="/final-ui-shell-v2.js?v=20260817-6f2-1" defer>',
]) {
  if (html.includes(marker)) throw new Error(`Premium admission bypass found in 6F-3 document: ${marker}`);
}

const forbiddenProductionUi = [
  '/netlify-api-boundary.js', '/netlify-realtime-client.js',
  '/vps-api-boundary.js?v=20260817-1', '/vps-realtime-client.js?v=20260817-6f1-2',
  '/final-ui-shell-v1.css', '/final-ui-shell-v1.js',
  '/ui/dashboard-v2.css', '/ui/dashboard-v2.js', '/ui/dashboard-actions-v2.js',
  'automation-home-v1', 'text-to-strategy-v1', 'strategy-ready-v1', 'timezone-schedule-v1',
  'automation-scheduler-action5', 'premium-subscription-action6e', 'final-dashboard-authority',
  'mobile-topbar-compact', 'tablet-navigation-fix',
];
for (const marker of forbiddenProductionUi) {
  if (html.includes(marker)) throw new Error(`Retired presentation leaked into direct VPS 6F-3 UI: ${marker}`);
}

const iconManifest = JSON.parse(await readFile(resolve(output, "deriv-quill-icons-v2.json"), "utf8"));
for (const key of ["over", "under", "matches", "differs", "even", "odd", "rise", "fall", "demoAccount", "realAccount", "usd", "volatility"]) {
  if (!iconManifest.exports?.[key]) throw new Error(`Official Deriv Quill icon provenance missing: ${key}`);
}

await writeFile(indexPath, html, "utf8");
await writeFile(
  resolve(output, "vps-build.json"),
  `${JSON.stringify({
    frontend_runtime: "full-vps-final-ui-6f3",
    deployment_topology: "direct-vps-only",
    ui_authority: "final-ui-shell-v2",
    premium_bootstrap: "final-premium-6f3",
    authenticated_ui: "six-screen-reconstruction-6f2-with-testing-free-access-6f3",
    production_asset_policy: "final-authority-whitelist-only",
    legacy_ui_loaded: false,
    legacy_ui_shipped: false,
    netlify_runtime_loaded: false,
    mockup_contract: "six-approved-mobile-screens-authoritative",
    run_panel: "deriv-transaction-ledger-v1",
    run_panel_source: "me-trades-today-real-and-virtual-stream",
    run_default_tab: "transactions-on-start-v1",
    instant_run: "one-click-save-if-needed-then-resume-worker-v1",
    journal_analysis: "live-public-deriv-tick-observability-mirror-v1",
    journal_financial_authority: "backend-private-websocket-only",
    deriv_icons: "official-quill-icons-2.4.18-build-time-static-svg",
    deriv_icon_repository: "deriv-com/quill-icons",
    deriv_icon_build_resolution: "esbuild-quill-only-react-native-externals-v1",
    linked_account_selector: "specific-linked-options-account-v1",
    public_origin: publicOrigin,
    public_testing_free_access: publicTestingFreeAccess,
    api_base: "/api",
    oauth_base: "/oauth",
    websocket_base: streamBase,
    api_boundary: "direct-vps-same-origin-rest-6f2",
    realtime_client: "testing-admitted-vps-realtime-client-v2",
    account_switching: "specific-linked-account-post-me-switch-account",
    text_to_strategy: "250-word-nearest-supported-review-first-v1",
    builder_execution: "canonical-custom-strategy-existing-worker-authority",
    schedule_execution: "persistent-server-scheduler-existing-worker-authority-v1",
    schedule_persistence: "postgres-restart-safe-exactly-once-claim-v1",
    schedule_ui_and_library: "reconstructed-6f2-v1",
    premium_access: publicTestingFreeAccess
      ? "public-testing-free-bypass-premium-retained-v2"
      : "weekly-linked-options-server-gate-action6a-v1",
    premium_period: "exact-7-days-no-grace-v1",
    premium_prices: "KES250-mpesa-only-retained-for-later-v1",
    premium_payment: "lipana-stk-verified-webhook-v1",
    premium_renewal: "manual-mpesa-after-exact-expiry-retained-v1",
    premium_ui: publicTestingFreeAccess ? "hidden-during-public-testing-v1" : "paid-gate-active-v1",
    premium_runtime_admission: publicTestingFreeAccess
      ? "testing-users-load-shell-realtime-and-run-controller-v2"
      : "unpaid-users-do-not-load-shell-or-realtime-v2",
    premium_unlock_authority: "future-paid-mode-server-entitlement-only-v1",
    final_product_qa: "oauth-accounts-free-testing-builder-ai-schedule-instant-trades-mobile-v2",
    generated_at: new Date().toISOString(),
  }, null, 2)}\n`,
  "utf8",
);

console.log("Direct VPS Action 6F-3 frontend built.");
console.log(`Public origin: ${publicOrigin}`);
console.log(`Realtime: ${streamBase}/ws/me/live (loaded immediately after authenticated testing access)`);
console.log(`Public testing free access: ${publicTestingFreeAccess ? "enabled" : "disabled"}`);
console.log("UI authority: final-ui-shell-v2 with public-testing run controller");
console.log(publicTestingFreeAccess
  ? "Premium: retained for later launch; public testing is free and paywall UI is hidden"
  : "Premium: paid server entitlement gate is active");
console.log("Run flow: instant start -> backend worker; scheduled start -> persistent scheduler -> same worker");
console.log("Journal: live public Deriv tick mirror; proposal/BUY remain backend private-WebSocket only");
console.log("No Netlify or retired Action UI is shipped in the production artifact");
