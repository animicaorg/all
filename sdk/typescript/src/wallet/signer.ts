/**
 * Post-quantum signers (Dilithium3, SPHINCS+ SHAKE-128s) with optional WASM backends.
 *
 * This module is intentionally backend-agnostic. In browsers or Node, you can:
 *  - Pass an explicit backend implementation (recommended for tight control)
 *  - Or rely on dynamic import of an optional peer '@animica/pq-wasm' (feature-gated)
 *
 * Security notes:
 *  - Seeds should be high-entropy (≥ 32 bytes). We internally derive an
 *    algorithm-specific sub-seed with HKDF(SHA3-256) and domain separation.
 *  - The secret key is kept in a closure and not exposed; a `.destroy()` method
 *    is provided to wipe in-memory buffers where feasible.
 *  - Domain separation: you can pass a `domain` to sign(); backends receive it
 *    as a context parameter (not concatenated to the message).
 */

import { hkdf } from '@noble/hashes/hkdf'
import { sha3_256 } from '@noble/hashes/sha3'
import { utf8ToBytes } from '../utils/bytes'

// ──────────────────────────────────────────────────────────────────────────────
// Types & public API
// ──────────────────────────────────────────────────────────────────────────────

export type AlgorithmId = 'dilithium3' | 'sphincs_shake_128s'

export interface SignRequest {
  message: Uint8Array
  /** Optional domain/context for domain-separated signatures. */
  domain?: Uint8Array | string
}

export interface SignResult {
  alg: AlgorithmId
  publicKey: Uint8Array
  signature: Uint8Array
}

/** Minimal signer interface returned by factory helpers. */
export interface Signer {
  readonly alg: AlgorithmId
  /** Raw public key bytes for this algorithm. */
  getPublicKey(): Promise<Uint8Array>
  /**
   * Sign a message; domain is passed to the backend as context.
   * Returns the raw signature bytes.
   */
  sign(message: Uint8Array, domain?: Uint8Array | string): Promise<Uint8Array>
  /** Optional local verify helper (delegates to backend if present). */
  verify?(message: Uint8Array, signature: Uint8Array, domain?: Uint8Array | string): Promise<boolean>
  /** Best-effort wipe of secret material. After calling, the signer becomes unusable. */
  destroy(): void
}

// Backend contracts (implemented by WASM/native bindings)
export interface DilithiumBackend {
  name: string
  keypairFromSeed(seed: Uint8Array): Promise<{ publicKey: Uint8Array; secretKey: Uint8Array }>
  sign(secretKey: Uint8Array, msg: Uint8Array, ctx?: Uint8Array): Promise<Uint8Array>
  verify?(publicKey: Uint8Array, msg: Uint8Array, sig: Uint8Array, ctx?: Uint8Array): Promise<boolean>
}

export interface SphincsBackend {
  name: string
  keypairFromSeed(seed: Uint8Array): Promise<{ publicKey: Uint8Array; secretKey: Uint8Array }>
  sign(secretKey: Uint8Array, msg: Uint8Array, ctx?: Uint8Array): Promise<Uint8Array>
  verify?(publicKey: Uint8Array, msg: Uint8Array, sig: Uint8Array, ctx?: Uint8Array): Promise<boolean>
}

export interface SignerOptions<B> {
  /**
   * HKDF info string for key derivation domain separation.
   * Defaults:
   *  - Dilithium: 'animica pq signer dilithium3 v1'
   *  - SPHINCS+: 'animica pq signer sphincs_shake_128s v1'
   */
  hkdfInfo?: string
  /** Optional backend implementation. If omitted, we attempt a dynamic import. */
  backend?: B
  /**
   * If true, we will NOT attempt dynamic import fallback when backend is absent.
   * Default: false (i.e., try to auto-load a WASM backend if available).
   */
  disableAutoWasm?: boolean
}

// Factory functions
export async function createDilithiumSigner(
  seed: Uint8Array,
  opts: SignerOptions<DilithiumBackend> = {}
): Promise<Signer> {
  assertBytes('seed', seed)
  const info = utf8ToBytes(opts.hkdfInfo ?? 'animica pq signer dilithium3 v1')
  const subSeed = hkdf(sha3_256, seed, /*salt*/ undefined, info, 32)

  const backend = opts.backend ?? (await maybeLoadBackend('dilithium'))
  if (!backend) throw new Error('Dilithium backend not available. Provide { backend } or install @animica/pq-wasm.')

  const { publicKey, secretKey } = await backend.keypairFromSeed(subSeed)
  wipe(subSeed)

  let destroyed = false

  return {
    alg: 'dilithium3',
    async getPublicKey() {
      if (destroyed) throw new Error('signer destroyed')
      return publicKey.slice()
    },
    async sign(message: Uint8Array, domain?: Uint8Array | string): Promise<Uint8Array> {
      if (destroyed) throw new Error('signer destroyed')
      assertBytes('message', message)
      const ctx = toContext(domain)
      return backend.sign(secretKey, message, ctx)
    },
    verify: backend.verify
      ? async (message: Uint8Array, signature: Uint8Array, domain?: Uint8Array | string) => {
          if (destroyed) throw new Error('signer destroyed')
          assertBytes('message', message)
          assertBytes('signature', signature)
          const ctx = toContext(domain)
          return backend.verify!(publicKey, message, signature, ctx)
        }
      : undefined,
    destroy() {
      destroyed = true
      wipe(secretKey)
      // public key isn't secret, but keep memory tidy for symmetry
      wipe(publicKey)
    }
  }
}

export async function createSphincsSigner(
  seed: Uint8Array,
  opts: SignerOptions<SphincsBackend> = {}
): Promise<Signer> {
  assertBytes('seed', seed)
  const info = utf8ToBytes(opts.hkdfInfo ?? 'animica pq signer sphincs_shake_128s v1')
  const subSeed = hkdf(sha3_256, seed, /*salt*/ undefined, info, 32)

  const backend = opts.backend ?? (await maybeLoadBackend('sphincs'))
  if (!backend) throw new Error('SPHINCS+ backend not available. Provide { backend } or install @animica/pq-wasm.')

  const { publicKey, secretKey } = await backend.keypairFromSeed(subSeed)
  wipe(subSeed)

  let destroyed = false

  return {
    alg: 'sphincs_shake_128s',
    async getPublicKey() {
      if (destroyed) throw new Error('signer destroyed')
      return publicKey.slice()
    },
    async sign(message: Uint8Array, domain?: Uint8Array | string): Promise<Uint8Array> {
      if (destroyed) throw new Error('signer destroyed')
      assertBytes('message', message)
      const ctx = toContext(domain)
      return backend.sign(secretKey, message, ctx)
    },
    verify: backend.verify
      ? async (message: Uint8Array, signature: Uint8Array, domain?: Uint8Array | string) => {
          if (destroyed) throw new Error('signer destroyed')
          assertBytes('message', message)
          assertBytes('signature', signature)
          const ctx = toContext(domain)
          return backend.verify!(publicKey, message, signature, ctx)
        }
      : undefined,
    destroy() {
      destroyed = true
      wipe(secretKey)
      wipe(publicKey)
    }
  }
}

// ──────────────────────────────────────────────────────────────────────────────
/** Try to dynamically import an optional WASM backend if present. */
async function maybeLoadBackend(kind: 'dilithium' | 'sphincs'): Promise<any | undefined> {
  // Users can provide their own backend; this is a convenience path.
  try {
    // The optional package should export { dilithium, sphincs } backends
    // matching the interfaces defined above.
    // We intentionally avoid static ESM import to keep it optional.
    // eslint-disable-next-line @typescript-eslint/ban-ts-comment
    // @ts-ignore
    const mod = await import('@animica/pq-wasm')
    if (!mod) return undefined
    if (kind === 'dilithium' && mod.dilithium) return mod.dilithium as DilithiumBackend
    if (kind === 'sphincs' && mod.sphincs) return mod.sphincs as SphincsBackend
    return undefined
  } catch {
    return undefined
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Utilities
// ──────────────────────────────────────────────────────────────────────────────

function toContext(domain?: Uint8Array | string): Uint8Array | undefined {
  if (domain === undefined) return utf8ToBytes('animica:sign:v1')
  if (typeof domain === 'string') return utf8ToBytes(domain)
  if (domain instanceof Uint8Array) return domain
  throw new Error('domain must be Uint8Array or string')
}

function assertBytes(name: string, x: unknown): asserts x is Uint8Array {
  if (!(x instanceof Uint8Array)) throw new Error(`${name} must be Uint8Array`)
}

function wipe(buf: Uint8Array | undefined | null) {
  if (!buf) return
  try { buf.fill(0) } catch { /* ignore */ }
}

export default {
  createDilithiumSigner,
  createSphincsSigner
}
