/**
 * RNG utilities for MV3 environments.
 *
 * - Default: cryptographically secure bytes from WebCrypto (getRandomValues).
 * - Tests: optional deterministic stream via HMAC(SHA3-256, seed || counter).
 *
 * IMPORTANT: The deterministic path is **only** for tests. Do not enable it in production.
 */

import { hmac } from '@noble/hashes/hmac';
import { sha3_256 } from '@noble/hashes/sha3';

/** Ensure we have WebCrypto in MV3/worker contexts. */
function getCrypto(): Crypto {
  const c = globalThis.crypto as Crypto | undefined;
  if (!c || typeof c.getRandomValues !== 'function') {
    throw new Error('WebCrypto unavailable: crypto.getRandomValues not found');
  }
  return c;
}

/** Internal deterministic state for tests (disabled by default). */
let _testSeed: Uint8Array | null = null;
let _counter = 0;

/** Enable deterministic bytes for tests. Not for production use. */
export function setTestSeed(seed: Uint8Array | number[] | ArrayBuffer): void {
  if (seed instanceof ArrayBuffer) _testSeed = new Uint8Array(seed);
  else if (Array.isArray(seed)) _testSeed = new Uint8Array(seed);
  else _testSeed = new Uint8Array(seed);
  _counter = 0;
}

/** Disable deterministic test mode and return to WebCrypto RNG. */
export function clearTestSeed(): void {
  _testSeed = null;
  _counter = 0;
}

/** True if deterministic test RNG is active. */
export function isDeterministic(): boolean {
  return _testSeed !== null;
}

/**
 * Produce cryptographically secure random bytes (default).
 * If a test seed was set, produce deterministic bytes using HMAC-SHA3-256(seed, counter).
 */
export function randomBytes(length: number): Uint8Array {
  if (!Number.isFinite(length) || length <= 0) {
    throw new Error('randomBytes: length must be > 0');
  }

  // Deterministic test path
  if (_testSeed) {
    const out = new Uint8Array(length);
    let written = 0;
    while (written < length) {
      // HMAC_SHA3_256(seed, be32(counter))
      const cnt = new Uint8Array(4);
      cnt[0] = (_counter >>> 24) & 0xff;
      cnt[1] = (_counter >>> 16) & 0xff;
      cnt[2] = (_counter >>> 8) & 0xff;
      cnt[3] = _counter & 0xff;
      _counter = (_counter + 1) >>> 0;

      const mac = hmac.create(sha3_256, _testSeed);
      mac.update(cnt);
      const block = mac.digest();

      const take = Math.min(block.length, length - written);
      out.set(block.subarray(0, take), written);
      written += take;
    }
    return out;
  }

  // Secure path via WebCrypto
  const u8 = new Uint8Array(length);
  getCrypto().getRandomValues(u8);
  return u8;
}

/** Convenience: hex-encode random bytes (lowercase). */
export function randomHex(length: number): string {
  const bytes = randomBytes(length);
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += bytes[i].toString(16).padStart(2, '0');
  return s;
}

/** Convenience: random uint32 (uniform over 0..2^32-1). */
export function randomU32(): number {
  const b = randomBytes(4);
  // Little-endian assemble
  return (b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)) >>> 0;
}
