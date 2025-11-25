/**
 * Randomness client — commit/reveal + beacon queries over JSON-RPC.
 *
 * Exposed RPC methods (per node impl):
 *  - rand.getParams() → RandParams
 *  - rand.getRound() → RoundInfo
 *  - rand.commit({ address, salt, payload }) → { ok, roundId, commitment }
 *  - rand.reveal({ address, salt, payload }) → { ok, roundId }
 *  - rand.getBeacon(roundId?) → BeaconOut | null
 *  - rand.getHistory({ cursor?, limit? }) → { beacons: BeaconOut[], nextCursor? }
 *
 * Helpers:
 *  - makeSalt(len=32) → 0x… random salt
 *  - commitmentOf(address, salt, payload) → 0x… (H("rand.commit.v1"|addr|salt|payload))
 */

import type { RpcClient } from '../tx/send'
import { bytesToHex, hexToBytes, isHex } from '../utils/bytes'
import { sha3_256 } from '../utils/hash'

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

export interface RandParams {
  roundLengthSeconds: number
  revealGraceSeconds: number
  vdfIterations: number
  securityLevel?: string | number
  [k: string]: unknown
}

export type RoundPhase = 'Commit' | 'Reveal' | 'VDF' | 'Closed'

export interface RoundInfo {
  roundId: number
  phase: RoundPhase
  /** RFC3339 timestamps if provided by node */
  commitOpenAt?: string
  revealOpenAt?: string
  revealCloseAt?: string
  vdfTargetAt?: string
  /** Optional chain coordinates */
  heightStart?: number
  heightEnd?: number
  [k: string]: unknown
}

export interface CommitArgs {
  address: string                // bech32m or 0x-hex
  salt: string | Uint8Array      // 0x… or bytes
  payload: string | Uint8Array   // 0x… or bytes
}

export interface CommitResult {
  ok: boolean
  roundId: number
  commitment: string             // 0x…
  [k: string]: unknown
}

export interface RevealArgs extends CommitArgs {}

export interface RevealResult {
  ok: boolean
  roundId: number
  [k: string]: unknown
}

export interface VDFProof {
  input: string           // 0x…
  proof: string           // 0x…
  iterations?: number
  [k: string]: unknown
}

export interface BeaconOut {
  roundId: number
  output: string          // final beacon bytes 0x…
  aggregate?: string      // aggregate of reveals 0x…
  vdf?: VDFProof
  qrngMixed?: boolean
  [k: string]: unknown
}

export interface HistoryQuery {
  cursor?: string
  limit?: number
}

export interface HistoryPage {
  beacons: BeaconOut[]
  nextCursor?: string
}

// ──────────────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────────────

export class RandomnessClient {
  constructor(private readonly rpc: RpcClient) {}

  async getParams(): Promise<RandParams> {
    return this.callSafe<RandParams>('rand.getParams', [])
  }

  async getRound(): Promise<RoundInfo> {
    return this.callSafe<RoundInfo>('rand.getRound', [])
  }

  async commit(args: CommitArgs): Promise<CommitResult> {
    const req = normalizeCommitArgs(args)
    const res = await this.callSafe<any>('rand.commit', [req])
    return normalizeCommitResult(res)
  }

  async reveal(args: RevealArgs): Promise<RevealResult> {
    const req = normalizeCommitArgs(args)
    const res = await this.callSafe<any>('rand.reveal', [req])
    return normalizeRevealResult(res)
  }

  async getBeacon(roundId?: number): Promise<BeaconOut | null> {
    const res = await this.callSafe<any>('rand.getBeacon', typeof roundId === 'number' ? [roundId] : [])
    return res ? normalizeBeacon(res) : null
  }

  async getHistory(q: HistoryQuery = {}): Promise<HistoryPage> {
    const res = await this.callSafe<any>('rand.getHistory', [q])
    return normalizeHistory(res)
  }

  // Low-level wrapper with friendlier error UX
  private async callSafe<T = unknown>(method: string, params: unknown[]): Promise<T> {
    try {
      // @ts-ignore runtime RpcClient has call(method, params)
      return await this.rpc.call(method, params)
    } catch (e: any) {
      throw new Error(`RandomnessClient: RPC ${method} failed: ${e?.message || String(e)}`)
    }
  }

  // ──────────────── Helpers (static) ────────────────

  /** Create a random salt (0x-hex), default 32 bytes. */
  static makeSalt(len = 32): string {
    const b = new Uint8Array(len)
    if (typeof crypto !== 'undefined' && 'getRandomValues' in crypto) {
      crypto.getRandomValues(b)
    } else {
      // Non-crypto fallback for constrained envs; acceptable only in tests.
      for (let i = 0; i < len; i++) b[i] = (Math.random() * 256) | 0
    }
    return '0x' + bytesToHex(b)
  }

  /**
   * Deterministic commitment used by the node:
   *   C = H("rand.commit.v1" | encodeAddress(addr) | salt | payload)
   */
  static commitmentOf(address: string, salt: string | Uint8Array, payload: string | Uint8Array): string {
    const dom = new TextEncoder().encode('rand.commit.v1')
    const addr = encodeAddress(address)
    const s = toBytes(salt)
    const p = toBytes(payload)
    const data = concat(dom, addr, s, p)
    return '0x' + bytesToHex(sha3_256(data))
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Normalizers
// ──────────────────────────────────────────────────────────────────────────────

function normalizeCommitArgs(a: CommitArgs) {
  return {
    address: a.address,
    salt: toHex(a.salt),
    payload: toHex(a.payload),
    // client-side computed commitment (server may recompute/ignore)
    commitment: RandomnessClient.commitmentOf(a.address, a.salt, a.payload)
  }
}

function normalizeCommitResult(x: any): CommitResult {
  if (!x || typeof x !== 'object') throw new Error('rand.commit: invalid response')
  const roundId = Number(x.roundId ?? x.round ?? NaN)
  const commitment = normalizeHex(x.commitment ?? x.commit ?? '')
  return { ok: !!x.ok, roundId, commitment, ...x }
}

function normalizeRevealResult(x: any): RevealResult {
  if (!x || typeof x !== 'object') throw new Error('rand.reveal: invalid response')
  const roundId = Number(x.roundId ?? x.round ?? NaN)
  return { ok: !!x.ok, roundId, ...x }
}

function normalizeBeacon(b: any): BeaconOut {
  if (!b || typeof b !== 'object') throw new Error('rand.getBeacon: invalid payload')
  const roundId = Number(b.roundId ?? b.round ?? NaN)
  const output = normalizeHex(b.output ?? b.out ?? '')
  const aggregate = b.aggregate ? normalizeHex(b.aggregate) : undefined
  const vdf = b.vdf ? normalizeVDF(b.vdf) : undefined
  const qrngMixed = !!b.qrngMixed
  const out: BeaconOut = { roundId, output, aggregate, vdf, qrngMixed }
  for (const [k, v] of Object.entries(b)) {
    if (!(k in out)) (out as any)[k] = v
  }
  return out
}

function normalizeVDF(v: any): VDFProof {
  if (!v || typeof v !== 'object') throw new Error('rand.getBeacon: invalid vdf')
  return {
    input: normalizeHex(v.input),
    proof: normalizeHex(v.proof),
    iterations: v.iterations != null ? Number(v.iterations) : undefined,
    ...v
  }
}

function normalizeHistory(h: any): HistoryPage {
  const beacons = Array.isArray(h?.beacons) ? h.beacons.map(normalizeBeacon) : []
  const nextCursor = typeof h?.nextCursor === 'string' ? h.nextCursor : undefined
  return { beacons, nextCursor }
}

// ──────────────────────────────────────────────────────────────────────────────
/* Byte helpers */
// ──────────────────────────────────────────────────────────────────────────────

function toHex(x: string | Uint8Array): string {
  if (typeof x === 'string') {
    if (isHex(x)) return x.startsWith('0x') ? x : ('0x' + x)
    return '0x' + bytesToHex(new TextEncoder().encode(x))
  }
  return '0x' + bytesToHex(x)
}

function toBytes(x: string | Uint8Array): Uint8Array {
  if (x instanceof Uint8Array) return x
  if (isHex(x)) return hexToBytes(x)
  return new TextEncoder().encode(x)
}

function normalizeHex(h: string): string {
  if (!h) throw new Error('Expected hex string')
  const s = h.startsWith('0x') || h.startsWith('0X') ? h : ('0x' + h)
  if (!isHex(s)) throw new Error(`Invalid hex: ${h}`)
  return s.toLowerCase()
}

function encodeAddress(addr: string): Uint8Array {
  // Accept hex (20/32 bytes) or bech32m-like. For hex, left-pad to 32 bytes.
  if (addr.startsWith('0x') || addr.startsWith('0X')) {
    const raw = hexToBytes(addr)
    if (raw.length === 32) return raw
    return padLeft(raw, 32)
  }
  // For bech32m, we normalize by hashing text bytes (chain's addr hash is 32b)
  return sha3_256(new TextEncoder().encode(addr))
}

function padLeft(b: Uint8Array, len: number): Uint8Array {
  if (b.length >= len) return b
  const out = new Uint8Array(len)
  out.set(b, len - b.length)
  return out
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0)
  const out = new Uint8Array(total)
  let off = 0
  for (const p of parts) { out.set(p, off); off += p.length }
  return out
}

export default {
  RandomnessClient
}
