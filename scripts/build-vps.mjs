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

/*
 * Reuse the proven dashboard compiler so compatibility assets and the VPS build
 * receive the same core dashboard. In VPS mode the generated _redirects file is
 * removed because Caddy owns public routing.
 */
process.env.BACKEND_ORIGIN = publicOrigin;
if (!String(process.env.DASHBOARD_WS_BASE_URL || "").trim()) {
  const stream = new URL(publicOrigin);
  stream.protocol = stream.protocol === "https:" ? "wss:" : "ws:";
  process.env.DASHBOARD_WS_BASE_URL = stream.origin;
}

await import(`./build-netlify.mjs?vps=${Date.now()}`);

await rm(resolve(output, "_redirects"), { force: true });

let html = await readFile(indexPath, "utf8");
html = html.replace(
  '<meta name="frontend-runtime" content="netlify-vps-split-v1">',
  '<meta name="frontend-runtime" content="full-vps-same-origin-v2">',
);

/*
 * Force a new browser URL for the local API boundary on every recovery release.
 * The old unversioned URL could leave a mobile browser executing the Netlify-era
 * 3.2-second timeout even after the VPS source had been corrected.
 */
html = html.replace(
  '<script src="/netlify-api-boundary.js"></script>',
  '<script src="/netlify-api-boundary.js?v=20260816-vps3"></script>',
);

if (!html.includes('/vps-seamless-experience.css')) {
  html = html.replace(
    "</head>",
    '  <link rel="stylesheet" href="/vps-seamless-experience.css?v=20260816-3">\n</head>',
  );
}

/*
 * This small non-deferred preloader intentionally runs before dashboard-v2's
 * deferred boot. It observes the existing signed WebSocket, suppresses the old
 * 15-second full-shell refresh while realtime is healthy, owns instant unchanged
 * Start/Resume/Stop, and repairs a transient authenticated-shell bootstrap miss.
 */
const dashboardMarker = '<script src="/dashboard-v2.js" defer></script>';
if (!html.includes(dashboardMarker)) {
  throw new Error("Full VPS dashboard marker was not found");
}
if (!html.includes('/vps-seamless-experience.js')) {
  html = html.replace(
    dashboardMarker,
    '  <script src="/vps-seamless-experience.js?v=20260816-3"></script>\n  ' + dashboardMarker,
  );
}

await writeFile(indexPath, html, "utf8");

await writeFile(
  resolve(output, "vps-build.json"),
  `${JSON.stringify({
    frontend_runtime: "full-vps-same-origin-v2",
    public_origin: publicOrigin,
    api_base: "/api",
    oauth_base: "/oauth",
    websocket_base: process.env.DASHBOARD_WS_BASE_URL,
    realtime_events: "same-origin signed websocket + private Docker event bus",
    generated_at: new Date().toISOString(),
  }, null, 2)}\n`,
  "utf8",
);

console.log("Full VPS frontend built.");
console.log(`Public origin: ${publicOrigin}`);
console.log("REST: same-origin /api/* -> Caddy -> API container");
console.log("OAuth: same-origin /oauth/* -> Caddy -> API container");
console.log(`Realtime: ${process.env.DASHBOARD_WS_BASE_URL}/ws/me/live`);
console.log("Live strategy scanner: private worker -> API event bus -> signed browser WebSocket");
