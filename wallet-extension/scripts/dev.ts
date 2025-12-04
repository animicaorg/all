/**
 * MV3 dev server with live reload for Animica Wallet.
 *
 * - Runs Vite in watch mode (Chrome/Firefox targets).
 * - Watches /public and copies assets into the dist folder on change.
 * - Injects a small dev-reload.js (no inline JS, MV3 CSP-safe) into HTML pages.
 * - Opens a local WebSocket server; when rebuilds finish, connected pages reload.
 *
 * Usage:
 *   npx tsx scripts/dev.ts [--browser chrome|firefox] [--port 17365]
 *
 * Notes:
 *   • Load the extension from the chosen dist folder:
 *       - Chrome:   wallet-extension/dist-chrome
 *       - Firefox:  wallet-extension/dist-firefox
 *   • Ensure your manifest.base.json allows `connect-src ws://localhost:* http://localhost:*` for
 *     `content_security_policy.extension_pages` in dev. The prod build should keep it strict.
 */

import path from "node:path";
import fs from "node:fs/promises";
import fssync from "node:fs";
import chokidar from "chokidar";
import { WebSocketServer } from "ws";
import { build, InlineConfig, LogLevel, mergeConfig } from "vite";
import { spawn } from "node:child_process";
import { ROOT, patchForChrome, patchForFirefox, readManifestBase } from "./manifest.js";

type Browser = "chrome" | "firefox";

const argv = new Map<string, string | true>();
for (const a of process.argv.slice(2)) {
  const m = /^--([^=]+)(?:=(.*))?$/.exec(a);
  if (m) argv.set(m[1], m[2] ?? true);
}
const BROWSER: Browser =
  (String(argv.get("browser") || process.env.BROWSER || "chrome").toLowerCase() as Browser) ===
  "firefox"
    ? "firefox"
    : "chrome";
const PORT = Number(argv.get("port") || process.env.WALLET_DEV_PORT || 17365);

const PUBLIC_DIR = path.join(ROOT, "public");
const DIST = path.join(ROOT, BROWSER === "chrome" ? "dist-chrome" : "dist-firefox");
const MANIFEST_BASE = path.join(ROOT, "manifest.base.json");

const HTML_PAGES = ["popup.html", "onboarding.html", "approve.html"];

const wsClients = new Set<WebSocket>();

async function writeManifest() {
  const base = await readManifestBase();
  const manifest =
    BROWSER === "chrome" ? patchForChrome(base) : patchForFirefox(base);
  const json = JSON.stringify(manifest, null, 2) + "\n";
  await ensureDir(DIST);
  await fs.writeFile(path.join(DIST, "manifest.json"), json, "utf8");
}

async function main() {
  banner(`Animica Wallet — dev (${BROWSER})`);
  await ensureDir(DIST);
  await writeManifest();

  // 1) Start WS server for live reload
  const wss = new WebSocketServer({ port: PORT });
  wss.on("connection", (ws) => {
    wsClients.add(ws);
    ws.onclose = () => wsClients.delete(ws);
    ws.onerror = () => wsClients.delete(ws);
  });
  info(`WS live-reload listening on ws://localhost:${PORT}`);

  // 2) Initial copy of /public → dist and inject dev-reload hook
  await copyPublicToDist();
  await writeDevReloadJS();
  await injectReloadScriptTags();

  // 3) Start Vite in watch mode targeting the chosen browser
  await startViteWatch(BROWSER, async () => {
    await writeManifest();
    await syncBuiltHtmlToRoot();
    await writeDevReloadJS();
    await injectReloadScriptTags();
    broadcastReload();
  });

  // 4) Watch /public for changes and mirror into dist
  watchPublic(async (ev, srcPath) => {
    const rel = path.relative(PUBLIC_DIR, srcPath);
    const outPath = path.join(DIST, rel);
    if (ev === "add" || ev === "change") {
      await ensureDir(path.dirname(outPath));
      await fs.copyFile(srcPath, outPath);
      await maybeInjectInto(outPath);
      info(`public → ${rel}`);
      broadcastReload();
    } else if (ev === "unlink") {
      await fs.rm(outPath, { force: true }).catch(() => {});
      info(`public (deleted) → ${rel}`);
      broadcastReload();
    } else if (ev === "addDir") {
      await ensureDir(outPath);
    } else if (ev === "unlinkDir") {
      await fs.rm(outPath, { force: true, recursive: true }).catch(() => {});
      broadcastReload();
    }
  });

  // 5) Watch manifest.base.json for edits and mirror into dist
  watchManifestBase(async () => {
    await writeManifest();
    broadcastReload();
  });

  // 6) Developer tips
  console.log(
    [
      "",
      dim("Tips:"),
      dim(` • Load "Unpacked" from: ${rel(DIST)}`),
      dim(" • Keep the extensions page open with Developer Mode enabled."),
      dim(" • UI pages auto-reload; for background SW changes Chrome may delay swap until idle."),
      dim(""),
    ].join("\n")
  );
}

// ───────────────────────────────────────────────────────────────────────────────
// Vite watch
// ───────────────────────────────────────────────────────────────────────────────

async function startViteWatch(browser: Browser, onEnd: () => Promise<void>) {
  // We invoke the Vite JS API in watch mode. The actual entrypoints & rollup config
  // live in vite.config.ts. We pass mode and outDir overrides to keep dist separate.
  const mode = browser;
  const outDir = DIST;

  // Use a child process for isolation (so vite's tsconfig path resolution works consistently).
  // `vite build --watch` mirrors the same semantics as programmatic API, and is simpler to keep robust.
  const viteBin = getLocalBin("vite");
  const args = ["build", "--watch", "--mode", mode, "--outDir", outDir, "--emptyOutDir"];
  info(`spawn: vite ${args.join(" ")}`);
  const child = spawn(viteBin, args, {
    cwd: ROOT,
    stdio: "inherit",
    env: { ...process.env, BROWSER: browser },
  });

  // On every rebuild, vite prints a line. We don't parse it — instead we debounce
  // our post-build hooks via a small timer on stdout "END"ish signals would be nicer,
  // but cross-version output differs.
  let debounceTimer: NodeJS.Timeout | null = null;
  let handling = false;
  const poke = (changedPath?: string) => {
    if (handling) return;
    if (changedPath && changedPath.endsWith("manifest.json")) return;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      handling = true;
      try {
        await onEnd();
      } finally {
        handling = false;
      }
    }, 150);
  };

  // Lightweight file watcher on dist to trigger post-build hook:
  chokidar
    .watch([path.join(DIST, "**/*")], { ignoreInitial: true, depth: 3 })
    .on("add", (p) => poke(p))
    .on("change", (p) => poke(p))
    .on("unlink", (p) => poke(p));

  child.on("exit", (code) => {
    if (code !== 0) {
      console.error(red("vite exited with code " + code));
      process.exit(code ?? 1);
    }
  });
}

// ───────────────────────────────────────────────────────────────────────────────
// Public copying & HTML injection
// ───────────────────────────────────────────────────────────────────────────────

async function copyPublicToDist() {
  if (!fssync.existsSync(PUBLIC_DIR)) return;
  await copyDir(PUBLIC_DIR, DIST);
  await Promise.all(
    HTML_PAGES.map(async (html) => {
      const p = path.join(DIST, html);
      if (fssync.existsSync(p)) await maybeInjectInto(p);
    })
  );
}

function watchPublic(
  onEvent: (event: "add" | "change" | "unlink" | "addDir" | "unlinkDir", p: string) => void | Promise<void>
) {
  const watcher = chokidar.watch(PUBLIC_DIR, {
    ignoreInitial: true,
    awaitWriteFinish: { stabilityThreshold: 80, pollInterval: 10 },
  });
  watcher
    .on("add", (p) => onEvent("add", p))
    .on("change", (p) => onEvent("change", p))
    .on("unlink", (p) => onEvent("unlink", p))
    .on("addDir", (p) => onEvent("addDir", p))
    .on("unlinkDir", (p) => onEvent("unlinkDir", p));
}

function watchManifestBase(onChange: () => void | Promise<void>) {
  chokidar.watch(MANIFEST_BASE, { ignoreInitial: true }).on("change", () => {
    void onChange();
  });
}

async function syncBuiltHtmlToRoot() {
  const builtDir = path.join(DIST, "public");

  await Promise.all(
    HTML_PAGES.map(async (html) => {
      const builtPath = path.join(builtDir, html);
      const targetPath = path.join(DIST, html);
      if (!fssync.existsSync(builtPath)) return;

      await ensureDir(path.dirname(targetPath));
      await fs.copyFile(builtPath, targetPath);
    })
  );
}

async function writeDevReloadJS() {
  const js = `
/* dev-reload.js — injected only in dev builds */
(() => {
  const PORT = ${JSON.stringify(PORT)};
  const url = (location.protocol === "https:" ? "wss://" : "ws://") + "localhost:" + PORT.toString();
  try {
    const ws = new WebSocket(url);
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg && msg.type === "reload" && (msg.target === "ui" || msg.target === "all")) {
          console.log("[wallet-dev] reload");
          location.reload();
        }
      } catch {}
    };
    ws.onopen = () => console.log("[wallet-dev] live reload connected");
    ws.onclose = () => console.log("[wallet-dev] live reload disconnected");
  } catch (e) {
    console.warn("[wallet-dev] WS connect failed", e);
  }
})();
`.trimStart();
  await fs.writeFile(path.join(DIST, "dev-reload.js"), js, "utf8");
}

async function injectReloadScriptTags() {
  const candidates = new Set<string>();

  for (const html of HTML_PAGES) {
    candidates.add(path.join(DIST, html));
    candidates.add(path.join(DIST, "public", html));
  }

  const rootFiles = await fs.readdir(DIST).catch(() => []);
  for (const f of rootFiles) {
    if (f.endsWith(".html")) {
      candidates.add(path.join(DIST, f));
    }
  }

  for (const html of candidates) {
    await maybeInjectInto(html);
  }
}

async function maybeInjectInto(htmlPath: string) {
  if (!fssync.existsSync(htmlPath)) return;
  const html = await fs.readFile(htmlPath, "utf8").catch(() => "");
  if (!html) return;

  // CSP-safe: reference a packaged script (no inline JS).
  const tag = `<script src="./dev-reload.js"></script>`;
  if (html.includes("dev-reload.js")) return;

  let out: string;
  if (html.includes("</body>")) {
    out = html.replace("</body>", `  ${tag}\n</body>`);
  } else {
    out = html + "\n" + tag + "\n";
  }
  await fs.writeFile(htmlPath, out, "utf8");
}

// ───────────────────────────────────────────────────────────────────────────────
// Live reload broadcast
// ───────────────────────────────────────────────────────────────────────────────

function broadcastReload() {
  const payload = JSON.stringify({ type: "reload", target: "ui", ts: Date.now() });
  for (const ws of wsClients) {
    try {
      ws.send(payload);
    } catch {}
  }
}

// ───────────────────────────────────────────────────────────────────────────────
// FS utils
// ───────────────────────────────────────────────────────────────────────────────

async function ensureDir(dir: string) {
  await fs.mkdir(dir, { recursive: true });
}
async function copyDir(src: string, dst: string) {
  const st = await fs.stat(src).catch(() => null);
  if (!st || !st.isDirectory()) return;
  await fs.mkdir(dst, { recursive: true });
  const entries = await fs.readdir(src, { withFileTypes: true });
  for (const e of entries) {
    const s = path.join(src, e.name);
    const d = path.join(dst, e.name);
    if (e.isDirectory()) await copyDir(s, d);
    else if (e.isSymbolicLink()) await fs.symlink(await fs.readlink(s), d);
    else await fs.copyFile(s, d);
  }
}

function getLocalBin(name: string): string {
  const bin = process.platform === "win32" ? `${name}.cmd` : name;
  const local = path.join(ROOT, "node_modules", ".bin", bin);
  return fssync.existsSync(local) ? local : bin;
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
function red(s: string) {
  return `\u001b[31m${s}\u001b[0m`;
}
function dim(s: string) {
  return `\u001b[2m${s}\u001b[0m`;
}

// ───────────────────────────────────────────────────────────────────────────────

main().catch((err) => {
  console.error("\n\u001b[31m[error]\u001b[0m", err?.stack || err);
  process.exit(1);
});
