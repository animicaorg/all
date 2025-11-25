/**
 * AIProof helpers — assemble lightweight proof references from outputs.
 *
 * This module helps clients:
 *  1) Compute a canonical digest of an AI output (bytes / string / object)
 *  2) Package an AIProof "reference" object that nodes can verify against
 *     attestation bundles, trap receipts, and QoS metrics
 *  3) Produce/consume a minimal CBOR envelope for transport
 *
 * NOTE: This is a *client-side* helper. Consensus verification of the AI proof
 * (TEE attestation checks, traps, QoS) happens in node software. We keep types
 * broad to accommodate different providers and attestation formats.
 */

import { sha3_256 } from '../utils/hash'
import { bytesToHex, hexToBytes, isHex } from '../utils/bytes'
import { encodeCanonicalCBOR, decodeCBOR } from '../utils/cbor'

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/** Generic attestation bundle (e.g., SGX/SEV/CCA) — provider-defined structure. */
export type AIAttestation = Record<string, unknown>

/** Optional trap receipts info used by verifiers for redundancy/trust checks. */
export interface AITrapInfo {
  /** Optional deterministic seed describing trap set selection (0x-hex). */
  trapSeed?: string
  /** Ratio of trap inputs that were correctly answered by provider [0..1]. */
  trapsRatio?: number
  /** Digest (0x-hex) of trap receipt set, if provided separately. */
  receiptsDigest?: string
}

/** Optional QoS/run-time metrics to assist pricing/SLA logic. */
export interface AIQoS {
  latencyMs?: number           // end-to-end latency for the job
  availability?: number        // long-term availability ratio [0..1]
  successRate?: number         // success ratio [0..1]
}

/** Canonical AI proof body expected by the chain's verifier. */
export interface AIProofBody {
  model: string                // identifier (e.g., gpt-4o-mini-2025-05)
  outputDigest: string         // 0x-hex sha3_256 digest of output payload
  outputMime?: string          // e.g., application/json, text/plain
  providerId?: string          // logical provider id (optional)
  jobId?: string               // queue/job id (optional but helps nullifier uniqueness)
  attestation: AIAttestation   // TEE/token bundle; opaque here
  traps?: AITrapInfo
  qos?: AIQoS
  // Future-extensible:
  [k: string]: unknown
}

/** Complete reference envelope used off-chain/on-wire. */
export interface AIProofRef {
  typeId: 'AIProof' | number
  body: AIProofBody
  /** Deterministic nullifier to prevent duplicated submissions. */
  nullifier: string            // 0x-hex
}

/** Builder inputs for creating an AIProofRef. */
export interface BuildAIProofInput {
  model: string
  /** Output payload (bytes | 0x-hex | UTF-8 string | JSON-like object). */
  output: Uint8Array | string | Record<string, unknown> | unknown[]
  outputMime?: string
  providerId?: string
  jobId?: string
  attestation: AIAttestation
  traps?: AITrapInfo
  qos?: AIQoS
}

/** Validation result (client-side sanity only). */
export interface ValidateResult {
  ok: boolean
  errors: string[]
  warnings: string[]
}

// ──────────────────────────────────────────────────────────────────────────────
// Public API
// ──────────────────────────────────────────────────────────────────────────────

/**
 * Compute a canonical digest for an AI output.
 *  - Uint8Array → bytes hashed as-is
 *  - 0x-hex string → parsed, hashed as bytes
 *  - other string → UTF-8 encoded then hashed
 *  - object/array → deterministically CBOR-encoded then hashed
 */
export function digestOutput(payload: BuildAIProofInput['output']): string {
  const bytes = toBytesCanonical(payload)
  return '0x' + bytesToHex(sha3_256(bytes))
}

/**
 * Build a minimal AIProofRef from the provided output + metadata.
 * The nullifier binds (providerId|jobId|model|outputDigest) under a domain tag.
 */
export function buildAIProofRef(input: BuildAIProofInput): AIProofRef {
  const outputDigest = digestOutput(input.output)
  const body: AIProofBody = {
    model: input.model,
    outputDigest,
    outputMime: input.outputMime,
    providerId: input.providerId,
    jobId: input.jobId,
    attestation: input.attestation,
    traps: normalizeTraps(input.traps),
    qos: normalizeQoS(input.qos)
  }
  const nullifier = computeNullifier(body)
  return { typeId: 'AIProof', body, nullifier }
}

/** Compute deterministic nullifier = H("ai.nullifier.v1"|providerId?|jobId?|model|outputDigest). */
export function computeNullifier(body: Pick<AIProofBody, 'providerId' | 'jobId' | 'model' | 'outputDigest'>): string {
  const dom = text('ai.nullifier.v1')

  const parts: Uint8Array[] = [dom]
  if (body.providerId) parts.push(text(body.providerId))
  if (body.jobId) parts.push(text(body.jobId))
  parts.push(text(body.model))
  parts.push(hexToBytes(normalizeHex(body.outputDigest)))

  return '0x' + bytesToHex(sha3_256(concat(...parts)))
}

/** Shallow client-side validation (format/ranges only). */
export function validateAIProofBody(body: AIProofBody): ValidateResult {
  const errors: string[] = []
  const warnings: string[] = []

  if (!body.model || typeof body.model !== 'string') errors.push('model missing/invalid')
  try { normalizeHex(body.outputDigest) } catch { errors.push('outputDigest must be 0x-hex sha3_256') }
  if (body.traps?.trapsRatio != null) {
    if (!(body.traps.trapsRatio >= 0 && body.traps.trapsRatio <= 1)) {
      errors.push('traps.trapsRatio must be in [0,1]')
    }
  }
  if (body.qos?.availability != null && !(body.qos.availability >= 0 && body.qos.availability <= 1)) {
    errors.push('qos.availability must be in [0,1]')
  }
  if (body.qos?.successRate != null && !(body.qos.successRate >= 0 && body.qos.successRate <= 1)) {
    errors.push('qos.successRate must be in [0,1]')
  }
  if (!body.attestation || typeof body.attestation !== 'object') {
    errors.push('attestation missing/invalid')
  }

  return { ok: errors.length === 0, errors, warnings }
}

/** Encode an AIProofRef into a minimal canonical CBOR envelope. */
export function toEnvelopeCBOR(ref: AIProofRef): Uint8Array {
  const env: AIProofRef = {
    typeId: typeof ref.typeId === 'number' ? ref.typeId : 'AIProof',
    body: {
      ...ref.body,
      outputDigest: normalizeHex(ref.body.outputDigest),
      traps: normalizeTraps(ref.body.traps),
      qos: normalizeQoS(ref.body.qos)
    },
    nullifier: normalizeHex(ref.nullifier)
  }
  return encodeCanonicalCBOR(env)
}

/** Decode a CBOR envelope back into a normalized AIProofRef. */
export function fromEnvelopeCBOR(data: Uint8Array): AIProofRef {
  const env = decodeCBOR(data) as AIProofRef
  if (!env || typeof env !== 'object') throw new Error('invalid envelope')
  if (!('typeId' in env) || !('body' in env) || !('nullifier' in env)) throw new Error('missing envelope fields')
  const out: AIProofRef = {
    typeId: (env.typeId === 'AIProof' || typeof env.typeId === 'number') ? env.typeId : 'AIProof',
    body: {
      ...env.body,
      outputDigest: normalizeHex(env.body.outputDigest),
      traps: normalizeTraps(env.body.traps),
      qos: normalizeQoS(env.body.qos)
    },
    nullifier: normalizeHex(env.nullifier)
  }
  return out
}

// ──────────────────────────────────────────────────────────────────────────────
// Internal helpers
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
  if (parts.length === 0) return new Uint8Array(0)
  const n = parts.reduce((a, p) => a + p.length, 0)
  const out = new Uint8Array(n)
  let off = 0
  for (const p of parts) { out.set(p, off); off += p.length }
  return out
}

/** Normalize traps object & basic hex checks. */
function normalizeTraps(t?: AITrapInfo): AITrapInfo | undefined {
  if (!t) return undefined
  const traps: AITrapInfo = { ...t }
  if (traps.trapSeed != null) traps.trapSeed = normalizeHex(traps.trapSeed)
  if (traps.receiptsDigest != null) traps.receiptsDigest = normalizeHex(traps.receiptsDigest)
  return traps
}

/** Normalize QoS fields to numbers (drop NaN/undefined). */
function normalizeQoS(q?: AIQoS): AIQoS | undefined {
  if (!q) return undefined
  const out: AIQoS = {}
  if (q.latencyMs != null) out.latencyMs = Number(q.latencyMs)
  if (q.availability != null) out.availability = Number(q.availability)
  if (q.successRate != null) out.successRate = Number(q.successRate)
  return out
}

/** Canonical byte representation for digesting arbitrary "output" values. */
function toBytesCanonical(x: BuildAIProofInput['output']): Uint8Array {
  if (x instanceof Uint8Array) return x
  if (typeof x === 'string') {
    if (isHex(x)) return hexToBytes(x)
    return new TextEncoder().encode(x)
  }
  // For objects/arrays: canonical CBOR so all languages match the digest
  return encodeCanonicalCBOR(x as any)
}

// ──────────────────────────────────────────────────────────────────────────────
// Default export
// ──────────────────────────────────────────────────────────────────────────────

export default {
  digestOutput,
  buildAIProofRef,
  computeNullifier,
  validateAIProofBody,
  toEnvelopeCBOR,
  fromEnvelopeCBOR
}
