import type { AddressSummary, BlockDetail, BlockSummary, HeadView, MempoolView, TxDetail } from '@animica/explorer2-shared'
import { RequestCoalescer, TtlCache } from './cache.js'
import { HttpError } from './errors.js'
import { normalizeBlockDetail, normalizeBlockSummary, normalizeHead, normalizeTxDetail, normalizeTxSummary } from './normalize.js'
import { clampLimit, nextCursorForHeight, parseCursor } from './pagination.js'
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
}

const RECENT_BLOCK_WINDOW = 20
const ADDRESS_SCAN_WINDOW = 50
const FINALIZED_BLOCK_DEPTH = 10 // Blocks older than this are considered finalized

export class ExplorerService {
  private cache: TtlCache
  private coalescer = new RequestCoalescer()

  constructor(
    private rpc: ChainClient,
    private cacheTtls: { head: number; blocks: number; tx: number },
    options?: { persistPath?: string }
  ) {
    this.cache = new TtlCache({ persistPath: options?.persistPath })
  }

  /**
   * Calculate cache TTL based on block age relative to head.
   * Recent blocks (within FINALIZED_BLOCK_DEPTH): short TTL (configured cacheTtls.blocks)
   * Finalized blocks (older than FINALIZED_BLOCK_DEPTH): very long TTL (24 hours)
   */
  private getBlockCacheTtl(blockHeight: number, headHeight: number): number {
    const age = headHeight - blockHeight
    if (age > FINALIZED_BLOCK_DEPTH) {
      // Finalized blocks: cache for 24 hours
      return 24 * 60 * 60 * 1000
    }
    // Recent blocks: use configured TTL
    return this.cacheTtls.blocks
  }

  async getHead(): Promise<{ head: HeadView; stats: any }> {
    return this.coalescer.run('head', async () => {
      const cached = this.cache.get<{ head: HeadView; stats: any }>('head')
      if (cached) return cached

      let headRaw: unknown
      try {
        headRaw = await this.safeRpc(() => this.rpc.getHead())
      } catch (err) {
        if (err instanceof HttpError && err.status === 503) {
          const cachedHead = this.cache.get<HeadView>('head-view')
          if (cachedHead) {
            const cachedStats = this.cache.get<any>('head-stats') ?? {}
            return { head: cachedHead, stats: cachedStats }
          }
        }
        throw err
      }

      const head = normalizeHead(headRaw)
      this.cache.set('head-view', head, this.cacheTtls.head)

      const [blocks, mempool, peers] = await Promise.all([
        this.getRecentBlocks(head.height),
        this.safeRpc(() => this.rpc.getMempoolStats()).catch(() => null),
        this.safeRpc(() => this.rpc.getPeers()).catch(() => [])
      ])

      const stats = buildNetworkStats(blocks, mempool, peers)
      const payload = { head, stats }
      this.cache.set('head', payload, this.cacheTtls.head)
      this.cache.set('head-stats', stats, this.cacheTtls.head)
      return payload
    })
  }

  async getBlocks(limitInput: number, cursor?: string): Promise<{ items: BlockSummary[]; nextCursor: string | null }> {
    const limit = clampLimit(limitInput)
    const cursorHeight = parseCursor(cursor)
    try {
      const headRaw = await this.safeRpc(() => this.rpc.getHead())
      const head = normalizeHead(headRaw)
      this.cache.set('head-view', head, this.cacheTtls.head)
      const startHeight = cursorHeight ?? head.height
      const heights = Array.from({ length: limit }, (_, i) => startHeight - i).filter((h) => h >= 0)

      const blocks = await Promise.all(
        heights.map((height) =>
          this.coalescer.run(`block:${height}`, async () => {
            const cached = this.cache.get<BlockSummary>(`block:${height}`)
            if (cached) return cached
            const raw = await this.safeRpc(() => this.rpc.getBlockByNumber(height, false, false))
            const summary = normalizeBlockSummary(raw)
            const ttl = this.getBlockCacheTtl(height, head.height)
            this.cache.set(`block:${height}`, summary, ttl)
            return summary
          })
        )
      )

      const minHeight = heights.length ? heights[heights.length - 1] : startHeight
      return { items: blocks, nextCursor: nextCursorForHeight(minHeight) }
    } catch (err) {
      if (err instanceof HttpError && err.status === 503) {
        const cachedHead = this.cache.get<HeadView>('head-view')
        if (!cachedHead) {
          return { items: [], nextCursor: null }
        }
        const startHeight = cursorHeight ?? cachedHead.height
        const heights = Array.from({ length: limit }, (_, i) => startHeight - i).filter((h) => h >= 0)
        const items = heights
          .map((height) => this.cache.get<BlockSummary>(`block:${height}`))
          .filter((block): block is BlockSummary => Boolean(block))

        const minHeight = heights.length ? heights[heights.length - 1] : startHeight
        return { items, nextCursor: items.length ? nextCursorForHeight(minHeight) : null }
      }
      throw err
    }
  }

  async getBlockDetail(hashOrHeight: string): Promise<BlockDetail> {
    const cacheKey = `block-detail:${hashOrHeight}`
    return this.coalescer.run(cacheKey, async () => {
      const cached = this.cache.get<BlockDetail>(cacheKey)
      if (cached) return cached
      const raw = await this.safeRpc(
        () =>
          isNumeric(hashOrHeight)
            ? this.rpc.getBlockByNumber(Number(hashOrHeight), true, false)
            : this.rpc.getBlockByHash(hashOrHeight, true, false),
        { allowNotFound: true }
      )
      if (!raw) throw new HttpError(404, 'Block not found')
      const detail = normalizeBlockDetail(raw)
      
      // Get head height to determine cache TTL
      const cachedHead = this.cache.get<HeadView>('head-view')
      const ttl = cachedHead && typeof detail.height === 'number'
        ? this.getBlockCacheTtl(detail.height, cachedHead.height)
        : this.cacheTtls.blocks
      
      this.cache.set(cacheKey, detail, ttl)
      return detail
    })
  }

  async getTxDetail(hash: string): Promise<TxDetail> {
    const cacheKey = `tx:${hash}`
    return this.coalescer.run(cacheKey, async () => {
      const cached = this.cache.get<TxDetail>(cacheKey)
      if (cached) return cached
      const tx = await this.safeRpc(() => this.rpc.getTransactionByHash(hash)).catch(() => null)
      const receipt = await this.safeRpc(() => this.rpc.getTransactionReceipt(hash)).catch(() => null)
      if (!tx && !receipt) throw new HttpError(404, 'Transaction not found')
      const detail = normalizeTxDetail(tx, receipt)
      this.cache.set(cacheKey, detail, this.cacheTtls.tx)
      return detail
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
      // Check cache first for block detail
      const cacheKey = `block-detail:${height}`
      let block = this.cache.get<BlockDetail>(cacheKey)
      
      if (!block) {
        block = await this.safeRpc(() => this.rpc.getBlockByNumber(height, true, false)).catch(() => null)
        if (block) {
          const ttl = this.getBlockCacheTtl(height, head.height)
          this.cache.set(cacheKey, block, ttl)
        }
      }
      
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
    try {
      const pending = await this.safeRpc(() => this.rpc.getMempoolPending())
      const stats = await this.safeRpc(() => this.rpc.getMempoolStats()).catch(() => null)
      this.cache.set('mempool-pending', pending, this.cacheTtls.head)
      if (stats) {
        this.cache.set('mempool-stats', stats, this.cacheTtls.head)
      }
      const start = parseCursor(cursor) ?? 0
      const slice = pending.slice(start, start + limit)
      const nextCursor = start + limit < pending.length ? String(start + limit) : null

      return {
        total: pending.length,
        entries: slice.map((hash) => ({ hash })),
        nextCursor,
        stats: stats ?? undefined
      }
    } catch (err) {
      if (err instanceof HttpError && err.status === 503) {
        const pending = this.cache.get<string[]>('mempool-pending') ?? []
        const stats = this.cache.get<{ count: number; totalBytes: number; oldestAgeSec: number | null }>(
          'mempool-stats'
        )
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
      throw err
    }
  }

  private async getRecentBlocks(headHeight: number): Promise<BlockSummary[]> {
    const heights = Array.from({ length: RECENT_BLOCK_WINDOW }, (_, i) => headHeight - i).filter((h) => h >= 0)
    const blocks = await Promise.all(
      heights.map(async (height) => {
        // Check cache first
        const cached = this.cache.get<BlockSummary>(`block:${height}`)
        if (cached) return cached
        
        // Fetch from RPC if not cached
        const raw = await this.safeRpc(() => this.rpc.getBlockByNumber(height, false, false)).catch(() => null)
        if (!raw) return null
        
        const summary = normalizeBlockSummary(raw)
        const ttl = this.getBlockCacheTtl(height, headHeight)
        this.cache.set(`block:${height}`, summary, ttl)
        return summary
      })
    )
    return blocks.filter(Boolean).map((block) => block as BlockSummary)
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
