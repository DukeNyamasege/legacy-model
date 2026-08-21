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
// Tabs -> tab content -> totals. Metrics still refresh on every tab, but the totals
// remain at the bottom as before instead of being moved above Transactions/Journal.
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
// 2. Marketing selector is presentation-only over one normal DOT demo account.
// Find the provider DOT row even when other linked accounts are present, hide every
// other real linked row from this UI, and generate exactly one visual ROT92069206
// row from the DOT row. Nothing is deleted or switched on the backend.
// ---------------------------------------------------------------------------
let marketing = read(marketingPath);
marketing = replaceOne(
  marketing,
  `  function providerAccount() {\n    const state = runtimeState();\n    const accounts = Array.isArray(state.accounts) ? state.accounts : [];\n    const selectedId = Number(state.selected_managed_id || 0);\n    const selected = accounts.find((item) => Number(item?.managed_account_id || 0) === selectedId)\n      || accounts.find((item) => item?.selected)\n      || null;\n    return isDotAccount(selected) ? selected : null;\n  }`,
  `  function providerAccount() {\n    const state = runtimeState();\n    const accounts = Array.isArray(state.accounts) ? state.accounts : [];\n    return accounts.find((item) => isDotAccount(item)) || null;\n  }`,
  "marketing workspace provider is the exact DOT account",
);
marketing = replaceOne(
  marketing,
  `  function removeRealRotRows() {\n    document.querySelectorAll("[data-account-id]").forEach((row) => {\n      if (row.classList.contains("marketing-synthetic-rot")) return;\n      const text = String(row.textContent || "").toUpperCase();\n      if (text.includes(ROT_ID) || (text.includes("ROT") && text.includes("206"))) row.remove();\n    });\n  }`,
  `  function removeRealRotRows() {\n    const providerId = managedId();\n    if (!providerId) return;\n    document.querySelectorAll(".top-account-switch [data-account-id]").forEach((row) => {\n      if (row.classList.contains("marketing-synthetic-rot")) return;\n      const rowId = Number(row.getAttribute("data-account-id") || 0);\n      if (rowId && rowId !== providerId) row.remove();\n    });\n  }`,
  "hide all non-provider linked accounts from marketing selector",
);
marketing = replaceOne(
  marketing,
  `    const symbol = row.querySelector(".direct-account-symbol");\n    if (isRot && symbol) {\n      const flag = document.createElement("span");\n      flag.className = "deriv-real-flag";\n      flag.setAttribute("aria-hidden", "true");\n      symbol.replaceWith(flag);\n    }\n    if (!isRot) {\n      const flag = row.querySelector(".deriv-real-flag");\n      if (flag) {\n        const demo = document.createElement("span");\n        demo.className = "direct-account-symbol";\n        demo.textContent = "D";\n        flag.replaceWith(demo);\n      }\n    }`,
  `    const symbol = row.querySelector(".direct-account-symbol,.deriv-demo-coin,.deriv-real-flag");\n    if (isRot && symbol && !symbol.classList.contains("deriv-real-flag")) {\n      const flag = document.createElement("span");\n      flag.className = "deriv-real-flag";\n      flag.setAttribute("aria-hidden", "true");\n      symbol.replaceWith(flag);\n    }\n    if (!isRot) {\n      const flag = row.querySelector(".deriv-real-flag");\n      if (flag) {\n        const demo = document.createElement("span");\n        demo.className = "deriv-demo-coin";\n        demo.setAttribute("aria-hidden", "true");\n        flag.replaceWith(demo);\n      }\n    }`,
  "ROT flag and DOT demo icon",
);
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
  `    version: "20260821-marketing-dot-rot-v6-two-row-ui-only",`,
  "marketing UI version",
);
marketing = replaceOne(
  marketing,
  `    provider_account_id: DOT_ID,\n    display_rot_id: ROT_ID,`,
  `    provider_account_id: DOT_ID,\n    provider_managed_id: managedId,\n    display_rot_id: ROT_ID,`,
  "export provider managed id",
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
  "return accounts.find((item) => isDotAccount(item)) || null",
  '.top-account-switch [data-account-id]',
  "rowId !== providerId) row.remove()",
  '.direct-account-symbol,.deriv-demo-coin,.deriv-real-flag',
  'flag.className = "deriv-real-flag"',
  "provider_managed_id: managedId",
  "display_balance: () => displayBalance()",
  "balance_for_view:",
  "render_now: renderMarketingUi",
  "marketing-dot-rot-v6-two-row-ui-only",
]) {
  if (!marketing.includes(required)) throw new Error(`marketing-ui-layout marketing invariant missing: ${required}`);
}
write(marketingPath, marketing);

// ---------------------------------------------------------------------------
// 3. Make the ordinary account renderer respect the UI-only projection.
// It must not restore the raw provider balance or recreate the hidden real account
// rows every render cycle. Synthetic DOT/ROT rows remain owned by the presentation
// layer. This only changes DOM rendering; account data and backend state stay intact.
// ---------------------------------------------------------------------------
let runtime = read(runtimePath);
runtime = replaceOne(
  runtime,
  `    accounts.forEach((account) => {\n      const id = Number(account.managed_account_id || 0);\n      if (!id || host.querySelector(\`[data-account-id="\${CSS.escape(String(id))}"]\`)) return;`,
  `    accounts.forEach((account) => {\n      const id = Number(account.managed_account_id || 0);\n      if (!id) return;\n      try {\n        const marketingUi = window.DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1;\n        const providerId = Number(marketingUi?.provider_managed_id?.() || 0);\n        if (marketingUi?.marketing_ui_active?.() && providerId && id !== providerId) return;\n      } catch (_) {}\n      if (host.querySelector(\`[data-account-id="\${CSS.escape(String(id))}"]\`)) return;`,
  "do not recreate hidden linked accounts",
);
runtime = replaceOne(
  runtime,
  `    const selected = selectedAccount();\n    const balance = providerBalance !== null ? providerBalance : selected?.balance;\n    const currency = providerCurrency || selected?.currency || "USD";`,
  `    const selected = selectedAccount();\n    let balance = providerBalance !== null ? providerBalance : selected?.balance;\n    const currency = providerCurrency || selected?.currency || "USD";\n    try {\n      const marketingUi = window.DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1;\n      if (marketingUi?.marketing_ui_active?.()) {\n        const projected = Number(marketingUi.display_balance?.());\n        if (Number.isFinite(projected)) balance = projected;\n      }\n    } catch (_) {}`,
  "top balance respects marketing projection",
);
runtime = replaceOne(
  runtime,
  `      const account = accounts.get(Number(row.getAttribute("data-account-id") || 0));\n      if (!account) return;\n      const idText = fullId(account);`,
  `      const rowManagedId = Number(row.getAttribute("data-account-id") || 0);\n      const account = accounts.get(rowManagedId);\n      if (!account) return;\n      try {\n        const marketingUi = window.DERIVADMIN_DIRECT_DEMO_RESET_ROUTER_V1;\n        const providerId = Number(marketingUi?.provider_managed_id?.() || 0);\n        if (marketingUi?.marketing_ui_active?.() && providerId && rowManagedId !== providerId) {\n          row.remove();\n          return;\n        }\n      } catch (_) {}\n      if (row.dataset.marketingView) return;\n      const idText = fullId(account);`,
  "hide linked rows and leave synthetic rows presentation-owned",
);
for (const required of [
  "marketingUi?.provider_managed_id?.()",
  "id !== providerId) return",
  "rowManagedId !== providerId",
  "row.remove()",
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
  "/final-ui-shell-v2.js?v=20260821-runpanel-bottom-v3",
);
if (!premium.includes("/final-ui-shell-v2.js?v=20260821-runpanel-bottom-v3")) {
  throw new Error("marketing-ui-layout shell cache-bust missing");
}
write(premiumPath, premium);

let index = read(indexPath);
index = index.replace(
  /\/final-premium-6f3\.js\?v=[^"']+/g,
  "/final-premium-6f3.js?v=20260821-marketing-layout-v3",
);
index = index.replace(
  /\/direct-demo-reset-router-v1\.js\?v=[^"']+/g,
  "/direct-demo-reset-router-v1.js?v=20260821-marketing-ui-v6",
);
index = index.replace(
  /\/direct-runtime-ux-v4\.js\?v=[^"']+/g,
  "/direct-runtime-ux-v4.js?v=20260821-marketing-balance-v8",
);
for (const required of [
  "/final-premium-6f3.js?v=20260821-marketing-layout-v3",
  "/direct-demo-reset-router-v1.js?v=20260821-marketing-ui-v6",
  "/direct-runtime-ux-v4.js?v=20260821-marketing-balance-v8",
]) {
  if (!index.includes(required)) throw new Error(`marketing-ui-layout cache invariant missing: ${required}`);
}
write(indexPath, index);

console.log("Marketing UI layout finalizer: Run totals restored below tab content");
console.log("Marketing UI layout finalizer: tutorial implementation badge removed");
console.log("Marketing UI layout finalizer: selector shows only DOT 75% and synthetic ROT 25%");
console.log("Marketing UI layout finalizer: ROT uses the Real-style flag and extra linked real rows stay hidden");
console.log("Marketing UI layout finalizer: selected 75/25 presentation balance survives normal runtime redraws");
console.log("Marketing UI layout finalizer: backend and Deriv execution path unchanged");
