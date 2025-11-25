/**
 * Mnemonic utilities (BIP-39-like, but SHA3-based and with a deterministic pseudo-wordlist).
 *
 * Differences vs BIP-39:
 * - Uses SHA3-256 for checksum (vs SHA-256 in BIP-39).
 * - Uses PBKDF2-HMAC-SHA3-512 with 2048 iterations for the intermediate seed (vs HMAC-SHA512).
 * - Then compresses to 32 bytes via HKDF-SHA3-256 ('animica/v1/seed') for master seed.
 * - Wordlist is a deterministic pseudo-wordlist of size 2048 generated from syllables.
 *   (We do NOT aim for BIP-39 cross-compatibility; this is "BIP-39-like".)
 *
 * Exposed API:
 *   - generateMnemonic(words = 12 | 24): string
 *   - validateMnemonic(m): boolean
 *   - mnemonicToSeed(mnemonic: string, passphrase = ''): Promise<Uint8Array>  // 32 bytes
 */

import { hkdfSha3_256, hmacSha3_512, sha3_256 } from '../pq/hkdf'; // implemented in pq/hkdf.ts

// --------------------------- Wordlist (2048 generated) ---------------------------

/**
 * We generate 2048 pronounceable pseudo-words using 8×8×8×4 combinations:
 *   word(i) = C1[i3] + V1[i2] + C2[i1] + V2[i0]
 * where:
 *   i0 in [0..3], i1 in [0..7], i2 in [0..7], i3 in [0..7] and
 *   i = (((i3 * 8 + i2) * 8 + i1) * 4 + i0).
 *
 * This is stable, deterministic, and reversible (via the inverse mapping table).
 * We cache the mapping word -> index on first use.
 */
const C: string[]  = ['b','c','d','f','g','h','j','k'];                 // 8
const V1: string[] = ['a','e','i','o','u','y','aa','ee'];               // 8
const C2: string[] = ['l','m','n','p','r','s','t','v'];                 // 8
const V2: string[] = ['a','e','i','o'];                                 // 4

function indexToWord(i: number): string {
  if (i < 0 || i >= 2048) throw new Error('word index out of range');
  const i0 = i % 4; const q0 = (i - i0) / 4;
  const i1 = q0 % 8; const q1 = (q0 - i1) / 8;
  const i2 = q1 % 8; const q2 = (q1 - i2) / 8;
  const i3 = q2 % 8;
  return C[i3] + V1[i2] + C2[i1] + V2[i0];
}

let WORD_TO_INDEX: Map<string, number> | null = null;

function ensureWordMap(): Map<string, number> {
  if (WORD_TO_INDEX) return WORD_TO_INDEX;
  const m = new Map<string, number>();
  for (let i = 0; i < 2048; i++) {
    m.set(indexToWord(i), i);
  }
  WORD_TO_INDEX = m;
  return m;
}

// --------------------------- Bit helpers ---------------------------

function bytesToBitString(bytes: Uint8Array): string {
  let s = '';
  for (let i = 0; i < bytes.length; i++) {
    s += bytes[i].toString(2).padStart(8, '0');
  }
  return s;
}

function bitStringToBytes(bitstr: string): Uint8Array {
  const outLen = Math.ceil(bitstr.length / 8);
  const out = new Uint8Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const chunk = bitstr.slice(i * 8, i * 8 + 8).padEnd(8, '0');
    out[i] = parseInt(chunk, 2);
  }
  return out;
}

function concatBits(a: string, b: string): string {
  return a + b;
}

// --------------------------- RNG ---------------------------

function randomBytes(n: number): Uint8Array {
  const b = new Uint8Array(n);
  crypto.getRandomValues(b);
  return b;
}

// --------------------------- Normalization ---------------------------

function nfkd(s: string): string {
  // Browser environments have ICU; Node 18+ also supports NFKD
  return s.normalize('NFKD');
}

const te = new TextEncoder();

// --------------------------- PBKDF2-HMAC-SHA3-512 ---------------------------

/**
 * PBKDF2 per RFC 8018 (F function), with HMAC-SHA3-512 as PRF.
 * @param password bytes
 * @param salt bytes
 * @param iterations >= 1
 * @param dkLen desired output length in bytes
 */
async function pbkdf2Sha3_512(
  password: Uint8Array,
  salt: Uint8Array,
  iterations: number,
  dkLen: number,
): Promise<Uint8Array> {
  if (iterations < 1) throw new Error('iterations must be >= 1');
  const hLen = 64; // SHA3-512 output
  const l = Math.ceil(dkLen / hLen);
  const r = dkLen - (l - 1) * hLen;

  const T = new Uint8Array(l * hLen);
  for (let i = 1; i <= l; i++) {
    // U1 = PRF(P, S || INT_32_BE(i))
    const block = new Uint8Array(salt.length + 4);
    block.set(salt, 0);
    block[block.length - 4] = (i >>> 24) & 0xff;
    block[block.length - 3] = (i >>> 16) & 0xff;
    block[block.length - 2] = (i >>> 8) & 0xff;
    block[block.length - 1] = i & 0xff;

    let Ui = await hmacSha3_512(password, block);
    const Ti = new Uint8Array(Ui); // accumulator

    for (let c = 2; c <= iterations; c++) {
      Ui = await hmacSha3_512(password, Ui);
      for (let j = 0; j < hLen; j++) Ti[j] ^= Ui[j];
    }
    T.set(Ti, (i - 1) * hLen);
  }

  return T.slice(0, (l - 1) * hLen + r);
}

// --------------------------- Checksum (SHA3-256) ---------------------------

/**
 * Compute checksum length in bits: ENT/32 (BIP-39 rule).
 * ENT must be a multiple of 32 between 128 and 256 for our purposes.
 */
function checksumBits(entropy: Uint8Array): string {
  const digest = sha3_256(entropy); // 32 bytes
  const csLen = (entropy.length * 8) / 32;
  const bitstr = bytesToBitString(digest);
  return bitstr.slice(0, csLen);
}

// --------------------------- Mnemonic API ---------------------------

/** Generate a new mnemonic (12 or 24 words). Default: 12. */
export function generateMnemonic(words: 12 | 24 = 12): string {
  const entBytes = words === 24 ? 32 : 16; // 256-bit or 128-bit entropy
  const entropy = randomBytes(entBytes);
  const cs = checksumBits(entropy);

  const bits = concatBits(bytesToBitString(entropy), cs);
  // Split into 11-bit indices
  const out: string[] = [];
  for (let i = 0; i < bits.length; i += 11) {
    const slice = bits.slice(i, i + 11);
    const idx = parseInt(slice, 2);
    out.push(indexToWord(idx));
  }
  return out.join(' ');
}

/** Validate a mnemonic against our checksum & dictionary. */
export function validateMnemonic(mnemonic: string): boolean {
  try {
    // Will throw if invalid
    void entropyFromMnemonic(mnemonic);
    return true;
  } catch {
    return false;
  }
}

/**
 * Convert mnemonic to a 32-byte master seed using:
 *   seed0 = PBKDF2-HMAC-SHA3-512(password=mnemonic_nfkd, salt="mnemonic"+passphrase_nfkd, iters=2048, dkLen=64)
 *   seed  = HKDF-SHA3-256(ikm=seed0, salt="animica/v1/seed", info="mnemonic-seed", L=32)
 */
export async function mnemonicToSeed(mnemonic: string, passphrase = ''): Promise<Uint8Array> {
  const m = nfkd(mnemonic.trim().replace(/\s+/g, ' '));
  const saltStr = 'mnemonic' + nfkd(passphrase);
  const seed0 = await pbkdf2Sha3_512(te.encode(m), te.encode(saltStr), 2048, 64);
  const seed = await hkdfSha3_256({
    ikm: seed0,
    salt: te.encode('animica/v1/seed'),
    info: te.encode('mnemonic-seed'),
    length: 32,
  });
  // Zeroize seed0 buffer
  seed0.fill(0);
  return seed;
}

// --------------------------- Entropy decode (for validation) ---------------------------

/**
 * Decode mnemonic back to raw entropy (and verify checksum).
 * Returns raw entropy bytes (without checksum). Throws on error.
 */
export function entropyFromMnemonic(mnemonic: string): Uint8Array {
  const words = mnemonic.trim().toLowerCase().split(/\s+/g);
  if (words.length !== 12 && words.length !== 24) {
    throw new Error('Mnemonic must be 12 or 24 words.');
  }
  const dict = ensureWordMap();

  const indices = words.map(w => {
    const idx = dict.get(w);
    if (idx == null) throw new Error(`Unknown mnemonic word: ${w}`);
    return idx;
  });

  // Join indices into bitstring
  let bits = '';
  for (const idx of indices) {
    bits += idx.toString(2).padStart(11, '0');
  }

  // ENT and CS lengths (BIP-39 rule)
  const totalBits = bits.length;                    // 132 for 12 words; 264 for 24 words
  const entLen = (totalBits / 33) * 32;             // 128 or 256
  const csLen = totalBits - entLen;                 // 4 or 8

  const entBits = bits.slice(0, entLen);
  const csBits = bits.slice(entLen);

  const entropy = bitStringToBytes(entBits);
  const expectedCs = checksumBits(entropy);
  if (expectedCs.slice(0, csLen) !== csBits) {
    throw new Error('Invalid mnemonic checksum.');
  }
  return entropy;
}

// --------------------------- Small helpers (exports) ---------------------------

export function mnemonicWordlistSize(): number {
  return 2048;
}
export function mnemonicWordAt(index: number): string {
  return indexToWord(index);
}
export function mnemonicIndexOf(word: string): number | undefined {
  return ensureWordMap().get(word);
}
