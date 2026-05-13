#!/usr/bin/env node
/**
 * Assemble a release-channel directory from runtime tarballs.
 *
 * This is deliberately a staging step only: it copies already-built tarballs
 * into one directory so manifest generation and pre-publish validation operate
 * on exactly the files that will be uploaded.
 */

import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import { basename, join } from "node:path";

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
function flag(name) {
  return process.argv.includes(`--${name}`);
}

const input = firstArg(["input", "dir"], join(process.cwd(), "dist", "runtime-bundles"));
const output = firstArg(["output", "out"], join(process.cwd(), "dist", "runtime-release"));
const channel = arg("channel", "stable");
const version = arg("version");
const platforms = (arg("platforms", "") || "")
  .split(",")
  .map((x) => x.trim())
  .filter(Boolean);
const clean = flag("clean");

if (!/^(stable|beta|dev)$/.test(channel)) {
  process.stderr.write("error: --channel must be one of stable, beta, dev\n");
  process.exit(64);
}
if (!version) {
  process.stderr.write("error: --version <semver> is required\n");
  process.exit(64);
}
if (!existsSync(input)) {
  process.stderr.write(`error: input directory does not exist: ${input}\n`);
  process.exit(64);
}
if (clean) rmSync(output, { recursive: true, force: true });
mkdirSync(output, { recursive: true });

const prefix = `animica-runtime-${channel}-${version}-`;
const files = readdirSync(input)
  .filter((f) => f.startsWith(prefix) && f.endsWith(".tar.gz"))
  .sort();

if (files.length === 0) {
  process.stderr.write(`error: no tarballs matching ${prefix}*.tar.gz in ${input}\n`);
  process.exit(1);
}

const copiedPlatforms = new Set();
for (const file of files) {
  const platform = file.slice(prefix.length).replace(/\.tar\.gz$/, "");
  copiedPlatforms.add(platform);
  cpSync(join(input, file), join(output, basename(file)));
  process.stdout.write(`+ ${file} (${statSync(join(output, file)).size} bytes)\n`);
}

const missing = platforms.filter((p) => !copiedPlatforms.has(p));
if (missing.length > 0) {
  process.stderr.write(`error: missing required platform bundle(s): ${missing.join(", ")}\n`);
  process.exit(1);
}

process.stdout.write(`assembled ${files.length} runtime bundle(s) in ${output}\n`);
