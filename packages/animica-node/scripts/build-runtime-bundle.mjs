#!/usr/bin/env node
/**
 * build-runtime-bundle.mjs
 *
 * Produces a runtime tarball that `animica-node install-runtime` can consume:
 *
 *   <out>/animica-runtime-<channel>-<version>-<platformKey>.tar.gz
 *
 * The tarball lays out as:
 *
 *   bin/animica           — launcher shim that exec's the bundled python
 *   bin/animica.cmd       — Windows launcher (when --platform win32-*)
 *   python/               — full python interpreter + site-packages
 *   share/animica/        — schemas/templates/etc. copied from --share <dir>
 *
 * Inputs:
 *
 *   --src      <dir>       Animica Python source root or package dir to copy
 *                          into share/animica (default: repo python/)
 *   --python   <dir>       Pre-built python tree to copy into python/
 *                          (e.g. a relocatable distribution from python-build-standalone)
 *   --channel  <name>      Release channel (default: stable)
 *   --version  <semver>    Release version (REQUIRED)
 *   --platform <key|os>    Target platform key, e.g. linux-x64, darwin-arm64, win32-x64.
 *                          If only OS is supplied, combine it with --arch.
 *   --arch     <arch>      Target arch when --platform is just OS (x64, arm64)
 *   --out      <dir>       Output dir (default: ./dist/runtime-bundles)
 *   --output   <dir>       Alias for --out
 *
 * This script is a release-engineering tool. It does NOT host anything,
 * does NOT publish anything. The tarballs it emits must then be uploaded
 * to the release channel that `manifest.json` points at, and listed in the
 * manifest produced by `generate-runtime-manifest.mjs`.
 *
 * The shim is intentionally minimal: it locates `python` next to itself
 * and invokes `python -m animica.cli.main`. That way the tarball is
 * self-contained and works whether or not the user has a system python.
 */

import { createHash, randomBytes } from "node:crypto";
import {
  chmodSync,
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  readlinkSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { gzipSync } from "node:zlib";

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  if (i !== -1 && i + 1 < process.argv.length) return process.argv[i + 1];
  return fallback;
}

function firstArg(names, fallback) {
  for (const name of names) {
    const v = arg(name);
    if (v !== undefined) return v;
  }
  return fallback;
}

function hostPlatformKey() {
  const p =
    process.platform === "win32"
      ? "win32"
      : process.platform === "darwin"
        ? "darwin"
        : "linux";
  const a =
    process.arch === "x64" ? "x64" : process.arch === "arm64" ? "arm64" : process.arch;
  return `${p}-${a}`;
}

function defaultSrcDir() {
  const candidates = [
    join(process.cwd(), "python"),
    resolve(process.cwd(), "..", "..", "python"),
    join(process.cwd(), "animica"),
  ];
  return candidates.find((p) => existsSync(join(p, "animica", "__init__.py")) || existsSync(join(p, "__init__.py"))) ?? candidates[0];
}

const channel = arg("channel", "stable");
const version = arg("version");
const platformArg = arg("platform", hostPlatformKey());
const archArg = arg("arch");
const platformKey = platformArg.includes("-") ? platformArg : `${platformArg}-${archArg ?? process.arch}`;
const srcDir = arg("src", defaultSrcDir());
const pythonDir = arg("python");
const outDir = firstArg(["out", "output"], join(process.cwd(), "dist", "runtime-bundles"));

if (!version) {
  process.stderr.write("error: --version is required\n");
  process.exit(64);
}
if (!existsSync(srcDir)) {
  process.stderr.write(`error: --src ${srcDir} does not exist\n`);
  process.exit(64);
}
if (!existsSync(join(srcDir, "animica", "__init__.py")) && !existsSync(join(srcDir, "__init__.py"))) {
  process.stderr.write(`error: --src ${srcDir} must be the Python source root containing animica/__init__.py or the animica package directory containing __init__.py\n`);
  process.exit(64);
}

mkdirSync(outDir, { recursive: true });

const stage = join(tmpdir(), `animica-bundle-${randomBytes(6).toString("hex")}`);
mkdirSync(stage, { recursive: true });

process.stdout.write(`staging in ${stage}\n`);

// Lay out the bundle.
mkdirSync(join(stage, "bin"), { recursive: true });
mkdirSync(join(stage, "share", "animica"), { recursive: true });
mkdirSync(join(stage, "python"), { recursive: true });

// Ensure ops/ is present in the bundle so the Python CLI can resolve compose
// files. `animica/config.py::get_network_defaults` derives a `repo_root` via
// `Path(__file__).resolve().parents[2]`, which inside the installed runtime is
// the `share/` directory. Without `share/ops/docker/docker-compose.<net>.yml`,
// `animica node up` exits immediately with "Docker Compose file not found"
// and the network container (e.g. `animica-mainnet-node`) is never created.
// We look for ops/ next to srcDir (i.e. <repo>/ops alongside <repo>/python).
const candidateOpsDirs = [
  resolve(srcDir, "..", "ops"),
  join(process.cwd(), "ops"),
  resolve(process.cwd(), "..", "..", "ops"),
];
const repoOps = candidateOpsDirs.find((p) => existsSync(p));
if (repoOps) {
  cpSync(repoOps, join(stage, "share", "ops"), { recursive: true });
  process.stdout.write(`bundled ops/ from ${repoOps}\n`);
} else {
  process.stderr.write(
    "warning: could not locate ops/ next to --src; the bundle will not include docker-compose files, and 'animica node up' will fail at runtime.\n",
  );
}

// Copy the Python source into share/animica. The launcher puts both share/
// and share/animica on PYTHONPATH so either a source root (animica/...) or an
// animica package directory keeps working.
cpSync(srcDir, join(stage, "share", "animica"), {
  recursive: true,
  filter: (s) => {
    // Skip noise dirs.
    if (/\.git($|\/)/.test(s)) return false;
    if (/__pycache__/.test(s)) return false;
    if (/\.pyc$/.test(s)) return false;
    if (/node_modules/.test(s)) return false;
    return true;
  },
});

// Copy python tree if provided.
if (pythonDir) {
  if (!existsSync(pythonDir)) {
    process.stderr.write(`error: --python ${pythonDir} does not exist\n`);
    process.exit(64);
  }
  cpSync(pythonDir, join(stage, "python"), { recursive: true });
} else {
  process.stderr.write(
    "warning: --python not provided. The resulting bundle will rely on a system python interpreter being available at runtime. For a truly self-contained npm-install flow, pass --python pointing at a relocatable python (e.g. from python-build-standalone).\n",
  );
}

// Write launcher shim.
const posixLauncher = `#!/bin/sh
# Animica node launcher (managed runtime ${channel}@${version}).
# Resolves the python that ships next to this shim.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_ROOT="$(cd "$HERE/.." && pwd)"
if [ -x "$RUNTIME_ROOT/python/bin/python3" ]; then
  PY="$RUNTIME_ROOT/python/bin/python3"
elif [ -x "$RUNTIME_ROOT/python/bin/python" ]; then
  PY="$RUNTIME_ROOT/python/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  echo "animica: no python interpreter found in bundle or on PATH" >&2
  exit 127
fi
PYTHONPATH="$RUNTIME_ROOT/share:$RUNTIME_ROOT/share/animica:\${PYTHONPATH:-}" exec "$PY" -m animica.cli.main "$@"
`;

const winLauncher = `@echo off
REM Animica node launcher (managed runtime ${channel}@${version}).
setlocal
set "HERE=%~dp0"
set "RUNTIME_ROOT=%HERE%.."
for %%P in ("%RUNTIME_ROOT%\\python\\python.exe" "%RUNTIME_ROOT%\\python\\bin\\python.exe") do (
  if exist %%~P (
    set "PY=%%~P"
    goto run
  )
)
where python >nul 2>nul && (set "PY=python") || (
  echo animica: no python interpreter found in bundle or on PATH 1>&2
  exit /b 127
)
:run
set "PYTHONPATH=%RUNTIME_ROOT%\\share;%RUNTIME_ROOT%\\share\\animica;%PYTHONPATH%"
"%PY%" -m animica.cli.main %*
`;

if (platformKey.startsWith("win32")) {
  writeFileSync(join(stage, "bin", "animica.cmd"), winLauncher);
  // Also drop the posix variant so the same bundle is usable inside WSL.
  writeFileSync(join(stage, "bin", "animica"), posixLauncher);
  chmodSync(join(stage, "bin", "animica"), 0o755);
} else {
  writeFileSync(join(stage, "bin", "animica"), posixLauncher);
  chmodSync(join(stage, "bin", "animica"), 0o755);
}

// Embed a small README so operators can identify the bundle on disk.
writeFileSync(
  join(stage, "BUNDLE.json"),
  JSON.stringify(
    { product: "animica-node-runtime", channel, version, platformKey, builtAt: new Date().toISOString() },
    null,
    2,
  ) + "\n",
);

// Build the tarball using node's built-in tools so we don't depend on the host
// `tar` binary. We emit POSIX ustar — the same format `runtime-manager`
// extracts. Symlinks in the python dir are followed (copied as regular files).
const tarballName = `animica-runtime-${channel}-${version}-${platformKey}.tar.gz`;
const tarballPath = join(outDir, tarballName);
const tarBuffer = buildTar(stage);
const gz = gzipSync(tarBuffer);
writeFileSync(tarballPath, gz);
const sha = createHash("sha256").update(gz).digest("hex");
const sz = statSync(tarballPath).size;
process.stdout.write(
  `wrote ${tarballPath}\n  size:  ${sz} bytes\n  sha256: ${sha}\n  entry:  bin/${platformKey.startsWith("win32") ? "animica.cmd" : "animica"}\n`,
);

// Clean up.
rmSync(stage, { recursive: true, force: true });

/* ---------------- tar writer ---------------- */

function buildTar(rootDir) {
  const blocks = [];
  walk(rootDir, "", blocks);
  // Two empty 512-byte blocks signal end-of-archive.
  blocks.push(Buffer.alloc(1024, 0));
  return Buffer.concat(blocks);
}

function walk(absDir, prefix, blocks) {
  const entries = readdirSync(absDir).sort();
  for (const name of entries) {
    const abs = join(absDir, name);
    const rel = prefix ? `${prefix}/${name}` : name;
    const stat = lstatSync(abs);
    if (stat.isDirectory()) {
      blocks.push(tarHeader(rel + "/", 0, stat.mode & 0o7777, "5"));
      walk(abs, rel, blocks);
      continue;
    }
    if (stat.isSymbolicLink()) {
      const link = readlinkSync(abs);
      blocks.push(tarHeader(rel, 0, 0o777, "2", link));
      continue;
    }
    if (!stat.isFile()) continue;
    const data = readFileSync(abs);
    blocks.push(tarHeader(rel, data.length, stat.mode & 0o7777, "0"));
    blocks.push(data);
    const pad = 512 - (data.length % 512 || 512);
    if (pad > 0 && pad < 512) blocks.push(Buffer.alloc(pad, 0));
  }
}

function tarHeader(name, size, mode, typeflag, linkname = "") {
  const buf = Buffer.alloc(512, 0);
  let n = name;
  let prefix = "";
  if (n.length > 100) {
    const cut = n.lastIndexOf("/", 100);
    if (cut > 0 && cut < 155) {
      prefix = n.slice(0, cut);
      n = n.slice(cut + 1);
    } else {
      n = n.slice(-100);
    }
  }
  buf.write(n.slice(0, 100), 0, 100);
  buf.write(mode.toString(8).padStart(7, "0") + "\0", 100, 8);
  buf.write("0000000\0", 108, 8); // uid
  buf.write("0000000\0", 116, 8); // gid
  buf.write(size.toString(8).padStart(11, "0") + "\0", 124, 12);
  buf.write(
    Math.floor(Date.now() / 1000)
      .toString(8)
      .padStart(11, "0") + "\0",
    136,
    12,
  );
  buf.write("        ", 148, 8); // checksum placeholder
  buf.write(typeflag, 156, 1);
  if (linkname) buf.write(linkname.slice(0, 100), 157, 100);
  buf.write("ustar  \0", 257, 8); // GNU magic + version
  if (prefix) buf.write(prefix.slice(0, 155), 345, 155);
  // Compute checksum.
  let sum = 0;
  for (const b of buf) sum += b;
  buf.write(sum.toString(8).padStart(6, "0") + "\0 ", 148, 8);
  return buf;
}
