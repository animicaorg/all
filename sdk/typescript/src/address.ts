/**
 * Address utilities for Animica.
 *
 * Address format (bech32m, lowercase):
 *    hrp = 'anim' (default) or network-specific (e.g., 'animt' for testnet)
 *    data = convertBits( [ alg_id (1 byte) || sha3_256(pubkey) (32 bytes) ], 8 -> 5, pad=true )
 *
 * Encoding:
 *    address = bech32mEncode(hrp, dataWords)
 *
 * Decoding:
 *    {hrp, words} = bech32mDecode(address)
 *    payload = convertBits(words, 5 -> 8, pad=false)
 *    alg_id = payload[0]
 *    pk_hash = payload[1:33]
 *
 * Supported algorithms (alg_id):
 *   - dilithium3           => 0x01
 *   - sphincs_shake_128s   => 0x02
 */

import { sha3_256 } from './utils/hash'
import { bech32mEncode, bech32mDecode, toWords, fromWords } from './utils/bech32'
import { bytesToHex } from './utils/bytes'
import type { AlgorithmId } from './wallet/signer'

export type Address = string

export const DEFAULT_HRP = 'anim' as const

/** Canonical numeric ids for PQ signature algorithms used in addresses. */
export const ALG_ID: Record<AlgorithmId, number> = {
  dilithium3: 0x01,
  sphincs_shake_128s: 0x02
}

/** Reverse mapping: numeric id -> AlgorithmId (throws on unknown). */
export function algIdToName(id: number): AlgorithmId {
  switch (id) {
    case 0x01: return 'dilithium3'
    case 0x02: return 'sphincs_shake_128s'
    default:
      throw new Error(`Unknown algorithm id: 0x${id.toString(16).padStart(2, '0')}`)
  }
}

/**
 * Derive a bech32m address from a raw public key and algorithm.
 *
 * The public key must be the canonical byte representation for the algorithm.
 * The address payload is: alg_id(1) || sha3_256(pubkey)(32).
 */
export function addressFromPublicKey(
  publicKey: Uint8Array,
  alg: AlgorithmId,
  hrp: string = DEFAULT_HRP
): Address {
  assertBytes('publicKey', publicKey)
  assertHrp(hrp)
  const algByte = ALG_ID[alg]
  if (algByte === undefined) throw new Error(`Unsupported algorithm: ${alg}`)

  const pkHash = sha3_256(publicKey)
  const payload = new Uint8Array(1 + pkHash.length)
  payload[0] = algByte
  payload.set(pkHash, 1)

  const words = toWords(payload) // 8->5 bits
  return bech32mEncode(hrp, words)
}

/** Build raw address payload bytes (alg_id || sha3_256(pubkey)). */
export function payloadFromPublicKey(
  publicKey: Uint8Array,
  alg: AlgorithmId
): Uint8Array {
  assertBytes('publicKey', publicKey)
  const algByte = ALG_ID[alg]
  if (algByte === undefined) throw new Error(`Unsupported algorithm: ${alg}`)
  const pkHash = sha3_256(publicKey)
  const payload = new Uint8Array(1 + pkHash.length)
  payload[0] = algByte
  payload.set(pkHash, 1)
  return payload
}

export interface DecodedAddress {
  hrp: string
  alg: AlgorithmId
  algId: number
  payload: Uint8Array        // 33 bytes: [alg_id || pk_hash]
  pubkeyHash: Uint8Array     // 32 bytes
}

/**
 * Decode and validate a bech32m address.
 * Throws on errors; returns structured components on success.
 */
export function decodeAddress(addr: Address): DecodedAddress {
  const { hrp, words, spec } = bech32mDecode(addr)
  if (spec !== 'bech32m') throw new Error('Address must be bech32m encoded')
  const payload = fromWords(words) // 5->8 bits
  if (payload.length !== 33) {
    throw new Error(`Invalid address payload length: ${payload.length} (expected 33)`)
  }
  const algId = payload[0]
  const alg = algIdToName(algId)
  const pubkeyHash = payload.slice(1)
  return { hrp, alg, algId, payload, pubkeyHash }
}

/**
 * Validate address shape, checksum, HRP and/or allowed algorithms.
 * Returns boolean; use `assertAddress` to get structured errors.
 */
export function isValidAddress(
  addr: string,
  opts?: { hrp?: string; allowedAlgs?: AlgorithmId[] }
): boolean {
  try {
    assertAddress(addr, opts)
    return true
  } catch {
    return false
  }
}

/**
 * Assert an address is valid; optionally enforce HRP and allowed algorithms.
 * Returns the decoded address on success (useful for downstream logic).
 */
export function assertAddress(
  addr: string,
  opts?: { hrp?: string; allowedAlgs?: AlgorithmId[] }
): DecodedAddress {
  const dec = decodeAddress(addr)
  if (opts?.hrp && dec.hrp !== opts.hrp) {
    throw new Error(`Address HRP mismatch: expected '${opts.hrp}', got '${dec.hrp}'`)
  }
  if (opts?.allowedAlgs && !opts.allowedAlgs.includes(dec.alg)) {
    throw new Error(`Address algorithm not allowed: ${dec.alg}`)
  }
  return dec
}

/** Pretty-short form: 'anim1abcd…wxyz' */
export function shortAddress(addr: Address, left: number = 6, right: number = 4): string {
  const [prefix, rest] = addr.split('1')
  if (!rest) return addr
  const mid = rest.length > left + right ? `${rest.slice(0, left)}…${rest.slice(-right)}` : rest
  return `${prefix}1${mid}`
}

/** Return hex string of the 33-byte payload. */
export function addressPayloadHex(addr: Address): string {
  const { payload } = decodeAddress(addr)
  return bytesToHex(payload)
}

// ──────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ──────────────────────────────────────────────────────────────────────────────

function assertBytes(name: string, x: unknown): asserts x is Uint8Array {
  if (!(x instanceof Uint8Array)) {
    throw new Error(`${name} must be Uint8Array`)
  }
}

function assertHrp(hrp: string) {
  if (typeof hrp !== 'string' || hrp.length < 1 || hrp.length > 83) {
    throw new Error('Invalid HRP length (1..83)')
  }
  if (hrp.toLowerCase() !== hrp) {
    throw new Error('HRP must be lowercase (bech32m)')
  }
}

export default {
  addressFromPublicKey,
  decodeAddress,
  isValidAddress,
  assertAddress,
  shortAddress,
  payloadFromPublicKey,
  addressPayloadHex,
  DEFAULT_HRP,
  ALG_ID,
  algIdToName
}
