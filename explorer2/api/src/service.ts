import type { AddressSummary, BlockDetail, BlockSummary, HeadView, MempoolView, TxDetail } from '@animica/explorer2-shared'
import { RequestCoalescer } from './cache.js'
import { HttpError } from './errors.js'
import { normalizeBlockDetail, normalizeBlockSummary, normalizeHead, normalizeTxDetail, normalizeTxSummary } from './normalize.js'
import { clampLimit, nextCursorForHeight, parseCursor } from './pagination.js'
import { normalizeTxHash } from './txHash.js'
import pino from 'pino'

const log = pino({ name: 'explorer-service' })
export interface ChainClient {
  getHead: () => Promise<unknown>
  getBlockByNumber: (height: number | string, includeTxs?: boolean, includeReceipts?: boolean) => Promise<unknown>
  getBlockByHash: (hash: string, includeTxs?: boolean, includeReceipts?: boolean) => Promise<unknown>
  getTransactionByHash: (hash: string) => Promise<unknown>
  getTransactionReceipt: (hash: string) => Promise<unknown>
  getMempoolPending: () => Promise<string[]>
  getMempoolStats: () => Promise<{ count: number; totalBytes: number; oldestAgeSec: number | null }>
  getPeers: () => Promise<unknown[]>
  getBalance: (address: string, tag?: 'latest' | 'pending') => Promise<string>
  getRichList?: (limit: number, offset: number) => Promise<unknown>
  getTotalSupply?: () => Promise<unknown>
}

const RECENT_BLOCK_WINDOW = 20
const ADDRESS_SCAN_WINDOW = 50

export class ExplorerService {
  private coalescer = new RequestCoalescer()

  constructor(
    private rpc: ChainClient
  ) {}

  async getHead(): Promise<{ head: HeadView; stats: any }> {
    return this.coalescer.run('head', async () => {
      const headRaw = await this.safeRpc(() => this.rpc.getHead())
      const head = normalizeHead(headRaw)

      const [blocks, mempool, peers] = await Promise.all([
        this.getRecentBlocks(head.height),
        this.safeRpc(() => this.rpc.getMempoolStats()).catch(() => null),
        this.safeRpc(() => this.rpc.getPeers()).catch(() => [])
      ])

      const stats = buildNetworkStats(blocks, mempool, peers)
      return { head, stats }
    })
  }

  async getBlocks(limitInput: number, cursor?: string): Promise<{ items: BlockSummary[]; nextCursor: string | null }> {
    const limit = clampLimit(limitInput)
    const cursorHeight = parseCursor(cursor)
    const headRaw = await this.safeRpc(() => this.rpc.getHead())
    const head = normalizeHead(headRaw)
    const startHeight = cursorHeight ?? head.height
    const heights = Array.from({ length: limit }, (_, i) => startHeight - i).filter((h) => h >= 0)

    const blocks = await Promise.all(
      heights.map((height) =>
        this.coalescer.run(`block:${height}`, async () => {
          const raw = await this.safeRpc(() => this.rpc.getBlockByNumber(height, false, false))
          return normalizeBlockSummary(raw)
        })
      )
    )

    const minHeight = heights.length ? heights[heights.length - 1] : startHeight
    return { items: blocks, nextCursor: nextCursorForHeight(minHeight) }
  }

  async getBlockDetail(hashOrHeight: string): Promise<BlockDetail> {
    const cacheKey = `block-detail:${hashOrHeight}`
    return this.coalescer.run(cacheKey, async () => {
      const raw = await this.safeRpc(
        () =>
          isNumeric(hashOrHeight)
            ? this.rpc.getBlockByNumber(Number(hashOrHeight), true, false)
            : this.rpc.getBlockByHash(hashOrHeight, true, false),
        { allowNotFound: true }
      )
      if (!raw) throw new HttpError(404, 'Block not found')
      return normalizeBlockDetail(raw)
    })
  }

  async getTxDetail(hash: string): Promise<TxDetail> {
    const normalizedHash = normalizeTxHash(hash)
    const cacheKey = `tx:${normalizedHash}`
    return this.coalescer.run(cacheKey, async () => {
      const tx = await this.safeRpc(() => this.rpc.getTransactionByHash(normalizedHash)).catch(() => null)
      const receipt = await this.safeRpc(() => this.rpc.getTransactionReceipt(normalizedHash)).catch(() => null)

      if (!tx && !receipt) {
        const pending = await this.safeRpc(() => this.rpc.getMempoolPending()).catch(() => [])
        const pendingMatch = pending.some((h) => normalizeTxHash(h) === normalizedHash)
        if (pendingMatch) {
          return normalizeTxDetail({ hash: normalizedHash, status: 'pending' }, null)
        }
        throw new HttpError(404, 'Transaction not found')
      }
      return normalizeTxDetail(tx ?? { hash: normalizedHash }, receipt)
    })
  }

  async getAddressDetail(address: string, limitInput: number, cursor?: string): Promise<AddressSummary> {
    const limit = clampLimit(limitInput)
    const [confirmedBalance, pendingBalance] = await Promise.all([
      this.safeRpc(() => this.rpc.getBalance(address, 'latest')).catch(() => null),
      this.safeRpc(() => this.rpc.getBalance(address, 'pending')).catch(() => null)
    ])

    const headRaw = await this.safeRpc(() => this.rpc.getHead())
    const head = normalizeHead(headRaw)
    const cursorHeight = parseCursor(cursor)
    const startHeight = cursorHeight ?? head.height
    const heights = Array.from({ length: ADDRESS_SCAN_WINDOW }, (_, i) => startHeight - i).filter((h) => h >= 0)

    const txs: any[] = []
    for (const height of heights) {
      const block = await this.safeRpc(() => this.rpc.getBlockByNumber(height, true, false)).catch(() => null)
      if (!block) continue
      
      const blockTxs = Array.isArray((block as any)?.txs) ? (block as any).txs : []
      for (const tx of blockTxs) {
        const summary = normalizeTxSummary(tx)
        if (summary.from === address || summary.to === address) {
          summary.status = 'confirmed'
          txs.push(summary)
        }
      }
      if (txs.length >= limit) break
    }

    const nextHeight = heights.length ? heights[heights.length - 1] : startHeight
    return {
      address,
      confirmedBalance,
      pendingBalance,
      txs: txs.slice(0, limit),
      nextCursor: nextCursorForHeight(nextHeight),
      scannedBlocks: heights.length,
      partial: true
    }
  }

  async getMempool(limitInput: number, cursor?: string): Promise<MempoolView> {
    const limit = clampLimit(limitInput, 1000)
    const pending = await this.safeRpc(() => this.rpc.getMempoolPending())
    const stats = await this.safeRpc(() => this.rpc.getMempoolStats()).catch(() => null)
    const start = parseCursor(cursor) ?? 0
    const slice = pending.slice(start, start + limit)
    const nextCursor = start + limit < pending.length ? String(start + limit) : null

    return {
      total: pending.length,
      entries: slice.map((hash) => ({ hash })),
      nextCursor,
      stats: stats ?? undefined
    }
  }

  async search(query: string): Promise<{ type: 'block' | 'tx' | 'address'; result: unknown } | { type: 'none' }> {
    const trimmed = query.trim()
    if (!trimmed) {
      return { type: 'none' }
    }

    // Try to detect what the user is searching for
    // Address: starts with anim1
    if (/^anim1/i.test(trimmed)) {
      try {
        const address = await this.getAddressDetail(trimmed, 10)
        return { type: 'address', result: address }
      } catch {
        return { type: 'none' }
      }
    }

    // Block by height: numeric only
    if (/^[0-9]+$/.test(trimmed)) {
      try {
        const block = await this.getBlockDetail(trimmed)
        return { type: 'block', result: block }
      } catch {
        return { type: 'none' }
      }
    }

    // Transaction or block hash: 0x...
    if (/^0x[a-fA-F0-9]+$/.test(trimmed)) {
      // Try transaction first
      try {
        const tx = await this.getTxDetail(trimmed)
        return { type: 'tx', result: tx }
      } catch {
        // Try block hash
        try {
          const block = await this.getBlockDetail(trimmed)
          return { type: 'block', result: block }
        } catch {
          return { type: 'none' }
        }
      }
    }

    return { type: 'none' }
  }

  async getRichList(limitInput: number, offset: number = 0): Promise<import('@animica/explorer2-shared').RichListResponse> {
    const limit = clampLimit(limitInput)
    const safeOffset = Math.max(0, offset)

    return this.coalescer.run(`richlist:${limit}:${safeOffset}`, async () => {
      // Try RPC method if available
      if (this.rpc.getRichList) {
        try {
          const raw = await this.safeRpc(() => this.rpc.getRichList!(limit, safeOffset))
          
          // Parse response
          const height = (raw as any).height ?? 0
          const totalAddresses = (raw as any).totalAddresses ?? 0
          const items = (raw as any).items ?? []
          
          // Get total supply for percentage calculation
          let totalSupply = BigInt(0)
          try {
            const supplyRaw = await this.safeRpc(() => this.rpc.getTotalSupply!())
            const supplyHex = (supplyRaw as any).totalSupply ?? '0x0'
            totalSupply = BigInt(supplyHex)
          } catch {
            // If total supply fails, percentages will be 0
          }
          
          // Format items with percentages
          const formattedItems = items.map((item: any) => {
            const balance = BigInt(item.balance ?? '0x0')
            const pctSupply = totalSupply > 0 
              ? Number((balance * BigInt(10000) / totalSupply)) / 100  // 2 decimal places
              : 0
            
            return {
              rank: item.rank ?? 0,
              address: item.address ?? '',
              balance: item.balance ?? '0x0',
              pctSupply
            }
          })
          
          return {
            height,
            items: formattedItems,
            totalAddresses,
            nextOffset: safeOffset + formattedItems.length < totalAddresses 
              ? safeOffset + formattedItems.length 
              : undefined
          }
        } catch (error) {
          // Log the actual error before falling back
          log.warn({ error, limit, safeOffset }, 'getRichList RPC call failed')
          // Fall through to local implementation if RPC fails
        }
      }
      
      // Fallback: local implementation would go here
      // For now, return empty result
      throw new HttpError(501, 'Rich list not available', 'Node does not support state.getRichList RPC method')
    })
  }

  async getRichListSummary(): Promise<import('@animica/explorer2-shared').RichListSummary> {
    return this.coalescer.run('richlist:summary', async () => {
      // Try RPC method if available
      if (this.rpc.getTotalSupply) {
        try {
          const raw = await this.safeRpc(() => this.rpc.getTotalSupply!())
          
          const height = (raw as any).height ?? 0
          const totalSupply = (raw as any).totalSupply ?? '0x0'
          const addressCount = (raw as any).addressCount ?? 0
          
          // Get top addresses to compute concentration metrics
          let top10Pct: number | undefined
          let top100Pct: number | undefined
          let top1000Pct: number | undefined
          
          try {
            const totalSupplyBig = BigInt(totalSupply)
            
            // Get top 10
            if (this.rpc.getRichList) {
              const top10Raw = await this.safeRpc(() => this.rpc.getRichList!(10, 0))
              const top10Items = (top10Raw as any).items ?? []
              const top10Sum = top10Items.reduce((sum: bigint, item: any) => 
                sum + BigInt(item.balance ?? '0x0'), BigInt(0))
              top10Pct = totalSupplyBig > 0 
                ? Number((top10Sum * BigInt(10000) / totalSupplyBig)) / 100
                : 0
              
              // Get top 100
              const top100Raw = await this.safeRpc(() => this.rpc.getRichList!(100, 0))
              const top100Items = (top100Raw as any).items ?? []
              const top100Sum = top100Items.reduce((sum: bigint, item: any) => 
                sum + BigInt(item.balance ?? '0x0'), BigInt(0))
              top100Pct = totalSupplyBig > 0 
                ? Number((top100Sum * BigInt(10000) / totalSupplyBig)) / 100
                : 0
              
              // Get top 1000 (if addressCount >= 1000)
              if (addressCount >= 1000) {
                const top1000Raw = await this.safeRpc(() => this.rpc.getRichList!(1000, 0))
                const top1000Items = (top1000Raw as any).items ?? []
                const top1000Sum = top1000Items.reduce((sum: bigint, item: any) => 
                  sum + BigInt(item.balance ?? '0x0'), BigInt(0))
                top1000Pct = totalSupplyBig > 0 
                  ? Number((top1000Sum * BigInt(10000) / totalSupplyBig)) / 100
                  : 0
              }
            }
          } catch {
            // Concentration metrics optional - if getRichList fails, just skip
            log.debug('Failed to compute concentration metrics (getRichList unavailable)')
          }
          
          return {
            height,
            totalSupply,
            addressCount,
            top10Pct,
            top100Pct,
            top1000Pct
          }
        } catch (error) {
          log.warn({ error }, 'getTotalSupply RPC call failed')
          throw new HttpError(501, 'Total supply not available', 'Node does not support state.getTotalSupply RPC method')
        }
      }
      
      throw new HttpError(501, 'Rich list summary not available', 'Node does not support required RPC methods')
    })
  }

  private async getRecentBlocks(headHeight: number): Promise<BlockSummary[]> {
    const heights = Array.from({ length: RECENT_BLOCK_WINDOW }, (_, i) => headHeight - i).filter((h) => h >= 0)
    const blocks = await Promise.all(
      heights.map(async (height) => {
        const raw = await this.safeRpc(() => this.rpc.getBlockByNumber(height, false, false)).catch(() => null)
        if (!raw) return null
        return normalizeBlockSummary(raw)
      })
    )
    return blocks.filter((block): block is BlockSummary => block !== null)
  }

  private async safeRpc<T>(fn: () => Promise<T>): Promise<T>
  private async safeRpc<T>(fn: () => Promise<T>, options: { allowNotFound: true }): Promise<T | null>
  private async safeRpc<T>(fn: () => Promise<T>, options?: { allowNotFound?: boolean }): Promise<T | null> {
    try {
      return await fn()
    } catch (error: any) {
      const message = error?.message ?? String(error)
      if (options?.allowNotFound && isNotFoundError(message)) {
        return null
      }
      throw new HttpError(503, 'RPC unavailable', message)
    }
  }
}

function buildNetworkStats(blocks: BlockSummary[], mempool: any, peers: any): any {
  const heights = blocks.map((b) => b.height)
  const times = blocks.map((b) => b.time).filter((t) => t > 0)
  const sortedTimes = [...times].sort((a, b) => a - b)
  const avgBlockTime =
    sortedTimes.length > 1 ? (sortedTimes[sortedTimes.length - 1] - sortedTimes[0]) / (sortedTimes.length - 1) : null
  const txCount = blocks.reduce((sum, b) => sum + b.txCount, 0)
  const tps = avgBlockTime && avgBlockTime > 0 ? txCount / (avgBlockTime * blocks.length) : null

  const peerList = Array.isArray(peers) ? peers : []
  const inbound = peerList.filter((p) => p?.direction === 'inbound').length
  const outbound = peerList.filter((p) => p?.direction === 'outbound').length

  return {
    peerCount: peerList.length,
    inboundPeers: inbound || null,
    outboundPeers: outbound || null,
    mempoolSize: mempool?.count ?? null,
    tps,
    avgBlockTime
  }
}

function isNumeric(value: string): boolean {
  return /^[0-9]+$/.test(value)
}

function isNotFoundError(message: string): boolean {
  return /not found|unknown block|missing|does not exist/i.test(message)
}
