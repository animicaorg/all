/**
 * Event filter & (scaffolded) decoder utilities.
 *
 * This module helps you:
 *  - Build a registry of events from an ABI
 *  - Match logs by event (topic0) and optional address
 *  - Produce a structured decoded shape with:
 *      • name, signature, topic0
 *      • indexed arguments (as hex from topics)
 *      • raw, non-indexed data bytes (you can further decode with ABI helpers)
 *
 * Notes
 * -----
 * Animica's Python-VM ABI uses a canonical, length-prefixed encoding for
 * arguments/returns. Your app can fully decode the non-indexed payload using
 * helpers exported by `../types/abi` (e.g., a tuple decoder). To keep this
 * module robust and dependency-light, we return the raw data for non-indexed
 * fields and also expose a hook to pass an external decoder if you want one.
 */

import { sha3_256 } from '../utils/hash'
import { bytesToHex, hexToBytes } from '../utils/bytes'
import { assertAddress } from '../address'

/** Minimal log shape compatible with most node receipts. */
export interface ChainLog {
  address?: string
  topics: string[]           // 0x… hex strings; topics[0] = event signature hash
  data: string | Uint8Array  // 0x… or raw bytes
  blockNumber?: number
  txHash?: string
  index?: number
  [k: string]: any
}

/** ABI param shape (subset) used for events. */
export interface ABIParam {
  name: string
  type: string
  indexed?: boolean
}

/** ABI event shape (subset). */
export interface ABIEvent {
  type: 'event'
  name: string
  inputs: ABIParam[]
  anonymous?: boolean
}

/** Top-level ABI item (only `event` items are used here). */
export type ABIItem = ABIEvent | { type: string; [k: string]: any }

/** Full ABI as an array of items. */
export type ABI = ABIItem[]

/** Registry entry for a single event. */
export interface EventRegistryEntry {
  readonly name: string
  readonly signature: string
  readonly topic0: string               // 0x… (lowercase)
  readonly indexed: ABIParam[]          // in declaration order
  readonly nonIndexed: ABIParam[]       // in declaration order
  readonly def: ABIEvent
}

/** Build a topic→entry registry from an ABI. */
export function buildEventRegistry(abi: ABI): Map<string, EventRegistryEntry> {
  const reg = new Map<string, EventRegistryEntry>()
  for (const item of abi ?? []) {
    if (!item || (item as any).type !== 'event') continue
    const ev = item as ABIEvent
    if (!ev.name || !Array.isArray(ev.inputs)) continue
    const signature = eventSignature(ev)
    const topic0 = '0x' + bytesToHex(sha3_256(text(signature))).toLowerCase()
    const indexed = ev.inputs.filter((p) => !!p.indexed)
    const nonIndexed = ev.inputs.filter((p) => !p.indexed)
    reg.set(topic0, { name: ev.name, signature, topic0, indexed, nonIndexed, def: ev })
  }
  return reg
}

/** Compute canonical signature string: e.g., `Transfer(address,uint256)`. */
export function eventSignature(ev: Pick<ABIEvent, 'name' | 'inputs'>): string {
  const types = (ev.inputs ?? []).map((p) => p.type)
  return `${ev.name}(${types.join(',')})`
}

/** Convenient predicate for topic0 hashing (lowercase 0x…). */
export function topicForSignature(signature: string): string {
  return '0x' + bytesToHex(sha3_256(text(signature))).toLowerCase()
}

/** Optional external decoder contract. */
export type NonIndexedDecoder =
  (entry: EventRegistryEntry, data: Uint8Array) => Record<string, unknown>

/** Decoded event result. */
export interface DecodedEvent {
  name: string
  signature: string
  topic0: string
  address?: string
  blockNumber?: number
  txHash?: string
  index?: number
  /** Named arguments; indexed are hex strings, non-indexed come from `decoded` (or empty). */
  args: Record<string, unknown>
  /** Raw mapping of only indexed params → topic hex. */
  indexed: Record<string, string>
  /** Raw data payload for non-indexed fields (ABI-encoded). */
  data: Uint8Array
  /** If a decoder was provided, contains its result; otherwise empty object. */
  decoded: Record<string, unknown>
}

/**
 * Match & decode logs:
 *  - Filters by optional `address` (bech32 or hex; case-insensitive for hex)
 *  - Matches topic0 against the ABI registry
 *  - Extracts indexed args from topics
 *  - Returns raw non-indexed `data` and optionally a decoded object via `decoder`
 */
export function matchAndDecodeLogs(
  logs: ChainLog[],
  abi: ABI,
  opts?: { address?: string; decoder?: NonIndexedDecoder }
): DecodedEvent[] {
  const reg = buildEventRegistry(abi)
  const wantAddr = opts?.address ? normalizeAddr(opts.address) : undefined
  const out: DecodedEvent[] = []

  for (const log of logs ?? []) {
    if (!Array.isArray(log.topics) || log.topics.length === 0) continue
    const topic0 = (log.topics[0] || '').toLowerCase()
    const entry = reg.get(topic0)
    if (!entry) continue

    if (wantAddr && log.address && normalizeAddr(log.address) !== wantAddr) {
      continue
    }

    const dataBytes = toBytes(log.data)
    const indexedMap: Record<string, string> = {}
    const args: Record<string, unknown> = {}

    // topics[1..] correspond to indexed params in declaration order
    for (let i = 0; i < entry.indexed.length; i++) {
      const p = entry.indexed[i]
      const t = log.topics[1 + i]
      if (!t) continue
      indexedMap[p.name || `arg${i}`] = t
      args[p.name || `arg${i}`] = t
    }

    // Attempt optional decoding of non-indexed payload with provided decoder
    let decoded: Record<string, unknown> = {}
    if (opts?.decoder) {
      try {
        decoded = opts.decoder(entry, dataBytes) || {}
        // Merge decoded (non-indexed) into args without clobbering indexed
        for (const [k, v] of Object.entries(decoded)) {
          if (!(k in args)) args[k] = v
        }
      } catch (e) {
        // Keep raw data; caller can retry with a different decoder
      }
    }

    out.push({
      name: entry.name,
      signature: entry.signature,
      topic0: entry.topic0,
      address: log.address,
      blockNumber: log.blockNumber,
      txHash: log.txHash,
      index: (log as any).index,
      args,
      indexed: indexedMap,
      data: dataBytes,
      decoded
    })
  }

  return out
}

/**
 * Helper to build a decoder that uses a tuple/ABI decoder you provide.
 * Example:
 *   import { tupleDecodeForTypes } from '../types/abi'
 *   const decoder = makeTupleDecoder((types, data) => tupleDecodeForTypes(types, data))
 */
export function makeTupleDecoder(
  tupleDecode: (types: string[], data: Uint8Array) => unknown[]
): NonIndexedDecoder {
  return (entry, data) => {
    const types = entry.nonIndexed.map((p) => p.type)
    const names = entry.nonIndexed.map((p, i) => p.name || `arg${i}`)
    const values = tupleDecode(types, data)
    const out: Record<string, unknown> = {}
    for (let i = 0; i < names.length; i++) out[names[i]] = (values as any[])[i]
    return out
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ──────────────────────────────────────────────────────────────────────────────

function toBytes(x: string | Uint8Array): Uint8Array {
  if (x instanceof Uint8Array) return x
  if (typeof x === 'string') {
    if (x.startsWith('0x') || x.startsWith('0X')) return hexToBytes(x)
    return new TextEncoder().encode(x)
  }
  throw new Error('log.data must be hex string or Uint8Array')
}

function text(s: string): Uint8Array {
  return new TextEncoder().encode(s)
}

function normalizeAddr(a: string): string {
  // Accept bech32m or hex; for hex compare lowercase
  if (a.startsWith('0x') || a.startsWith('0X')) return a.toLowerCase()
  // Basic sanity for bech32m (leave as-is); upstream assert will throw if used elsewhere
  try { assertAddress(a) } catch { /* ignore here */ }
  return a
}

export default {
  buildEventRegistry,
  eventSignature,
  topicForSignature,
  matchAndDecodeLogs,
  makeTupleDecoder
}
