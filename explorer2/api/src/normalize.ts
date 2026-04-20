import type { Address, BlockDetail, BlockSummary, HeadView, TxDetail, TxSummary } from '@animica/explorer2-shared'
import { bech32m } from 'bech32'

const HEX_PREFIX = /^0x/i
const HEX_BODY = /^[0-9a-f]+$/i
const ADDRESS_HRP = 'anim'
const DEFAULT_ADDRESS_ALG_ID = 0x1001

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
}

function getPath(root: unknown, path: string[]): unknown {
  let cursor: unknown = root
  for (const segment of path) {
    const record = asRecord(cursor)
    if (!record) return undefined
    cursor = record[segment]
  }
  return cursor
}

function firstPathValue(root: unknown, paths: string[][]): unknown {
  for (const path of paths) {
    const value = getPath(root, path)
    if (value !== undefined && value !== null) return value
  }
  return undefined
}

function toNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return undefined
    if (/^0x[0-9a-f]+$/i.test(trimmed)) {
      const parsed = Number.parseInt(trimmed, 16)
      return Number.isNaN(parsed) ? undefined : parsed
    }
    if (!/^-?[0-9]+$/.test(trimmed)) return undefined
    const parsed = Number.parseInt(trimmed, 10)
    return Number.isNaN(parsed) ? undefined : parsed
  }
  return undefined
}

function toTimestampSeconds(value: unknown): number | undefined {
  const parsed = toNumber(value)
  if (parsed === undefined) return undefined

  const abs = Math.abs(parsed)
  // Normalize common epoch encodings to seconds.
  // - >= 1e18: nanoseconds
  // - >= 1e15: microseconds
  // - >= 1e12: milliseconds
  if (abs >= 1e18) return Math.trunc(parsed / 1_000_000_000)
  if (abs >= 1e15) return Math.trunc(parsed / 1_000_000)
  if (abs >= 1e12) return Math.trunc(parsed / 1_000)
  return Math.trunc(parsed)
}

function firstTimestampSeconds(...values: unknown[]): number | undefined {
  for (const value of values) {
    const normalized = toTimestampSeconds(value)
    if (normalized !== undefined) return normalized
  }
  return undefined
}

function toStringValue(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  if (typeof value === 'bigint') return value.toString()
  return undefined
}

function toHex(value: Uint8Array): string {
  return `0x${Buffer.from(value).toString('hex')}`
}

function normalizeHexString(value: string): string | null {
  let normalized = value.trim()
  if (!normalized) return null
  if (HEX_PREFIX.test(normalized)) normalized = normalized.slice(2)
  if (!normalized || !HEX_BODY.test(normalized)) return null
  if (normalized.length % 2 === 1) normalized = `0${normalized}`
  return normalized.toLowerCase()
}

function parseBigInt(value: unknown): bigint | null {
  if (typeof value === 'bigint') return value >= 0n ? value : null
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return BigInt(Math.floor(value))
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return null
    try {
      const parsed = BigInt(trimmed)
      return parsed >= 0n ? parsed : null
    } catch {
      return null
    }
  }
  return null
}

function valueToBytes(value: unknown): Uint8Array | null {
  if (value instanceof Uint8Array) return value
  if (Buffer.isBuffer(value)) return Uint8Array.from(value)
  if (Array.isArray(value) && value.every((item) => typeof item === 'number' && Number.isInteger(item) && item >= 0 && item <= 255)) {
    return Uint8Array.from(value)
  }
  if (typeof value !== 'string') return null

  const trimmed = value.trim()
  if (!trimmed) return null
  if (/^anim1/i.test(trimmed)) {
    try {
      const decoded = bech32m.decode(trimmed.toLowerCase())
      return Uint8Array.from(bech32m.fromWords(decoded.words))
    } catch {
      return null
    }
  }

  const normalizedHex = normalizeHexString(trimmed)
  if (!normalizedHex) return null
  return Uint8Array.from(Buffer.from(normalizedHex, 'hex'))
}

function digestFromAddressBytes(bytes: Uint8Array): Uint8Array | null {
  if (bytes.length === 20) {
    const out = new Uint8Array(32)
    out.set(bytes, 12)
    return out
  }
  if (bytes.length >= 32) return bytes.slice(bytes.length - 32)
  return null
}

function withDefaultAlgPrefix(digest: Uint8Array): Uint8Array {
  const payload = new Uint8Array(34)
  payload[0] = (DEFAULT_ADDRESS_ALG_ID >> 8) & 0xff
  payload[1] = DEFAULT_ADDRESS_ALG_ID & 0xff
  payload.set(digest, 2)
  return payload
}

function encodeAddressPayload(payload: Uint8Array): string | null {
  try {
    return bech32m.encode(ADDRESS_HRP, bech32m.toWords(Buffer.from(payload))).toLowerCase()
  } catch {
    return null
  }
}

export function canonicalAddressKey(value: unknown): string | null {
  if (value === undefined || value === null) return null
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return null
    if (trimmed.toLowerCase().startsWith('system:')) return trimmed.toLowerCase()
  }

  const bytes = valueToBytes(value)
  if (!bytes) {
    if (typeof value === 'string') return value.trim().toLowerCase()
    return null
  }

  const digest = digestFromAddressBytes(bytes)
  if (!digest) return null
  return toHex(digest).toLowerCase()
}

export function normalizeAddress(value: unknown): Address | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value === 'number') return `0x${value.toString(16)}`
  if (typeof value === 'bigint') return `0x${value.toString(16)}`

  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return undefined
    if (trimmed.toLowerCase().startsWith('system:')) return trimmed.toLowerCase()

    if (/^anim1/i.test(trimmed)) {
      try {
        bech32m.decode(trimmed.toLowerCase())
        return trimmed.toLowerCase()
      } catch {
        // Fall through and try generic parsing.
      }
    }

    const bytes = valueToBytes(trimmed)
    if (!bytes) return trimmed

    if (bytes.length === 34) {
      const encoded = encodeAddressPayload(bytes)
      if (encoded) return encoded
    }

    const digest = digestFromAddressBytes(bytes)
    if (digest) {
      const encoded = encodeAddressPayload(withDefaultAlgPrefix(digest))
      if (encoded) return encoded
    }

    return toHex(bytes)
  }

  const bytes = valueToBytes(value)
  if (!bytes) return undefined

  if (bytes.length === 34) {
    const encoded = encodeAddressPayload(bytes)
    if (encoded) return encoded
  }

  const digest = digestFromAddressBytes(bytes)
  if (digest) {
    const encoded = encodeAddressPayload(withDefaultAlgPrefix(digest))
    if (encoded) return encoded
  }

  return toHex(bytes)
}

function extractTxFrom(tx: any): unknown {
  return firstPathValue(tx, [
    ['from'],
    ['sender'],
    ['from_'],
    ['frm'],
    ['tx', 'from'],
    ['tx', 'sender'],
    ['body', 'from'],
    ['body', 'sender'],
    ['payload', 'v', 'from'],
    ['tx', 'payload', 'v', 'from'],
    ['body', 'payload', 'v', 'from']
  ])
}

function extractTxTo(tx: any): unknown {
  return firstPathValue(tx, [
    ['to'],
    ['recipient'],
    ['tx', 'to'],
    ['tx', 'recipient'],
    ['body', 'to'],
    ['body', 'recipient'],
    ['payload', 'to'],
    ['payload', 'v', 'to'],
    ['tx', 'payload', 'to'],
    ['tx', 'payload', 'v', 'to'],
    ['body', 'payload', 'to'],
    ['body', 'payload', 'v', 'to']
  ])
}

function extractRewardValue(tx: any): string | undefined {
  const candidates = [
    tx?.rewards,
    tx?.tx?.rewards,
    tx?.body?.rewards,
    tx?.payload?.v?.rewards,
    tx?.tx?.payload?.v?.rewards,
    tx?.body?.payload?.v?.rewards
  ]
  for (const candidate of candidates) {
    if (!Array.isArray(candidate) || candidate.length === 0) continue
    let total = 0n
    let found = false
    for (const reward of candidate) {
      const amountRaw = firstPathValue(reward, [['amount'], ['value'], ['reward']])
      const amount = parseBigInt(amountRaw)
      if (amount === null) continue
      total += amount
      found = true
    }
    if (found) return total.toString()
  }
  return undefined
}

function extractRewardRecipient(tx: any): unknown {
  const candidates = [
    tx?.rewards,
    tx?.tx?.rewards,
    tx?.body?.rewards,
    tx?.payload?.v?.rewards,
    tx?.tx?.payload?.v?.rewards,
    tx?.body?.payload?.v?.rewards
  ]
  for (const candidate of candidates) {
    if (!Array.isArray(candidate) || candidate.length === 0) continue
    const firstReward = candidate[0]
    const recipient = firstPathValue(firstReward, [['to'], ['address'], ['recipient'], ['miner'], ['payout']])
    if (recipient !== undefined && recipient !== null) return recipient
  }
  return undefined
}

function extractTxValue(tx: any): string | undefined {
  const direct = firstPathValue(tx, [
    ['value'],
    ['amount'],
    ['transferAmount'],
    ['tx', 'value'],
    ['tx', 'amount'],
    ['body', 'value'],
    ['body', 'amount'],
    ['payload', 'v', 'value'],
    ['payload', 'v', 'amount'],
    ['tx', 'payload', 'v', 'value'],
    ['tx', 'payload', 'v', 'amount'],
    ['body', 'payload', 'v', 'value'],
    ['body', 'payload', 'v', 'amount'],
    ['receipt', 'value'],
    ['receipt', 'amount']
  ])
  const directText = toStringValue(direct)
  if (directText !== undefined) return directText
  return extractRewardValue(tx)
}

function inferMinerAddress(header: any, block: any, txs: any[]): Address | undefined {
  const direct = normalizeAddress(
    header?.miner ??
      header?.coinbase ??
      header?.proposer ??
      header?.beneficiary ??
      block?.miner ??
      block?.coinbase ??
      block?.proposer ??
      block?.beneficiary
  )
  if (direct) return direct

  for (const tx of txs.slice(0, 3)) {
    const kindRaw = firstPathValue(tx, [
      ['kind'],
      ['type'],
      ['tx', 'kind'],
      ['tx', 'type'],
      ['body', 'kind'],
      ['body', 'type']
    ])
    const kind = typeof kindRaw === 'string' ? kindRaw.toLowerCase() : ''
    const fromKey = canonicalAddressKey(extractTxFrom(tx))
    const likelyCoinbase =
      kind.includes('coinbase') ||
      kind.includes('reward') ||
      kind.includes('miner') ||
      fromKey === `0x${'0'.repeat(64)}`

    if (!likelyCoinbase) continue

    const fromTo = normalizeAddress(extractTxTo(tx))
    if (fromTo) return fromTo
    const rewardRecipient = normalizeAddress(extractRewardRecipient(tx))
    if (rewardRecipient) return rewardRecipient
  }

  return undefined
}

function getHeader(block: any): any {
  if (!block) return null
  return block.header ?? block
}

export function normalizeHead(head: any): HeadView {
  return {
    height: toNumber(head?.height ?? head?.number) ?? 0,
    canonicalHeight: toNumber(head?.canonicalHeight ?? head?.canonical_height),
    hash: head?.hash ?? head?.headerHash ?? '0x0',
    time:
      firstTimestampSeconds(
        head?.time,
        head?.timestamp,
        head?.ts,
        head?.header?.time,
        head?.header?.timestamp,
        head?.header?.ts
      ) ?? 0,
    chainId: toNumber(head?.chainId),
    thetaMicro: toNumber(
      head?.thetaMicro ??
        head?.theta_micro ??
        head?.header?.thetaMicro ??
        head?.header?.theta_micro
    )
  }
}

export function normalizeBlockSummary(block: any): BlockSummary {
  const header = getHeader(block)
  const height = toNumber(header?.height ?? header?.number ?? block?.number) ?? 0
  const canonicalHeight = toNumber(header?.canonicalHeight ?? header?.canonical_height ?? block?.canonicalHeight ?? block?.canonical_height)
  const hash = header?.hash ?? header?.headerHash ?? block?.hash ?? '0x0'
  const time =
    firstTimestampSeconds(
      header?.time,
      header?.timestamp,
      header?.ts,
      block?.time,
      block?.timestamp,
      block?.ts
    ) ?? 0
  const txs = Array.isArray(block?.txs) ? block.txs : Array.isArray(block?.transactions) ? block.transactions : []
  const txCount = toNumber(block?.txCount ?? block?.tx_count ?? header?.txCount ?? header?.tx_count) ?? txs.length
  return {
    height,
    canonicalHeight,
    hash,
    time,
    txCount,
    miner: inferMinerAddress(header, block, txs),
    thetaMicro: toNumber(
      header?.thetaMicro ??
        header?.theta_micro ??
        block?.thetaMicro ??
        block?.theta_micro
    )
  }
}

export function normalizeTxSummary(tx: any): TxSummary {
  if (typeof tx === 'string') {
    return { hash: tx }
  }
  return {
    hash: tx?.hash ?? tx?.txHash ?? tx?.txid ?? '0x0',
    from: normalizeAddress(extractTxFrom(tx)),
    to: normalizeAddress(extractTxTo(tx)),
    nonce: toNumber(tx?.nonce) ?? tx?.nonce,
    value: extractTxValue(tx)
  }
}

export function normalizeBlockDetail(block: any): BlockDetail {
  const header = getHeader(block)
  const txs = Array.isArray(block?.txs) ? block.txs : Array.isArray(block?.transactions) ? block.transactions : []
  return {
    height: toNumber(header?.height ?? header?.number ?? block?.number) ?? 0,
    canonicalHeight: toNumber(header?.canonicalHeight ?? header?.canonical_height ?? block?.canonicalHeight ?? block?.canonical_height),
    hash: header?.hash ?? header?.headerHash ?? block?.hash ?? '0x0',
    parentHash: header?.parentHash ?? header?.parent ?? header?.prevHash,
    time:
      firstTimestampSeconds(
        header?.time,
        header?.timestamp,
        header?.ts,
        block?.time,
        block?.timestamp,
        block?.ts
      ) ?? 0,
    chainId: toNumber(header?.chainId),
    difficulty: header?.difficulty ?? header?.target ?? header?.thetaMicro ?? null,
    nonce: toNumber(header?.nonce),
    miner: inferMinerAddress(header, block, txs),
    txs: txs.map(normalizeTxSummary),
    raw: block
  }
}

export function normalizeTxDetail(tx: any, receipt: any | null): TxDetail {
  const hash = tx?.hash ?? tx?.txHash ?? receipt?.txHash ?? '0x0'
  const blockHeight = toNumber(
    receipt?.blockNumber ??
      receipt?.block_height ??
      receipt?.blockHeight ??
      tx?.blockNumber ??
      tx?.block_height ??
      tx?.blockHeight
  )
  const statusRaw = receipt?.status ?? tx?.status
  const statusText = typeof statusRaw === 'string' ? statusRaw.toUpperCase() : statusRaw
  const failed = statusText === 'REVERT' || statusText === 'OOG' || statusText === 'FAILED' || statusText === 0
  const isIncluded = blockHeight !== undefined && blockHeight !== null
  const status = failed ? 'failed' : isIncluded ? 'confirmed' : 'pending'
  return {
    hash,
    status,
    blockHash: receipt?.blockHash ?? receipt?.block_hash ?? tx?.blockHash ?? tx?.block_hash,
    blockHeight,
    from: normalizeAddress(extractTxFrom(tx)),
    to: normalizeAddress(extractTxTo(tx)),
    value: extractTxValue(tx),
    gasUsed: toStringValue(receipt?.gasUsed),
    feePaid: toStringValue(receipt?.feePaid ?? receipt?.fee ?? tx?.feePaid ?? tx?.fee),
    raw: tx,
    receipt
  }
}

export function isHexLike(value: string): boolean {
  return HEX_PREFIX.test(value)
}
