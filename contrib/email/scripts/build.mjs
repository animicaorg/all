//!/usr/bin/env node
/**
 * MJML → HTML builder for Animica email templates.
 *
 * Usage:
 *   node contrib/email/scripts/build.mjs
 *
 * Env flags:
 *   DRY_RUN=1            # print outputs to stdout, don't write dist/*
 *   BASE_URL=https://assets.animica.dev  # rewrite absolute href/src starting with "/"
 *   STRICT=1             # enforce payload checks (size ≤ MAX_HTML_KB, no external fonts)
 *   MAX_HTML_KB=100      # override max HTML size (default 100 KB)
 *   INLINE_MAX_KB=40     # max size for inlining local images (default 40 KB)
 */

import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const ROOT = path.resolve(__dirname, "..", "..", "..", ".."); // repo root
const TEMPLATES_DIR = path.resolve(ROOT, "contrib/email/templates");
const ASSETS_DIR = path.resolve(ROOT, "contrib/email/assets");
const OUT_DIR = path.resolve(ROOT, "contrib/email/dist");

const DRY_RUN = process.env.DRY_RUN === "1";
const STRICT = process.env.STRICT === "1";
const BASE_URL = process.env.BASE_URL || "";
const MAX_HTML_KB = Number(process.env.MAX_HTML_KB || 100);
const INLINE_MAX_KB = Number(process.env.INLINE_MAX_KB || 40);

function log(...args) { console.log("[email-build]", ...args); }
function warn(...args) { console.warn("[email-build][warn]", ...args); }
function fail(...args) { console.error("[email-build][error]", ...args); process.exitCode = 1; }

async function loadMJML() {
  try {
    // Dynamic import works in both ESM/CJS contexts when invoked via Node
    const mod = await import("mjml");
    // mjml default export is a function mjml2html
    return mod.default || mod.mjml2html || mod;
  } catch (e) {
    fail("Cannot find 'mjml'. Install it first: npm i -D mjml");
    throw e;
  }
}

// Minimal, fast post-processing utilities (regex-based to avoid extra deps)
function applyBaseUrl(html, baseUrl) {
  if (!baseUrl) return html;
  // Replace src="/..." and href="/..." with BASE_URL + path
  // Avoid protocol-relative (//) and already-absolute URLs (http:, https:, data:, cid:)
  return html
    .replace(/(\s(?:src|href))="\/(?!\/)([^"]+)"/g, (_m, attr, p) => `${attr}="${baseUrl.replace(/\/+$/,"")}/${p}"`);
}

async function maybeInlineLocalImages(html) {
  // Inline <img src="assets/..."> and <img src="./assets/..."> if file exists & small enough
  const re = /<img\b[^>]*\ssrc="([^"]+)"[^>]*>/gi;
  const tasks = [];
  let m;
  while ((m = re.exec(html)) !== null) {
    const src = m[1];
    if (/^(data:|https?:|cid:)/i.test(src)) continue;
    // Resolve relative to ASSETS_DIR if path contains "assets/"
    let p = src;
    if (!path.isAbsolute(p)) {
      // handle common relative forms
      if (p.startsWith("./")) p = p.slice(2);
      if (!p.startsWith("contrib/email/assets/") && p.startsWith("assets/")) {
        p = path.join("contrib/email", p);
      }
      p = path.resolve(ROOT, p);
    }
    if (!p.includes(path.resolve(ASSETS_DIR))) {
      // Only inline from email assets dir
      continue;
    }
    tasks.push((async () => {
      try {
        const buf = await fs.readFile(p);
        const kb = Math.round(buf.byteLength / 1024);
        if (kb > INLINE_MAX_KB) {
          warn(`Skip inlining ${path.basename(p)} (${kb} KB > ${INLINE_MAX_KB} KB)`);
          return;
        }
        const ext = path.extname(p).toLowerCase().replace(".", "");
        const mime = ({
          png: "image/png",
          jpg: "image/jpeg",
          jpeg: "image/jpeg",
          gif: "image/gif",
          webp: "image/webp",
          svg: "image/svg+xml"
        }[ext]) || "application/octet-stream";
        const dataUri = `data:${mime};base64,${buf.toString("base64")}`;
        html = html.replace(new RegExp(`(\\ssrc=")${escapeRegExp(src)}(")`, "g"), `$1${dataUri}$2`);
        log(`Inlined ${path.basename(p)} (${kb} KB)`);
      } catch {
        warn(`Could not inline ${src} (missing or unreadable)`);
      }
    })());
  }
  await Promise.all(tasks);
  return html;
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function validateHtml(html) {
  const kb = Math.round(Buffer.byteLength(html, "utf8") / 1024);
  const problems = [];

  if (STRICT && kb > MAX_HTML_KB) {
    problems.push(`HTML size ${kb} KB exceeds MAX_HTML_KB=${MAX_HTML_KB}.`);
  }
  // Rudimentary check for external webfonts (commonly blocked by clients)
  if (STRICT && /fonts\.googleapis\.com|fonts\.gstatic\.com/i.test(html)) {
    problems.push("External webfonts detected (Google Fonts). Remove for better deliverability.");
  }
  // Look for overly wide explicit width attributes that may exceed 600px container
  const tooWide = [...html.matchAll(/\bwidth="?(\d{3,4})"?/g)]
    .map(m => Number(m[1]))
    .filter(n => n > 650);
  if (tooWide.length > 0) {
    warn(`Found elements with width > 650px: ${Array.from(new Set(tooWide)).slice(0, 5).join(", ")}…`);
  }

  return { kb, problems };
}

async function compileOne(mjml2html, filePath) {
  const name = path.basename(filePath, ".mjml");
  const src = await fs.readFile(filePath, "utf8");
  const res = mjml2html(src, {
    // Allow relative paths from templates dir
    filePath: TEMPLATES_DIR,
    keepComments: false,
    beautify: false,
    minify: true,
    validationLevel: "strict"
  });

  if (res.errors?.length) {
    res.errors.forEach(e => warn(`MJML: ${e.formattedMessage || e.message || JSON.stringify(e)}`));
    if (STRICT) throw new Error(`MJML validation failed for ${name}.`);
  }

  let html = res.html;
  html = applyBaseUrl(html, BASE_URL);
  html = await maybeInlineLocalImages(html);

  const { kb, problems } = validateHtml(html);
  if (problems.length) {
    problems.forEach(p => (STRICT ? fail(p) : warn(p)));
    if (STRICT) throw new Error(`Strict checks failed for ${name}.`);
  }

  const outPath = path.join(OUT_DIR, `${name}.html`);
  if (DRY_RUN) {
    log(`DRY_RUN: ${name}.html (${kb} KB)\n${"-".repeat(60)}\n`);
    process.stdout.write(html + "\n");
  } else {
    await fs.mkdir(OUT_DIR, { recursive: true });
    await fs.writeFile(outPath, html, "utf8");
    log(`Wrote ${path.relative(ROOT, outPath)} (${kb} KB)`);
  }
}

async function main() {
  log("Root:", ROOT);
  await fs.mkdir(OUT_DIR, { recursive: true });

  const mjml2html = await loadMJML();

  // Discover *.mjml in templates dir
  const entries = await fs.readdir(TEMPLATES_DIR, { withFileTypes: true });
  const mjmlFiles = entries
    .filter(e => e.isFile() && e.name.toLowerCase().endsWith(".mjml"))
    .map(e => path.join(TEMPLATES_DIR, e.name))
    .sort(); // stable

  if (mjmlFiles.length === 0) {
    fail(`No .mjml files found in ${path.relative(ROOT, TEMPLATES_DIR)}`);
    return;
  }

  log(`Found ${mjmlFiles.length} template(s): ${mjmlFiles.map(p => path.basename(p)).join(", ")}`);
  for (const f of mjmlFiles) {
    try {
      await compileOne(mjml2html, f);
    } catch (e) {
      fail(`Failed building ${path.basename(f)}: ${e.message}`);
      if (STRICT) process.exit(1);
    }
  }

  log("Done.");
}

main().catch(err => {
  fail(err.stack || err.message || String(err));
  process.exit(1);
});
