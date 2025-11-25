/**
 * Canonical SignBytes & raw transaction CBOR encoders.
 *
 * This module turns an `UnsignedTx` (from ./build) into:
 *   - SignBytes: domain-separated canonical CBOR for signing
 *   - Raw/signed TX envelope: canonical CBOR body + signature object
 *
 * The exact shapes mirror the Python SDK and the node's canonical encoders:
 *
 * SignBytes (CBOR array):
 *   [
 *     "animica/tx-sign/v1",
 *     {
 *       kind: "transfer"|"call"|"deploy",
 *       chainId: uint,
 *       from: tstr(bech32m),
 *       to: tstr? (omit for deploy),
 *       nonce: uint/bignum,
 *       gasPrice: uint/bignum,
 *       gasLimit: uint/bignum,
 *       value: uint/bignum?,
 *       data: bstr?,                // call/deploy payload
 *       accessList: [               // optional, normalized & sorted
 *         [ tstr(address), [ bstr(storageKey32), ... ] ],
 *         ...
 *       ]?
 *     }
 *   ]
 *
 * Signed Tx Envelope (CBOR map, canonical ordering by key):
 *   {
 *     body: <the same inner body map as above>,
 *     sig:  {
 *       algId: uint,               // canonical PQ alg id (e.g., 0x01 dilithium3)
 *       publicKey: bstr,
 *       signature: bstr
 *     }
 *   }
 *
 * The CBOR encoder used here must be canonical (deterministic key ordering).
 */

import type { UnsignedTx, AccessList } from './build'
import { encodeCanonical as cborEncode } from '../utils/cbor'
import { assertAddress } from '../address'
import { hexToBytes } from '../utils/bytes'
import type { AlgorithmId } from '../wallet/signer'
import { ALG_ID } from '../address'

// ──────────────────────────────────────────────────────────────────────────────
// Public API
// ──────────────────────────────────────────────────────────────────────────────

/** Construct canonical SignBytes for an unsigned transaction. */
export function makeSignBytes(tx: UnsignedTx): Uint8Array {
  const body = buildBody(tx)
  const signArray: readonly [string, Record<string, unknown>] = [
    SIGN_DOMAIN,
    body
  ]
  return cborEncode(signArray)
}

/**
 * Encode a signed transaction into the raw envelope suitable for submission
 * to the node (e.g., via JSON-RPC `tx.sendRawTransaction` after hexifying).
 */
export function encodeSignedTx(
  tx: UnsignedTx,
  signature: Uint8Array,
  publicKey: Uint8Array,
  alg: AlgorithmId
): Uint8Array {
  const body = buildBody(tx)
  const env = {
    body,
    sig: {
      algId: ALG_ID[alg] ?? fail(`Unsupported alg: ${alg}`),
      publicKey,
      signature
    }
  }
  return cborEncode(env)
}

/** Convenience: build the inner CBOR body object without domain wrapper. */
export function buildBody(tx: UnsignedTx): Record<string, unknown> {
  validateUnsigned(tx)

  const body: Record<string, unknown> = {
    kind: tx.kind,
    chainId: tx.chainId | 0,               // ensure number-ish (CBOR uint)
    from: tx.from,
    nonce: toBigint(tx.nonce),
    gasPrice: toBigint(tx.gasPrice),
    gasLimit: toBigint(tx.gasLimit)
  }

  if (tx.to && tx.kind !== 'deploy') body.to = tx.to
  if (tx.value !== undefined) body.value = toBigint(tx.value)
  if (tx.data && tx.data.length > 0) body.data = tx.data

  if (tx.accessList && tx.accessList.length > 0) {
    body.accessList = normalizeAccessList(tx.accessList)
  }

  return body
}

// ──────────────────────────────────────────────────────────────────────────────
// Internals
// ──────────────────────────────────────────────────────────────────────────────

const SIGN_DOMAIN = 'animica/tx-sign/v1' as const

function validateUnsigned(tx: UnsignedTx) {
  if (!tx) fail('tx is required')
  if (!tx.kind) fail('tx.kind is required')
  if (typeof tx.chainId !== 'number' || !Number.isInteger(tx.chainId) || tx.chainId < 0) {
    fail('tx.chainId must be a non-negative integer')
  }
  assertAddress(tx.from)
  if (tx.to && tx.kind !== 'deploy') assertAddress(tx.to)
  // ensure bigints are sane
  toBigint(tx.nonce)
  toBigint(tx.gasPrice)
  toBigint(tx.gasLimit)
  if (tx.value !== undefined) toBigint(tx.value)
}

function toBigint(x: bigint | number | string): bigint {
  if (typeof x === 'bigint') return x
  if (typeof x === 'number') {
    if (!Number.isFinite(x) || x < 0) fail('invalid number for bigint')
    return BigInt(Math.trunc(x))
  }
  if (typeof x === 'string') {
    if (x.startsWith('0x') || x.startsWith('0X')) return BigInt(x)
    if (!/^\d+$/.test(x)) fail('invalid decimal string for bigint')
    return BigInt(x)
  }
  fail('unsupported bigint-like value')
}

function normalizeAccessList(al: AccessList) {
  // Sort by address for determinism; keys inside each entry also sorted
  const sorted = [...al].map((e) => ({
    address: (e.address ?? '').toLowerCase(),
    storageKeys: [...(e.storageKeys ?? [])]
  })).sort((a, b) => a.address.localeCompare(b.address))

  return sorted.map((e) => {
    assertAddress(e.address)
    const keys = e.storageKeys.map((k) => {
      if (typeof k !== 'string' || !k.startsWith('0x')) {
        fail('storageKeys must be hex strings (0x…)')
      }
      const b = hexToBytes(k)
      if (b.length !== 32) fail(`storage key must be 32 bytes, got ${b.length}`)
      return b
    }).sort((a, b) => cmpBytes(a, b))
    // Represent as tuple [address, [keys...]] for compactness and ordering
    return [e.address, keys] as const
  })
}

function cmpBytes(a: Uint8Array, b: Uint8Array): number {
  const n = Math.min(a.length, b.length)
  for (let i = 0; i < n; i++) {
    const d = a[i] - b[i]
    if (d) return d
  }
  return a.length - b.length
}

function fail(msg: string): never {
  throw new Error(`encode: ${msg}`)
}

// ──────────────────────────────────────────────────────────────────────────────
// Re-exports (types)
// ──────────────────────────────────────────────────────────────────────────────

export type { UnsignedTx } from './build'
export type { AlgorithmId } from '../wallet/signer'

export default {
  makeSignBytes,
  encodeSignedTx,
  buildBody
}
