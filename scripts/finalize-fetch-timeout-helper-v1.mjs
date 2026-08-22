import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`fetch-timeout-helper missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

let engine = read(enginePath);

// The source execution engine defines fetchWithTimeout() between connectPublic()
// and connectPrivate(). Later public-transport finalizers replace that region and
// can accidentally remove the helper while leaving Start/OAuth code calling it.
// Restore the exact bounded same-origin helper at the final production boundary.
const helper = `  async function fetchWithTimeout(url, options, timeoutMs) {\n    const controller = new AbortController();\n    const timer = setTimeout(() => controller.abort(), timeoutMs);\n    try {\n      return await originalFetch(url, { credentials: "include", cache: "no-store", ...options, signal: controller.signal });\n    } finally {\n      clearTimeout(timer);\n    }\n  }\n\n`;

const helperMarker = "  async function fetchWithTimeout(";
const credentialMarker = "  function clearDirectBrowserCredential() {";

if (!engine.includes(helperMarker)) {
  const markerIndex = engine.indexOf(credentialMarker);
  if (markerIndex < 0) {
    throw new Error("fetch-timeout-helper cannot restore helper: OAuth credential boundary missing");
  }
  engine = engine.slice(0, markerIndex) + helper + engine.slice(markerIndex);
}

const helperCount = engine.split(helperMarker).length - 1;
if (helperCount !== 1) {
  throw new Error(`fetch-timeout-helper expected exactly one helper definition, got ${helperCount}`);
}

for (const required of [
  "async function fetchWithTimeout(url, options, timeoutMs)",
  'apiPath("/me/direct-execution/bootstrap")',
  'apiPath("/me/direct-execution/arm")',
  'apiPath("/me/runtime-sync")',
  "response = await fetchWithTimeout(",
  "const response = await fetchWithTimeout(",
]) {
  if (!engine.includes(required)) {
    throw new Error(`fetch-timeout-helper final engine invariant missing: ${required}`);
  }
}

if (!engine.includes('const originalFetch = window.fetch.bind(window);')) {
  throw new Error("fetch-timeout-helper originalFetch authority missing");
}

write(enginePath, engine);

let index = read(indexPath);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260822-fetch-timeout-restored-v1",
);
if (!index.includes("/deriv-direct-execution-v2.js?v=20260822-fetch-timeout-restored-v1")) {
  throw new Error("fetch-timeout-helper engine cache invariant missing");
}
write(indexPath, index);

console.log("FETCH_TIMEOUT_HELPER_V1_INSTALLED helper_defined_once=true arm_fetch_bounded=true bootstrap_fetch_bounded=true runtime_sync_bounded=true finalizer_last=true");
