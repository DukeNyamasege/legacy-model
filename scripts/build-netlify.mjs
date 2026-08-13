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
html = html
  .replace('<meta name="api-base-url" content="">', '<meta name="api-base-url" content="/api">')
  .replace(
    '<meta name="api-base-url" content="/api">',
    `<meta name="api-base-url" content="/api">\n  <meta name="stream-base-url" content="${streamBase}">\n  <meta name="frontend-runtime" content="netlify-vps-split-v1">`,
  )
  .replaceAll('href="/ui/dashboard-v2.css"', 'href="/dashboard-v2.css"')
  .replaceAll('src="/ui/dashboard-v2.js"', 'src="/dashboard-v2.js"')
  .replaceAll('src="/ui/dashboard-actions-v2.js"', 'src="/dashboard-actions-v2.js"');

const boundaryScript = '  <script src="/netlify-api-boundary.js"></script>\n';
if (!html.includes('/netlify-api-boundary.js')) {
  html = html.replace("</head>", `${boundaryScript}</head>`);
}

if (!html.includes('/result-ui-fixes.css')) {
  html = html.replace(
    "</head>",
    '  <link rel="stylesheet" href="/result-ui-fixes.css">\n</head>',
  );
}

const dashboardMarker = '<script src="/dashboard-v2.js" defer></script>';
if (!html.includes(dashboardMarker)) {
  throw new Error("Static dashboard-v2 script marker was not found");
}
const runtimeScript = '  <script src="/custom-runtime-client.js" defer></script>\n';
if (!html.includes('/custom-runtime-client.js')) {
  html = html.replace(dashboardMarker, `${runtimeScript}  ${dashboardMarker}`);
}
const oauthScript = '\n  <script src="/oauth-direct-runtime.js" defer></script>';
if (!html.includes('/oauth-direct-runtime.js')) {
  html = html.replace(dashboardMarker, `${dashboardMarker}${oauthScript}`);
}
const realtimeScript = '  <script src="/netlify-realtime-client.js" defer></script>\n';
if (!html.includes('/netlify-realtime-client.js')) {
  html = html.replace("</body>", `${realtimeScript}</body>`);
}

const resultScriptMarker = '<script src="./result-based-strategy.js" defer></script>';
if (!html.includes('/prediction-ui-fix.js')) {
  if (html.includes(resultScriptMarker)) {
    html = html.replace(
      resultScriptMarker,
      '  <script src="/prediction-ui-fix.js" defer></script>\n  ' + resultScriptMarker,
    );
  } else {
    html = html.replace("</body>", '  <script src="/prediction-ui-fix.js" defer></script>\n</body>');
  }
}
if (!html.includes('/result-ui-fixes.js')) {
  if (html.includes(resultScriptMarker)) {
    html = html.replace(
      resultScriptMarker,
      resultScriptMarker + '\n  <script src="/result-ui-fixes.js" defer></script>',
    );
  } else {
    html = html.replace("</body>", '  <script src="/result-ui-fixes.js" defer></script>\n</body>');
  }
}

await writeFile(indexPath, html, "utf8");

const cssPath = resolve(output, "dashboard-v2.css");
const [desktopCss, mobileCss] = await Promise.all([
  readFile(resolve(root, "dashboard", "dashboard-v2.css"), "utf8"),
  readFile(resolve(root, "dashboard", "mobile-first-compact.css"), "utf8"),
]);
await writeFile(cssPath, `${desktopCss}\n\n/* NETLIFY FINAL MOBILE LAYER */\n${mobileCss}\n`, "utf8");

const redirects = [];
if (backendOrigin) {
  redirects.push(`/api/* ${backendOrigin}/:splat 200`);
  redirects.push(`/oauth/* ${backendOrigin}/oauth/:splat 200`);
  redirects.push(`/backend-health ${backendOrigin}/health/frontend-backend 200`);
}
redirects.push("/* /index.html 200");
await writeFile(resolve(output, "_redirects"), `${redirects.join("\n")}\n`, "utf8");

console.log("Netlify production frontend built.");
console.log(`REST/OAuth backend: ${backendOrigin || "not configured (static preview only)"}`);
console.log(`Realtime backend: ${streamBase || "not configured (HTTP fallback only)"}`);
