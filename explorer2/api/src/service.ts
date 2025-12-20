import type { AddressSummary, BlockDetail, BlockSummary, HeadView, MempoolView, TxDetail } from '@animica/explorer2-shared'
import { RequestCoalescer, TtlCache } from './cache'
import { HttpError } from './errors'
import { normalizeBlockDetail, normalizeBlockSummary, normalizeHead, normalizeTxDetail, normalizeTxSummary } from './normalize'
import { clampLimit, nextCursorForHeight, parseCursor } from './pagination'
import { RpcClient } from './rpcClient'

const RECENT_BLOCK_WINDOW = 20
const ADDRESS_SCAN_WINDOW = 50

export class ExplorerService {
  private cache = new TtlCache()
  private coalescer = new RequestCoalescer()

  constructor(
    private rpc: RpcClient,
    private cacheTtls: { head: number; blocks: number; tx: number }
  ) {}

  async getHead(): Promise<{ head: HeadView; stats: any }> {
    return this.coalescer.run('head', async () => {
      const cached = this.cache.get<{ head: HeadView; stats: any }>('head')
      if (cached) return cached

      const headRaw = await this.safeRpc(() => this.rpc.getHead())
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
            this.cache.set(`block:${height}`, summary, this.cacheTtls.blocks)
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
      const raw = await this.safeRpc(() =>
        isNumeric(hashOrHeight)
          ? this.rpc.getBlockByNumber(Number(hashOrHeight), true, false)
          : this.rpc.getBlockByHash(hashOrHeight, true, false)
      )
      if (!raw) throw new HttpError(404, 'Block not found')
      const detail = normalizeBlockDetail(raw)
      this.cache.set(cacheKey, detail, this.cacheTtls.blocks)
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
      const block = await this.safeRpc(() => this.rpc.getBlockByNumber(height, true, false)).catch(() => null)
      if (!block) continue
      const blockTxs = Array.isArray(block?.txs) ? block.txs : []
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
    const limit = clampLimit(limitInput)
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
      heights.map((height) =>
        this.safeRpc(() => this.rpc.getBlockByNumber(height, false, false)).catch(() => null)
      )
    )
    return blocks.filter(Boolean).map((block) => normalizeBlockSummary(block))
  }

  private async safeRpc<T>(fn: () => Promise<T>): Promise<T> {
    try {
      return await fn()
    } catch (error: any) {
      throw new HttpError(503, 'RPC unavailable', error?.message ?? String(error))
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
