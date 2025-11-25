/**
 * Deterministic subkeys for PQ schemes (Dilithium3, SPHINCS+-SHAKE-128s).
 *
 * Derivation model (stable across platforms):
 *   subseed = HKDF-SHA3-256(ikm=masterSeed,
 *                           salt="Animica wallet seed v1",
 *                           info="animica/keyring/v1/<algo>/<role>/<account>/<index>",
 *                           length = algo_seed_len)
 *   keypair = scheme.keypairFromSeed(subseed)
 *
 * Notes
 * - This file performs *deterministic* derivation. masterSeed comes from mnemonic.ts
 *   (PBKDF/HKDF-SHA3). No secrets are stored here; storage.ts handles persistence.
 * - The resulting addresses are chain-agnostic (no chainId in derivation).
 * - If you rotate derivation semantics, bump the "v1" tag in the info string.
 */

import type { KeyAlgo } from './storage';
import { hkdf } from '../pq/hkdf';
import * as DIL from '../pq/dilithium3';
import * as SPX from '../pq/sphincs_shake_128s';

export type DeriveRole = 'sign' | 'auth';

export interface DeriveOpts {
  /** Logical account (hardened concept for UI grouping). Defaults to 0. */
  account?: number;
  /** Address index within the account (0-based). */
  index: number;
  /** Role tag (changes derivation domain). Defaults to 'sign'. */
  role?: DeriveRole;
}

export interface Keypair {
  algo: KeyAlgo;
  publicKey: Uint8Array;
  secretKey: Uint8Array;
  /** Human-friendly path string (not used in derivation itself). */
  path: string;
}

/** Fixed HKDF salt (domain separation for wallet derivation). */
const HKDF_SALT = new TextEncoder().encode('Animica wallet seed v1');

/** Required seed lengths for supported algos (in bytes). */
const SEED_LEN: Record<KeyAlgo, number> = {
  'dilithium3': 32,            // 32-byte seed feeds Dilithium keygen
  'sphincs_shake_128s': 48,     // 3×N with N=16 → 48 bytes for SPHINCS+ seeds
} as const;

/** Build a canonical info string for HKDF expand phase. */
function hkdfInfo(algo: KeyAlgo, role: DeriveRole, account: number, index: number): Uint8Array {
  const s = `animica/keyring/v1/${algo}/${role}/${account}/${index}`;
  return new TextEncoder().encode(s);
}

/** Pure string path for display / export (not parsed for derivation). */
function displayPath(algo: KeyAlgo, role: DeriveRole, account: number, index: number): string {
  // "m/animica-v1/<algo>/<role>/<account>/<index>"
  return `m/animica-v1/${algo}/${role}/${account}/${index}`;
}

/**
 * Derive a subseed for a given algo & position.
 */
export async function deriveSubseed(
  masterSeed: Uint8Array,
  algo: KeyAlgo,
  { account = 0, index, role = 'sign' }: DeriveOpts,
): Promise<Uint8Array> {
  const len = SEED_LEN[algo];
  if (!len) throw new Error(`Unsupported algo: ${algo}`);
  if (!(masterSeed instanceof Uint8Array) || masterSeed.length === 0) {
    throw new Error('masterSeed must be non-empty Uint8Array');
  }
  const info = hkdfInfo(algo, role, account, index);
  return hkdf({ ikm: masterSeed, salt: HKDF_SALT, info, length: len });
}

/**
 * Derive a deterministic keypair for the requested algorithm.
 * Uses scheme.keypairFromSeed(subseed) via our PQ wrappers.
 */
export async function deriveKeypair(
  masterSeed: Uint8Array,
  algo: KeyAlgo,
  opts: DeriveOpts,
): Promise<Keypair> {
  const { account = 0, index, role = 'sign' } = opts;
  const subseed = await deriveSubseed(masterSeed, algo, { account, index, role });

  switch (algo) {
    case 'dilithium3': {
      const derivePk = (DIL as any).derivePublicKey;
      if (typeof derivePk === 'function') {
        const pk = await derivePk(subseed, index);
        return {
          algo,
          publicKey: toU8(pk),
          secretKey: toU8(subseed),
          path: displayPath(algo, role, account, index),
        };
      }
      const keypairFromSeed = (DIL as any).keypairFromSeed;
      if (typeof keypairFromSeed === 'function') {
        const { publicKey, secretKey } = await keypairFromSeed(subseed);
        return {
          algo,
          publicKey: toU8(publicKey),
          secretKey: toU8(secretKey),
          path: displayPath(algo, role, account, index),
        };
      }
      throw new Error('Dilithium3 wrapper missing keypairFromSeed(seed) export');
    }
    case 'sphincs_shake_128s': {
      const derivePk = (SPX as any).derivePublicKey;
      if (typeof derivePk === 'function') {
        const pk = await derivePk(subseed, index);
        return {
          algo,
          publicKey: toU8(pk),
          secretKey: toU8(subseed),
          path: displayPath(algo, role, account, index),
        };
      }
      const keypairFromSeed = (SPX as any).keypairFromSeed;
      if (typeof keypairFromSeed === 'function') {
        const { publicKey, secretKey } = await keypairFromSeed(subseed);
        return {
          algo,
          publicKey: toU8(publicKey),
          secretKey: toU8(secretKey),
          path: displayPath(algo, role, account, index),
        };
      }
      throw new Error('SPHINCS+ wrapper missing keypairFromSeed(seed) export');
    }
    default:
      throw new Error(`Unsupported algo: ${algo as string}`);
  }
}

/**
 * Derive a batch of keypairs for a contiguous index range.
 * Useful for account discovery / UI lists.
 */
export async function deriveBatch(
  masterSeed: Uint8Array,
  algo: KeyAlgo,
  startIndex: number,
  count: number,
  account = 0,
  role: DeriveRole = 'sign',
): Promise<Keypair[]> {
  if (count < 0) throw new Error('count must be >= 0');
  const out: Keypair[] = [];
  for (let i = 0; i < count; i++) {
    out.push(await deriveKeypair(masterSeed, algo, { account, index: startIndex + i, role }));
  }
  return out;
}

/** Narrow helper to ensure Uint8Array output when wrappers return ArrayBuffer or Buffer-like. */
function toU8(x: Uint8Array | ArrayBuffer | number[] | Buffer): Uint8Array {
  if (x instanceof Uint8Array) return x;
  if (x instanceof ArrayBuffer) return new Uint8Array(x);
  // @ts-ignore Node Buffer
  if (typeof Buffer !== 'undefined' && typeof (Buffer as any).isBuffer === 'function' && (Buffer as any).isBuffer(x)) {
    // @ts-ignore Node Buffer
    return new Uint8Array((x as Buffer).buffer, (x as Buffer).byteOffset, (x as Buffer).byteLength);
  }
  if (Array.isArray(x)) return new Uint8Array(x);
  throw new Error('Unsupported key bytes shape from PQ wrapper');
}

export const _internal = {
  SEED_LEN,
  HKDF_SALT,
  hkdfInfo,
  displayPath,
};
