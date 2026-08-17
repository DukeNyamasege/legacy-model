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
 * Reuse the proven production dashboard compiler so the Netlify fallback and the
 * VPS build receive the exact same UI assets and cache-bust revisions. In VPS
 * mode the generated _redirects file is removed because host Nginx owns routing.
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
html = html
  .replace(
    '<meta name="frontend-runtime" content="netlify-vps-split-v1">',
    '<meta name="frontend-runtime" content="full-vps-same-origin-v1">',
  )
  .replace(
    '<script src="/netlify-api-boundary.js"></script>',
    '<script src="/vps-api-boundary.js?v=20260817-1"></script>',
  );

/* Action 1: authenticated Automation Home + universal mobile app shell.
 * The assets live in dashboard/ and are copied into dist by build-netlify.mjs.
 * Install them only in the full-VPS product for now; they do not change worker
 * execution or the existing Builder/Trades controllers. */
if (!html.includes('/automation-home-v1.css?v=20260817-1')) {
  html = html.replace(
    "</head>",
    '  <link rel="stylesheet" href="/automation-home-v1.css?v=20260817-1">\n</head>',
  );
}
if (!html.includes('/automation-home-v1.js?v=20260817-1')) {
  html = html.replace(
    "</body>",
    '  <script src="/automation-home-v1.js?v=20260817-1" defer></script>\n</body>',
  );
}

if (!html.includes('/vps-api-boundary.js?v=20260817-1')) {
  throw new Error("Full VPS API boundary was not installed into the production HTML");
}
if (html.includes('<script src="/netlify-api-boundary.js"></script>')) {
  throw new Error("Netlify 3.2-second API boundary must not remain active on full VPS");
}
if (!html.includes('/automation-home-v1.css?v=20260817-1')) {
  throw new Error("Action 1 Automation Home stylesheet was not installed");
}
if (!html.includes('/automation-home-v1.js?v=20260817-1')) {
  throw new Error("Action 1 Automation Home controller was not installed");
}

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
    authenticated_ui: "automation-home-action1-v1",
    generated_at: new Date().toISOString(),
  }, null, 2)}\n`,
  "utf8",
);

console.log("Full VPS frontend built.");
console.log(`Public origin: ${publicOrigin}`);
console.log("REST: same-origin /api/* -> host Nginx -> API container");
console.log("OAuth: same-origin /oauth/* -> host Nginx -> API container");
console.log("API boundary: full-vps-same-origin-rest-v3 (no 3.2s false timeout)");
console.log("Authenticated UI: automation-home-action1-v1");
console.log(`Realtime: ${process.env.DASHBOARD_WS_BASE_URL}/ws/me/live`);
