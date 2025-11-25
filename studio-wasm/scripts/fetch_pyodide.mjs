// @animica/studio-wasm — Fetch & lock Pyodide assets into vendor/
//
// Usage:
//   node scripts/fetch_pyodide.mjs
//   node scripts/fetch_pyodide.mjs --version 0.24.1
//   node scripts/fetch_pyodide.mjs --cdn https://cdn.jsdelivr.net/pyodide/v0.24.1/full
//   node scripts/fetch_pyodide.mjs --offline .cache/pyodide
//   node scripts/fetch_pyodide.mjs --force
//
// Behavior:
// - Chooses source in this order: --offline dir (if files exist) → --cdn / env CDN → default CDN for version
// - Streams files to studio-wasm/vendor/ and computes sha256/sha384 + sizes
// - Writes/updates studio-wasm/pyodide.lock.json with version and per-file checksums
// - Skips downloads if lock + files already match unless --force provided
//
// Env vars (fallbacks):
//   PYODIDE_VERSION, PYODIDE_USE_CDN ("true"/"false"), PYODIDE_CDN, PYODIDE_OFFLINE_DIR
//
// Note: File names are expected to be: pyodide.js, pyodide.wasm, pyodide.data
// If your distribution differs (e.g., *.asm.*), tweak the ASSETS list below.

import fs from 'node:fs/promises';
import fssync from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';

// Node 18+ has global fetch. If not, users can polyfill.

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PKG_ROOT = path.resolve(__dirname, '..');
const VENDOR_DIR = path.join(PKG_ROOT, 'vendor');
const LOCK_PATH = path.join(PKG_ROOT, 'pyodide.lock.json');

const DEFAULT_VERSION = '0.24.1';
const ASSETS = ['pyodide.js', 'pyodide.wasm', 'pyodide.data'];

function parseArgs(argv) {
  const args = new Map();
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (!next || next.startsWith('--')) {
        args.set(key, true);
      } else {
        args.set(key, next);
        i++;
      }
    }
  }
  return args;
}

function envBool(name, def) {
  const v = process.env[name];
  if (v == null) return def;
  return String(v).toLowerCase() === 'true';
}
function envStr(name, def) {
  const v = process.env[name];
  return v == null || v === '' ? def : v;
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

function fileExists(p) {
  try { fssync.accessSync(p); return true; } catch { return false; }
}

function toSRI(algo, buf) {
  const b64 = crypto.createHash(algo).update(buf).digest('base64');
  return `${algo}-${b64}`;
}

async function hashFile(p) {
  const h256 = crypto.createHash('sha256');
  const h384 = crypto.createHash('sha384');
  const stat = await fs.stat(p);
  await new Promise((resolve, reject) => {
    const s = fssync.createReadStream(p);
    s.on('data', chunk => { h256.update(chunk); h384.update(chunk); });
    s.on('end', resolve);
    s.on('error', reject);
  });
  const sha256 = h256.digest('hex');
  const sha384 = h384.digest('hex');
  const sri256 = `sha256-${Buffer.from(sha256, 'hex').toString('base64')}`;
  const sri384 = `sha384-${Buffer.from(sha384, 'hex').toString('base64')}`;
  return { size: stat.size, sha256, sha384, sri256, sri384, mtime: stat.mtimeMs };
}

async function copyFile(src, dst) {
  await ensureDir(path.dirname(dst));
  await fs.copyFile(src, dst);
}

async function downloadTo(url, dst) {
  await ensureDir(path.dirname(dst));
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok || !res.body) {
    throw new Error(`Failed to fetch ${url}: ${res.status} ${res.statusText}`);
  }
  const tmp = `${dst}.tmp-${Date.now()}`;
  const ws = fssync.createWriteStream(tmp);
  await new Promise((resolve, reject) => {
    res.body.pipe(ws);
    res.body.on('error', reject);
    ws.on('finish', resolve);
    ws.on('error', reject);
  });
  await fs.rename(tmp, dst);
}

async function readLock() {
  if (!fileExists(LOCK_PATH)) return null;
  try {
    const buf = await fs.readFile(LOCK_PATH, 'utf8');
    return JSON.parse(buf);
  } catch {
    return null;
  }
}

async function writeLock(lock) {
  const pretty = JSON.stringify(lock, null, 2);
  await fs.writeFile(LOCK_PATH, `${pretty}\n`);
}

function chooseBaseUrl(version) {
  // Default CDN layout
  return `https://cdn.jsdelivr.net/pyodide/v${version}/full`;
}

async function main() {
  const args = parseArgs(process.argv);

  const version = String(
    args.get('version') ??
    envStr('PYODIDE_VERSION', DEFAULT_VERSION)
  );

  const force = args.get('force') === true;

  const offlineDir = args.get('offline') ?? envStr('PYODIDE_OFFLINE_DIR', '');
  // Determine CDN usage
  const useCdn =
    (args.has('cdn') ? true : envBool('PYODIDE_USE_CDN', false));
  const cdnBase =
    args.get('cdn') ??
    envStr('PYODIDE_CDN', chooseBaseUrl(version));

  console.log(`[studio-wasm] Fetch Pyodide v${version}`);
  console.log(`[studio-wasm] vendor dir: ${path.relative(process.cwd(), VENDOR_DIR)}`);
  console.log(`[studio-wasm] lock file : ${path.relative(process.cwd(), LOCK_PATH)}`);

  await ensureDir(VENDOR_DIR);

  const prevLock = await readLock();

  // If not forcing, and lock matches and files exist, short-circuit
  if (!force && prevLock?.version === version) {
    const allMatch = await Promise.all(
      (prevLock.files ?? []).map(async f => {
        const p = path.join(VENDOR_DIR, f.name);
        if (!fileExists(p)) return false;
        const h = await hashFile(p);
        return h.sha256 === f.sha256 && h.sha384 === f.sha384 && h.size === f.size;
      })
    );
    if (allMatch.length === ASSETS.length && allMatch.every(Boolean)) {
      console.log('[studio-wasm] All assets present and match lock — nothing to do.');
      return;
    }
  }

  const results = [];

  for (const name of ASSETS) {
    const dst = path.join(VENDOR_DIR, name);

    // Prefer offline cache if file exists there
    const offlinePath = offlineDir ? path.resolve(offlineDir, name) : null;
    const haveOffline = offlinePath && fileExists(offlinePath);

    if (haveOffline) {
      console.log(`- copying from offline cache: ${name}`);
      await copyFile(offlinePath, dst);
    } else if (useCdn) {
      const url = `${cdnBase.replace(/\/+$/, '')}/${name}`;
      console.log(`- downloading from CDN: ${url}`);
      await downloadTo(url, dst);
    } else {
      // Neither offline nor CDN allowed → error
      throw new Error(
        `No source for ${name}. Provide --offline DIR or enable CDN via --cdn or PYODIDE_USE_CDN=true`
      );
    }

    const h = await hashFile(dst);
    results.push({
      name,
      size: h.size,
      sha256: h.sha256,
      sha384: h.sha384,
      sri256: h.sri256,
      sri384: h.sri384,
      mtime: h.mtime
    });
  }

  const lock = {
    version,
    createdAt: new Date().toISOString(),
    source: {
      mode: offlineDir && results.length ? 'offline' : (useCdn ? 'cdn' : 'unknown'),
      offlineDir: offlineDir || null,
      cdnBase: useCdn ? cdnBase : null
    },
    files: results
  };

  await writeLock(lock);

  console.log('\n[studio-wasm] Wrote lock file with checksums:');
  for (const f of results) {
    console.log(
      `  • ${f.name}  size=${f.size}  sha256=${f.sha256.slice(0, 12)}…  sha384=${f.sha384.slice(0, 12)}…`
    );
  }
  console.log('\nDone.');
}

main().catch(err => {
  console.error('[studio-wasm] fetch_pyodide failed:', err?.stack || err?.message || err);
  process.exit(1);
});
