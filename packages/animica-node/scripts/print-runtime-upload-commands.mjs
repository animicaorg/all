#!/usr/bin/env node
/**
 * Print exact commands for uploading a staged runtime release directory.
 * This script does not upload anything.
 */

import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

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

const dir = firstArg(["dir", "input"], join(process.cwd(), "dist", "runtime-release"));
const channel = arg("channel", "stable");
const host = arg("host", "user@releases.animica.org");
const remoteRoot = arg("remote-root", "/var/www/releases.animica.org/runtime");
const publicBase = arg("public-base", `https://releases.animica.org/runtime/${channel}`);

if (!existsSync(dir)) {
  process.stderr.write(`error: release directory does not exist: ${dir}\n`);
  process.exit(64);
}

const files = readdirSync(dir).filter((f) => f === "manifest.json" || f.endsWith(".tar.gz")).sort();
if (files.length === 0) {
  process.stderr.write(`error: no manifest/tarballs found in ${dir}\n`);
  process.exit(1);
}

process.stdout.write(`# Files ready to upload from ${dir}\n`);
for (const file of files) process.stdout.write(`# - ${file}\n`);
process.stdout.write("\n");
process.stdout.write("mkdir -p command on the release host:\n");
process.stdout.write(`ssh ${host} 'mkdir -p ${remoteRoot}/${channel}'\n\n`);
process.stdout.write("rsync upload command:\n");
process.stdout.write(`rsync -avz --checksum ${dir.replace(/\/$/, "")}/ ${host}:${remoteRoot}/${channel}/\n\n`);
process.stdout.write("verification commands:\n");
process.stdout.write(`curl -fsSI ${publicBase}/manifest.json\n`);
process.stdout.write(`curl -fsS ${publicBase}/manifest.json\n`);
