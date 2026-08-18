import fs from "node:fs";
import path from "node:path";

const file = path.join(process.cwd(), "dist", "direct-runtime-ux-v4.js");
let source = fs.readFileSync(file, "utf8");

const before = `    const currency = providerCurrency || selected?.currency || "USD";\n    document.querySelectorAll(".top-account-switch .account-switch-summary strong,.balance-pill b").forEach((node) => {`;
const after = `    const currency = providerCurrency || selected?.currency || "USD";\n    const selectedIdText = fullId(selected);\n    document.querySelectorAll(".top-account-switch .account-switch-summary small").forEach((node) => {\n      if (selectedIdText && node.textContent !== selectedIdText) node.textContent = selectedIdText;\n      if (selectedIdText) node.title = selectedIdText;\n    });\n    document.querySelectorAll(".top-account-switch .account-switch-summary strong,.balance-pill b").forEach((node) => {`;

const count = source.split(before).length - 1;
if (count !== 1) {
  throw new Error(`Direct UX full selected account ID patch expected one summary block, found ${count}`);
}
source = source.replace(before, after);
fs.writeFileSync(file, source, "utf8");
console.log("Direct UX shows the full selected Deriv Options account ID");
