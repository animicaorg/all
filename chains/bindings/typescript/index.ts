/**
 * Animica Chains — TypeScript bindings & schema validation
 *
 * Usage:
 *   import { loadChain, loadRegistry, verifySelfChecksum, verifyAgainstChecksums } from "./chains/bindings/typescript";
 *
 * Notes:
 * - Runtime validation uses AJV (JSON Schema 2020-12). Install in your consumer:
 *     npm i ajv ajv-formats
 * - This file is framework-agnostic (Node 18+). For ESM, keep "type": "module" in package.json or compile via tsc.
 */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import Ajv, { DefinedError } from "ajv";
import addFormats from "ajv-formats";

/* -------------------------------- Types (mirror chain.schema.json & registry.schema.json) ----------------------- */

export interface NativeCurrency {
  name: string; symbol: string; decimals: number;
}
export interface RpcCfg { http: string[]; ws: string[]; }
export interface Explorer { name: string; url: string; }
export interface Faucet { name: string; url: string; }
export interface P2P {
  protocols: string[]; port: number; seeds: string[]; bootnodes: string[];
}
export interface Addresses {
  format: "bech32m"; hrp: "am";
  pubkeyTypes: Array<"ed25519" | "secp256k1" | "dilithium3" | "sphincs+">;
}
export interface PQCfg {
  sigDefault: "ed25519" | "secp256k1" | "dilithium3" | "sphincs+";
  kex: "kyber-768" | "ntru-hps-509";
  policyVersion: string; // YYYY-MM
}
export interface VMCfg {
  lang: "python"; version: string; gasModel: string;
}
export interface DACfg {
  maxBlobSizeBytes: number; nmtNamespaceBytes: number; rsRate: string; // "10/16"
}
export interface RandomnessCfg {
  method: "vdf+qrng" | "commit-reveal" | "drand" | "contract";
  contract?: string; params?: Record<string, unknown>;
}
export interface GenesisCfg { hash: string; timestamp: string; initialHeight: number; }
export interface GovernanceCfg { site?: string; votingPeriodDays: number; }
export interface LinksCfg { website?: string; studio?: string; explorer?: string; docs?: string; }

export interface Chain {
  schemaVersion: string;
  name: string;
  chainId: number;
  network: "mainnet" | "testnet" | "localnet";
  status: "planned" | "active" | "dev" | "deprecated";
  testnet: boolean;

  nativeCurrency: NativeCurrency;
  rpc: RpcCfg;
  explorers: Explorer[];
  faucets?: Faucet[];
  p2p: P2P;
  addresses: Addresses;
  pq: PQCfg;
  vm: VMCfg;
  da: DACfg;
  randomness?: RandomnessCfg;
  genesis: GenesisCfg;
  governance: GovernanceCfg;
  links?: LinksCfg;
  features: Array<"poies" | "pq" | "ai" | "quantum" | "da" | "vm_py">;

  checksum: string; // sha256 hex (lowercase)
}

export interface RegistryEntry {
  key: string;
  name: string;
  chainId: number;
  network: "mainnet" | "testnet" | "localnet";
  status: "planned" | "active" | "dev" | "deprecated";
  testnet: boolean;
  path: string;
  checksum: string | "<sha256-to-be-generated>";
  icons?: {
    svg?: string; svgDark?: string; png64?: string; png128?: string;
  };
}
export interface Registry {
  schemaVersion: string;
  generatedAt: string; // ISO timestamp
  entries: RegistryEntry[];
}

/* -------------------------------- AJV: compile validators (lazy singletons) ------------------------------------ */

let ajv: Ajv | null = null;
let validateChainFn: ((data: unknown) => data is Chain) | null = null;
let validateRegistryFn: ((data: unknown) => data is Registry) | null = null;

function getAjv(): Ajv {
  if (ajv) return ajv;
  ajv = new Ajv({ strict: true, allErrors: true });
  addFormats(ajv);
  // Load schemas from repo relative paths
  const chainSchemaPath = path.resolve("chains/schemas/chain.schema.json");
  const registrySchemaPath = path.resolve("chains/schemas/registry.schema.json");
  const chainSchema = JSON.parse(fs.readFileSync(chainSchemaPath, "utf8"));
  const registrySchema = JSON.parse(fs.readFileSync(registrySchemaPath, "utf8"));
  ajv.addSchema(chainSchema, "chain.schema.json");
  ajv.addSchema(registrySchema, "registry.schema.json");
  return ajv;
}

function compileValidators() {
  if (validateChainFn && validateRegistryFn) return;
  const a = getAjv();
  validateChainFn = a.getSchema("chain.schema.json") as any;
  validateRegistryFn = a.getSchema("registry.schema.json") as any;
  if (!validateChainFn) throw new Error("Failed to compile chain.schema.json");
  if (!validateRegistryFn) throw new Error("Failed to compile registry.schema.json");
}

/* -------------------------------- Utilities -------------------------------------------------------------------- */

export function computeSha256Hex(bufOrPath: Buffer | string): string {
  const hash = crypto.createHash("sha256");
  if (typeof bufOrPath === "string") {
    const data = fs.readFileSync(bufOrPath);
    hash.update(data);
  } else {
    hash.update(bufOrPath);
  }
  return hash.digest("hex");
}

export function readJson<T = unknown>(filePath: string): T {
  const raw = fs.readFileSync(filePath, "utf8");
  try {
    return JSON.parse(raw) as T;
  } catch (e) {
    throw new Error(`Invalid JSON (${filePath}): ${(e as Error).message}`);
  }
}

function formatAjvErrors(errors: DefinedError[]): string {
  return errors
    .map((e) => {
      const path = e.instancePath || "(root)";
      const msg = e.message || "validation error";
      const more = e.params ? ` (${JSON.stringify(e.params)})` : "";
      return ` - ${path}: ${msg}${more}`;
    })
    .join("\n");
}

/* -------------------------------- Public API: load & validate --------------------------------------------------- */

export function loadChain(filePath: string): Chain {
  compileValidators();
  const json = readJson<unknown>(filePath);
  if (!validateChainFn!(json)) {
    const details = formatAjvErrors(validateChainFn!.errors as DefinedError[]);
    throw new Error(`chains: ${filePath} failed schema validation:\n${details}`);
  }
  return json as Chain;
}

export function loadRegistry(filePath: string): Registry {
  compileValidators();
  const json = readJson<unknown>(filePath);
  if (!validateRegistryFn!(json)) {
    const details = formatAjvErrors(validateRegistryFn!.errors as DefinedError[]);
    throw new Error(`registry: ${filePath} failed schema validation:\n${details}`);
  }
  return json as Registry;
}

/* -------------------------------- Checksums --------------------------------------------------------------------- */

/**
 * Verify that the chain file's embedded "checksum" equals the SHA-256 of the raw file bytes.
 * Returns { ok, actual, embedded }.
 *
 * Note: This intentionally hashes the file *including* the embedded field, which will not match.
 * In Animica, the source of truth is `chains/checksums.txt`. Use verifyAgainstChecksums for canonical checks.
 * This helper instead checks that the embedded value matches the signed list entry (when provided).
 */
export function verifySelfChecksum(chainPath: string, expectedFromList?: string): { ok: boolean; actual: string; embedded?: string } {
  const raw = fs.readFileSync(chainPath);
  const actual = computeSha256Hex(raw);
  const chain = readJson<Partial<Chain>>(chainPath);
  const embedded = typeof chain.checksum === "string" ? chain.checksum : undefined;

  // Self-hash of a JSON that includes its own checksum will not equal embedded; compare to provided list if given.
  if (expectedFromList) {
    return { ok: expectedFromList === embedded, actual, embedded };
  }
  // If no list provided, just return both for caller-side decision.
  return { ok: false, actual, embedded };
}

/**
 * Parse chains/checksums.txt (format: "<sha256>  <path>") into a Map.
 */
export function parseChecksumsFile(checksumsPath: string): Map<string, string> {
  const text = fs.readFileSync(checksumsPath, "utf8");
  const map = new Map<string, string>();
  for (const line of text.split(/\r?\n/)) {
    const l = line.trim();
    if (!l || l.startsWith("#")) continue;
    const m = l.match(/^([0-9a-fA-F]{64})\s+(.+)$/);
    if (m) {
      map.set(m[2], m[1].toLowerCase());
    }
  }
  return map;
}

/**
 * Verify a set of chain files against chains/checksums.txt and (optionally) that their embedded checksum equals the list value.
 */
export function verifyAgainstChecksums(
  checksumsPath: string,
  files?: string[]
): Array<{ path: string; ok: boolean; reason?: string; fileHash: string; listHash?: string; embedded?: string }> {
  const list = parseChecksumsFile(checksumsPath);
  const targets = files && files.length ? files : Array.from(list.keys());
  const results: Array<{ path: string; ok: boolean; reason?: string; fileHash: string; listHash?: string; embedded?: string }> = [];

  for (const p of targets) {
    if (!fs.existsSync(p)) {
      results.push({ path: p, ok: false, reason: "missing file", fileHash: "", listHash: list.get(p) });
      continue;
    }
    const buf = fs.readFileSync(p);
    const fileHash = computeSha256Hex(buf);
    const listHash = list.get(p);
    const embedded = (() => {
      try { return (readJson<Partial<Chain>>(p).checksum as string | undefined) ?? undefined; }
      catch { return undefined; }
    })();

    if (!listHash) {
      results.push({ path: p, ok: false, reason: "no entry in checksums.txt", fileHash, listHash, embedded });
      continue;
    }
    const ok = fileHash.toLowerCase() === listHash.toLowerCase() && (!embedded || embedded.toLowerCase() === listHash.toLowerCase());
    results.push({ path: p, ok, reason: ok ? undefined : "hash mismatch (file and/or embedded)", fileHash, listHash, embedded });
  }
  return results;
}

/* -------------------------------- Convenience: load by key from registry --------------------------------------- */

export function resolveFromRegistry(registryPath: string, key: string): { entry: RegistryEntry; chain: Chain } {
  const reg = loadRegistry(registryPath);
  const entry = reg.entries.find(e => e.key === key);
  if (!entry) throw new Error(`registry: key not found: ${key}`);
  const entryPath = path.resolve(entry.path);
  const chain = loadChain(entryPath);
  return { entry, chain };
}

/* -------------------------------- CLI helpers (optional) -------------------------------------------------------- */
/* Compile this file (tsc) and you can run:
 *   node dist/chains/bindings/typescript/index.js check chains/checksums.txt
 * or
 *   node dist/chains/bindings/typescript/index.js show animica-testnet
 */
if (require.main === module) {
  (async () => {
    const [,, cmd, arg] = process.argv;
    try {
      if (cmd === "check") {
        const checksums = arg || "chains/checksums.txt";
        const results = verifyAgainstChecksums(checksums);
        const bad = results.filter(r => !r.ok);
        for (const r of results) {
          console.log(`${r.ok ? "OK  " : "FAIL"} ${r.path} file=${r.fileHash} list=${r.listHash ?? "-"} embedded=${r.embedded ?? "-"}`);
        }
        process.exit(bad.length ? 1 : 0);
      } else if (cmd === "show") {
        const key = arg || "animica-testnet";
        const { entry, chain } = resolveFromRegistry("chains/registry.json", key);
        console.log(JSON.stringify({ entry, chain }, null, 2));
      } else {
        console.log("Usage:");
        console.log("  check <chains/checksums.txt>  # verify hashes & embedded checksums");
        console.log("  show <registry-key>           # print registry entry + chain JSON");
      }
    } catch (e) {
      console.error(String(e));
      process.exit(2);
    }
  })();
}
