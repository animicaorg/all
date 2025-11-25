#!/usr/bin/env node
/**
 * build_chain_meta.mjs
 *
 * Validate and merge chain metadata from a `/chains/` directory into a single
 * canonical JSON bundle for the website build. Intended to be run in CI or
 * from package.json scripts (e.g., "prebuild").
 *
 * Inputs (in priority order):
 *  - CLI:     --dir <path>                  (directory containing *.json chain files)
 *             --out <path>                  (output directory for generated files)
 *             --strict                      (treat warnings as errors)
 *  - ENV:     CHAINS_DIR, CHAINMETA_OUT_DIR
 *  - Default: dir = "<repo>/website/chains", out = "<repo>/website/src/generated"
 *
 * Outputs:
 *  - <out>/chainmeta.json        (pretty-printed)
 *  - <out>/chainmeta.min.json    (minified)
 *
 * Both files have the shape:
 *   {
 *     ok: true,
 *     generatedAt: "ISO-STRING",
 *     dir: "<absolute path>",
 *     count: <n>,
 *     sha256: "<hex digest of minified payload>",
 *     chains: [ { id, chainId, name, rpc:[], ... } ]
 *   }
 *
 * Validation performed:
 *   - File must be valid JSON.
 *   - `id` is required and must match filename stem.
 *   - `chainId` must be a finite integer (>= 0).
 *   - `name` required (string).
 *   - `rpc` is string or array of strings; coerced to array.
 *   - `explorer` optional string.
 *   - No duplicate `id` or `chainId` (warn/err).
 *   - RPC endpoints must be HTTPS unless localhost/127.0.0.1.
 *   - Optional cross-check with `index.json` (if present): warns on drift.
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));

/* ----------------------------- CLI / ENV parse ---------------------------- */

function parseArgs(argv) {
  const opts = { dir: null, out: null, strict: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dir" && argv[i + 1]) { opts.dir = argv[++i]; continue; }
    if (a === "--out" && argv[i + 1]) { opts.out = argv[++i]; continue; }
    if (a === "--strict") { opts.strict = true; continue; }
    if (a === "-h" || a === "--help") {
      console.log(`Usage: node ${path.basename(process.argv[1])} [--dir DIR] [--out DIR] [--strict]`);
      process.exit(0);
    }
  }
  return opts;
}

const cli = parseArgs(process.argv);
const CHAINS_DIR = path.resolve(
  cli.dir ||
  process.env.CHAINS_DIR ||
  path.join(__dirname, "..", "chains")
);
const OUT_DIR = path.resolve(
  cli.out ||
  process.env.CHAINMETA_OUT_DIR ||
  path.join(__dirname, "..", "src", "generated")
);
const STRICT = !!cli.strict;

/* --------------------------------- Utils --------------------------------- */

const isLocalhostUrl = (u) => {
  try {
    const x = new URL(u);
    return x.hostname === "localhost" || x.hostname === "127.0.0.1";
  } catch { return false; }
};

const isHttpsUrl = (u) => {
  try { return new URL(u).protocol === "https:"; } catch { return false; }
};

const sha256Hex = (bufOrStr) =>
  crypto.createHash("sha256").update(bufOrStr).digest("hex");

async function readJson(fp) {
  const raw = await fs.readFile(fp, "utf8");
  try { return JSON.parse(raw); }
  catch (e) {
    throw new Error(`Invalid JSON: ${e.message}`);
  }
}

function filenameStem(fp) {
  return path.basename(fp).replace(/\.json$/i, "");
}

function normalizeChain(rec, fp) {
  const stem = filenameStem(fp);

  // id
  if (!rec.id) rec.id = stem;
  if (typeof rec.id !== "string" || rec.id.trim() === "") {
    throw new Error(`"id" must be a non-empty string (file ${path.basename(fp)})`);
  }
  if (rec.id !== stem) {
    throw new Error(`"id" (${rec.id}) must match filename stem (${stem}) in ${path.basename(fp)}`);
  }

  // name
  if (typeof rec.name !== "string" || rec.name.trim() === "") {
    throw new Error(`"name" is required and must be a non-empty string (${path.basename(fp)})`);
  }

  // chainId
  const cid = Number(rec.chainId);
  if (!Number.isFinite(cid) || !Number.isInteger(cid) || cid < 0) {
    throw new Error(`"chainId" must be a non-negative integer (${path.basename(fp)})`);
  }
  rec.chainId = cid;

  // rpc → array
  if (rec.rpc == null) rec.rpc = [];
  if (!Array.isArray(rec.rpc)) rec.rpc = [rec.rpc];
  rec.rpc = rec.rpc.map(String);

  // explorer (optional)
  if (rec.explorer != null) rec.explorer = String(rec.explorer);

  // caip2 (optional) default
  if (!rec.caip2) rec.caip2 = `animica:${rec.chainId}`;

  // status default
  if (!rec.status) rec.status = rec.testnet ? "testnet" : "unknown";

  return rec;
}

function validateRpcEndpoints(rec, warnings) {
  for (const rpc of rec.rpc) {
    if (isLocalhostUrl(rpc)) continue;
    if (!isHttpsUrl(rpc)) {
      warnings.push(`[rpc] Non-HTTPS endpoint for ${rec.id}: ${rpc}`);
    }
  }
}

function stableSortChains(list) {
  return list.sort((a, b) => {
    const ac = Number(a.chainId ?? Number.MAX_SAFE_INTEGER);
    const bc = Number(b.chainId ?? Number.MAX_SAFE_INTEGER);
    if (ac !== bc) return ac - bc;
    return String(a.id).localeCompare(String(b.id));
  });
}

async function ensureDir(d) {
  await fs.mkdir(d, { recursive: true });
}

/* ------------------------------- Main build ------------------------------- */

async function build() {
  const warnings = [];
  const errors = [];

  // 1) Read directory
  let entries;
  try {
    entries = await fs.readdir(CHAINS_DIR, { withFileTypes: true });
  } catch (e) {
    throw new Error(`Chains directory not found: ${CHAINS_DIR} (${e.message})`);
  }

  const files = entries
    .filter((e) => e.isFile() && /\.json$/i.test(e.name) && !/^\./.test(e.name))
    .map((e) => path.join(CHAINS_DIR, e.name));

  if (files.length === 0) {
    throw new Error(`No *.json files found in ${CHAINS_DIR}`);
  }

  // 2) Read optional index.json to cross-check names
  let indexMap = new Map();
  const idxPath = path.join(CHAINS_DIR, "index.json");
  if (files.some((f) => path.basename(f).toLowerCase() === "index.json")) {
    try {
      const idx = await readJson(idxPath);
      if (Array.isArray(idx?.chains)) {
        for (const ent of idx.chains) {
          if (ent?.id) indexMap.set(String(ent.id), ent);
        }
      }
    } catch (e) {
      warnings.push(`index.json present but could not be parsed: ${e.message}`);
    }
  }

  // 3) Load, normalize, validate
  const byId = new Map();
  const byChainId = new Map();
  const chains = [];

  for (const fp of files) {
    // Skip index.json from the chain list itself
    if (path.basename(fp).toLowerCase() === "index.json") continue;

    let rec;
    try {
      rec = await readJson(fp);
    } catch (e) {
      errors.push(`${path.basename(fp)}: ${e.message}`);
      continue;
    }

    try {
      rec = normalizeChain(rec, fp);
      validateRpcEndpoints(rec, warnings);

      // duplicate checks
      if (byId.has(rec.id)) {
        throw new Error(`Duplicate id "${rec.id}" (also in ${path.basename(byId.get(rec.id).__file)})`);
      }
      if (byChainId.has(rec.chainId)) {
        warnings.push(`Duplicate chainId ${rec.chainId}: ${byChainId.get(rec.chainId)} and ${rec.id}`);
      }

      // cross-check index.json name (if present)
      const idxEnt = indexMap.get(rec.id);
      if (idxEnt && idxEnt.name && idxEnt.name !== rec.name) {
        warnings.push(`Name drift for ${rec.id}: index.json="${idxEnt.name}" vs file="${rec.name}"`);
      }

      // Attach filename for better error messages (non-emitted)
      Object.defineProperty(rec, "__file", { value: fp, enumerable: false });
      byId.set(rec.id, rec);
      byChainId.set(rec.chainId, rec.id);
      chains.push(rec);
    } catch (e) {
      errors.push(`${path.basename(fp)}: ${e.message}`);
    }
  }

  if (errors.length) {
    const msg = `Validation failed:\n- ` + errors.join("\n- ");
    throw new Error(msg);
  }

  // 4) Sort + shape + hash
  stableSortChains(chains);

  // Strip non-enumerables and ensure stable key order for hash
  const shaped = chains.map((r) => {
    const {
      id, chainId, name, rpc, explorer, testnet, docs, faucets, caip2, status,
      features, blockTimeMs, finality, notes, ...rest
    } = r;

    return {
      id, chainId, name, rpc, explorer, testnet, docs, faucets, caip2, status,
      ...(features != null ? { features } : {}),
      ...(blockTimeMs != null ? { blockTimeMs } : {}),
      ...(finality != null ? { finality } : {}),
      ...(notes != null ? { notes } : {}),
      // Include any extra fields (sorted by key)
      ...Object.fromEntries(Object.keys(rest).sort().map((k) => [k, rest[k]])),
    };
  });

  const payload = {
    ok: true,
    generatedAt: new Date().toISOString(),
    dir: CHAINS_DIR,
    count: shaped.length,
    chains: shaped,
  };

  const minJson = JSON.stringify(payload);
  const prettyJson = JSON.stringify({ ...payload, sha256: sha256Hex(minJson) }, null, 2);
  const minWithHash = JSON.stringify({ ...payload, sha256: sha256Hex(minJson) });

  // 5) Write outputs
  await ensureDir(OUT_DIR);
  const prettyPath = path.join(OUT_DIR, "chainmeta.json");
  const minPath = path.join(OUT_DIR, "chainmeta.min.json");

  await fs.writeFile(prettyPath, prettyJson + "\n", "utf8");
  await fs.writeFile(minPath, minWithHash, "utf8");

  // 6) Emit summary
  const httpsOk = shaped.flatMap((r) =>
    r.rpc?.filter((u) => !isLocalhostUrl(u) && isHttpsUrl(u)) ?? []
  ).length;

  const httpsWarn = shaped.flatMap((r) =>
    r.rpc?.filter((u) => !isLocalhostUrl(u) && !isHttpsUrl(u)) ?? []
  ).length;

  if (warnings.length) {
    const header = STRICT ? "Warnings (treated as errors due to --strict):" : "Warnings:";
    console.warn(header);
    for (const w of warnings) console.warn(" - " + w);
    if (STRICT) {
      throw new Error(`Aborting due to warnings in strict mode (${warnings.length})`);
    }
  }

  console.log(`\n✔ chainmeta built`);
  console.log(`   source:   ${CHAINS_DIR}`);
  console.log(`   output:   ${OUT_DIR}`);
  console.log(`   files:    ${chains.length} chains`);
  console.log(`   https:    ${httpsOk} ok, ${httpsWarn} non-https (excluding localhost)`);
  console.log(`   pretty:   ${path.relative(process.cwd(), prettyPath)}`);
  console.log(`   minified: ${path.relative(process.cwd(), minPath)}`);
  console.log(`   sha256:   ${sha256Hex(minWithHash)}\n`);
}

/* --------------------------------- Invoke -------------------------------- */

build().catch((err) => {
  console.error("\n✖ build_chain_meta failed:");
  console.error(err?.stack || err?.message || String(err));
  process.exit(1);
});
