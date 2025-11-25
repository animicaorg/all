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
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, "..");

const PUBLIC_DIR = path.join(ROOT, "public");
const DIST_CHROME = path.join(ROOT, "dist-chrome");
const DIST_FIREFOX = path.join(ROOT, "dist-firefox");
const MANIFEST_BASE = path.join(ROOT, "manifest.base.json");
const DIST_MANIFESTS_DIR = path.join(ROOT, "dist-manifests");

type Json = Record<string, any>;

async function main() {
  banner("Animica Wallet — build");

  await ensureDir(DIST_MANIFESTS_DIR);
  await ensureDir(DIST_CHROME);
  await ensureDir(DIST_FIREFOX);

  const base = await readJson<Json>(MANIFEST_BASE);

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
  await runViteBuild({
    mode: "firefox",
    outDir: DIST_FIREFOX,
    env: { BROWSER: "firefox" },
  });

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
// Manifest transforms
// ───────────────────────────────────────────────────────────────────────────────

function patchForChrome(base: Json): Json {
  const m: Json = structuredClone(base);

  // Ensure MV3 minimum version (Chrome)
  if (!m.minimum_chrome_version) m.minimum_chrome_version = "108";

  // Keep ESM service worker (supported by Chromium MV3).
  // Ensure action popups point to copied HTML in dist root.
  if (!m.action) {
    m.action = { default_title: "Animica Wallet", default_popup: "popup.html" };
  } else {
    m.action.default_popup = "popup.html";
  }

  // Sanity: background config exists
  if (!m.background) {
    m.background = { service_worker: "background.js", type: "module" };
  }

  return sortKeys(m);
}

function patchForFirefox(base: Json): Json {
  const m: Json = structuredClone(base);

  // Firefox MV3 (as of 2024–2025) is stricter; avoid "type": "module" for SW.
  if (!m.background) m.background = {};
  m.background.service_worker = "background.js";
  if (m.background.type) delete m.background.type;

  // Gecko-specific settings (addon ID + min version)
  const addonId = process.env.FIREFOX_ADDON_ID || "wallet@animica.dev";
  m.browser_specific_settings = {
    gecko: { id: addonId, strict_min_version: "109.0" },
  };

  // Firefox uses the same "action" structure; ensure popup filename.
  if (!m.action) {
    m.action = { default_title: "Animica Wallet", default_popup: "popup.html" };
  } else {
    m.action.default_popup = "popup.html";
  }

  // Firefox doesn't support "minimum_chrome_version"
  if (m.minimum_chrome_version) delete m.minimum_chrome_version;

  // CSP differences can cause rejections; keep simple, non-module-safe default.
  if (m.content_security_policy?.extension_pages) {
    m.content_security_policy.extension_pages =
      "script-src 'self'; object-src 'self'; base-uri 'self'";
  }

  return sortKeys(m);
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

async function readJson<T = any>(p: string): Promise<T> {
  const buf = await fs.readFile(p, "utf8");
  return JSON.parse(buf) as T;
}

async function writePrettyJson(p: string, obj: any) {
  const json = JSON.stringify(obj, null, 2) + "\n";
  await ensureDir(path.dirname(p));
  await fs.writeFile(p, json, "utf8");
}

function sortKeys(obj: any): any {
  if (Array.isArray(obj)) return obj.map(sortKeys);
  if (obj && typeof obj === "object") {
    return Object.fromEntries(
      Object.keys(obj)
        .sort()
        .map((k) => [k, sortKeys(obj[k])])
    );
  }
  return obj;
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
