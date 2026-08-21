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
// Tabs -> tab content -> totals. Metrics still refresh on every tab, but totals
// remain below the Summary/Transactions/Journal body as in the approved UI.
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
// 2. Marketing selector is UI-only over the currently selected DOT demo account.
// Start a fresh v6 browser ledger from the CURRENT provider demo balance so stale
// v4 presentation state cannot survive this correction. Backend selection remains
// DOT; Demo/Real tabs become visual DOT/ROT selectors and never submit a real ID.
// ---------------------------------------------------------------------------
let marketing = read(marketingPath);
marketing = replaceOne(
  marketing,
  `  const VIEW_KEY = "derivadmin-marketing-demo-view-v4";\n  const LEDGER_PREFIX = "derivadmin-marketing-demo-ui-ledger-v4:";\n  const OWNER_PREFIX = "derivadmin-marketing-demo-ui-contract-owners-v4:";`,
  `  const VIEW_KEY = "derivadmin-marketing-demo-view-v6";\n  const LEDGER_PREFIX = "derivadmin-marketing-demo-ui-ledger-v6:";\n  const OWNER_PREFIX = "derivadmin-marketing-demo-ui-contract-owners-v6:";`,
  "fresh marketing UI storage namespace",
);
marketing = replaceOne(
  marketing,
  `    return { version: 4, provider, dot, rot, updated_at: Date.now() };`,
  `    return { version: 6, provider, dot, rot, updated_at: Date.now() };`,
  "fresh ledger version",
);
marketing = replaceOne(
  marketing,
  `      && Number(value.version) === 4`,
  `      && Number(value.version) === 6`,
  "ledger validation version",
);
marketing = replaceOne(
  marketing,
  `      version: 4,\n      provider: roundMoney(parsed.provider),`,
  `      version: 6,\n      provider: roundMoney(parsed.provider),`,
  "ledger read version",
);
marketing = replaceOne(
  marketing,
  `      version: 4,\n      provider: roundMoney(ledger.provider),`,
  `      version: 6,\n      provider: roundMoney(ledger.provider),`,
  "ledger write version",
);
marketing = replaceOne(
  marketing,
  `  function removeRealRotRows() {\n    document.querySelectorAll("[data-account-id]").forEach((row) => {\n      if (row.classList.contains("marketing-synthetic-rot")) return;\n      const text = String(row.textContent || "").toUpperCase();\n      if (text.includes(ROT_ID) || (text.includes("ROT") && text.includes("206"))) row.remove();\n    });\n  }`,
  `  function removeRealRotRows() {\n    const providerId = managedId();\n    if (!providerId) return;\n    document.querySelectorAll(".top-account-switch [data-account-id]").forEach((row) => {\n      if (row.classList.contains("marketing-synthetic-rot")) return;\n      if (row.matches("[data-account-kind]")) return;\n      const rowId = Number(row.getAttribute("data-account-id") || 0);\n      if (rowId && rowId !== providerId) row.remove();\n    });\n  }`,
  "hide all non-provider linked rows from marketing selector",
);
marketing = replaceOne(
  marketing,
  `    row.classList.toggle("marketing-dot-view", !isRot);\n    row.classList.toggle("marketing-rot-view", isRot);\n    row.classList.toggle("selected", view() === selectedView);`,
  `    row.classList.toggle("marketing-dot-view", !isRot);\n    row.classList.toggle("marketing-rot-view", isRot);\n    row.classList.toggle("demo", !isRot);\n    row.classList.toggle("real", isRot);\n    row.dataset.accountKindRow = isRot ? "real" : "demo";\n    row.classList.toggle("selected", view() === selectedView);`,
  "DOT and ROT visual account kinds",
);
marketing = replaceOne(
  marketing,
  `    const symbol = row.querySelector(".direct-account-symbol");\n    if (isRot && symbol) {\n      const flag = document.createElement("span");\n      flag.className = "deriv-real-flag";\n      flag.setAttribute("aria-hidden", "true");\n      symbol.replaceWith(flag);\n    }\n    if (!isRot) {\n      const flag = row.querySelector(".deriv-real-flag");\n      if (flag) {\n        const demo = document.createElement("span");\n        demo.className = "direct-account-symbol";\n        demo.textContent = "D";\n        flag.replaceWith(demo);\n      }\n    }\n    if (isRot) row.querySelectorAll("[data-demo-reset],.direct-demo-reset").forEach((node) => node.remove());`,
  `    const symbol = row.querySelector(".direct-account-symbol,.deriv-demo-coin,.deriv-real-flag");\n    if (isRot && symbol && !symbol.classList.contains("deriv-real-flag")) {\n      const flag = document.createElement("span");\n      flag.className = "deriv-real-flag";\n      flag.setAttribute("aria-hidden", "true");\n      symbol.replaceWith(flag);\n    }\n    if (!isRot) {\n      const flag = row.querySelector(".deriv-real-flag");\n      if (flag) {\n        const demo = document.createElement("span");\n        demo.className = "deriv-demo-coin";\n        demo.setAttribute("aria-hidden", "true");\n        flag.replaceWith(demo);\n      }\n    }\n    if (isRot) {\n      const em = row.querySelector("em");\n      if (em) {\n        const strong = document.createElement("strong");\n        strong.className = "marketing-rot-balance";\n        strong.textContent = money(ledger.rot);\n        em.replaceWith(strong);\n      }\n      row.querySelectorAll("[data-demo-reset],.direct-demo-reset").forEach((node) => node.remove());\n    }`,
  "ROT flag and real-style balance row",
);
marketing = replaceOne(
  marketing,
  `  function renderTopAccount(ledger) {\n    const selectedView = view();\n    const visible = visibleBalance(ledger, selectedView);`,
  `  function renderTopAccount(ledger) {\n    const selectedView = view();\n    const visible = visibleBalance(ledger, selectedView);\n    const topSwitch = document.querySelector(".top-account-switch");\n    if (topSwitch) {\n      topSwitch.classList.toggle("demo", selectedView === "dot");\n      topSwitch.classList.toggle("real", selectedView === "rot");\n      topSwitch.querySelectorAll("[data-account-kind]").forEach((tab) => {\n        const kind = String(tab.dataset.accountKind || "").toLowerCase();\n        const targetView = kind === "real" ? "rot" : "dot";\n        tab.dataset.marketingView = targetView;\n        tab.removeAttribute("data-account-id");\n        tab.classList.toggle("active", targetView === selectedView);\n      });\n      const summary = topSwitch.querySelector(".account-switch-summary");\n      const icon = summary?.querySelector(".direct-account-symbol,.deriv-demo-coin,.deriv-real-flag");\n      if (selectedView === "rot" && icon && !icon.classList.contains("deriv-real-flag")) {\n        const flag = document.createElement("span");\n        flag.className = "deriv-real-flag";\n        flag.setAttribute("aria-hidden", "true");\n        icon.replaceWith(flag);\n      } else if (selectedView === "dot" && icon?.classList.contains("deriv-real-flag")) {\n        const demo = document.createElement("span");\n        demo.className = "deriv-demo-coin";\n        demo.setAttribute("aria-hidden", "true");\n        icon.replaceWith(demo);\n      }\n    }`,
  "top selector tabs and icon are UI-only",
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
  `    version: "20260821-marketing-dot-rot-v7-safe-two-view-ui",`,
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
  "derivadmin-marketing-demo-ui-ledger-v6",
  "Number(value.version) === 6",
  '.top-account-switch [data-account-id]',
  "rowId !== providerId) row.remove()",
  'row.dataset.accountKindRow = isRot ? "real" : "demo"',
  'flag.className = "deriv-real-flag"',
  'strong.className = "marketing-rot-balance"',
  'tab.removeAttribute("data-account-id")',
  "tab.dataset.marketingView = targetView",
  "provider_managed_id: managedId",
  "display_balance: () => displayBalance()",
  "balance_for_view:",
  "render_now: renderMarketingUi",
  "marketing-dot-rot-v7-safe-two-view-ui",
]) {
  if (!marketing.includes(required)) throw new Error(`marketing-ui-layout marketing invariant missing: ${required}`);
}
write(marketingPath, marketing);

// ---------------------------------------------------------------------------
// 3. The normal account renderer may retain backend account data, but it must not
// recreate hidden real rows or repaint the UI split with provider total. These are
// DOM-only filters; no account is deleted and no backend session is switched.
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
  "/final-ui-shell-v2.js?v=20260821-runpanel-bottom-v4",
);
if (!premium.includes("/final-ui-shell-v2.js?v=20260821-runpanel-bottom-v4")) {
  throw new Error("marketing-ui-layout shell cache-bust missing");
}
write(premiumPath, premium);

let index = read(indexPath);
index = index.replace(
  /\/final-premium-6f3\.js\?v=[^"']+/g,
  "/final-premium-6f3.js?v=20260821-marketing-layout-v4",
);
index = index.replace(
  /\/direct-demo-reset-router-v1\.js\?v=[^"']+/g,
  "/direct-demo-reset-router-v1.js?v=20260821-marketing-ui-v7",
);
index = index.replace(
  /\/direct-runtime-ux-v4\.js\?v=[^"']+/g,
  "/direct-runtime-ux-v4.js?v=20260821-marketing-balance-v9",
);
for (const required of [
  "/final-premium-6f3.js?v=20260821-marketing-layout-v4",
  "/direct-demo-reset-router-v1.js?v=20260821-marketing-ui-v7",
  "/direct-runtime-ux-v4.js?v=20260821-marketing-balance-v9",
]) {
  if (!index.includes(required)) throw new Error(`marketing-ui-layout cache invariant missing: ${required}`);
}
write(indexPath, index);

console.log("Marketing UI layout finalizer: Run totals restored below tab content");
console.log("Marketing UI layout finalizer: tutorial implementation badge removed");
console.log("Marketing UI layout finalizer: fresh current provider balance is split 75% DOT / 25% ROT");
console.log("Marketing UI layout finalizer: Demo/Real tabs are UI-only DOT/ROT selectors with no backend real-account ID");
console.log("Marketing UI layout finalizer: selector shows only DOT plus synthetic ROT; extra linked real rows stay hidden");
console.log("Marketing UI layout finalizer: ROT uses the Real-style flag and real-style balance row");
console.log("Marketing UI layout finalizer: backend and Deriv execution path unchanged");
