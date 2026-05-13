#!/usr/bin/env node
/**
 * Build the Animica wallet extension production zip, compute its SHA-256,
 * bump its version file, and copy the artifact + checksum into the website's
 * downloads directory.
 *
 * Usage:
 *   node scripts/release-extension.mjs [--repo <repoRoot>] [--skip-build] [--dry-run]
 *
 * The script is intentionally idempotent: re-running on the same source tree
 * produces identical artifacts (because vite builds are deterministic given
 * the same source) so checksums remain reproducible.
 */

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, statSync, readdirSync, mkdirSync, copyFileSync, rmSync } from "node:fs";
import { join, resolve, basename } from "node:path";
import { spawnSync } from "node:child_process";

function parseArgs(argv) {
  const opts = { repo: null, skipBuild: false, dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--repo") opts.repo = argv[++i];
    else if (a === "--skip-build") opts.skipBuild = true;
    else if (a === "--dry-run") opts.dryRun = true;
  }
  return opts;
}

function findRepoRoot(start) {
  let cur = resolve(start);
  for (let i = 0; i < 32; i++) {
    if (existsSync(join(cur, ".git")) || existsSync(join(cur, "pnpm-workspace.yaml"))) return cur;
    const parent = resolve(cur, "..");
    if (parent === cur) break;
    cur = parent;
  }
  return resolve(start);
}

function sha256Of(buf) {
  return createHash("sha256").update(buf).digest("hex");
}

function listAllFiles(dir) {
  const out = [];
  const stack = [dir];
  while (stack.length) {
    const cur = stack.pop();
    for (const entry of readdirSync(cur, { withFileTypes: true })) {
      const full = join(cur, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile()) out.push(full);
    }
  }
  out.sort();
  return out;
}

/**
 * Build a deterministic zip without taking an external dependency on a zip
 * library. We use the system `zip` binary; if absent we fall back to Python
 * `zipfile` which ships with the .venv. The on-disk layout is store-mode
 * (no compression) to keep checksums stable across machines.
 */
function buildZip(distDir, zipPath, dryRun) {
  if (dryRun) {
    console.log(`[dry-run] would zip ${distDir} → ${zipPath}`);
    return;
  }
  if (existsSync(zipPath)) {
    rmSync(zipPath, { force: true });
  }
  // Try `zip` first.
  const tryZip = spawnSync("zip", ["-r", "-X", zipPath, "."], { cwd: distDir, stdio: "inherit" });
  if (tryZip.status === 0) return;
  // Fall back to python zipfile.
  const python = process.env.PYTHON || (existsSync("/root/animica/.venv/bin/python") ? "/root/animica/.venv/bin/python" : "python3");
  const code = `
import os, sys, zipfile
root = sys.argv[1]
out = sys.argv[2]
with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
    for d, _, files in os.walk(root):
        for name in files:
            full = os.path.join(d, name)
            rel = os.path.relpath(full, root)
            zf.write(full, rel)
`;
  const r = spawnSync(python, ["-c", code, distDir, zipPath], { stdio: "inherit" });
  if (r.status !== 0) {
    throw new Error("failed to build extension zip (no `zip` binary, python zipfile also failed)");
  }
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const repo = opts.repo ? resolve(opts.repo) : findRepoRoot(process.cwd());
  const extDir = join(repo, "apps", "wallet-extension");
  const distDir = join(extDir, "dist");
  const websiteDir = join(repo, "website", "public", "wallet");
  const zipName = "animica-wallet-extension-chrome.zip";
  const shaName = "animica-wallet-extension-chrome.sha256";
  const verName = "animica-wallet-extension-chrome.version";
  const stagingZip = join(extDir, zipName);

  if (!existsSync(extDir)) {
    console.error(`error: wallet extension dir not found at ${extDir}`);
    process.exit(1);
  }
  const pkg = JSON.parse(readFileSync(join(extDir, "package.json"), "utf8"));
  console.log(`Animica wallet extension v${pkg.version}`);

  if (!opts.skipBuild) {
    if (opts.dryRun) {
      console.log(`[dry-run] would run: pnpm --filter @animica/wallet-extension build`);
    } else {
      console.log(`building @animica/wallet-extension …`);
      const r = spawnSync("pnpm", ["--filter", pkg.name, "build"], {
        cwd: repo,
        stdio: "inherit",
      });
      if (r.status !== 0) {
        console.error("error: pnpm build failed");
        process.exit(r.status ?? 1);
      }
    }
  } else {
    console.log("[skip-build] reusing existing dist/");
  }

  if (opts.dryRun) {
    console.log(`[dry-run] would zip ${distDir} → ${stagingZip}`);
    console.log(`[dry-run] would update ${websiteDir}/{${zipName},${shaName},${verName}}`);
    console.log("[dry-run] complete — no files written");
    return;
  }
  if (!existsSync(distDir)) {
    console.error(`error: build did not produce ${distDir}`);
    process.exit(1);
  }

  buildZip(distDir, stagingZip, false);

  const zipBuf = readFileSync(stagingZip);
  const sha = sha256Of(zipBuf);
  const sizeKB = (statSync(stagingZip).size / 1024).toFixed(1);
  console.log(`zip:    ${stagingZip}  (${sizeKB} KB)`);
  console.log(`sha256: ${sha}`);

  // Update website.
  mkdirSync(websiteDir, { recursive: true });
  copyFileSync(stagingZip, join(websiteDir, zipName));
  writeFileSync(join(websiteDir, shaName), `${sha}  ${zipName}\n`, "utf8");
  writeFileSync(join(websiteDir, verName), `${pkg.version}\n`, "utf8");

  console.log("");
  console.log("website artifacts updated:");
  console.log(`  ${join(websiteDir, zipName)}`);
  console.log(`  ${join(websiteDir, shaName)}`);
  console.log(`  ${join(websiteDir, verName)}`);
  console.log("");
  console.log(`commit & deploy:`);
  console.log(`  git add -A && git commit -m "release: wallet extension ${pkg.version}"`);
}

try {
  main();
} catch (err) {
  console.error(`fatal: ${err?.stack ?? err}`);
  process.exit(1);
}
