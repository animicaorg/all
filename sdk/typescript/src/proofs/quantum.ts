/**
 * QuantumProof helpers — assemble lightweight proof references from circuit & results.
 *
 * This client-side module helps you:
 *  1) Digest a quantum circuit specification and its measured results
 *  2) Package a QuantumProof "reference" object that node verifiers can bind to
 *     provider attestations, trap-circuit outcomes, and QoS metrics
 *  3) Produce/consume a minimal canonical CBOR envelope for transport
 *
 * NOTE:
 *  - Consensus-grade verification (provider attestations, trap checks, QoS/SLA)
 *    happens in the node. This module only handles canonicalization and hashing.
 */

import { sha3_256 } from '../utils/hash'
import { bytesToHex, hexToBytes, isHex } from '../utils/bytes'
import { encodeCanonicalCBOR, decodeCBOR } from '../utils/cbor'

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/** Provider attestation bundle (e.g., QPU identity, environment), opaque here. */
export type QuantumAttestation = Record<string, unknown>

/** Trap-circuit metadata for redundancy/accuracy checks. */
export interface QuantumTrapInfo {
  /** Deterministic seed describing trap selection (0x-hex). */
  trapSeed?: string
  /** Fraction of traps answered correctly, in [0..1]. */
  trapsRatio?: number
  /** Digest (0x-hex) of a separate receipts set, if used. */
  receiptsDigest?: string
}

/** Optional QoS metrics. */
export interface QuantumQoS {
  latencyMs?: number
  availability?: number   // [0..1]
  successRate?: number    // [0..1]
}

/** Canonical proof body verifiers expect. */
export interface QuantumProofBody {
  circuitDigest: string   // 0x-hex sha3_256(canonical circuit)
  resultDigest: string    // 0x-hex sha3_256(canonical results)
  shots?: number          // number of shots used (if applicable)
  device?: string         // logical device/model id
  providerId?: string
  jobId?: string
  attestation: QuantumAttestation
  traps?: QuantumTrapInfo
  qos?: QuantumQoS
  // Future fields allowed:
  [k: string]: unknown
}

/** On-wire reference envelope. */
export interface QuantumProofRef {
  typeId: 'QuantumProof' | number
  body: QuantumProofBody
  /** Deterministic nullifier to prevent duplicate submissions. */
  nullifier: string        // 0x-hex
}

/** Inputs for building a proof reference. */
export interface BuildQuantumProofInput {
  circuit: Uint8Array | string | Record<string, unknown> | unknown[]
  result: Uint8Array | string | Record<string, unknown> | unknown[]
  shots?: number
  device?: string
  providerId?: string
  jobId?: string
  attestation: QuantumAttestation
  traps?: QuantumTrapInfo
  qos?: QuantumQoS
}

/** Shallow validation result. */
export interface ValidateResult {
  ok: boolean
  errors: string[]
  warnings: string[]
}

// ──────────────────────────────────────────────────────────────────────────────
// Public API
// ──────────────────────────────────────────────────────────────────────────────

/** Digest a quantum circuit specification (object/bytes/string → sha3_256). */
export function digestCircuit(circuit: BuildQuantumProofInput['circuit']): string {
  const bytes = toBytesCanonical(circuit)
  return '0x' + bytesToHex(sha3_256(bytes))
}

/** Digest quantum results (counts/bitstrings/bytes → sha3_256). */
export function digestResult(result: BuildQuantumProofInput['result']): string {
  const bytes = toBytesCanonical(result)
  return '0x' + bytesToHex(sha3_256(bytes))
}

/**
 * Build a QuantumProofRef from circuit/result and metadata.
 * Nullifier binds (providerId?|jobId?|device?|circuitDigest|resultDigest).
 */
export function buildQuantumProofRef(input: BuildQuantumProofInput): QuantumProofRef {
  const circuitDigest = digestCircuit(input.circuit)
  const resultDigest = digestResult(input.result)

  const body: QuantumProofBody = {
    circuitDigest,
    resultDigest,
    shots: input.shots,
    device: input.device,
    providerId: input.providerId,
    jobId: input.jobId,
    attestation: input.attestation,
    traps: normalizeTraps(input.traps),
    qos: normalizeQoS(input.qos)
  }
  const nullifier = computeNullifier(body)
  return { typeId: 'QuantumProof', body, nullifier }
}

/** Deterministic nullifier = H("quantum.nullifier.v1"|providerId?|jobId?|device?|circuitDigest|resultDigest). */
export function computeNullifier(body: Pick<QuantumProofBody, 'providerId' | 'jobId' | 'device' | 'circuitDigest' | 'resultDigest'>): string {
  const dom = text('quantum.nullifier.v1')
  const parts: Uint8Array[] = [dom]
  if (body.providerId) parts.push(text(body.providerId))
  if (body.jobId) parts.push(text(body.jobId))
  if (body.device) parts.push(text(body.device))
  parts.push(hexToBytes(normalizeHex(body.circuitDigest)))
  parts.push(hexToBytes(normalizeHex(body.resultDigest)))
  return '0x' + bytesToHex(sha3_256(concat(...parts)))
}

/** Client-side format/range validation. */
export function validateQuantumProofBody(body: QuantumProofBody): ValidateResult {
  const errors: string[] = []
  const warnings: string[] = []

  try { normalizeHex(body.circuitDigest) } catch { errors.push('circuitDigest must be 0x-hex sha3_256') }
  try { normalizeHex(body.resultDigest) } catch { errors.push('resultDigest must be 0x-hex sha3_256') }

  if (body.shots != null) {
    if (!Number.isFinite(body.shots) || body.shots <= 0) errors.push('shots must be a positive number')
  }
  if (!body.attestation || typeof body.attestation !== 'object') errors.push('attestation missing/invalid')

  if (body.traps?.trapsRatio != null) {
    if (!(body.traps.trapsRatio >= 0 && body.traps.trapsRatio <= 1)) errors.push('traps.trapsRatio must be in [0,1]')
  }
  if (body.qos?.availability != null && !(body.qos.availability >= 0 && body.qos.availability <= 1)) {
    errors.push('qos.availability must be in [0,1]')
  }
  if (body.qos?.successRate != null && !(body.qos.successRate >= 0 && body.qos.successRate <= 1)) {
    errors.push('qos.successRate must be in [0,1]')
  }

  return { ok: errors.length === 0, errors, warnings }
}

/** Encode a QuantumProofRef into canonical CBOR. */
export function toEnvelopeCBOR(ref: QuantumProofRef): Uint8Array {
  const env: QuantumProofRef = {
    typeId: typeof ref.typeId === 'number' ? ref.typeId : 'QuantumProof',
    body: {
      ...ref.body,
      circuitDigest: normalizeHex(ref.body.circuitDigest),
      resultDigest: normalizeHex(ref.body.resultDigest),
      traps: normalizeTraps(ref.body.traps),
      qos: normalizeQoS(ref.body.qos)
    },
    nullifier: normalizeHex(ref.nullifier)
  }
  return encodeCanonicalCBOR(env)
}

/** Decode a canonical CBOR envelope into a normalized QuantumProofRef. */
export function fromEnvelopeCBOR(data: Uint8Array): QuantumProofRef {
  const env = decodeCBOR(data) as QuantumProofRef
  if (!env || typeof env !== 'object') throw new Error('invalid envelope')
  if (!('typeId' in env) || !('body' in env) || !('nullifier' in env)) throw new Error('missing envelope fields')
  const out: QuantumProofRef = {
    typeId: (env.typeId === 'QuantumProof' || typeof env.typeId === 'number') ? env.typeId : 'QuantumProof',
    body: {
      ...env.body,
      circuitDigest: normalizeHex(env.body.circuitDigest),
      resultDigest: normalizeHex(env.body.resultDigest),
      traps: normalizeTraps(env.body.traps),
      qos: normalizeQoS(env.body.qos)
    },
    nullifier: normalizeHex(env.nullifier)
  }
  return out
}

// ──────────────────────────────────────────────────────────────────────────────
/* Internals */
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
function normalizeTraps(t?: QuantumTrapInfo): QuantumTrapInfo | undefined {
  if (!t) return undefined
  const traps: QuantumTrapInfo = { ...t }
  if (traps.trapSeed != null) traps.trapSeed = normalizeHex(traps.trapSeed)
  if (traps.receiptsDigest != null) traps.receiptsDigest = normalizeHex(traps.receiptsDigest)
  return traps
}

/** Normalize QoS fields to numbers (drop NaN/undefined). */
function normalizeQoS(q?: QuantumQoS): QuantumQoS | undefined {
  if (!q) return undefined
  const out: QuantumQoS = {}
  if (q.latencyMs != null) out.latencyMs = Number(q.latencyMs)
  if (q.availability != null) out.availability = Number(q.availability)
  if (q.successRate != null) out.successRate = Number(q.successRate)
  return out
}

/** Deterministic bytes for arbitrary inputs (Uint8Array | hex | utf8 | object). */
function toBytesCanonical(x: Uint8Array | string | Record<string, unknown> | unknown[]): Uint8Array {
  if (x instanceof Uint8Array) return x
  if (typeof x === 'string') {
    if (isHex(x)) return hexToBytes(x)
    return new TextEncoder().encode(x)
  }
  return encodeCanonicalCBOR(x as any)
}

// ──────────────────────────────────────────────────────────────────────────────
// Default export
// ──────────────────────────────────────────────────────────────────────────────

export default {
  digestCircuit,
  digestResult,
  buildQuantumProofRef,
  computeNullifier,
  validateQuantumProofBody,
  toEnvelopeCBOR,
  fromEnvelopeCBOR
}
