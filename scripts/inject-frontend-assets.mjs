import { readFileSync, writeFileSync } from "node:fs";

const path = "dist/index.html";
let html = readFileSync(path, "utf8");

const scripts = [
  ["direct-pip-precision-v1.js", "20260819-live-fix-v2"],
  ["direct-financial-fence-v1.js", "20260818-direct-fence-v2"],
  ["direct-socket-control-v1.js", "20260818-direct-socket-v1"],
  ["direct-hard-stop-fence-v1.js", "20260818-browser-hard-stop-v1"],
  ["direct-reset-authority-v1.js", "20260818-reset-authority-v1"],
  ["direct-interaction-guard-v3.js", "20260818-interaction-v4-one-flow"],
  ["deriv-direct-execution-v2.js", "20260819-provider-settlement-v9"],
  ["direct-strategy-persistence-v1.js", "20260818-direct-persist-v1"],
  ["direct-continuity-checkpoint-v1.js", "20260819-direct-continuity-v3-fixed-split-stake"],
  ["direct-ui-cleanup-v1.js", "20260819-direct-cleanup-v2-single-panel"],
  ["direct-builder-loaded-v2.js", "20260818-builder-loaded-v2"],
  ["direct-runtime-ux-v4.js", "20260818-runtime-ux-v6"],
  ["direct-demo-reset-router-v1.js", "20260818-demo-reset-router-v1"],
  ["direct-transaction-ledger-v6.js", "20260819-provider-ledger-v11"],
  ["direct-run-panel-authority-v6.js", "20260819-single-run-panel-v3"],
  ["mobile-layout-authority-v1.js", "20260819-desktop-panel-handle-v4"],
  ["run-panel-usability-v1.js", "20260819-summary-clear-v3"],
  ["scheduler-v2-ui.js", "20260819-live-fix-v2"],
];

for (const [file, version] of scripts) {
  const tag = `<script src="/${file}?v=${version}" defer></script>`;
  if (!html.includes(tag)) html = html.replace("</body>", `${tag}\n</body>`);
}

const theme = '<link rel="stylesheet" href="/tutorial-camera-theme-v1.css?v=20260819-block-workspace-v5">';
if (!html.includes(theme)) html = html.replace("</head>", `${theme}\n</head>`);

writeFileSync(path, html);
