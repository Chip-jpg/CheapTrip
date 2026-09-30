/**
 * The black window of v0.8 (B47): `useEffect(() => window.scrollTo?.(0, 0), [step])` returned
 * what scrollTo returns, a Promise in current Chromium and WebView2. React took it for the
 * cleanup, called it when the setup wizard closed, and removed the whole app. Effects here
 * are blocks, so only an explicit `return` hands React a cleanup.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname);

function sources(folder: string): string[] {
  return readdirSync(folder).flatMap((name) => {
    const path = join(folder, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("effects", () => {
  it("are blocks, never an expression whose value React would call as the cleanup", () => {
    const risky = /\buse(?:Layout)?Effect\(\s*(?:\(\)\s*=>\s*[^\s{]|(?!\(\))[A-Za-z_$])/g;
    const found = sources(SRC).flatMap((path) => {
      const text = readFileSync(path, "utf-8");
      return [...text.matchAll(risky)].map((m) => `${relative(SRC, path)}:${text.slice(0, m.index).split("\n").length}`);
    });
    expect(found).toEqual([]);
  });

  it("finds the kind of effect that blanked the window", () => {
    const risky = /\buse(?:Layout)?Effect\(\s*(?:\(\)\s*=>\s*[^\s{]|(?!\(\))[A-Za-z_$])/;
    expect(risky.test("useEffect(() => window.scrollTo?.(0, 0), [step]);")).toBe(true);
    expect(risky.test("useEffect(reload, [reload]);")).toBe(true);
    expect(risky.test("useEffect(() => { window.scrollTo?.(0, 0); }, [step]);")).toBe(false);
  });
});
