/**
 * Renders CheapTrip's icons from the design's mark (the Stitch "CheapTrip app icon"):
 *
 *   packaging/icons/cheaptrip.ico     CheapTrip.exe, cheaptrip-cli.exe and the installer (16–256 px)
 *   desktop/assets/cheaptrip.png      notifications (256 px)
 *   desktop/assets/tray-<state>.png   the tray icon for each engine state (64 px)
 *   ui/public/favicon.svg             the app's screens
 *
 * Small sizes use a simplified mark (no dashed outer ring, bolder lines) so they stay
 * legible at 16 px; the tray icons show the engine's state in the mark's dot.
 * The rendered files are committed; run this again after changing the mark:
 *
 *   NODE_PATH=$(npm root -g) node packaging/icons/make_icons.mjs   (needs Playwright's Chromium)
 */
import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

const BRAND_DOT = "#3DD598";
const STATE_DOTS = {
  running: "#3DD598", searching: "#4CC2FF", paused: "#FFA500", attention: "#FF5449", stopped: "#9AA0A6",
};

/** The mark on a 48-unit grid. `small`: the 16–24 px variant; `dot`: its colour (null: none). */
export function mark({ small = false, dot = BRAND_DOT, dotAt = [33, 15], dotR = 3 } = {}) {
  const ring = small
    ? `<circle cx="24" cy="24" r="11" stroke="rgba(255,255,255,0.55)" stroke-width="2.5"/>`
    : `<circle cx="24" cy="24" r="16" stroke="rgba(255,255,255,0.25)" stroke-width="1.5" stroke-dasharray="2 3"/>
  <circle cx="24" cy="24" r="10" stroke="rgba(255,255,255,0.4)" stroke-width="1.5"/>`;
  const star = small
    ? `<path d="M24 7 L27 21 L41 24 L27 27 L24 41 L21 27 L7 24 L21 21 Z" fill="#ffffff"/>`
    : `<path d="M24 10 L26 21 L37 23 L26 25 L24 36 L22 25 L11 23 L22 21 Z" fill="#ffffff" opacity="0.95"/>`;
  const dotSvg = dot
    ? `<circle cx="${dotAt[0]}" cy="${dotAt[1]}" r="${dotR + 1.5}" fill="#003B6F"/><circle cx="${dotAt[0]}" cy="${dotAt[1]}" r="${dotR}" fill="${dot}"/>`
    : "";
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="48" height="48" fill="none">
  <defs>
    <linearGradient id="brandGrad" x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
      <stop stop-color="#0067C0"/>
      <stop offset="1" stop-color="#004E8C"/>
    </linearGradient>
  </defs>
  <rect width="48" height="48" rx="10" fill="url(#brandGrad)"/>
  ${ring}
  ${star}
  ${dotSvg}
</svg>`;
}

async function render(page, svg, size) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(`<html><body style="margin:0;background:transparent">
    <img src="data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}" width="${size}" height="${size}"
         style="display:block"></body></html>`);
  await page.locator("img").evaluate((img) => img.decode());
  return page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
}

/** A .ico holding one PNG per size (Windows Vista and later read PNG entries). */
function ico(images) {
  const header = Buffer.alloc(6 + 16 * images.length);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  let offset = header.length;
  images.forEach(({ size, png }, i) => {
    const entry = 6 + 16 * i;
    header.writeUInt8(size >= 256 ? 0 : size, entry);
    header.writeUInt8(size >= 256 ? 0 : size, entry + 1);
    header.writeUInt16LE(1, entry + 4); // colour planes
    header.writeUInt16LE(32, entry + 6); // bits per pixel
    header.writeUInt32LE(png.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += png.length;
  });
  return Buffer.concat([header, ...images.map((i) => i.png)]);
}

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM ?? "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
const page = await browser.newPage({ deviceScaleFactor: 1 });

const sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256];
const images = [];
for (const size of sizes) {
  images.push({ size, png: await render(page, mark({ small: size <= 24 }), size) });
}
writeFileSync(join(ROOT, "packaging", "icons", "cheaptrip.ico"), ico(images));
writeFileSync(join(ROOT, "desktop", "assets", "cheaptrip.png"), images.at(-1).png);
for (const [state, color] of Object.entries(STATE_DOTS)) {
  const svg = mark({ small: true, dot: color, dotAt: [37, 37], dotR: 8 });
  writeFileSync(join(ROOT, "desktop", "assets", `tray-${state}.png`), await render(page, svg, 64));
}
writeFileSync(join(ROOT, "ui", "public", "favicon.svg"), mark());
writeFileSync(join(ROOT, "packaging", "icons", "cheaptrip.svg"), mark());
await browser.close();
console.log("icons written");
