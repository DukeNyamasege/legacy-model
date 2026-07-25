import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "dist");
const apiBase = (process.env.DASHBOARD_API_BASE_URL || "")
  .trim()
  .replace(/\/+$/, "");

if (apiBase) {
  const url = new URL(apiBase);
  if (url.protocol !== "https:" && url.hostname !== "localhost") {
    throw new Error("DASHBOARD_API_BASE_URL must use HTTPS");
  }
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new Error("DASHBOARD_API_BASE_URL must be an origin without a path");
  }
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(resolve(root, "dashboard"), output, { recursive: true });

const indexPath = resolve(output, "index.html");
const html = await readFile(indexPath, "utf8");
const escapedApiBase = apiBase
  .replaceAll("&", "&amp;")
  .replaceAll('"', "&quot;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;");
const marker = '<meta name="api-base-url" content="">';
if (!html.includes(marker)) {
  throw new Error("Dashboard API meta tag was not found");
}
await writeFile(
  indexPath,
  html.replace(marker, `<meta name="api-base-url" content="${escapedApiBase}">`),
  "utf8",
);

console.log(
  apiBase
    ? `Dashboard preview built for API ${apiBase}`
    : "Dashboard preview built without an external API origin",
);
