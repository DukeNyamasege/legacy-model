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

function renderable(value) {
  return typeof value === "function" || Boolean(value && typeof value === "object" && (value.$$typeof || value.render || value.type));
}

function iconExports(namespace) {
  return Object.entries(namespace).filter(([name, value]) => /icon$/i.test(name) && renderable(value));
}

function choose(label, namespaces, exactNames, termSets) {
  const entries = namespaces.flatMap((namespace) => iconExports(namespace));
  for (const wanted of exactNames.map(normalize)) {
    const match = entries.find(([name]) => normalize(name) === wanted);
    if (match) return match;
  }
  for (const terms of termSets) {
    const normalizedTerms = terms.map(normalize);
    const matches = entries
      .filter(([name]) => normalizedTerms.every((term) => normalize(name).includes(term)))
      .sort((a, b) => a[0].length - b[0].length || a[0].localeCompare(b[0]));
    if (matches.length) return matches[0];
  }
  const hints = entries.map(([name]) => name)
    .filter((name) => /(demo|virtual|real|account|wallet|over|under|rise|fall|match|differ|even|odd|usd|volatility)/i.test(name))
    .slice(0, 120);
  throw new Error(`Official Deriv Quill icon not found for ${label}. Relevant exports: ${hints.join(", ")}`);
}

function svgMarkup(entry, label) {
  const [exportName, Component] = entry;
  const markup = renderToStaticMarkup(React.createElement(Component, {
    width: 32,
    height: 32,
    iconSize: "md",
    "aria-label": label,
    focusable: false,
  }));
  if (!markup.includes("<svg")) throw new Error(`Deriv Quill export ${exportName} did not render an SVG`);
  return { exportName, markup };
}

// Quill 2.4.18 exposes official Deriv account light/dark glyphs, but does not
// expose separate semantic Demo/Real account glyphs. Use the exact official
// Deriv account light glyph for both account types and let the product UI's
// explicit DEMO/REAL text label carry the account-state meaning. Never fabricate
// a distinct icon that is not present in the official package.
const derivAccountGlyph = choose(
  "Deriv account",
  [Accounts],
  ["AccountsDerivAccountLightIcon", "AccountsDerivAccountDarkIcon"],
  [["deriv", "account", "light"], ["deriv", "account"]],
);

const selected = {
  over: svgMarkup(choose("Digit Over", [TradeTypes], ["TradeTypesDigitOverIcon", "TradeTypesDigitsOverIcon", "TradeTypesOverIcon"], [["digit", "over"], ["over"]]), "Digit Over"),
  under: svgMarkup(choose("Digit Under", [TradeTypes], ["TradeTypesDigitUnderIcon", "TradeTypesDigitsUnderIcon", "TradeTypesUnderIcon"], [["digit", "under"], ["under"]]), "Digit Under"),
  matches: svgMarkup(choose("Matches", [TradeTypes], ["TradeTypesMatchesIcon", "TradeTypesDigitMatchesIcon"], [["match"]]), "Matches"),
  differs: svgMarkup(choose("Differs", [TradeTypes], ["TradeTypesDiffersIcon", "TradeTypesDigitDiffersIcon"], [["differ"]]), "Differs"),
  even: svgMarkup(choose("Even", [TradeTypes], ["TradeTypesEvenIcon", "TradeTypesDigitEvenIcon"], [["even"]]), "Even"),
  odd: svgMarkup(choose("Odd", [TradeTypes], ["TradeTypesOddIcon", "TradeTypesDigitOddIcon"], [["odd"]]), "Odd"),
  rise: svgMarkup(choose("Rise", [TradeTypes], ["TradeTypesRiseIcon", "TradeTypesVanillaCallIcon"], [["rise"], ["call"]]), "Rise"),
  fall: svgMarkup(choose("Fall", [TradeTypes], ["TradeTypesFallIcon", "TradeTypesVanillaPutIcon"], [["fall"], ["put"]]), "Fall"),
  demoAccount: svgMarkup(derivAccountGlyph, "Deriv Demo account"),
  realAccount: svgMarkup(derivAccountGlyph, "Deriv Real account"),
  usd: svgMarkup(choose("USD", [Currencies], ["CurrencyUsdIcon", "CurrenciesUsdIcon", "CurrencyUSDIcon", "CurrenciesUSDIcon"], [["usd"]]), "USD"),
  volatility: svgMarkup(choose("Volatility market", [Markets, Legacy], ["MarketsVolatilityIcon", "MarketVolatilityIcon"], [["volatility"]]), "Volatility market"),
};

await mkdir(output, { recursive: true });
const publicIcons = Object.fromEntries(Object.entries(selected).map(([key, value]) => [key, value.markup]));
const provenance = Object.fromEntries(Object.entries(selected).map(([key, value]) => [key, value.exportName]));
const semantics = {
  account_icons: "shared_official_deriv_account_glyph_with_explicit_demo_real_ui_labels",
  account_icon_export: provenance.demoAccount,
};

await writeFile(resolve(output, "deriv-quill-icons-v2.js"), `/* Official @deriv/quill-icons 2.4.18; generated during direct VPS build. */\nwindow.DERIV_QUILL_ICONS=Object.freeze(${JSON.stringify(publicIcons)});\nwindow.DERIV_QUILL_ICON_EXPORTS=Object.freeze(${JSON.stringify(provenance)});\n`, "utf8");
await writeFile(resolve(output, "deriv-quill-icons-v2.json"), `${JSON.stringify({ package: "@deriv/quill-icons", version: "2.4.18", repository: "deriv-com/quill-icons", source: "official-build-time-static-svg", exports: provenance, semantics }, null, 2)}\n`, "utf8");

console.log("Official Deriv Quill icons exported for 6F-2:");
for (const [key, value] of Object.entries(provenance)) console.log(`  ${key}: ${value}`);
console.log(`  account semantics: ${semantics.account_icons}`);
