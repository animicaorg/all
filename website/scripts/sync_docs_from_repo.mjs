#!/usr/bin/env node
/**
 * sync_docs_from_repo.mjs — Optional: copy a subset of /docs MD/MDX into website/src/docs/
 *
 * Supports either:
 *   A) Local source directory:   --src /path/to/repo/docs
 *   B) Git repo clone (shallow): --git https://github.com/org/repo.git [--ref main] [--prefix docs]
 *
 * Examples:
 *   node website/scripts/sync_docs_from_repo.mjs --src ../monorepo/docs \
 *     --dest website/src/docs \
 *     --include "**\\/*.mdx" --include "**\\/*.md" \
 *     --include "**\\/*.{png,jpg,jpeg,svg,webp,gif}" \
 *     --exclude "**\\/node_modules/**" --clean
 *
 *   node website/scripts/sync_docs_from_repo.mjs --git https://github.com/animica-labs/animica \
 *     --ref main --prefix docs \
 *     --dest website/src/docs --clean --verbose
 *
 * Flags:
 *   --src PATH        Local source directory (mutually exclusive with --git)
 *   --git URL         Git repository URL (mutually exclusive with --src)
 *   --ref NAME        Git ref/branch/tag to checkout (default: default branch)
 *   --prefix PATH     Subdirectory within --src/clone to use as root (e.g., "docs")
 *   --dest PATH       Destination directory (default: website/src/docs)
 *   --include GLOB    Include patterns (may repeat). Default: "**\\/*.{md,mdx,png,jpg,jpeg,svg,gif,webp}"
 *   --exclude GLOB    Exclude patterns (may repeat). Default: "**\\/node_modules/**","**\\/.git/**",".DS_Store"
 *   --clean           Remove destination before copying
 *   --dry-run         Print actions but don't write
 *   --rewrite-md      Rewrite ".md" links to ".mdx" when target exists (default on)
 *   --no-rewrite-md   Disable md→mdx link rewrite
 *   --verbose         Log progress
 *
 * Exit codes:
 *   0 on success, 1 on errors or missing required flags, 2 on git/IO failure
 */

import { promises as fs } from "node:fs";
import fssync from "node:fs";
import path from "node:path";
import os from "node:os";
import { execFileSync } from "node:child_process";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));

/* ------------------------------ CLI parsing ----------------------------- */

function parseArgs(argv) {
  const opts = {
    src: null,
    git: null,
    ref: null,
    prefix: null,
    dest: "website/src/docs",
    include: [],
    exclude: [],
    clean: false,
    dryRun: false,
    rewriteMd: true,
    verbose: false,
  };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--src" && argv[i + 1]) opts.src = argv[++i];
    else if (a === "--git" && argv[i + 1]) opts.git = argv[++i];
    else if (a === "--ref" && argv[i + 1]) opts.ref = argv[++i];
    else if (a === "--prefix" && argv[i + 1]) opts.prefix = argv[++i];
    else if (a === "--dest" && argv[i + 1]) opts.dest = argv[++i];
    else if (a === "--include" && argv[i + 1]) opts.include.push(argv[++i]);
    else if (a === "--exclude" && argv[i + 1]) opts.exclude.push(argv[++i]);
    else if (a === "--clean") opts.clean = true;
    else if (a === "--dry-run") opts.dryRun = true;
    else if (a === "--rewrite-md") opts.rewriteMd = true;
    else if (a === "--no-rewrite-md") opts.rewriteMd = false;
    else if (a === "--verbose") opts.verbose = true;
    else if (a === "-h" || a === "--help") usage(0);
  }
  // Defaults
  if (opts.include.length === 0) {
    opts.include = [
      "*.md",
      "*.mdx",
      "*.png",
      "*.jpg",
      "*.jpeg",
      "*.svg",
      "*.gif",
      "*.webp",
      "*.yaml",
      "*.yml",
      "SIDEBAR.yaml",
      "**/*.md",
      "**/*.mdx",
      "**/*.png",
      "**/*.jpg",
      "**/*.jpeg",
      "**/*.svg",
      "**/*.gif",
      "**/*.webp",
      "**/*.yaml",
      "**/*.yml",
      "**/SIDEBAR.yaml",
    ];
  }
  if (opts.exclude.length === 0) {
    opts.exclude = ["**/node_modules/**", "**/.git/**", ".DS_Store"];
  }
  // Validate modes
  if (!!opts.src === !!opts.git) {
    console.error("✖ Specify exactly one of --src or --git");
    usage(1);
  }
  return opts;
}

function usage(code = 0) {
  console.log(`Usage:
  # Local dir
  node ${path.basename(process.argv[1])} --src ../repo/docs --dest website/src/docs --clean

  # Git clone
  node ${path.basename(process.argv[1])} --git https://github.com/org/repo.git --ref main --prefix docs --dest website/src/docs --clean

  Flags:
    --include GLOB   (repeatable)   default: ["**\\/*.md", "**\\/*.mdx", assets, "**\\/*.ya?ml", "**/SIDEBAR.yaml"]
    --exclude GLOB   (repeatable)   default: "**/node_modules/**", "**/.git/**", ".DS_Store"
    --dry-run, --verbose, --rewrite-md/--no-rewrite-md
`);
  process.exit(code);
}

const OPTS = parseArgs(process.argv);

/* ------------------------------ Utilities ------------------------------- */

const isWindows = process.platform === "win32";

function log(...a) { if (OPTS.verbose) console.log(...a); }
function err(...a) { console.error(...a); }

async function ensureDir(d) { await fs.mkdir(d, { recursive: true }).catch(() => {}); }

async function rimraf(target) {
  await fs.rm(target, { recursive: true, force: true }).catch(() => {});
}

function toRegex(glob) {
  // Convert a simple glob (**/*, *, ?, {a,b}, ext lists) into RegExp
  let re = glob
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")           // escape regex chars
    .replace(/\*\*/g, ":::DS")                      // temp marker for **
    .replace(/\*/g, "[^/]*")                        // * -> any except /
    .replace(/::\:DS/g, ".*")                       // ** -> any, including /
    .replace(/\?/g, "[^/]");                        // ? -> single char except /
  // ext group {a,b} -> (a|b)
  re = re.replace(/\{([^}]+)\}/g, (_, body) => {
    return "(" + body.split(",").map(s => s.trim().replace(/[.+^${}()|[\]\\]/g, "\\$&")).join("|") + ")";
  });
  if (glob.startsWith("**/")) {
    re = re.replace(/^\.\*\\\//, "(?:.*\\/)?");
  }
  return new RegExp("^" + re + "$", isWindows ? "i" : "");
}

function makeFilter(includes, excludes) {
  const inc = includes.map(toRegex);
  const exc = excludes.map(toRegex);
  return (relPath) => {
    const unixy = relPath.split(path.sep).join("/");
    const okInc = inc.length === 0 || inc.some(r => r.test(unixy));
    const okExc = !exc.some(r => r.test(unixy));
    return okInc && okExc;
  };
}

async function walkFiles(root) {
  const out = [];
  async function walk(dir) {
    for (const ent of await fs.readdir(dir, { withFileTypes: true })) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        if (ent.name === ".git" || ent.name === "node_modules") continue;
        await walk(full);
      } else if (ent.isFile()) {
        out.push(full);
      }
    }
  }
  await walk(root);
  return out;
}

function relFrom(base, file) {
  const rel = path.relative(base, file);
  return rel.split(path.sep).join("/");
}

async function fileExists(p) {
  try { await fs.access(p); return true; } catch { return false; }
}

/* ---------------------------- Git clone (opt) --------------------------- */

async function prepareSourceFromGit(url, ref, prefix) {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "animica-docs-"));
  const cloneDir = path.join(tmp, "repo");
  try {
    const args = ["clone", "--depth=1"];
    if (ref) args.push("--branch", ref);
    args.push(url, cloneDir);
    log("git", args.join(" "));
    execFileSync("git", args, { stdio: OPTS.verbose ? "inherit" : "ignore" });
  } catch (e) {
    err("✖ git clone failed:", e.message || e);
    process.exit(2);
  }
  const sourceDir = prefix ? path.join(cloneDir, prefix) : cloneDir;
  return { tmpRoot: tmp, sourceDir };
}

/* ------------------------------ Link rewrite ---------------------------- */

function rewriteMdLinks(content, destPathAbs, destRoot) {
  // If a link points to "./foo.md" and "./foo.mdx" exists in destRoot, rewrite to ".mdx"
  // This runs AFTER a prior copy pass for previous files; best effort only.
  const re = /(\]\()([^)\s]+?\.md)(#[^)]+)?\)/g; // ](path.md#hash)
  return content.replace(re, (m, pre, pth, hash = "") => {
    try {
      const targetAbs = path.resolve(path.dirname(destPathAbs), pth);
      // Replace .md with .mdx and check existence under destRoot
      const relToRoot = path.relative(destRoot, targetAbs);
      const mdxAlt = path.join(destRoot, relToRoot).replace(/\.md$/i, ".mdx");
      if (fssync.existsSync(mdxAlt)) {
        const newHref = pth.replace(/\.md$/i, ".mdx");
        return `${pre}${newHref}${hash})`;
      }
    } catch { /* ignore */ }
    return m;
  });
}

/* --------------------------------- Main --------------------------------- */

(async function main() {
  const start = Date.now();

  // Resolve source
  let srcRoot = null;
  let tmpToClean = null;
  if (OPTS.git) {
    const { tmpRoot, sourceDir } = await prepareSourceFromGit(OPTS.git, OPTS.ref, OPTS.prefix);
    srcRoot = sourceDir;
    tmpToClean = tmpRoot;
  } else {
    srcRoot = OPTS.prefix ? path.join(OPTS.src, OPTS.prefix) : OPTS.src;
  }

  if (!srcRoot || !(await fileExists(srcRoot))) {
    err(`✖ Source not found: ${srcRoot}`);
    process.exit(1);
  }

  const destRoot = path.resolve(OPTS.dest);
  if (OPTS.clean) {
    log("Cleaning destination:", destRoot);
    if (!OPTS.dryRun) await rimraf(destRoot);
  }
  if (!OPTS.dryRun) await ensureDir(destRoot);

  const filter = makeFilter(OPTS.include, OPTS.exclude);
  const allFiles = await walkFiles(srcRoot);
  const picked = allFiles
    .map(f => ({ abs: f, rel: relFrom(srcRoot, f) }))
    .filter(({ rel }) => filter(rel));

  if (picked.length === 0) {
    err("⚠ No files matched include/exclude filters.");
  } else {
    log(`Selected ${picked.length} file(s) to sync.`);
  }

  const summary = {
    source: OPTS.git ? { git: OPTS.git, ref: OPTS.ref || null, prefix: OPTS.prefix || null } : { src: path.resolve(srcRoot) },
    dest: destRoot,
    include: OPTS.include,
    exclude: OPTS.exclude,
    clean: OPTS.clean,
    dryRun: OPTS.dryRun,
    rewriteMd: OPTS.rewriteMd,
    files: [],
  };

  // First pass: copy bytes
  for (const { abs, rel } of picked) {
    const destAbs = path.join(destRoot, rel);
    const destDir = path.dirname(destAbs);
    const ext = path.extname(rel).toLowerCase();

    if (!OPTS.dryRun) await ensureDir(destDir);

    if (ext === ".md" || ext === ".mdx") {
      let content = await fs.readFile(abs, "utf8");
      // Normalize frontmatter newlines (optional; keep as-is)
      if (!OPTS.dryRun) {
        await fs.writeFile(destAbs.replace(/\.md$/i, ".md"), content, "utf8").catch(() => {}); // write .md if .mdx not preferred
        // Prefer preserving original extension
        await fs.writeFile(destAbs, content, "utf8");
      }
    } else {
      // Binary or assets
      if (!OPTS.dryRun) {
        const data = await fs.readFile(abs);
        await fs.writeFile(destAbs, data);
      }
    }

    summary.files.push({ from: abs, to: destAbs });
    log("✓ copied", rel);
  }

  // Optional second pass: rewrite .md links → .mdx where the .mdx exists
  if (OPTS.rewriteMd && !OPTS.dryRun) {
    const mdLike = summary.files.filter(f => f.to.match(/\.(md|mdx)$/i));
    for (const f of mdLike) {
      try {
        const content = await fs.readFile(f.to, "utf8");
        const updated = rewriteMdLinks(content, f.to, destRoot);
        if (updated !== content) {
          await fs.writeFile(f.to, updated, "utf8");
          log("↺ rewrote md→mdx links in", path.relative(destRoot, f.to));
        }
      } catch { /* ignore */ }
    }
  }

  // Write sync log
  const logPath = path.join(destRoot, "_SYNC_LOG.json");
  const elapsedMs = Date.now() - start;
  summary.stats = { count: summary.files.length, elapsed_ms: elapsedMs, when: new Date().toISOString() };

  if (!OPTS.dryRun) {
    await fs.writeFile(logPath, JSON.stringify(summary, null, 2), "utf8");
  }
  log("Wrote", logPath);

  // Cleanup temp dir if we cloned
  if (tmpToClean) {
    try { await rimraf(tmpToClean); } catch { /* ignore */ }
  }

  // Report
  const relDest = path.relative(process.cwd(), destRoot) || destRoot;
  console.log(`✅ Sync complete: ${summary.files.length} file(s) → ${relDest} (${(elapsedMs/1000).toFixed(2)}s)`);
  process.exit(0);
})().catch((e) => {
  err("✖ sync_docs_from_repo failed:", e?.stack || e?.message || String(e));
  process.exit(2);
});
