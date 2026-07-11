import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const source = resolve(root, "dashboard");
const output = resolve(root, "dist");
const rawApiBase = (process.env.DASHBOARD_API_BASE_URL || "").trim().replace(/\/+$/, "");
const hasExternalApiBase = Boolean(rawApiBase);
let apiUrl = null;
if (hasExternalApiBase) {
  apiUrl = new URL(rawApiBase);
  if (apiUrl.protocol !== "https:" && apiUrl.hostname !== "localhost") {
    throw new Error("API_BASE_URL must use HTTPS outside local development");
  }
  if (apiUrl.pathname !== "/" || apiUrl.search || apiUrl.hash) {
    throw new Error(
      "API_BASE_URL must contain only the backend origin, without a path",
    );
  }
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(source, output, { recursive: true });

const indexPath = resolve(output, "index.html");
const sourceHtml = await readFile(indexPath, "utf8");
const escapedApiBase = rawApiBase
  .replaceAll("&", "&amp;")
  .replaceAll('"', "&quot;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;");
const builtHtml = sourceHtml.replace("__API_BASE_URL__", escapedApiBase);
if (builtHtml === sourceHtml) {
  throw new Error("Dashboard API URL placeholder was not found");
}
await writeFile(indexPath, builtHtml, "utf8");

const connectSrc = hasExternalApiBase
  ? `'self' ${apiUrl.origin} ${apiUrl.origin.replace(/^https?:/, "wss:")} ${apiUrl.origin.replace(/^https?:/, "ws:")}`
  : "'self'";
const headers = `/*
  Content-Security-Policy: default-src 'self'; connect-src ${connectSrc}; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
  Referrer-Policy: strict-origin-when-cross-origin
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY

/index.html
  Cache-Control: no-cache
`;
await writeFile(resolve(output, "_headers"), headers, "utf8");
console.log(
  hasExternalApiBase
    ? `Netlify dashboard built for ${apiUrl.origin}`
    : "Netlify dashboard built for same-origin functions",
);
