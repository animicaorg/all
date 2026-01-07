import type { AddressSummary, BlockDetail, BlockSummary, HeadView, MempoolView, TxDetail } from '@animica/explorer2-shared'
import { RequestCoalescer } from './cache.js'
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
    const cacheKey = `tx:${hash}`
    return this.coalescer.run(cacheKey, async () => {
      const tx = await this.safeRpc(() => this.rpc.getTransactionByHash(hash)).catch(() => null)
      const receipt = await this.safeRpc(() => this.rpc.getTransactionReceipt(hash)).catch(() => null)
      if (!tx && !receipt) throw new HttpError(404, 'Transaction not found')
      return normalizeTxDetail(tx, receipt)
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
