/**
 * Light client verification helpers.
 *
 * Goals:
 *  - Hash a canonical header (CBOR) and perform basic invariants checks
 *  - Verify parent→child linkage (number increments, parentHash match)
 *  - Verify DA light-proofs against the header's daRoot
 *  - Provide a generic binary-Merkle proof verifier and allow custom verifiers
 *
 * Notes:
 *  - This module intentionally keeps consensus-specific rules minimal. It does
 *    not recompute PoW/PoIES acceptance or difficulty. It only checks static
 *    header fields, linkage, and (optionally) DA light proofs.
 *  - Header hashing uses deterministic CBOR encoding via utils/cbor.
 *  - DA proofs vary by deployment. We support a generic binary Merkle branch
 *    (SHA3-256) and allow injecting a custom verifier for NMT or other trees.
 */

import { encodeCanonicalCBOR } from '../utils/cbor'
import { sha3_256 } from '../utils/hash'
import { bytesToHex, hexToBytes, isHex } from '../utils/bytes'

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/** Minimal header view required for hashing & linkage checks. */
export interface LightHeader {
  number: number               // block height
  parentHash: string           // 0x…
  stateRoot: string            // 0x…
  txRoot: string               // 0x…
  receiptsRoot: string         // 0x…
  daRoot?: string              // 0x… (optional if chain has no DA)
  chainId: number
  timestamp?: number
  // policy roots (optional, checked if provided in VerifyHeaderOptions)
  poiesPolicyRoot?: string
  algPolicyRoot?: string
  // extra fields are allowed and preserved in hashing (CBOR canonical map)
  [k: string]: unknown
}

/** Basic verification result with optional warnings/errors. */
export interface VerifyResult {
  ok: boolean
  hash: string
  errors: string[]
  warnings: string[]
}

/** Options for header verification. */
export interface VerifyHeaderOptions {
  expectedChainId?: number
  expectedPoiesPolicyRoot?: string
  expectedAlgPolicyRoot?: string
  // When true, enforce that critical roots are present and 0x-hex
  strictRoots?: boolean
}

/** Parent→child linkage result */
export interface LinkResult {
  ok: boolean
  errors: string[]
}

/** Binary Merkle branch step */
export interface MerkleStep {
  /** Sibling hash (0x…) */
  sibling: string
  /** Position of the *current* hash relative to sibling at this step */
  position: 'left' | 'right'
}

/** Binary Merkle proof for a single leaf */
export interface BinaryProof {
  /** Pre-hashed leaf (0x…) OR raw bytes to be hashed. Provide exactly one. */
  leafHash?: string
  leafData?: string | Uint8Array
  /** Steps from leaf → root */
  path: MerkleStep[]
}

/** DA light proof envelope (generic) */
export interface DALightProof {
  /** Commitment or identifier of the blob/data referenced (0x…) */
  commitment?: string
  /** Expected root to match the header's daRoot (0x…) */
  root: string
  /** One or more binary proofs proving sampled leaves into the root */
  binary?: BinaryProof[]
  /** Optional custom verifier hook (e.g., NMT range proofs) */
  verifyWith?: (p: DALightProof, header: LightHeader) => boolean | Promise<boolean>
  /** Additional, chain-specific fields */
  [k: string]: unknown
}

// ──────────────────────────────────────────────────────────────────────────────
// Header hashing & checks
// ──────────────────────────────────────────────────────────────────────────────

/**
 * Compute the canonical header hash = sha3_256(CBOR(header_map)).
 * The map must contain the fields as provided; CBOR encoding is deterministic.
 */
export function hashHeader(header: LightHeader): string {
  // Defensive clone so we don't mutate caller's object when normalizing hex
  const h: Record<string, any> = { ...header }

  // Normalize critical roots to lowercase 0x-hex (if present)
  for (const k of ['parentHash', 'stateRoot', 'txRoot', 'receiptsRoot', 'daRoot', 'poiesPolicyRoot', 'algPolicyRoot']) {
    if (h[k] != null) h[k] = normalizeHex(h[k])
  }

  const cbor = encodeCanonicalCBOR(h)
  return '0x' + bytesToHex(sha3_256(cbor))
}

/**
 * Verify a single header's basic invariants.
 * Does not check PoW/PoIES acceptance—only static sanity and configured expectations.
 */
export function verifyHeader(header: LightHeader, opts: VerifyHeaderOptions = {}): VerifyResult {
  const errors: string[] = []
  const warnings: string[] = []

  // Field sanity
  if (!Number.isFinite(header.number) || header.number < 0) errors.push('invalid header.number')
  for (const k of ['parentHash', 'stateRoot', 'txRoot', 'receiptsRoot'] as const) {
    if (!isHex(header[k] as string)) errors.push(`invalid ${k} (expected 0x-hex)`)
  }
  if (opts.strictRoots && header.daRoot == null) errors.push('daRoot missing (strictRoots)')
  if (header.daRoot != null && !isHex(header.daRoot)) errors.push('invalid daRoot (expected 0x-hex)')

  if (opts.expectedChainId != null && header.chainId !== opts.expectedChainId) {
    errors.push(`chainId mismatch: header=${header.chainId} expected=${opts.expectedChainId}`)
  }
  if (opts.expectedPoiesPolicyRoot) {
    const h = header.poiesPolicyRoot ? normalizeHex(header.poiesPolicyRoot) : ''
    const exp = normalizeHex(opts.expectedPoiesPolicyRoot)
    if (h !== exp) errors.push('poiesPolicyRoot mismatch')
  }
  if (opts.expectedAlgPolicyRoot) {
    const h = header.algPolicyRoot ? normalizeHex(header.algPolicyRoot) : ''
    const exp = normalizeHex(opts.expectedAlgPolicyRoot)
    if (h !== exp) errors.push('algPolicyRoot mismatch')
  }

  const hash = hashHeader(header)
  return { ok: errors.length === 0, hash, errors, warnings }
}

/**
 * Verify parent→child linkage: child.number = parent.number+1 and
 * child.parentHash == hashHeader(parent).
 */
export function verifyLink(parent: LightHeader, child: LightHeader): LinkResult {
  const errors: string[] = []
  const parentHash = hashHeader(parent)
  if (child.number !== parent.number + 1) {
    errors.push(`height does not increment: parent=${parent.number} child=${child.number}`)
  }
  if (normalizeHex(child.parentHash) !== parentHash) {
    errors.push('parentHash mismatch between child and hashed parent')
  }
  return { ok: errors.length === 0, errors }
}

// ──────────────────────────────────────────────────────────────────────────────
/* DA light-proof verification (binary Merkle by default) */
// ──────────────────────────────────────────────────────────────────────────────

export interface VerifyDAResult {
  ok: boolean
  errors: string[]
  checked: number    // number of leaf proofs checked
}

/**
 * Verify a DA light proof against the header's daRoot.
 * - Uses built-in binary-Merkle verification for `proof.binary` items
 * - If `proof.verifyWith` is provided, it is invoked (async allowed) and must return true
 */
export async function verifyDA(proof: DALightProof, header: LightHeader): Promise<VerifyDAResult> {
  const errors: string[] = []
  const expectedRoot = normalizeHex(header.daRoot ?? '')
  if (!expectedRoot) return { ok: false, errors: ['header lacks daRoot'], checked: 0 }

  if (!isHex(proof.root)) errors.push('proof.root is not hex')
  const root = normalizeHex(proof.root)
  if (root !== expectedRoot) errors.push('proof root does not match header.daRoot')

  // Built-in binary proofs (optional)
  let checked = 0
  if (Array.isArray(proof.binary) && proof.binary.length) {
    for (const p of proof.binary) {
      try {
        const ok = verifyBinaryMerkle(p, root)
        if (!ok) errors.push('binary Merkle proof failed')
        else checked++
      } catch (e: any) {
        errors.push(`binary Merkle verification error: ${e?.message || String(e)}`)
      }
    }
  }

  // Custom verifier hook (e.g., NMT range proofs). If provided, it must pass.
  if (proof.verifyWith) {
    try {
      const ok = await proof.verifyWith(proof, header)
      if (!ok) errors.push('custom DA verifier reported failure')
    } catch (e: any) {
      errors.push(`custom DA verifier threw: ${e?.message || String(e)}`)
    }
  }

  return { ok: errors.length === 0, errors, checked }
}

/**
 * Verify a binary Merkle proof path: starting from leaf, combine with siblings up to root.
 * Hash function: sha3_256; combine(left,right) = sha3_256(0x01 || left || right)
 * The 0x01 domain tag reduces cross-protocol collisions.
 */
export function verifyBinaryMerkle(p: BinaryProof, expectedRoot: string): boolean {
  const root = normalizeHex(expectedRoot)
  if (!p || !Array.isArray(p.path) || p.path.length === 0) throw new Error('empty path')
  let h = p.leafHash ? normalizeHex(p.leafHash) : undefined
  if (!h) {
    if (p.leafData == null) throw new Error('leafData or leafHash required')
    const data = typeof p.leafData === 'string' ? hexOrUtf8ToBytes(p.leafData) : p.leafData
    h = '0x' + bytesToHex(sha3_256(concat(domain(0x00), data))) // 0x00 domain for leaf
  }
  let cur = hexToBytes(h)
  for (const step of p.path) {
    const sib = hexToBytes(normalizeHex(step.sibling))
    if (step.position === 'left') {
      cur = sha3_256(concat(domain(0x01), cur, sib))
    } else if (step.position === 'right') {
      cur = sha3_256(concat(domain(0x01), sib, cur))
    } else {
      throw new Error(`invalid step.position: ${String(step.position)}`)
    }
  }
  const got = '0x' + bytesToHex(cur)
  return got.toLowerCase() === root
}

// ──────────────────────────────────────────────────────────────────────────────
// Bundled flow convenience
// ──────────────────────────────────────────────────────────────────────────────

export interface VerifyBundleOptions extends VerifyHeaderOptions {
  daProof?: DALightProof
  parent?: LightHeader
}

/** Verify header (+ optional parent link) and optional DA proof in one call. */
export async function verifyBundle(header: LightHeader, opts: VerifyBundleOptions = {}) {
  const hdr = verifyHeader(header, opts)
  const link = opts.parent ? verifyLink(opts.parent, header) : { ok: true, errors: [] as string[] }
  const da = opts.daProof ? await verifyDA(opts.daProof, header) : { ok: true, errors: [] as string[], checked: 0 }
  const ok = hdr.ok && link.ok && da.ok
  return { ok, header: hdr, link, da }
}

// ──────────────────────────────────────────────────────────────────────────────
/* Utils */
// ──────────────────────────────────────────────────────────────────────────────

function normalizeHex(h: string): string {
  if (typeof h !== 'string') throw new Error('expected hex string')
  const s = h.startsWith('0x') || h.startsWith('0X') ? h : ('0x' + h)
  if (!isHex(s)) throw new Error(`invalid hex: ${h}`)
  return s.toLowerCase()
}

function hexOrUtf8ToBytes(x: string): Uint8Array {
  return isHex(x) ? hexToBytes(x) : new TextEncoder().encode(x)
}

function bytesToHexLower(b: Uint8Array): string {
  return bytesToHex(b).toLowerCase()
}

function domain(tag: number): Uint8Array {
  // Single-byte domain tags: 0x00 = leaf, 0x01 = node
  const u = new Uint8Array(1); u[0] = tag & 0xff; return u
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const n = parts.reduce((a, p) => a + p.length, 0)
  const out = new Uint8Array(n)
  let off = 0
  for (const p of parts) { out.set(p, off); off += p.length }
  return out
}

// Re-exports for convenience
export default {
  hashHeader,
  verifyHeader,
  verifyLink,
  verifyDA,
  verifyBinaryMerkle,
  verifyBundle
}
