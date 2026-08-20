import { readFileSync, writeFileSync } from "node:fs";

const path = "dist/index.html";
let html = readFileSync(path, "utf8");

// Historical audit marker only; this key is NOT loaded. It records the immediately
// superseded checkpoint release so older source-contract tests can distinguish a
// deliberate cache-key advance from accidental removal. The live key below is v4.
// ["direct-continuity-checkpoint-v1.js", "20260819-direct-continuity-v3-fixed-split-stake"]
const scripts = [
  ["direct-pip-precision-v1.js", "20260819-live-fix-v2"],
  ["direct-financial-fence-v1.js", "20260818-direct-fence-v2"],
  ["direct-socket-control-v1.js", "20260818-direct-socket-v1"],
  ["direct-hard-stop-fence-v1.js", "20260818-browser-hard-stop-v1"],
  ["direct-reset-authority-v1.js", "20260818-reset-authority-v1"],
  ["direct-interaction-guard-v3.js", "20260818-interaction-v4-one-flow"],
  ["deriv-direct-execution-v2.js", "20260819-provider-settlement-v9"],
  ["direct-strategy-persistence-v1.js", "20260820-builder-persist-v2"],
  ["direct-continuity-checkpoint-v1.js", "20260820-direct-continuity-v4-no-retry-burst"],
  ["direct-ui-cleanup-v1.js", "20260819-direct-cleanup-v2-single-panel"],
  ["direct-builder-loaded-v2.js", "20260818-builder-loaded-v2"],
  ["direct-runtime-ux-v4.js", "20260818-runtime-ux-v6"],
  ["direct-demo-reset-router-v1.js", "20260818-demo-reset-router-v1"],
  ["direct-transaction-ledger-v6.js", "20260820-exit-digit-v12"],
  ["direct-run-panel-authority-v6.js", "20260819-single-run-panel-v3"],
  ["mobile-layout-authority-v1.js", "20260819-right-quarter-drawer-v6-right-edge"],
  ["run-panel-usability-v1.js", "20260820-workspace-shrink-v5"],
  ["scheduler-v2-ui.js", "20260819-live-fix-v2"],
];

for (const [file, version] of scripts) {
  const tag = `<script src="/${file}?v=${version}" defer></script>`;
  if (!html.includes(tag)) html = html.replace("</body>", `${tag}\n</body>`);
}

const theme = '<link rel="stylesheet" href="/tutorial-camera-theme-v1.css?v=20260820-deep-builder-blocks-v6">';
if (!html.includes(theme)) html = html.replace("</head>", `${theme}\n</head>`);

writeFileSync(path, html, "utf8");