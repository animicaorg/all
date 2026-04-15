import type { Address, BlockDetail, BlockSummary, HeadView, TxDetail, TxSummary } from '@animica/explorer2-shared'

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
  if (value instanceof Uint8Array) return toHex(value)
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
    time: toNumber(head?.time ?? head?.timestamp ?? head?.header?.time) ?? 0,
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
  const time = toNumber(header?.time ?? header?.timestamp ?? block?.time) ?? 0
  const txs = Array.isArray(block?.txs) ? block.txs : Array.isArray(block?.transactions) ? block.transactions : []
  return {
    height,
    canonicalHeight,
    hash,
    time,
    txCount: txs.length,
    miner: normalizeAddress(header?.miner),
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
    from: normalizeAddress(tx?.from ?? tx?.sender),
    to: normalizeAddress(tx?.to ?? tx?.recipient),
    value: toStringValue(tx?.value),
    gasUsed: toStringValue(receipt?.gasUsed),
    feePaid: toStringValue(receipt?.feePaid ?? receipt?.fee ?? tx?.feePaid ?? tx?.fee),
    raw: tx,
    receipt
  }
}

export function isHexLike(value: string): boolean {
  return HEX_PREFIX.test(value)
}
