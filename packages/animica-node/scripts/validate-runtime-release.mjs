#!/usr/bin/env node
/**
 * Validate a runtime release directory before publishing.
 *
 * Checks:
 *   - manifest.json schema shape
 *   - every asset has sha256, bytes, URL, and entry
 *   - every asset URL basename exists in the release directory
 *   - hosted file bytes match manifest bytes and sha256
 *   - optional Ed25519 manifest signature if a public key is supplied
 */

import { createHash, createPublicKey, verify } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import { gunzipSync } from "node:zlib";

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
const manifestPath = firstArg(["manifest", "manifest-path"], join(dir, "manifest.json"));
const requirePlatforms = (arg("require-platforms", "") || "")
  .split(",")
  .map((x) => x.trim())
  .filter(Boolean);
const publicKeyArg = arg("public-key", process.env.ANIMICA_RUNTIME_MANIFEST_PUBLIC_KEY);

if (!existsSync(manifestPath)) {
  process.stderr.write(`error: manifest not found: ${manifestPath}\n`);
  process.exit(64);
}

let manifest;
try {
  manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
} catch (err) {
  process.stderr.write(`error: manifest is not valid JSON: ${err.message}\n`);
  process.exit(1);
}

try {
  validateManifest(manifest);
  verifySignatureIfConfigured(manifest, publicKeyArg);
  validateArtifacts(manifest);
} catch (err) {
  process.stderr.write(`error: ${err.message}\n`);
  process.exit(1);
}

process.stdout.write(
  `OK ${manifest.channel}@${manifest.version}: ${Object.keys(manifest.assets).length} artifact(s) validated from ${dir}\n`,
);

function validateManifest(m) {
  if (!m || typeof m !== "object") throw new Error("manifest must be an object");
  if (m.schema !== 1) throw new Error("manifest.schema must be 1");
  if (!/^(stable|beta|dev)$/.test(m.channel)) throw new Error("manifest.channel must be stable, beta, or dev");
  if (typeof m.version !== "string" || !/^\d+\.\d+\.\d+/.test(m.version)) {
    throw new Error("manifest.version must be semver-like");
  }
  if (typeof m.generatedAt !== "string" || Number.isNaN(Date.parse(m.generatedAt))) {
    throw new Error("manifest.generatedAt must be an ISO timestamp");
  }
  if (!m.assets || typeof m.assets !== "object" || Array.isArray(m.assets)) {
    throw new Error("manifest.assets must be an object");
  }
  if (Object.keys(m.assets).length === 0) throw new Error("manifest.assets must not be empty");
  for (const platform of requirePlatforms) {
    if (!m.assets[platform]) throw new Error(`required platform missing from manifest: ${platform}`);
  }
  for (const [platform, asset] of Object.entries(m.assets)) {
    if (!/^(linux|darwin|win32)-[a-z0-9][a-z0-9._-]*$/.test(platform)) {
      throw new Error(`invalid platform key: ${platform}`);
    }
    if (!asset || typeof asset !== "object") throw new Error(`assets[${platform}] must be an object`);
    if (typeof asset.url !== "string" || !/^https?:\/\//.test(asset.url)) {
      throw new Error(`assets[${platform}].url must be http(s)`);
    }
    if (typeof asset.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(asset.sha256)) {
      throw new Error(`assets[${platform}].sha256 must be a 64-char lowercase hex digest`);
    }
    if (!Number.isSafeInteger(asset.bytes) || asset.bytes <= 0) {
      throw new Error(`assets[${platform}].bytes must be a positive integer`);
    }
    if (typeof asset.entry !== "string" || !asset.entry || asset.entry.includes("..") || asset.entry.startsWith("/")) {
      throw new Error(`assets[${platform}].entry must be a safe relative path`);
    }
  }
  if (m.signature !== undefined) {
    if (!m.signature || typeof m.signature !== "object") throw new Error("manifest.signature must be an object");
    if (m.signature.algorithm !== "ed25519") throw new Error("manifest.signature.algorithm must be ed25519");
    if (typeof m.signature.value !== "string" || !m.signature.value) {
      throw new Error("manifest.signature.value must be base64");
    }
  }
}

function validateArtifacts(m) {
  for (const [platform, asset] of Object.entries(m.assets)) {
    const file = join(dir, basename(new URL(asset.url).pathname));
    if (!existsSync(file)) throw new Error(`asset file missing for ${platform}: ${file}`);
    const bytes = readFileSync(file);
    const size = statSync(file).size;
    if (size !== asset.bytes) throw new Error(`byte size mismatch for ${platform}: expected ${asset.bytes}, got ${size}`);
    const observed = createHash("sha256").update(bytes).digest("hex");
    if (observed !== asset.sha256) {
      throw new Error(`sha256 mismatch for ${platform}: expected ${asset.sha256}, got ${observed}`);
    }
    validateArchiveLayout(platform, asset, bytes);
  }
}

function validateArchiveLayout(platform, asset, gzBytes) {
  let raw;
  try {
    raw = gunzipSync(gzBytes);
  } catch (err) {
    throw new Error(`asset ${platform} is not a valid gzip stream: ${err.message}`);
  }
  const entries = new Set();
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
    if (fullPath) entries.add(fullPath);
  }
  const required = ["BUNDLE.json", asset.entry];
  for (const entry of required) {
    if (!entries.has(entry)) throw new Error(`asset ${platform} missing archive entry: ${entry}`);
  }
  const hasSourceRootLayout =
    entries.has("share/animica/animica/__init__.py") && entries.has("share/animica/animica/cli/main.py");
  const hasPackageLayout =
    entries.has("share/animica/__init__.py") && entries.has("share/animica/cli/main.py");
  if (!hasSourceRootLayout && !hasPackageLayout) {
    throw new Error(`asset ${platform} missing animica CLI source layout under share/animica`);
  }
  if (platform.startsWith("win32") && !entries.has("bin/animica.cmd")) {
    throw new Error(`asset ${platform} missing Windows launcher: bin/animica.cmd`);
  }
  if (!platform.startsWith("win32") && asset.entry !== "bin/animica") {
    throw new Error(`asset ${platform} must use bin/animica as entry`);
  }
  const hasPythonTree = [...entries].some((e) => e.startsWith("python/") && e !== "python/");
  if (!hasPythonTree) {
    process.stderr.write(`warning: asset ${platform} has no bundled python tree; it will require system python at runtime\n`);
  }
}

function verifySignatureIfConfigured(m, publicKeyText) {
  if (!m.signature) return;
  if (!publicKeyText) {
    throw new Error("manifest is signed but no --public-key or ANIMICA_RUNTIME_MANIFEST_PUBLIC_KEY was provided");
  }
  const normalized = publicKeyText.includes("BEGIN PUBLIC KEY")
    ? publicKeyText.replace(/\\n/g, "\n")
    : Buffer.from(publicKeyText, "base64");
  const publicKey = createPublicKey(
    typeof normalized === "string"
      ? normalized
      : { key: normalized, format: "der", type: "spki" },
  );
  const ok = verify(
    null,
    Buffer.from(canonicalPayload(m), "utf8"),
    publicKey,
    Buffer.from(m.signature.value, "base64"),
  );
  if (!ok) throw new Error("manifest signature verification failed");
}

function sortedForJson(value) {
  if (Array.isArray(value)) return value.map(sortedForJson);
  if (value && typeof value === "object") {
    const out = {};
    for (const k of Object.keys(value).sort()) {
      if (value[k] !== undefined) out[k] = sortedForJson(value[k]);
    }
    return out;
  }
  return value;
}

function canonicalPayload(m) {
  const { signature: _signature, ...unsigned } = m;
  return JSON.stringify(sortedForJson(unsigned));
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
