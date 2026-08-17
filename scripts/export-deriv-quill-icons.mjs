import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as Accounts from "@deriv/quill-icons/Accounts";
import * as Currencies from "@deriv/quill-icons/Currencies";
import * as Legacy from "@deriv/quill-icons/Legacy";
import * as Markets from "@deriv/quill-icons/Markets";
import * as TradeTypes from "@deriv/quill-icons/TradeTypes";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "dist");

const normalize = (value) => String(value || "").replace(/[^a-z0-9]/gi, "").toLowerCase();

function iconExports(namespace) {
  return Object.entries(namespace).filter(
    ([name, value]) => /icon$/i.test(name) && typeof value === "function",
  );
}

function choose(label, namespaces, exactNames, termSets) {
  const entries = namespaces.flatMap((namespace) => iconExports(namespace));
  const exact = exactNames.map(normalize);
  for (const wanted of exact) {
    const found = entries.find(([name]) => normalize(name) === wanted);
    if (found) return found;
  }
  for (const terms of termSets) {
    const wanted = terms.map(normalize);
    const ranked = entries
      .filter(([name]) => wanted.every((term) => normalize(name).includes(term)))
      .sort((a, b) => a[0].length - b[0].length || a[0].localeCompare(b[0]));
    if (ranked.length) return ranked[0];
  }
  const hints = entries
    .map(([name]) => name)
    .filter((name) => /(demo|real|account|over|under|rise|fall|match|differ|even|odd|usd|volatility)/i.test(name))
    .slice(0, 80);
  throw new Error(`Official Deriv Quill icon not found for ${label}. Relevant exports: ${hints.join(", ")}`);
}

function svgMarkup(entry, label) {
  const [exportName, Component] = entry;
  const markup = renderToStaticMarkup(
    React.createElement(Component, {
      width: 32,
      height: 32,
      "aria-label": label,
      focusable: false,
    }),
  );
  if (!markup.includes("<svg")) {
    throw new Error(`Deriv Quill export ${exportName} did not render an SVG`);
  }
  return { exportName, markup };
}

const selected = {
  over: svgMarkup(
    choose("Digit Over", [TradeTypes], [
      "TradeTypesDigitOverIcon",
      "TradeTypesDigitsOverIcon",
      "TradeTypesOverIcon",
    ], [["over"]]),
    "Digit Over",
  ),
  under: svgMarkup(
    choose("Digit Under", [TradeTypes], [
      "TradeTypesDigitUnderIcon",
      "TradeTypesDigitsUnderIcon",
      "TradeTypesUnderIcon",
    ], [["under"]]),
    "Digit Under",
  ),
  matches: svgMarkup(
    choose("Matches", [TradeTypes], ["TradeTypesMatchesIcon", "TradeTypesDigitMatchesIcon"], [["match"]]),
    "Matches",
  ),
  differs: svgMarkup(
    choose("Differs", [TradeTypes], ["TradeTypesDiffersIcon", "TradeTypesDigitDiffersIcon"], [["differ"]]),
    "Differs",
  ),
  even: svgMarkup(
    choose("Even", [TradeTypes], ["TradeTypesEvenIcon", "TradeTypesDigitEvenIcon"], [["even"]]),
    "Even",
  ),
  odd: svgMarkup(
    choose("Odd", [TradeTypes], ["TradeTypesOddIcon", "TradeTypesDigitOddIcon"], [["odd"]]),
    "Odd",
  ),
  rise: svgMarkup(
    choose("Rise", [TradeTypes], ["TradeTypesRiseIcon", "TradeTypesVanillaCallIcon"], [["rise"], ["call"]]),
    "Rise",
  ),
  fall: svgMarkup(
    choose("Fall", [TradeTypes], ["TradeTypesFallIcon", "TradeTypesVanillaPutIcon"], [["fall"], ["put"]]),
    "Fall",
  ),
  demoAccount: svgMarkup(
    choose("Deriv Demo account", [Accounts, Legacy], [
      "AccountsDemoIcon",
      "AccountDemoIcon",
      "LegacyAccountDemoIcon",
      "LegacyDemoAccountIcon",
    ], [["account", "demo"], ["demo"]]),
    "Deriv Demo account",
  ),
  realAccount: svgMarkup(
    choose("Deriv Real account", [Accounts, Legacy], [
      "AccountsRealIcon",
      "AccountRealIcon",
      "LegacyAccountRealIcon",
      "LegacyRealAccountIcon",
    ], [["account", "real"], ["real"]]),
    "Deriv Real account",
  ),
  usd: svgMarkup(
    choose("USD", [Currencies], ["CurrencyUsdIcon", "CurrenciesUsdIcon"], [["usd"]]),
    "USD",
  ),
  volatility: svgMarkup(
    choose("Volatility market", [Markets], ["MarketsVolatilityIcon", "MarketVolatilityIcon"], [["volatility"]]),
    "Volatility market",
  ),
};

await mkdir(output, { recursive: true });
const publicIcons = Object.fromEntries(
  Object.entries(selected).map(([key, value]) => [key, value.markup]),
);
const provenance = Object.fromEntries(
  Object.entries(selected).map(([key, value]) => [key, value.exportName]),
);

await writeFile(
  resolve(output, "deriv-quill-icons-v1.js"),
  `/* Official @deriv/quill-icons 2.4.18; generated at VPS build time. */\n` +
    `window.DERIV_QUILL_ICONS=Object.freeze(${JSON.stringify(publicIcons)});\n` +
    `window.DERIV_QUILL_ICON_EXPORTS=Object.freeze(${JSON.stringify(provenance)});\n`,
  "utf8",
);
await writeFile(
  resolve(output, "deriv-quill-icons-v1.json"),
  `${JSON.stringify({ package: "@deriv/quill-icons", version: "2.4.18", repository: "deriv-com/quill-icons", exports: provenance }, null, 2)}\n`,
  "utf8",
);

console.log("Official Deriv Quill icons exported:");
for (const [key, value] of Object.entries(provenance)) console.log(`  ${key}: ${value}`);
