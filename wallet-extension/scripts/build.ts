/**
 * Build script: generates per-browser MV3 manifests, copies public assets,
 * and runs Vite bundling for Chrome and Firefox targets.
 *
 * Usage:
 *   npx tsx scripts/build.ts
 *
 * Env (optional):
 *   FIREFOX_ADDON_ID=wallet@animica.dev
 *   VITE_RPC_URL=...
 *   VITE_CHAIN_ID=...
 */

import fs from "node:fs/promises";
import fssync from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { ROOT, patchForChrome, patchForFirefox, readManifestBase } from "./manifest.js";

const PUBLIC_DIR = path.join(ROOT, "public");
const DIST_CHROME = path.join(ROOT, "dist-chrome");
const DIST_FIREFOX = path.join(ROOT, "dist-firefox");
const DIST_MANIFESTS_DIR = path.join(ROOT, "dist-manifests");
const HTML_PAGES = ["popup.html", "onboarding.html", "approve.html"];

async function main() {
  banner("Animica Wallet — build");

  await ensureDir(DIST_MANIFESTS_DIR);
  await ensureDir(DIST_CHROME);
  await ensureDir(DIST_FIREFOX);

  const base = await readManifestBase();

  const chromeManifest = patchForChrome(base);
  const firefoxManifest = patchForFirefox(base);

  await writePrettyJson(
    path.join(DIST_MANIFESTS_DIR, "manifest.chrome.json"),
    chromeManifest
  );
  await writePrettyJson(
    path.join(DIST_MANIFESTS_DIR, "manifest.firefox.json"),
    firefoxManifest
  );

  // Clean output dirs (but keep folder)
  await emptyDir(DIST_CHROME);
  await emptyDir(DIST_FIREFOX);

  // Copy static assets first (HTML, icons, fonts)
  await copyDir(PUBLIC_DIR, DIST_CHROME);
  await copyDir(PUBLIC_DIR, DIST_FIREFOX);

  // Bundle code for each target via Vite.
  // We pass different modes and outDirs so vite.config.ts can key off them.
  await runViteBuild({
    mode: "chrome",
    outDir: DIST_CHROME,
    env: { BROWSER: "chrome" },
  });
  await syncBuiltHtml(DIST_CHROME);
  await runViteBuild({
    mode: "firefox",
    outDir: DIST_FIREFOX,
    env: { BROWSER: "firefox" },
  });
  await syncBuiltHtml(DIST_FIREFOX);

  // Drop generated manifests into each built bundle root.
  await writePrettyJson(path.join(DIST_CHROME, "manifest.json"), chromeManifest);
  await writePrettyJson(
    path.join(DIST_FIREFOX, "manifest.json"),
    firefoxManifest
  );

  success(
    `Build complete.\n` +
      `- ${rel(DIST_CHROME)} (Chrome MV3)\n` +
      `- ${rel(DIST_FIREFOX)} (Firefox MV3)\n` +
      `- ${rel(DIST_MANIFESTS_DIR)}/manifest.{chrome,firefox}.json`
  );
}

// ───────────────────────────────────────────────────────────────────────────────
// Vite runner
// ───────────────────────────────────────────────────────────────────────────────

async function runViteBuild(opts: {
  mode: string;
  outDir: string;
  env?: Record<string, string>;
}) {
  const viteBin = getLocalBin("vite");
  const args = [
    "build",
    "--mode",
    opts.mode,
    "--outDir",
    opts.outDir,
    "--emptyOutDir",
  ];

  info(`vite ${args.join(" ")}`);
  await spawnP(viteBin, args, {
    cwd: ROOT,
    env: { ...process.env, ...(opts.env || {}) },
  });
}

async function syncBuiltHtml(outDir: string) {
  const builtDir = path.join(outDir, "public");

  await Promise.all(
    HTML_PAGES.map(async (html) => {
      const built = path.join(builtDir, html);
      const target = path.join(outDir, html);
      if (!fssync.existsSync(built)) return;

      await fs.copyFile(built, target);
    })
  );
}

// ───────────────────────────────────────────────────────────────────────────────
// FS helpers
// ───────────────────────────────────────────────────────────────────────────────

async function ensureDir(dir: string) {
  await fs.mkdir(dir, { recursive: true });
}

async function emptyDir(dir: string) {
  if (!fssync.existsSync(dir)) {
    await fs.mkdir(dir, { recursive: true });
    return;
  }
  const entries = await fs.readdir(dir);
  await Promise.all(
    entries.map(async (e) => {
      const p = path.join(dir, e);
      const st = await fs.lstat(p);
      if (st.isDirectory()) {
        await fs.rm(p, { recursive: true, force: true });
      } else {
        await fs.rm(p, { force: true });
      }
    })
  );
}

async function copyDir(src: string, dst: string) {
  const st = await fs.stat(src).catch(() => null);
  if (!st) return;
  if (!st.isDirectory()) throw new Error(`copyDir: ${src} is not a directory`);
  await fs.mkdir(dst, { recursive: true });
  const entries = await fs.readdir(src, { withFileTypes: true });
  for (const e of entries) {
    const s = path.join(src, e.name);
    const d = path.join(dst, e.name);
    if (e.isDirectory()) {
      await copyDir(s, d);
    } else if (e.isSymbolicLink()) {
      const target = await fs.readlink(s);
      await fs.symlink(target, d);
    } else {
      await fs.copyFile(s, d);
    }
  }
}

async function writePrettyJson(p: string, obj: any) {
  const json = JSON.stringify(obj, null, 2) + "\n";
  await ensureDir(path.dirname(p));
  await fs.writeFile(p, json, "utf8");
}

// ───────────────────────────────────────────────────────────────────────────────
// Process helpers
// ───────────────────────────────────────────────────────────────────────────────

function getLocalBin(name: string): string {
  const bin =
    process.platform === "win32" ? `${name}.cmd` : name;
  const local = path.join(ROOT, "node_modules", ".bin", bin);
  return fssync.existsSync(local) ? local : bin;
}

function spawnP(
  cmd: string,
  args: string[],
  opts: { cwd?: string; env?: NodeJS.ProcessEnv }
): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      stdio: "inherit",
      cwd: opts.cwd,
      env: opts.env,
    });
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} ${args.join(" ")} exited with ${code}`));
    });
    child.on("error", reject);
  });
}

// ───────────────────────────────────────────────────────────────────────────────
// Pretty logs
// ───────────────────────────────────────────────────────────────────────────────

function rel(p: string) {
  return path.relative(ROOT, p) || ".";
}
function banner(s: string) {
  console.log(`\n\u001b[1m${s}\u001b[0m`);
}
function info(s: string) {
  console.log(`\u001b[36m[info]\u001b[0m ${s}`);
}
function success(s: string) {
  console.log(`\u001b[32m[ok]\u001b[0m ${s}`);
}

// ───────────────────────────────────────────────────────────────────────────────

main().catch((err) => {
  console.error("\n\u001b[31m[error]\u001b[0m", err?.stack || err);
  process.exit(1);
});
