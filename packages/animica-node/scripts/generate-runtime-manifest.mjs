#!/usr/bin/env node
/**
 * generate-runtime-manifest.mjs
 *
 * Reads a directory of runtime tarballs (built by build-runtime-bundle.mjs)
 * and emits a manifest.json that the `animica-node` runtime-manager can
 * fetch. The manifest is the only thing operators need to host alongside
 * the tarballs — install logic is fully verified client-side from the
 * SHA-256 digests in this file.
 *
 * Usage:
 *
 *   node generate-runtime-manifest.mjs \
 *     --dir   <dir>     directory containing tarballs (default: dist/runtime-bundles)
 *     --base  <url>     base URL where the tarballs will be hosted (REQUIRED)
 *     --channel <name>  filter to one channel (default: stable)
 *     --version <semver> filter to one version (REQUIRED)
 *     --out   <path>    output path (default: <dir>/manifest.json)
 *
 * Tarball file names must follow:
 *   animica-runtime-<channel>-<version>-<platform>.tar.gz
 *
 * The script:
 *   1. enumerates matching tarballs
 *   2. hashes each
 *   3. probes its entry path by sniffing the first directory listing in the tar
 *      (we accept "bin/animica" or "bin/animica.cmd" — anything else is logged)
 *   4. emits a manifest with one asset per platform key
 *
 * It refuses to overwrite an existing manifest of the same channel+version
 * unless --force is passed.
 */

import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { gunzipSync } from "node:zlib";

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  if (i !== -1 && i + 1 < process.argv.length) return process.argv[i + 1];
  return fallback;
}
function flag(name) {
  return process.argv.includes(`--${name}`);
}

const dir = arg("dir", join(process.cwd(), "dist", "runtime-bundles"));
const base = arg("base");
const channel = arg("channel", "stable");
const version = arg("version");
const out = arg("out", join(dir, "manifest.json"));
const force = flag("force");

if (!base) {
  process.stderr.write("error: --base <url> is required (e.g. https://releases.animica.org/runtime/stable)\n");
  process.exit(64);
}
if (!version) {
  process.stderr.write("error: --version <semver> is required\n");
  process.exit(64);
}
if (!existsSync(dir)) {
  process.stderr.write(`error: --dir ${dir} does not exist\n`);
  process.exit(64);
}

const prefix = `animica-runtime-${channel}-${version}-`;
const files = readdirSync(dir).filter((f) => f.startsWith(prefix) && f.endsWith(".tar.gz"));
if (files.length === 0) {
  process.stderr.write(`error: no tarballs in ${dir} matching ${prefix}*.tar.gz\n`);
  process.exit(1);
}

const assets = {};
for (const f of files) {
  const m = f.match(/^animica-runtime-([^-]+)-(.+?)-([^.]+(?:-[^.]+)*)\.tar\.gz$/);
  // Expected: animica-runtime-<channel>-<version>-<platformKey>.tar.gz
  // Use stricter slice based on known prefix.
  const platformKey = f.slice(prefix.length).replace(/\.tar\.gz$/, "");
  const path = join(dir, f);
  const bytes = readFileSync(path);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const entry = sniffEntry(bytes, platformKey);
  if (!entry) {
    process.stderr.write(`warning: ${f} had no recognized entry, skipping\n`);
    continue;
  }
  assets[platformKey] = {
    url: `${base.replace(/\/$/, "")}/${f}`,
    sha256,
    bytes: statSync(path).size,
    entry,
  };
  process.stdout.write(`+ ${platformKey}: ${f}  (${assets[platformKey].bytes} bytes)\n`);
}

const manifest = {
  schema: 1,
  channel,
  version,
  generatedAt: new Date().toISOString(),
  assets,
};

if (existsSync(out) && !force) {
  // Refuse to overwrite if version differs — preserves existing release history.
  try {
    const prior = JSON.parse(readFileSync(out, "utf8"));
    if (prior && prior.version && prior.version !== version) {
      process.stderr.write(
        `error: ${out} already exists with version ${prior.version}. Pass --force to overwrite.\n`,
      );
      process.exit(1);
    }
  } catch {
    /* fall through */
  }
}

mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify(manifest, null, 2) + "\n");
process.stdout.write(`wrote ${out}  (${Object.keys(assets).length} platform(s))\n`);

/* ---------------- helpers ---------------- */

function sniffEntry(gzBytes, platformKey) {
  // Decompress and walk the tar to find a bin/animica or bin/animica.cmd entry.
  let raw;
  try {
    raw = gunzipSync(gzBytes);
  } catch (err) {
    process.stderr.write(`  decompression failed: ${err.message}\n`);
    return null;
  }
  let offset = 0;
  while (offset + 512 <= raw.length) {
    const header = raw.subarray(offset, offset + 512);
    if (header.every((b) => b === 0)) break;
    const name = readStr(header, 0, 100);
    const sizeStr = readStr(header, 124, 12).trim();
    const prefix = readStr(header, 345, 155);
    const fullPath = (prefix ? prefix + "/" : "") + name;
    const size = parseInt(sizeStr || "0", 8) || 0;
    offset += 512 + Math.ceil(size / 512) * 512;
    if (!fullPath) continue;
    if (fullPath === "bin/animica" || fullPath === "bin/animica.cmd") {
      // On win32 prefer .cmd; on posix prefer the shell script.
      if (platformKey.startsWith("win32") && fullPath === "bin/animica.cmd") return fullPath;
      if (!platformKey.startsWith("win32") && fullPath === "bin/animica") return fullPath;
    }
  }
  // Second pass: accept either if the preferred wasn't found.
  offset = 0;
  while (offset + 512 <= raw.length) {
    const header = raw.subarray(offset, offset + 512);
    if (header.every((b) => b === 0)) break;
    const name = readStr(header, 0, 100);
    const sizeStr = readStr(header, 124, 12).trim();
    const prefix = readStr(header, 345, 155);
    const fullPath = (prefix ? prefix + "/" : "") + name;
    const size = parseInt(sizeStr || "0", 8) || 0;
    offset += 512 + Math.ceil(size / 512) * 512;
    if (fullPath === "bin/animica" || fullPath === "bin/animica.cmd") return fullPath;
  }
  return null;
}

function readStr(buf, off, len) {
  let end = off + len;
  for (let i = off; i < off + len; i++) {
    if (buf[i] === 0) {
      end = i;
      break;
    }
  }
  return buf.toString("utf8", off, end);
}
