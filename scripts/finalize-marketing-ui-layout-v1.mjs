import fs from "node:fs";

const shellPath = "dist/final-ui-shell-v2.js";
const marketingPath = "dist/direct-demo-reset-router-v1.js";
const runtimePath = "dist/direct-runtime-ux-v4.js";
const premiumPath = "dist/final-premium-6f3.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`marketing-ui-layout missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`marketing-ui-layout ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

function replaceBetween(source, start, end, replacement, label) {
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`marketing-ui-layout ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

// ---------------------------------------------------------------------------
// 1. Restore the approved Run-panel order.
// Tabs -> tab content -> totals. The previous finalizer moved totals above the
// Transactions/Journal body, which is the UI regression shown in production.
// Metrics still refresh on every tab; only their visual position is restored.
// ---------------------------------------------------------------------------
let shell = read(shellPath);
shell = replaceOne(
  shell,
  `        <div class="run-panel-tabs">\${runPanelTabs(activeTab)}</div>\n        <div class="run-panel-stats" data-run-uniform-metrics="true">\${runPanelStatsMarkup(stats, currency)}</div>\n        <div class="run-panel-body">\${runPanelContent(activeTab, stats, currency, running)}</div>`,
  `        <div class="run-panel-tabs">\${runPanelTabs(activeTab)}</div>\n        <div class="run-panel-body">\${runPanelContent(activeTab, stats, currency, running)}</div>\n        <div class="run-panel-stats" data-run-uniform-metrics="true">\${runPanelStatsMarkup(stats, currency)}</div>`,
  "run totals below tab content",
);
const bodyMarker = '<div class="run-panel-body">${runPanelContent(activeTab, stats, currency, running)}</div>';
const statsMarker = '<div class="run-panel-stats" data-run-uniform-metrics="true">${runPanelStatsMarkup(stats, currency)}</div>';
if (shell.indexOf(bodyMarker) < 0 || shell.indexOf(statsMarker) < 0 || shell.indexOf(bodyMarker) > shell.indexOf(statsMarker)) {
  throw new Error("marketing-ui-layout run panel totals are not below tab content");
}
write(shellPath, shell);

// ---------------------------------------------------------------------------
// 2. Marketing account stays one normal Deriv demo account.
// Remove the explanatory banner entirely. It was an implementation note, not part
// of the approved UI. Expose a tiny presentation API so the ordinary runtime can
// render the selected 75%/25% visual balance instead of repainting provider total.
// No fetch, account switch, WebSocket send, proposal, BUY or receipt is changed.
// ---------------------------------------------------------------------------
let marketing = read(marketingPath);
marketing = replaceBetween(
  marketing,
  `  function renderBadge() {`,
  `  function renderMarketingUi() {`,
  `  function renderBadge() {\n    document.querySelectorAll(".marketing-tutorial-runtime-badge").forEach((node) => node.remove());\n  }\n\n  function displayBalance(selectedView = view()) {\n    if (!active()) return null;\n    const value = visibleBalance(readLedger(), selectedView === "rot" ? "rot" : "dot");\n    return Math.round(Number(value || 0) * 100) / 100;\n  }\n\n`,
  "remove tutorial banner and expose display balance",
);
marketing = replaceOne(
  marketing,
  `    version: "20260821-marketing-dot-rot-v4-ui-only",`,
  `    version: "20260821-marketing-dot-rot-v5-ui-only-layout",`,
  "marketing UI version",
);
marketing = replaceOne(
  marketing,
  `    available_balance: () => visibleBalance(readLedger()),\n    reset_projection: splitReset,`,
  `    available_balance: () => displayBalance(),\n    display_balance: () => displayBalance(),\n    balance_for_view: (requestedView) => displayBalance(requestedView === "rot" ? "rot" : "dot"),\n    render_now: renderMarketingUi,\n    reset_projection: splitReset,`,
  "marketing display balance API",
);
if (marketing.includes("One Deriv demo account") || marketing.includes("UI split · DOT 75% · ROT 25%")) {
  throw new Error("marketing-ui-layout tutorial implementation banner survived production finalization");
}
for (const required of [
  "display_balance: () => displayBalance()",
  "balance_for_view:",
  "render_now: renderMarketingUi",
  "marketing-dot-rot-v5-ui-only-layout",
]) {
  if (!marketing.includes(required)) throw new Error(`marketing-ui-layout marketing invariant missing: ${required}`);
}
write(marketingPath, marketing);

// ---------------------------------------------------------------------------
// 3. Make the ordinary account renderer respect the UI-only projection.
// The runtime redraws account balance frequently. Previously each redraw restored
// the raw Deriv provider balance, making the split appear to disappear. Read the
// projection only for presentation; providerBalance itself remains untouched.
// Synthetic DOT/ROT rows are also left to the presentation renderer so the normal
// account renderer cannot overwrite their labels or amounts.
// ---------------------------------------------------------------------------
let runtime = read(runtimePath);
runtime = replaceOne(
  runtime,
  `    const selected = selectedAccount();\n    const balance = providerBalance !== null ? providerBalance : selected?.balance;\n    const currency = providerCurrency || selected?.currency || "USD";`,
  `    const selected = selectedAccount();\n    let balance = providerBalance !== null ? providerBalance : selected?.balance;\n    const currency = providerCurrency || selected?.currency || "USD";\n    try {\n      const marketingUi = window.DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1;\n      if (marketingUi?.marketing_ui_active?.()) {\n        const projected = Number(marketingUi.display_balance?.());\n        if (Number.isFinite(projected)) balance = projected;\n      }\n    } catch (_) {}`,
  "top balance respects marketing projection",
);
runtime = replaceOne(
  runtime,
  `      const account = accounts.get(Number(row.getAttribute("data-account-id") || 0));\n      if (!account) return;\n      const idText = fullId(account);`,
  `      const account = accounts.get(Number(row.getAttribute("data-account-id") || 0));\n      if (!account) return;\n      if (row.dataset.marketingView) return;\n      const idText = fullId(account);`,
  "synthetic marketing rows remain presentation-owned",
);
for (const required of [
  "marketingUi?.marketing_ui_active?.()",
  "marketingUi.display_balance?.()",
  "if (row.dataset.marketingView) return;",
]) {
  if (!runtime.includes(required)) throw new Error(`marketing-ui-layout runtime invariant missing: ${required}`);
}
write(runtimePath, runtime);

// ---------------------------------------------------------------------------
// 4. Cache-bust all corrected presentation assets.
// ---------------------------------------------------------------------------
let premium = read(premiumPath);
premium = premium.replace(
  /\/final-ui-shell-v2\.js\?v=[^"']+/g,
  "/final-ui-shell-v2.js?v=20260821-runpanel-bottom-v2",
);
if (!premium.includes("/final-ui-shell-v2.js?v=20260821-runpanel-bottom-v2")) {
  throw new Error("marketing-ui-layout shell cache-bust missing");
}
write(premiumPath, premium);

let index = read(indexPath);
index = index.replace(
  /\/final-premium-6f3\.js\?v=[^"']+/g,
  "/final-premium-6f3.js?v=20260821-marketing-layout-v2",
);
index = index.replace(
  /\/direct-demo-reset-router-v1\.js\?v=[^"']+/g,
  "/direct-demo-reset-router-v1.js?v=20260821-marketing-ui-v5",
);
index = index.replace(
  /\/direct-runtime-ux-v4\.js\?v=[^"']+/g,
  "/direct-runtime-ux-v4.js?v=20260821-marketing-balance-v7",
);
for (const required of [
  "/final-premium-6f3.js?v=20260821-marketing-layout-v2",
  "/direct-demo-reset-router-v1.js?v=20260821-marketing-ui-v5",
  "/direct-runtime-ux-v4.js?v=20260821-marketing-balance-v7",
]) {
  if (!index.includes(required)) throw new Error(`marketing-ui-layout cache invariant missing: ${required}`);
}
write(indexPath, index);

console.log("Marketing UI layout finalizer: Run totals restored below tab content");
console.log("Marketing UI layout finalizer: tutorial implementation badge removed");
console.log("Marketing UI layout finalizer: selected 75/25 presentation balance survives normal runtime redraws");
console.log("Marketing UI layout finalizer: backend and Deriv execution path unchanged");
