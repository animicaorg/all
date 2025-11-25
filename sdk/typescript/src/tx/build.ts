/**
 * Transaction builders for @animica/sdk (TypeScript).
 *
 * Exposes high-level helpers to construct unsigned transactions for:
 *   - transfer
 *   - call
 *   - deploy
 *
 * Also provides lightweight intrinsic-gas estimators so callers can supply a
 * sensible `gasLimit` if they don't want to hardcode values. These are
 * conservative client-side estimates; the authoritative check is on-chain.
 */

import { assertAddress } from '../address'
import { sha3_256 } from '../utils/hash'
import { bytesToHex, hexToBytes } from '../utils/bytes'

/** Supported transaction kinds. */
export type TxKind = 'transfer' | 'call' | 'deploy'

/** Access list entry (EIP-2930-like shape). */
export interface AccessListEntry {
  address: string               // bech32m address
  storageKeys: string[]         // hex 0x… keys (32 bytes each)
}
export type AccessList = AccessListEntry[]

/** Unsigned transaction shape used by encoder/signing pipeline. */
export interface UnsignedTx {
  kind: TxKind
  chainId: number
  from: string                  // bech32m
  to?: string                   // for transfer/call
  nonce: bigint
  gasPrice: bigint
  gasLimit: bigint
  value?: bigint                // optional tip/value for transfer/call/deploy
  data?: Uint8Array             // call/deploy payload (already ABI-encoded if applicable)
  accessList?: AccessList
}

/** Convenience: numbers accepted from UI code. */
export type Bigish = bigint | number | string

// ──────────────────────────────────────────────────────────────────────────────
// Builders
// ──────────────────────────────────────────────────────────────────────────────

export interface TransferOpts {
  chainId: number
  from: string
  to: string
  value: Bigish
  nonce: Bigish
  gasPrice: Bigish
  gasLimit?: Bigish
  accessList?: AccessList
}

export function buildTransfer(opts: TransferOpts): UnsignedTx {
  const from = assertAddr(opts.from)
  const to = assertAddr(opts.to)
  const value = toBig(opts.value)
  const nonce = toBig(opts.nonce)
  const gasPrice = toBig(opts.gasPrice)
  const accessList = normalizeAccessList(opts.accessList)

  const est = estimateIntrinsicGas('transfer', 0, accessList)
  const gasLimit = toBig(opts.gasLimit ?? addPercent(est, 10n))

  return {
    kind: 'transfer',
    chainId: opts.chainId,
    from,
    to,
    nonce,
    gasPrice,
    gasLimit,
    value,
    accessList: emptyOr(accessList)
  }
}

export interface CallOpts {
  chainId: number
  from: string
  to: string
  /** Pre-encoded call data bytes (ABI-encoded elsewhere). */
  data?: Uint8Array | string
  /** Optional value to transfer along with the call. */
  value?: Bigish
  nonce: Bigish
  gasPrice: Bigish
  gasLimit?: Bigish
  accessList?: AccessList
}

export function buildCall(opts: CallOpts): UnsignedTx {
  const from = assertAddr(opts.from)
  const to = assertAddr(opts.to)
  const nonce = toBig(opts.nonce)
  const gasPrice = toBig(opts.gasPrice)
  const data = normalizeData(opts.data)
  const value = opts.value !== undefined ? toBig(opts.value) : undefined
  const accessList = normalizeAccessList(opts.accessList)

  const est = estimateIntrinsicGas('call', data?.length ?? 0, accessList)
  const gasLimit = toBig(opts.gasLimit ?? addPercent(est, 20n)) // extra headroom for code paths

  return {
    kind: 'call',
    chainId: opts.chainId,
    from,
    to,
    nonce,
    gasPrice,
    gasLimit,
    value,
    data,
    accessList: emptyOr(accessList)
  }
}

export interface DeployOpts {
  chainId: number
  from: string
  /** Contract code (IR or VM bytecode) — exact format per network. */
  code: Uint8Array | string
  /** Manifest/ABI blob already encoded for the target chain (optional). */
  manifest?: Uint8Array | string
  /** Optional value to fund contract treasury at deploy. */
  value?: Bigish
  nonce: Bigish
  gasPrice: Bigish
  gasLimit?: Bigish
  accessList?: AccessList
}

/**
 * Build a deploy transaction. `code` and optional `manifest` are concatenated
 * at the ABI/encoding layer as required by the chain (encoder handles that).
 * Here we just carry them as `data` bytes: data = hash-tag | code | manifest...
 * For now we simply pack: data = code || manifest (if provided), and leave the
 * exact framing to the encoder (src/tx/encode.ts).
 */
export function buildDeploy(opts: DeployOpts): UnsignedTx {
  const from = assertAddr(opts.from)
  const nonce = toBig(opts.nonce)
  const gasPrice = toBig(opts.gasPrice)
  const code = normalizeData(opts.code)
  const manifest = opts.manifest ? normalizeData(opts.manifest) : undefined
  const value = opts.value !== undefined ? toBig(opts.value) : undefined
  const accessList = normalizeAccessList(opts.accessList)

  const combined = manifest ? concatBytes(code, manifest) : code
  const est = estimateIntrinsicGas('deploy', combined.length, accessList)
  const gasLimit = toBig(opts.gasLimit ?? addPercent(est, 30n)) // deployments often need more

  return {
    kind: 'deploy',
    chainId: opts.chainId,
    from,
    nonce,
    gasPrice,
    gasLimit,
    value,
    data: combined,
    accessList: emptyOr(accessList)
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Intrinsic gas estimation (client-side heuristic; chain enforces truth)
// ──────────────────────────────────────────────────────────────────────────────

/**
 * Conservative intrinsic gas estimator. These values should mirror
 * `execution/gas/intrinsic.py` on the node side, but we keep them decoupled
 * and slightly padded for safety.
 */
export function estimateIntrinsicGas(
  kind: TxKind,
  dataLen: number = 0,
  accessList?: AccessList
): bigint {
  const acc = accessList ?? []
  const accEntries = BigInt(acc.length)
  const accKeys = BigInt(acc.reduce((n, e) => n + (e.storageKeys?.length ?? 0), 0))
  const ACCESS_ADDR_COST = 2400n
  const ACCESS_KEY_COST = 1900n

  switch (kind) {
    case 'transfer': {
      const BASE = 21000n
      return BASE + accEntries * ACCESS_ADDR_COST + accKeys * ACCESS_KEY_COST
    }
    case 'call': {
      const BASE = 53000n
      const PER_BYTE = 16n
      return BASE + BigInt(dataLen) * PER_BYTE
        + accEntries * ACCESS_ADDR_COST + accKeys * ACCESS_KEY_COST
    }
    case 'deploy': {
      const BASE = 120000n
      const PER_BYTE = 20n
      return BASE + BigInt(dataLen) * PER_BYTE
        + accEntries * ACCESS_ADDR_COST + accKeys * ACCESS_KEY_COST
    }
    default:
      // exhaustive guard; TypeScript should never hit this
      return 50000n
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

function assertAddr(a: string): string {
  // Throws with a clear message if invalid
  assertAddress(a)
  return a
}

function toBig(x: Bigish): bigint {
  if (typeof x === 'bigint') return x
  if (typeof x === 'number') {
    if (!Number.isFinite(x) || x < 0) throw new Error('Invalid number for BigInt')
    return BigInt(Math.trunc(x))
  }
  if (typeof x === 'string') {
    if (x.startsWith('0x') || x.startsWith('0X')) return BigInt(x)
    // decimal string
    if (!/^\d+$/.test(x)) throw new Error('Invalid BigInt decimal string')
    return BigInt(x)
  }
  throw new Error('Value must be bigint | number | string')
}

function normalizeData(d?: Uint8Array | string): Uint8Array | undefined {
  if (d === undefined) return undefined
  if (d instanceof Uint8Array) return d
  if (typeof d === 'string') {
    if (d.startsWith('0x') || d.startsWith('0X')) return hexToBytes(d)
    // treat as UTF-8 string payload (developer convenience)
    const enc = new TextEncoder()
    return enc.encode(d)
  }
  throw new Error('data must be Uint8Array | hex string | utf8 string')
}

function normalizeAccessList(al?: AccessList): AccessList {
  if (!al) return []
  return al.map((e) => {
    assertAddress(e.address)
    return {
      address: e.address,
      storageKeys: (e.storageKeys ?? []).map((k) => {
        if (typeof k !== 'string' || !k.startsWith('0x')) {
          throw new Error('storageKeys must be hex strings (0x…)')
        }
        return k.toLowerCase()
      })
    }
  })
}

function emptyOr<T extends any[]>(arr?: T): T | undefined {
  return arr && arr.length > 0 ? arr : undefined
}

function addPercent(x: bigint, pct: bigint): bigint {
  return (x * (100n + pct)) / 100n
}

function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length)
  out.set(a, 0)
  out.set(b, a.length)
  return out
}

// Optional small helper: produce a deterministic "sign bytes" domain tag
// for debugging or pre-sign hashing previews.
export function previewSignBytes(tx: UnsignedTx): string {
  // Not consensus-critical; just a developer-facing preview
  const enc = new TextEncoder()
  const parts: Uint8Array[] = []
  parts.push(enc.encode(tx.kind))
  parts.push(enc.encode(String(tx.chainId)))
  parts.push(enc.encode(tx.from))
  if (tx.to) parts.push(enc.encode(tx.to))
  parts.push(enc.encode(tx.nonce.toString()))
  parts.push(enc.encode(tx.gasPrice.toString()))
  parts.push(enc.encode(tx.gasLimit.toString()))
  if (tx.value !== undefined) parts.push(enc.encode(tx.value.toString()))
  if (tx.data) parts.push(tx.data)
  const joined = parts.reduce((acc, cur) => concatBytes(acc, cur), new Uint8Array())
  return '0x' + bytesToHex(sha3_256(joined))
}

export default {
  buildTransfer,
  buildCall,
  buildDeploy,
  estimateIntrinsicGas,
  previewSignBytes
}
