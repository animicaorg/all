/**
 * hash.ts
 * -------
 * Minimal hashing utilities for the browser/worker environment.
 *
 * - Uses WebCrypto (SubtleCrypto) for SHA-256 / SHA-512.
 * - Provides a pluggable provider for SHA3-256 and Keccak-256 so we can route
 *   to Pyodide (Python) or any JS implementation at runtime without adding
 *   heavy dependencies to this package.
 *
 * Usage:
 *   import { hash, sha256, sha512, setHashProvider } from "./hash";
 *   const d = await sha256(bytes);
 *
 *   // Later, once Pyodide is loaded, provide SHA3/Keccak:
 *   setHashProvider({
 *     async digest(algo, data) {
 *       // Example: call into Pyodide (pseudo-code)
 *       // return pyDigest(algo, data);
 *       throw new Error("not wired"); // replace with real bridge
 *     }
 *   });
 */

import { bytesFrom, bytesToHex } from "./bytes";

/* --------------------------------- Types ---------------------------------- */

export type BuiltinAlgo = "sha256" | "sha512";
export type ExtendedAlgo = "sha3_256" | "keccak256";
export type AnyAlgo = BuiltinAlgo | ExtendedAlgo;

export interface HashProvider {
  /** Must implement SHA3-256 and Keccak-256. Return digest bytes. */
  digest(algo: ExtendedAlgo, data: Uint8Array): Promise<Uint8Array>;
}

/* --------------------------- Pluggable Provider ---------------------------- */

let provider: HashProvider | null = null;

export function setHashProvider(p: HashProvider | null): void {
  provider = p;
}

export function getHashProvider(): HashProvider | null {
  return provider;
}

/* ------------------------------ SubtleCrypto ------------------------------- */

function getSubtle(): SubtleCrypto {
  // Browser / Worker
  const g = globalThis as any;
  if (g.crypto && g.crypto.subtle) return g.crypto.subtle as SubtleCrypto;

  // Node.js >= 18 has global webcrypto, but in some bundlers it might be under require('crypto').webcrypto
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const nodeCrypto = require("crypto");
    if (nodeCrypto?.webcrypto?.subtle) {
      return nodeCrypto.webcrypto.subtle as SubtleCrypto;
    }
  } catch {
    /* ignore */
  }
  throw new Error("SubtleCrypto not available: SHA-256/SHA-512 require WebCrypto.");
}

/* --------------------------------- Digest --------------------------------- */

export async function hash(algo: AnyAlgo, input: string | ArrayBuffer | ArrayBufferView | Uint8Array | number[]): Promise<Uint8Array> {
  const data = bytesFrom(input);

  if (algo === "sha256" || algo === "sha512") {
    const subtle = getSubtle();
    const name = algo === "sha256" ? "SHA-256" : "SHA-512";
    const buf = await subtle.digest({ name }, data);
    return new Uint8Array(buf);
  }

  // Extended algorithms require a provider (e.g., wired via Pyodide)
  if (!provider) {
    throw new Error(
      `No hash provider registered for ${algo}. ` +
      `Call setHashProvider(...) after initializing Pyodide or a JS SHA3/Keccak implementation.`
    );
  }
  return provider.digest(algo, data);
}

/* ----------------------------- Convenience APIs --------------------------- */

export async function sha256(input: Parameters<typeof hash>[1]): Promise<Uint8Array> {
  return hash("sha256", input);
}

export async function sha512(input: Parameters<typeof hash>[1]): Promise<Uint8Array> {
  return hash("sha512", input);
}

export async function sha3_256(input: Parameters<typeof hash>[1]): Promise<Uint8Array> {
  return hash("sha3_256", input);
}

export async function keccak256(input: Parameters<typeof hash>[1]): Promise<Uint8Array> {
  return hash("keccak256", input);
}

/* ----------------------------- Hex Convenience ---------------------------- */

export async function hashHex(algo: AnyAlgo, input: Parameters<typeof hash>[1]): Promise<`0x${string}`> {
  const out = await hash(algo, input);
  return bytesToHex(out, { withPrefix: true }) as `0x${string}`;
}

export async function sha256Hex(input: Parameters<typeof hash>[1]): Promise<`0x${string}`> {
  const out = await sha256(input);
  return bytesToHex(out, { withPrefix: true }) as `0x${string}`;
}

export async function sha512Hex(input: Parameters<typeof hash>[1]): Promise<`0x${string}`> {
  const out = await sha512(input);
  return bytesToHex(out, { withPrefix: true }) as `0x${string}`;
}

export async function sha3_256Hex(input: Parameters<typeof hash>[1]): Promise<`0x${string}`> {
  const out = await sha3_256(input);
  return bytesToHex(out, { withPrefix: true }) as `0x${string}`;
}

export async function keccak256Hex(input: Parameters<typeof hash>[1]): Promise<`0x${string}`> {
  const out = await keccak256(input);
  return bytesToHex(out, { withPrefix: true }) as `0x${string}`;
}

/* --------------------------------- Default -------------------------------- */

export default {
  setHashProvider,
  getHashProvider,
  hash,
  sha256,
  sha512,
  sha3_256,
  keccak256,
  hashHex,
  sha256Hex,
  sha512Hex,
  sha3_256Hex,
  keccak256Hex,
};
