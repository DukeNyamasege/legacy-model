import fs from "node:fs";

const uxPath = "dist/direct-runtime-ux-v4.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`marketing-tutorial-account-v1 missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`marketing-tutorial-account-v1 ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

function replaceBetween(source, start, end, replacement, label) {
  if (source.includes(replacement)) return source;
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`marketing-tutorial-account-v1 ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

let ux = read(uxPath);
let index = read(indexPath);

ux = replaceOne(
  ux,
  `  function selectedAccount() {\n    return accounts.get(selectedManagedId) || Array.from(accounts.values()).find((item) => item.selected) || null;\n  }\n`,
  `  function selectedAccount() {\n    return accounts.get(selectedManagedId) || Array.from(accounts.values()).find((item) => item.selected) || null;\n  }\n\n  function marketingRotSelected(account = selectedAccount()) {\n    return Boolean(\n      account?.marketing_tutorial\n      && account?.simulation_only\n      && String(account?.tutorial_view || "").toLowerCase() === "rot"\n    );\n  }\n\n  function presentationRatio(account = selectedAccount()) {\n    if (!marketingRotSelected(account)) return 1;\n    const ratio = Number(account?.tutorial_balance_ratio);\n    return Number.isFinite(ratio) && ratio > 0 && ratio <= 1 ? ratio : 0.25;\n  }\n\n  function presentationBalance(value, account = selectedAccount()) {\n    const number = Number(value);\n    if (!Number.isFinite(number)) return value;\n    return Math.round(number * presentationRatio(account) * 100000000) / 100000000;\n  }\n`,
  "marketing presentation balance helpers",
);

ux = replaceOne(
  ux,
  `      row.innerHTML = \`<span class="direct-account-symbol">\${type === "real" ? "R" : "D"}</span><span><b>\${type === "real" ? "Real account" : "Demo account"}</b><small>\${esc(fullId(account))}</small></span>\${type === "demo" ? \`<em><span class="direct-demo-balance">\${esc(money(account.balance, account.currency))}</span><span data-demo-reset class="direct-demo-reset">Reset balance</span></em>\` : \`<strong>\${esc(money(account.balance, account.currency))}</strong>\`};`,
  `      const accountIcon = type === "real"\n        ? '<span class="deriv-real-flag" aria-hidden="true"></span>'\n        : '<span class="deriv-demo-coin" aria-hidden="true"></span>';\n      row.innerHTML = \`\${accountIcon}<span><b>\${esc(account.label || (type === "real" ? "Real account" : "Demo account"))}</b><small>\${esc(fullId(account))}</small></span>\${type === "demo" ? \`<em><span class="direct-demo-balance">\${esc(money(account.balance, account.currency))}</span><span data-demo-reset class="direct-demo-reset">Reset balance</span></em>\` : \`<strong>\${esc(money(account.balance, account.currency))}</strong>\`};`,
  "generated account row uses canonical real/demo icon",
);

const tutorialBadge = `  function renderTutorialBadge() {\n    const panel = document.querySelector(".global-run-panel");\n    if (!panel) return;\n    const account = selectedAccount();\n    const active = marketingRotSelected(account);\n    let badge = panel.querySelector(".marketing-tutorial-runtime-badge");\n    if (!active) {\n      badge?.remove();\n      return;\n    }\n    if (!badge) {\n      badge = document.createElement("div");\n      badge.className = "marketing-tutorial-runtime-badge";\n      badge.style.cssText = "margin:7px 12px 0;padding:6px 9px;border-radius:9px;border:1px solid rgba(84,200,255,.2);background:rgba(8,24,40,.72);display:flex;align-items:center;gap:7px;font-size:8px;line-height:1.2;letter-spacing:.02em";\n      const sheet = panel.querySelector(".run-panel-sheet");\n      sheet?.prepend(badge);\n    }\n    badge.innerHTML = '<span style="font-weight:900;text-transform:uppercase">Tutorial</span><b>Demo execution</b><small style="opacity:.7">ROT view · linked DOT demo</small>';\n  }\n\n`;
ux = replaceOne(
  ux,
  `  function restoreTab() {`,
  `${tutorialBadge}  function restoreTab() {`,
  "tutorial disclosure badge",
);

ux = replaceOne(
  ux,
  `      renderRunState();\n      renderLoadedBadge();\n      renderStrategyCard();\n      renderAccounts();`,
  `      renderRunState();\n      renderLoadedBadge();\n      renderTutorialBadge();\n      renderStrategyCard();\n      renderAccounts();`,
  "render tutorial badge",
);

const directBalanceHandler = `  window.addEventListener("derivadmin:direct-balance", (event) => {\n    const detail = event.detail || {};\n    const account = selectedAccount();\n    providerBalance = presentationBalance(detail.balance, account);\n    providerCurrency = String(detail.currency || "USD").toUpperCase();\n    if (account) {\n      account.balance = providerBalance;\n      account.currency = providerCurrency;\n      // A ROT tutorial row is presentation-only. The provider correctly reports\n      // the underlying DOT demo login; never let it overwrite the visible ROT ID.\n      if (detail.loginid && !marketingRotSelected(account)) account.account_id = String(detail.loginid);\n    }\n    queueRender();\n  });\n\n`;
ux = replaceBetween(
  ux,
  `  window.addEventListener("derivadmin:direct-balance", (event) => {`,
  `  window.addEventListener("derivadmin:direct-balance-live", (event) => {`,
  directBalanceHandler,
  "provider absolute balance projection",
);

const liveBalanceHandler = `  window.addEventListener("derivadmin:direct-balance-live", (event) => {\n    const detail = event.detail || {};\n    const account = selectedAccount();\n    const ratio = presentationRatio(account);\n    const currency = String(detail.currency || providerCurrency || account?.currency || "USD").toUpperCase();\n    const absolute = Number(detail.balance);\n    const delta = Number(detail.delta);\n    if (Number.isFinite(absolute)) providerBalance = Math.round(absolute * ratio * 100000000) / 100000000;\n    else if (Number.isFinite(delta)) providerBalance = Number(providerBalance ?? account?.balance ?? 0) + (delta * ratio);\n    else return;\n    providerBalance = Math.round(Number(providerBalance) * 100000000) / 100000000;\n    providerCurrency = currency;\n    if (account) {\n      account.balance = providerBalance;\n      account.currency = providerCurrency;\n    }\n    queueRender();\n  });\n\n`;
ux = replaceBetween(
  ux,
  `  window.addEventListener("derivadmin:direct-balance-live", (event) => {`,
  `  window.addEventListener("derivadmin:demo-balance-reset", (event) => {`,
  liveBalanceHandler,
  "provider live balance projection",
);

index = index.replace(/direct-runtime-ux-v4\.js\?v=[^"']+/g, "direct-runtime-ux-v4.js?v=20260821-marketing-dot-rot-v1");

for (const required of [
  "function marketingRotSelected(",
  "function presentationRatio(",
  "function renderTutorialBadge(",
  "ROT view · linked DOT demo",
  "if (detail.loginid && !marketingRotSelected(account))",
  "delta * ratio",
]) {
  if (!ux.includes(required)) throw new Error(`marketing-tutorial-account-v1 UX invariant missing: ${required}`);
}
if (ux.includes('<span class="direct-account-symbol">${type === "real" ? "R" : "D"}</span>')) {
  throw new Error("marketing-tutorial-account-v1 legacy generated account letter icon survived");
}
if (!index.includes("direct-runtime-ux-v4.js?v=20260821-marketing-dot-rot-v1")) {
  throw new Error("marketing-tutorial-account-v1 cache-bust missing");
}

write(uxPath, ux);
write(indexPath, index);
console.log("MARKETING_TUTORIAL_ACCOUNT_V1_INSTALLED dot_full=true rot_quarter=true rot_real_style=true provider_execution=dot_demo tutorial_disclosure=true");
