/**
 * Starts the engine with the app's screens on a throwaway folder: a few days of seeded
 * price history (scripts/bench.py), setup done, run as the desktop app (on Linux its
 * "window" is the browser, which the tests open). Returns the teardown.
 */
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

export default async function globalSetup(): Promise<() => Promise<void>> {
  const home = mkdtempSync(join(tmpdir(), "cheaptrip-e2e-"));
  const seed = spawnSync(PYTHON, [join(ROOT, "scripts", "bench.py"), "--step", "seed", "--folder", home, "--days", "3"],
    { cwd: home, encoding: "utf-8" });
  if (seed.status !== 0) throw new Error(`seeding failed:\n${seed.stdout.slice(-3000)}\n${seed.stderr.slice(-3000)}`);

  const env = {
    ...process.env,
    CHEAPTRIP_HOME: home,
    DATABASE_URL: `sqlite+aiosqlite:///${home.replace(/\\/g, "/")}/bench.db`,
    CHEAPTRIP_PREFS_PATH: join(home, "prefs.yaml"),
    SETUP_DONE: "true",
    BROWSER: "true",  // the app would open a browser: `true` is a do-nothing command
  };
  const app = spawn(PYTHON, [join(ROOT, "main.py"), "app"], { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  app.stdout.on("data", (chunk) => { output += chunk; });
  app.stderr.on("data", (chunk) => { output += chunk; });

  let running: { port: number; token: string } | undefined;
  for (let i = 0; i < 120 && !running; i++) {
    try {
      running = JSON.parse(readFileSync(join(home, "app.json"), "utf-8"));
    } catch {
      if (app.exitCode !== null) break;
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  if (!running) {
    app.kill();
    throw new Error(`the app didn't start:\n${output.slice(-3000)}`);
  }
  process.env.CHEAPTRIP_E2E = JSON.stringify({ home, port: running.port, token: running.token });

  return async () => {
    app.kill();
    await new Promise((r) => setTimeout(r, 500));
    rmSync(home, { recursive: true, force: true });
  };
}
