import type { Address, BlockDetail, BlockSummary, HeadView, TxDetail, TxSummary } from '@animica/explorer2-shared'
import { addressToBech32 } from './utils/bech32.js'
import { decode as cborDecode } from 'cbor'

const HEX_PREFIX = /^0x/i

function toNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    if (HEX_PREFIX.test(value)) {
      const parsed = Number.parseInt(value, 16)
      return Number.isNaN(parsed) ? undefined : parsed
    }
    const parsed = Number.parseInt(value, 10)
    return Number.isNaN(parsed) ? undefined : parsed
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

function normalizeAddress(value: unknown): Address | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value === 'string') return value
  if (typeof value === 'number') return `0x${value.toString(16)}`
  if (typeof value === 'bigint') return `0x${value.toString(16)}`
  if (value instanceof Uint8Array) return addressToBech32(value)
  return undefined
}

function getHeader(block: any): any {
  if (!block) return null
  return block.header ?? block
}

/**
 * Extract miner address from block header's extra field.
 * The extra field is CBOR-encoded and may contain {coinbase: bytes, ...}
 */
function extractMinerFromExtra(header: any): Address | undefined {
  try {
    const extra = header?.extra
    if (!extra) return undefined
    
    // Convert extra to Buffer if needed
    let extraBuffer: Buffer
    if (Buffer.isBuffer(extra)) {
      extraBuffer = extra
    } else if (extra instanceof Uint8Array) {
      extraBuffer = Buffer.from(extra)
    } else if (typeof extra === 'string') {
      // Handle hex string
      const hexStr = extra.startsWith('0x') ? extra.slice(2) : extra
      extraBuffer = Buffer.from(hexStr, 'hex')
    } else {
      return undefined
    }
    
    // Skip empty extra field
    if (extraBuffer.length === 0) return undefined
    
    // Decode CBOR
    const decoded = cborDecode(extraBuffer) as any
    if (decoded && decoded.coinbase) {
      // Coinbase is the miner address
      return normalizeAddress(decoded.coinbase)
    }
    
    return undefined
  } catch (error) {
    // Silently fail if extra field cannot be decoded
    return undefined
  }
}

export function normalizeHead(head: any): HeadView {
  return {
    height: toNumber(head?.height ?? head?.number) ?? 0,
    canonicalHeight: toNumber(head?.canonicalHeight ?? head?.canonical_height),
    hash: head?.hash ?? head?.headerHash ?? '0x0',
    time: toNumber(head?.time ?? head?.timestamp ?? head?.header?.time) ?? 0,
    chainId: toNumber(head?.chainId)
  }
}

export function normalizeBlockSummary(block: any): BlockSummary {
  const header = getHeader(block)
  const height = toNumber(header?.height ?? header?.number ?? block?.number) ?? 0
  const canonicalHeight = toNumber(header?.canonicalHeight ?? header?.canonical_height ?? block?.canonicalHeight ?? block?.canonical_height)
  const hash = header?.hash ?? header?.headerHash ?? block?.hash ?? '0x0'
  const time = toNumber(header?.time ?? header?.timestamp ?? block?.time) ?? 0
  const txs = Array.isArray(block?.txs) ? block.txs : Array.isArray(block?.transactions) ? block.transactions : []
  
  // Try to get miner from header.miner first, then from extra field
  const miner = normalizeAddress(header?.miner) ?? extractMinerFromExtra(header)
  
  return {
    height,
    canonicalHeight,
    hash,
    time,
    txCount: txs.length,
    miner,
    orphaned: block?.orphaned ?? header?.orphaned
  }
}

export function normalizeTxSummary(tx: any): TxSummary {
  if (typeof tx === 'string') {
    return { hash: tx }
  }
  return {
    hash: tx?.hash ?? tx?.txHash ?? '0x0',
    from: normalizeAddress(tx?.from),
    to: normalizeAddress(tx?.to),
    nonce: toNumber(tx?.nonce) ?? tx?.nonce,
    value: toStringValue(tx?.value)
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
    time: toNumber(header?.time ?? header?.timestamp ?? block?.time) ?? 0,
    chainId: toNumber(header?.chainId),
    difficulty: header?.difficulty ?? header?.target ?? header?.thetaMicro ?? null,
    nonce: toNumber(header?.nonce),
    orphaned: block?.orphaned ?? header?.orphaned,
    txs: txs.map(normalizeTxSummary),
    raw: block
  }
}

export function normalizeTxDetail(tx: any, receipt: any | null): TxDetail {
  const hash = tx?.hash ?? tx?.txHash ?? receipt?.txHash ?? '0x0'
  const blockHeight = toNumber(receipt?.blockNumber ?? tx?.blockNumber)
  const statusRaw = receipt?.status ?? tx?.status
  const status = statusRaw === 'REVERT' || statusRaw === 'OOG' ? 'failed' : blockHeight ? 'confirmed' : 'pending'
  return {
    hash,
    status,
    blockHash: receipt?.blockHash ?? tx?.blockHash,
    blockHeight,
    from: normalizeAddress(tx?.from),
    to: normalizeAddress(tx?.to),
    value: toStringValue(tx?.value),
    gasUsed: toStringValue(receipt?.gasUsed),
    feePaid: toStringValue(receipt?.feePaid ?? receipt?.fee),
    raw: tx,
    receipt
  }
}

export function isHexLike(value: string): boolean {
  return HEX_PREFIX.test(value)
}

export function normalizeRichList(data: any): any {
  const entries = Array.isArray(data?.entries) ? data.entries : []
  return {
    entries: entries.map((entry: any) => ({
      address: normalizeAddress(entry?.address) ?? '0x0',
      balance: entry?.balance ?? '0x0',
      percentage: typeof entry?.percentage === 'number' ? entry.percentage : 0
    })),
    totalSupply: data?.totalSupply ?? '0x0',
    totalAccounts: toNumber(data?.totalAccounts) ?? 0,
    hasMore: Boolean(data?.hasMore)
  }
}
