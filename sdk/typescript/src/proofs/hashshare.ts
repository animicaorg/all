/**
 * HashShare proof (dev tools) — local u-draw & threshold checks.
 *
 * This module helps build and verify HashShare-style proofs client-side for
 * testing and developer tooling. It does NOT perform consensus acceptance; it
 * only reproduces the uniform draw u from (headerHash, nonce[, mixSeed]) and
 * computes S = -ln(u). You can compare S against a micro-nats threshold Θ_µ.
 *
 * Hash domains (by convention here):
 *   - draw domain:   "hashshare.u.v1"
 *   - nullifier dom: "hashshare.nullifier.v1"
 */

import { sha3_256, keccak256 } from '../utils/hash'
import { bytesToHex, hexToBytes, isHex } from '../utils/bytes'
import { encodeCanonicalCBOR, decodeCBOR } from '../utils/cbor'

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

export interface HashShareBody {
  headerHash: string   // 0x… (32 bytes)
  nonce: string        // 0x… (arbitrary length; commonly 8 or 32 bytes)
  mixSeed?: string     // 0x… (optional extra bind)
}

export interface HashShareProof extends HashShareBody {
  /** Deterministic u in (0,1], derived from headerHash/nonce/mixSeed */
  u: number
  /** Score S(u) = -ln(u) in natural units (nats) */
  S: number
  /** Deterministic nullifier for reuse-prevention (dev parity with node) */
  nullifier: string    // 0x…
}

export interface VerifyOptions {
  /** Difficulty threshold in micro-nats; if set, we check S >= Θ (converted to nats). */
  thetaMicro?: number
  /** Choose hash for the draw (defaults to sha3_256). */
  hashFn?: 'sha3_256' | 'keccak256'
}

export interface VerifyResult {
  ok: boolean
  /** Recomputed u (ignores proof.u if present) */
  u: number
  /** Recomputed S in nats */
  S: number
  /** d_ratio = S / Θ (if Θ provided), else null */
  ratio: number | null
  /** True if threshold provided and S >= Θ; else null if no Θ provided */
  meets: boolean | null
  /** Recomputed nullifier */
  nullifier: string
  errors: string[]
}

// Optional envelope helpers (useful when mirroring on-chain formats)
export interface ProofEnvelope {
  typeId: 'HashShare' | number
  body: HashShareBody
  nullifier: string
}

// ──────────────────────────────────────────────────────────────────────────────
// Public API
// ──────────────────────────────────────────────────────────────────────────────

/** Build a local HashShare proof object for tooling/tests. */
export function buildHashShare(body: HashShareBody, opts: VerifyOptions = {}): HashShareProof {
  const headerHash = normalizeHex(body.headerHash)
  const nonce = normalizeHex(body.nonce)
  const mixSeed = body.mixSeed ? normalizeHex(body.mixSeed) : undefined

  const u = drawU(headerHash, nonce, mixSeed, opts.hashFn)
  const S = score(u)
  const nullifier = computeNullifier({ headerHash, nonce, mixSeed })
  return { headerHash, nonce, mixSeed, u, S, nullifier }
}

/** Verify a HashShare proof against optional threshold Θ_µ. */
export function verifyHashShare(bodyOrProof: HashShareBody | HashShareProof, opts: VerifyOptions = {}): VerifyResult {
  const headerHash = normalizeHex(bodyOrProof.headerHash)
  const nonce = normalizeHex(bodyOrProof.nonce)
  const mixSeed = bodyOrProof.mixSeed ? normalizeHex(bodyOrProof.mixSeed) : undefined

  const u = drawU(headerHash, nonce, mixSeed, opts.hashFn)
  const S = score(u)
  const nullifier = computeNullifier({ headerHash, nonce, mixSeed })

  const errors: string[] = []
  // If caller passed a HashShareProof with u/S fields, ensure they match recomputed
  if ('u' in bodyOrProof && typeof bodyOrProof.u === 'number') {
    if (!approxEqual(bodyOrProof.u, u)) errors.push('u mismatch vs recomputed')
  }
  if ('S' in bodyOrProof && typeof bodyOrProof.S === 'number') {
    if (!approxEqual(bodyOrProof.S, S)) errors.push('S mismatch vs recomputed')
  }
  if ('nullifier' in bodyOrProof && typeof (bodyOrProof as any).nullifier === 'string') {
    const given = normalizeHex((bodyOrProof as any).nullifier)
    if (given !== nullifier) errors.push('nullifier mismatch vs recomputed')
  }

  let ratio: number | null = null
  let meets: boolean | null = null
  if (opts.thetaMicro != null) {
    const thetaNats = opts.thetaMicro / 1_000_000
    ratio = thetaNats > 0 ? S / thetaNats : Infinity
    meets = S >= thetaNats
  }

  const ok = errors.length === 0 && (meets !== false)
  return { ok, u, S, ratio, meets, nullifier, errors }
}

/** Convert to a minimal CBOR envelope (typeId + body + nullifier). */
export function toEnvelopeCBOR(p: HashShareProof | HashShareBody): Uint8Array {
  const env: ProofEnvelope = {
    typeId: 'HashShare',
    body: {
      headerHash: normalizeHex(p.headerHash),
      nonce: normalizeHex(p.nonce),
      ...(p.mixSeed ? { mixSeed: normalizeHex(p.mixSeed) } : {})
    },
    nullifier: computeNullifier(p)
  }
  return encodeCanonicalCBOR(env)
}

/** Decode a CBOR envelope previously created by `toEnvelopeCBOR`. */
export function fromEnvelopeCBOR(data: Uint8Array): ProofEnvelope {
  const env = decodeCBOR(data) as ProofEnvelope
  if (!env || typeof env !== 'object') throw new Error('invalid envelope')
  if (!('typeId' in env) || !('body' in env) || !('nullifier' in env)) throw new Error('missing envelope fields')
  env.nullifier = normalizeHex(env.nullifier)
  env.body = {
    headerHash: normalizeHex(env.body.headerHash),
    nonce: normalizeHex(env.body.nonce),
    ...(env.body.mixSeed ? { mixSeed: normalizeHex(env.body.mixSeed) } : {})
  }
  return env
}

// ──────────────────────────────────────────────────────────────────────────────
// Core math & hashing
// ──────────────────────────────────────────────────────────────────────────────

/**
 * Deterministic draw u ∈ (0, 1] from (headerHash, nonce[, mixSeed]).
 * digest = H( "hashshare.u.v1" | headerHash | nonce | mixSeed? )
 * u = (int(digest) + 1) / 2^256   (avoid 0; yields (0,1])
 */
export function drawU(
  headerHash: string,
  nonce: string,
  mixSeed?: string,
  hashFn: VerifyOptions['hashFn'] = 'sha3_256'
): number {
  const dom = text('hashshare.u.v1')
  const hh = hexToBytes(normalizeHex(headerHash))
  const nn = hexToBytes(normalizeHex(nonce))
  const ms = mixSeed ? hexToBytes(normalizeHex(mixSeed)) : new Uint8Array(0)

  const data = concat(dom, hh, nn, ms)
  const digest = hashFn === 'keccak256' ? keccak256(data) : sha3_256(data)

  // Convert 32-byte digest to BigInt (big-endian)
  let acc = 0n
  for (const b of digest) acc = (acc << 8n) | BigInt(b)
  const two256 = 1n << 256n
  const num = acc + 1n
  const u = Number(num) / Number(two256)  // safe enough for uniformity use; we don't need exact 256-bit ratio
  // Clamp to (0,1] against float rounding
  return u > 1 ? 1 : (u <= 0 ? Number.MIN_VALUE : u)
}

/** Score function S(u) = -ln(u) (natural log, in nats). */
export function score(u: number): number {
  if (!(u > 0 && u <= 1)) throw new Error('u must be in (0,1]')
  return -Math.log(u)
}

/**
 * Deterministic nullifier = H("hashshare.nullifier.v1" | headerHash | nonce | mixSeed?)
 * Useful to prevent duplicate submissions of the same HashShare for a header.
 */
export function computeNullifier(p: HashShareBody): string {
  const dom = text('hashshare.nullifier.v1')
  const hh = hexToBytes(normalizeHex(p.headerHash))
  const nn = hexToBytes(normalizeHex(p.nonce))
  const ms = p.mixSeed ? hexToBytes(normalizeHex(p.mixSeed)) : new Uint8Array(0)
  const dig = sha3_256(concat(dom, hh, nn, ms))
  return '0x' + bytesToHex(dig)
}

// ──────────────────────────────────────────────────────────────────────────────
// Utilities
// ──────────────────────────────────────────────────────────────────────────────

function normalizeHex(h: string): string {
  if (typeof h !== 'string') throw new Error('expected hex string')
  const s = h.startsWith('0x') || h.startsWith('0X') ? h : ('0x' + h)
  if (!isHex(s)) throw new Error(`invalid hex: ${h}`)
  return s.toLowerCase()
}

function text(s: string): Uint8Array {
  return new TextEncoder().encode(s)
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const n = parts.reduce((a, p) => a + p.length, 0)
  const out = new Uint8Array(n)
  let off = 0
  for (const p of parts) { out.set(p, off); off += p.length }
  return out
}

function approxEqual(a: number, b: number, eps = 1e-12): boolean {
  return Math.abs(a - b) <= eps * Math.max(1, Math.abs(a), Math.abs(b))
}

// ──────────────────────────────────────────────────────────────────────────────
// Default export
// ──────────────────────────────────────────────────────────────────────────────

export default {
  buildHashShare,
  verifyHashShare,
  drawU,
  score,
  computeNullifier,
  toEnvelopeCBOR,
  fromEnvelopeCBOR
}
