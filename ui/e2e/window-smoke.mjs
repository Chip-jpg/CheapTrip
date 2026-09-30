/**
 * The desktop app's real window (Edge WebView2), driven through its debugging port:
 * every sidebar item, a deal's details, the sidebar toggle and the theme switch.
 *
 *   CHEAPTRIP_DEVTOOLS_PORT=9222 python main.py app        (then, from ui/)
 *   node e2e/window-smoke.mjs --port 9222 --home <the app's folder> --out <folder>
 *
 * For each step it records page errors, console errors, failed API calls and
 * whether the screen has content, and keeps two pictures: the page as WebView2
 * draws it, and (on Windows) the screen as Windows shows it, where a GPU fault
 * would be black even though the page is fine. It writes report.json and exits
 * 1 when a step failed.
 *
 * With no window to connect to (no WebView2 on this PC), it opens the running
 * app's screens (from <home>/app.json) in Microsoft Edge instead, and says so.
 */
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { chromium } from "@playwright/test";

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, arg, i, all) =>
  arg.startsWith("--") ? [...pairs, [arg.slice(2), all[i + 1]]] : pairs, []));
const port = Number(args.port ?? 9222);
const home = args.home ? resolve(args.home) : undefined;
const out = resolve(args.out ?? "window-smoke");
const waitSeconds = Number(args.wait ?? 120);
mkdirSync(out, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const report = { mode: undefined, steps: [], notes: [] };
const note = (text) => { report.notes.push(text); console.log(text); };

async function connectToWindow() {
  const until = Date.now() + waitSeconds * 1000;
  while (Date.now() < until) {
    try {
      const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`, { timeout: 5000 });
      for (let i = 0; i < 60; i++) {
        const page = browser.contexts().flatMap((c) => c.pages()).find((p) => p.url().startsWith("http://127.0.0.1"));
        if (page) return { browser, page };
        await sleep(1000);
      }
      note("connected to WebView2, but it never opened the app's page");
      return { browser, page: undefined };
    } catch {
      await sleep(2000);
    }
  }
  return undefined;
}

async function openInEdge() {
  if (!home) return undefined;
  let app;
  for (let i = 0; i < 60 && !app; i++) {
    try { app = JSON.parse(readFileSync(join(home, "app.json"), "utf-8")); } catch { await sleep(1000); }
  }
  if (!app) return undefined;
  const browser = await chromium.launch(process.platform === "win32" ? { channel: "msedge" } : {});
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(`http://127.0.0.1:${app.port}/?token=${app.token}#/deals`);
  return { browser, page };
}

/** What Windows shows in the window's area: a picture, and how much of it is one flat colour. */
function screenCapture(page, file) {
  if (process.platform !== "win32") return Promise.resolve(undefined);
  return page.evaluate(() => ({ x: window.screenX, y: window.screenY, w: window.outerWidth, h: window.outerHeight }))
    .then(({ x, y, w, h }) => {
      const script = `
        Add-Type -AssemblyName System.Drawing
        $bmp = New-Object System.Drawing.Bitmap ${Math.max(w, 1)}, ${Math.max(h, 1)}
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen(${x}, ${y}, 0, 0, $bmp.Size)
        $bmp.Save('${file.replace(/'/g, "''")}')
        $counts = @{}; $n = 0
        for ($i = 4; $i -lt $bmp.Width; $i += 12) { for ($j = 40; $j -lt $bmp.Height; $j += 12) {
          $p = $bmp.GetPixel($i, $j); $key = "{0},{1},{2}" -f [int]($p.R / 8), [int]($p.G / 8), [int]($p.B / 8)
          $counts[$key] = 1 + [int]$counts[$key]; $n++ } }
        $top = ($counts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 1)
        Write-Output ("{0}|{1}" -f $top.Key, [math]::Round($top.Value / [math]::Max($n, 1), 3))`;
      const [colour, share] = execFileSync("powershell", ["-NoProfile", "-Command", script], { encoding: "utf-8" }).trim().split("|");
      return { colour: colour.split(",").map((c) => Number(c) * 8), flatShare: Number(share) };
    })
    .catch((err) => ({ error: String(err).slice(0, 300) }));
}

async function check(page, name, action, problems) {
  const before = problems.length;
  let failed;
  try {
    await action();
  } catch (err) {
    failed = String(err).split("\n")[0];
  }
  await sleep(2500);
  const state = await page.evaluate(() => {
    const root = document.getElementById("root");
    const main = document.querySelector("main");
    const box = main?.getBoundingClientRect();
    const probe = box && document.elementFromPoint(box.left + box.width / 2, box.top + Math.min(240, box.height / 2));
    return {
      hash: location.hash,
      rootChildren: root?.childElementCount ?? 0,
      mainText: (main?.innerText ?? "").trim().length,
      probe: probe ? `${probe.tagName.toLowerCase()}${probe.id ? "#" + probe.id : ""}` : null,
      theme: document.documentElement.dataset.theme,
      visibility: document.visibilityState,
    };
  }).catch((err) => ({ error: String(err).split("\n")[0] }));
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  await page.screenshot({ path: join(out, `${slug}.page.png`) }).catch((err) => problems.push(`${name}: no page picture (${err})`));
  const screen = await screenCapture(page, join(out, `${slug}.screen.png`));
  const errors = problems.slice(before);
  const blank = !state.rootChildren || state.mainText < 20;
  const flat = screen?.flatShare !== undefined && screen.flatShare > 0.97;
  const ok = !failed && !state.error && !blank && !flat && errors.length === 0;
  report.steps.push({ name, ok, failed, state, screen, errors });
  console.log(`${ok ? "ok  " : "FAIL"} ${name}  ${JSON.stringify({ ...state, screen, failed, errors })}`);
}

const connected = await connectToWindow();
if (connected?.page) {
  report.mode = "webview2";
  note(`driving the app's WebView2 window through port ${port}`);
} else {
  note("no WebView2 window to drive: opening the running app's screens in " +
       (process.platform === "win32" ? "Microsoft Edge" : "Chromium") + " instead");
}
const target = connected?.page ? connected : await openInEdge();
if (!target?.page) {
  note("the app never started (no window and no app.json)");
  writeFileSync(join(out, "report.json"), JSON.stringify(report, null, 2));
  process.exit(1);
}
report.mode ??= "browser";
const { browser, page } = target;
const problems = [];
page.on("pageerror", (err) => problems.push(`page error: ${err.message}`));
page.on("console", (msg) => { if (msg.type() === "error") problems.push(`console: ${msg.text().slice(0, 300)}`); });
page.on("response", (res) => {
  if (res.url().includes("/api/") && res.status() >= 400) problems.push(`HTTP ${res.status()} ${res.request().method()} ${new URL(res.url()).pathname}`);
});
page.on("crash", () => problems.push("the page crashed"));
page.on("close", () => problems.push("the page closed"));

await page.waitForSelector("aside a", { timeout: 60_000 }).catch(() => note("the sidebar never appeared"));
await check(page, "Start", async () => {}, problems);
for (const label of ["Destinations", "Activity", "Settings", "Setup wizard", "Notifications", "Deals"]) {
  await check(page, label, () => page.locator("aside a", { hasText: label }).first().click({ timeout: 10_000 }), problems);
}
await check(page, "Deal details", async () => {
  const details = page.locator("main [data-testid=deal-card]").first().getByText("Details");
  if (await details.count()) await details.first().click({ timeout: 10_000 });
  else await page.locator("main [data-testid=deal-row]").first().click({ timeout: 10_000 });
}, problems);
await check(page, "Back to Deals", () => page.keyboard.press("Escape"), problems);
await check(page, "Sidebar collapsed", () => page.getByLabel("Show or hide the navigation").click({ timeout: 10_000 }), problems);
await check(page, "Destinations, collapsed", () => page.locator("aside a").nth(1).click({ timeout: 10_000 }), problems);
await check(page, "Sidebar open", () => page.getByLabel("Show or hide the navigation").click({ timeout: 10_000 }), problems);
await check(page, "Other theme", () => page.getByLabel(/Switch to the (light|dark) theme/).click({ timeout: 10_000 }), problems);
for (const label of ["Activity", "Settings", "Deals"]) {
  await check(page, `${label}, other theme`, () => page.locator("aside a", { hasText: label }).first().click({ timeout: 10_000 }), problems);
}
await check(page, "Theme back", () => page.getByLabel(/Switch to the (light|dark) theme/).click({ timeout: 10_000 }), problems);

writeFileSync(join(out, "report.json"), JSON.stringify(report, null, 2));
const failures = report.steps.filter((s) => !s.ok);
console.log(`\n${report.mode}: ${report.steps.length - failures.length} of ${report.steps.length} steps fine`);
await browser.close().catch(() => {});
process.exit(failures.length ? 1 : 0);
