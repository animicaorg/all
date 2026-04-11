import type {
  AddressSummary,
  BlockDetail,
  BlockSummary,
  ContractDeployment,
  ContractDeploymentFeed,
  ContractDeploymentKind,
  HeadView,
  MempoolView,
  TxDetail
} from '@animica/explorer2-shared'
import { RequestCoalescer } from './cache.js'
import { HttpError } from './errors.js'
import { normalizeBlockDetail, normalizeBlockSummary, normalizeHead, normalizeTxDetail, normalizeTxSummary } from './normalize.js'
import { clampLimit, nextCursorForHeight, parseCursor } from './pagination.js'
import { normalizeTxHash } from './txHash.js'
import pino from 'pino'
import { TxLifecycleStore } from './txLifecycle.js'

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
const CONTRACT_DEPLOYMENT_SCAN_DEFAULT = 240
const CONTRACT_DEPLOYMENT_SCAN_MAX = 1000
const CONTRACT_DEPLOYMENT_LIMIT_MAX = 200

export class ExplorerService {
  private coalescer = new RequestCoalescer()
  private txLifecycle = new TxLifecycleStore()

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
      const detail = normalizeBlockDetail(raw)
      const rawTxs = Array.isArray((raw as any)?.txs)
        ? (raw as any).txs
        : Array.isArray((raw as any)?.transactions)
          ? (raw as any).transactions
          : []
      rawTxs.forEach((rawTx: any, index: number) => {
        try {
          const summary = normalizeTxSummary(rawTx)
          const normalizedTxHash = normalizeTxHash(String(summary.hash))
          this.txLifecycle.upsertConfirmed({
            hash: normalizedTxHash,
            includedHeight: detail.height,
            includedBlockHash: String(detail.hash),
            includedIndex: index,
            timestamp: detail.time,
            from: summary.from,
            to: summary.to,
            value: summary.value,
            fee: rawTx?.feePaid ?? rawTx?.fee,
            rawTx
          })
          log.debug({ txHash: summary.hash, normalizedHash: normalizedTxHash, insertResult: 'upserted' }, 'block ingestion tx upsert')
        } catch (error) {
          log.warn({ txHash: rawTx?.hash, error }, 'block ingestion tx skipped due to invalid hash format')
        }
      })
      return detail
    })
  }

  async getTxDetail(hash: string): Promise<TxDetail & { tx_hash: string; included_height: number | null; included_block_hash: string | null; confirmations: number; timestamp: number | null; explorer_head_height: number }> {
    const normalizedHash = normalizeTxHash(hash)
    const cacheKey = `tx:${normalizedHash}`
    return this.coalescer.run(cacheKey, async () => {
      log.debug({ normalizedHash, store: 'confirmed+pending' }, 'tx lookup start')
      const head = normalizeHead(await this.safeRpc(() => this.rpc.getHead()))

      const storeRecord = this.txLifecycle.get(normalizedHash)
      if (storeRecord?.status === 'confirmed') {
        const enrichedRecord = await this.enrichConfirmedRecordIfMissing(storeRecord)
        const confirmations = enrichedRecord.included_height ? Math.max(0, head.height - enrichedRecord.included_height + 1) : 0
        log.debug({ normalizedHash, store: 'lifecycle-store-confirmed', result: 'hit' }, 'tx lookup result')
        return {
          hash: enrichedRecord.tx_hash,
          tx_hash: enrichedRecord.tx_hash,
          status: enrichedRecord.status,
          blockHash: enrichedRecord.included_block_hash ?? undefined,
          blockHeight: enrichedRecord.included_height ?? undefined,
          included_height: enrichedRecord.included_height,
          included_block_hash: enrichedRecord.included_block_hash,
          confirmations,
          timestamp: enrichedRecord.timestamp,
          explorer_head_height: head.height,
          from: enrichedRecord.from,
          to: enrichedRecord.to,
          value: enrichedRecord.value,
          feePaid: enrichedRecord.fee,
          fee: enrichedRecord.fee,
          raw: enrichedRecord.rawTx ?? { hash: enrichedRecord.tx_hash },
          receipt: enrichedRecord.rawReceipt
        }
      }

      const tx = await this.safeRpc(() => this.rpc.getTransactionByHash(normalizedHash)).catch(() => null)
      const receipt = await this.safeRpc(() => this.rpc.getTransactionReceipt(normalizedHash)).catch(() => null)
      if (tx || receipt) {
        const detail = normalizeTxDetail(tx ?? { hash: normalizedHash }, receipt)
        const includedHeight = detail.blockHeight ?? null
        const includedBlockHash = detail.blockHash ? String(detail.blockHash) : null
        const confirmations = includedHeight ? Math.max(0, head.height - includedHeight + 1) : 0

        if (includedHeight) {
          this.txLifecycle.upsertConfirmed({
            hash: normalizedHash,
            includedHeight,
            includedBlockHash: includedBlockHash ?? normalizedHash,
            includedIndex: 0,
            timestamp: head.time,
            from: detail.from,
            to: detail.to,
            value: detail.value,
            fee: detail.feePaid,
            rawTx: detail.raw,
            rawReceipt: detail.receipt
          })
        } else {
          this.txLifecycle.recordPending(normalizedHash, {
            from: detail.from,
            to: detail.to,
            value: detail.value,
            fee: detail.feePaid,
            rawTx: detail.raw
          })
        }

        log.debug({ normalizedHash, store: includedHeight ? 'confirmed-rpc' : 'pending-rpc', result: 'hit' }, 'tx lookup result')
        return {
          ...detail,
          tx_hash: normalizedHash,
          included_height: includedHeight,
          included_block_hash: includedBlockHash,
          confirmations,
          timestamp: includedHeight ? head.time : null,
          explorer_head_height: head.height,
          fee: detail.feePaid
        }
      }

      const pending = await this.safeRpc(() => this.rpc.getMempoolPending()).catch(() => [])
      const normalizedPending = pending.flatMap((h) => {
        try {
          return [this.txLifecycle.recordPending(h)]
        } catch {
          return []
        }
      })

      if (normalizedPending.includes(normalizedHash)) {
        const pendingRecord = this.txLifecycle.get(normalizedHash)
        const detail = normalizeTxDetail(pendingRecord?.rawTx ?? { hash: normalizedHash, status: 'pending' }, null)
        log.debug({ normalizedHash, store: 'mempool', result: 'hit' }, 'tx lookup result')
        return {
          ...detail,
          tx_hash: normalizedHash,
          from: pendingRecord?.from ?? detail.from,
          to: pendingRecord?.to ?? detail.to,
          value: pendingRecord?.value ?? detail.value,
          feePaid: pendingRecord?.fee ?? detail.feePaid,
          fee: pendingRecord?.fee ?? detail.feePaid,
          included_height: null,
          included_block_hash: null,
          confirmations: 0,
          timestamp: null,
          explorer_head_height: head.height
        }
      }

      if (storeRecord?.status === 'pending') {
        log.debug({ normalizedHash, store: 'lifecycle-store-pending', result: 'hit' }, 'tx lookup result')
        return {
          hash: storeRecord.tx_hash,
          tx_hash: storeRecord.tx_hash,
          status: 'pending',
          included_height: null,
          included_block_hash: null,
          confirmations: 0,
          timestamp: null,
          explorer_head_height: head.height,
          from: storeRecord.from,
          to: storeRecord.to,
          value: storeRecord.value,
          feePaid: storeRecord.fee,
          fee: storeRecord.fee,
          raw: storeRecord.rawTx ?? { hash: storeRecord.tx_hash },
          receipt: storeRecord.rawReceipt
        }
      }

      const recentBlockMatch = await this.findTxInRecentBlocks(head.height, normalizedHash)
      if (recentBlockMatch) {
        const confirmations = Math.max(0, head.height - recentBlockMatch.includedHeight + 1)
        this.txLifecycle.upsertConfirmed({
          hash: normalizedHash,
          includedHeight: recentBlockMatch.includedHeight,
          includedBlockHash: recentBlockMatch.includedBlockHash,
          includedIndex: recentBlockMatch.includedIndex,
          timestamp: recentBlockMatch.timestamp,
          from: recentBlockMatch.tx.from,
          to: recentBlockMatch.tx.to,
          value: recentBlockMatch.tx.value,
          fee: recentBlockMatch.tx.feePaid,
          rawTx: recentBlockMatch.tx.raw,
          rawReceipt: recentBlockMatch.tx.receipt
        })
        log.debug({ normalizedHash, store: 'recent-block-scan', result: 'hit' }, 'tx lookup result')
        return {
          ...recentBlockMatch.tx,
          tx_hash: normalizedHash,
          included_height: recentBlockMatch.includedHeight,
          included_block_hash: recentBlockMatch.includedBlockHash,
          confirmations,
          timestamp: recentBlockMatch.timestamp,
          explorer_head_height: head.height
        }
      }

      log.debug({ normalizedHash, store: 'confirmed+mempool+lifecycle-store', result: 'miss' }, 'tx lookup result')
      throw new HttpError(404, 'Transaction not found')
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

  async getContractDeployments(limitInput: number, scanBlocksInput: number): Promise<ContractDeploymentFeed> {
    const limit = Math.max(1, Math.min(CONTRACT_DEPLOYMENT_LIMIT_MAX, Number(limitInput) || 24))
    const scanBlocks = Math.max(
      limit,
      Math.min(CONTRACT_DEPLOYMENT_SCAN_MAX, Number(scanBlocksInput) || CONTRACT_DEPLOYMENT_SCAN_DEFAULT)
    )
    const head = normalizeHead(await this.safeRpc(() => this.rpc.getHead()))
    const heights = Array.from({ length: scanBlocks }, (_, i) => head.height - i).filter((height) => height >= 0)
    const items: ContractDeployment[] = []
    let scannedBlocks = 0

    for (const height of heights) {
      if (items.length >= limit) break
      const block = await this.safeRpc(() => this.rpc.getBlockByNumber(height, true, true)).catch(() => null)
      if (!block) continue
      scannedBlocks += 1

      const blockDetail = normalizeBlockDetail(block)
      const txs = Array.isArray((block as any)?.txs)
        ? (block as any).txs
        : Array.isArray((block as any)?.transactions)
          ? (block as any).transactions
          : []
      const receipts = Array.isArray((block as any)?.receipts) ? (block as any).receipts : []

      for (let i = 0; i < txs.length; i += 1) {
        const tx = txs[i]
        const receipt = tx?.receipt ?? receipts[i] ?? null
        const txDetail = normalizeTxDetail(tx, receipt)
        const deployment = buildContractDeployment(txDetail, tx, receipt, blockDetail)
        if (!deployment) continue
        items.push(deployment)
        if (items.length >= limit) break
      }
    }

    const successful = items.filter((item) => item.status === 'confirmed').length
    const failed = items.length - successful
    const uniqueDeployers = new Set(
      items
        .map((item) => item.deployer)
        .filter((value): value is string => typeof value === 'string' && value.length > 0)
    )
    const uniqueContracts = new Set(
      items
        .map((item) => item.contractAddress)
        .filter((value): value is string => typeof value === 'string' && value.length > 0)
    )

    return {
      headHeight: head.height,
      scannedBlocks,
      stats: {
        total: items.length,
        successful,
        failed,
        uniqueDeployers: uniqueDeployers.size,
        uniqueContracts: uniqueContracts.size
      },
      spotlight: items.find((item) => item.status === 'confirmed') ?? items[0] ?? null,
      items
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


  async backfillConfirmedTxsMissingFields(limitInput: number = 100): Promise<{ scanned: number; updated: number; remainingEstimate: number }> {
    const limit = Math.max(1, Math.min(500, Number(limitInput) || 100))
    const candidates = this.txLifecycle.getMissingConfirmedFields(limit)
    let updated = 0

    for (const record of candidates) {
      const tx = await this.safeRpc(() => this.rpc.getTransactionByHash(record.tx_hash)).catch(() => null)
      const receipt = await this.safeRpc(() => this.rpc.getTransactionReceipt(record.tx_hash)).catch(() => null)
      const detail = normalizeTxDetail(tx ?? { hash: record.tx_hash }, receipt)
      const patched = this.txLifecycle.patchConfirmedFields(record.tx_hash, {
        from: detail.from,
        to: detail.to,
        value: detail.value,
        fee: detail.feePaid,
        rawTx: detail.raw,
        rawReceipt: detail.receipt
      })
      if (patched && patched.from && patched.to && patched.value) {
        updated += 1
      }
    }

    const remainingEstimate = this.txLifecycle.countMissingConfirmedFields()
    return { scanned: candidates.length, updated, remainingEstimate }
  }

  private async enrichConfirmedRecordIfMissing(record: import('./txLifecycle.js').TxLookupRecord): Promise<import('./txLifecycle.js').TxLookupRecord> {
    if (record.from && record.to && record.value) {
      return record
    }

    const tx = await this.safeRpc(() => this.rpc.getTransactionByHash(record.tx_hash)).catch(() => null)
    const receipt = await this.safeRpc(() => this.rpc.getTransactionReceipt(record.tx_hash)).catch(() => null)
    const detail = normalizeTxDetail(tx ?? { hash: record.tx_hash }, receipt)
    const patched = this.txLifecycle.patchConfirmedFields(record.tx_hash, {
      from: detail.from,
      to: detail.to,
      value: detail.value,
      fee: detail.feePaid,
      rawTx: detail.raw,
      rawReceipt: detail.receipt
    })
    if (patched) {
      log.info({ txHash: record.tx_hash }, 'lazy backfill filled missing confirmed tx fields')
      return patched
    }
    return record
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
    return blocks.filter((block: BlockSummary | null): block is BlockSummary => block !== null)
  }

  private async findTxInRecentBlocks(headHeight: number, targetHash: string): Promise<{
    tx: TxDetail
    includedHeight: number
    includedBlockHash: string
    includedIndex: number
    timestamp: number | null
  } | null> {
    const heights = Array.from({ length: RECENT_BLOCK_WINDOW }, (_, i) => headHeight - i).filter((h) => h >= 0)

    for (const height of heights) {
      const block = await this.safeRpc(() => this.rpc.getBlockByNumber(height, true, true)).catch(() => null)
      if (!block) continue

      const detail = normalizeBlockDetail(block)
      const txs = Array.isArray((block as any)?.txs)
        ? (block as any).txs
        : Array.isArray((block as any)?.transactions)
          ? (block as any).transactions
          : []

      for (let i = 0; i < txs.length; i += 1) {
        const tx = txs[i]
        const hash = tx?.hash ?? tx?.txHash
        if (!hash) continue

        let normalized: string
        try {
          normalized = normalizeTxHash(String(hash))
        } catch {
          continue
        }

        if (normalized !== targetHash) continue

        const txDetail = normalizeTxDetail(tx, tx?.receipt ?? null)
        return {
          tx: txDetail,
          includedHeight: detail.height,
          includedBlockHash: String(detail.hash),
          includedIndex: i,
          timestamp: detail.time || null
        }
      }
    }

    return null
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

function buildContractDeployment(txDetail: TxDetail, rawTx: any, receipt: any, block: BlockDetail): ContractDeployment | null {
  if (txDetail.status === 'pending') return null

  const contractAddress =
    extractContractAddress(receipt) ??
    extractContractAddress(rawTx?.receipt) ??
    extractContractAddress(txDetail.receipt) ??
    extractContractAddress(txDetail.raw)
  const noRecipient = hasNoRecipient(txDetail, rawTx)
  const inferredKind = inferDeploymentKind(rawTx, txDetail.raw, receipt)
  const isDeployLike = Boolean(contractAddress) || noRecipient || inferredKind !== 'unknown'
  if (!isDeployLike) return null

  const label =
    findFirstStringByKey(rawTx, ['contractName', 'contract_name', 'manifestName']) ??
    findFirstStringByKey(rawTx?.manifest, ['name']) ??
    findFirstStringByKey(receipt, ['contractName', 'contract_name']) ??
    null

  return {
    txHash: String(txDetail.hash),
    blockHeight: txDetail.blockHeight ?? block.height,
    blockHash: txDetail.blockHash ? String(txDetail.blockHash) : String(block.hash),
    blockTime: block.time || null,
    deployer: txDetail.from,
    contractAddress,
    status: txDetail.status === 'failed' ? 'failed' : 'confirmed',
    kind: resolveDeploymentKind(contractAddress, noRecipient, inferredKind),
    feePaid: txDetail.feePaid,
    gasUsed: txDetail.gasUsed,
    codeSizeBytes: inferCodeSizeBytes(rawTx, txDetail.raw, receipt),
    label
  }
}

function resolveDeploymentKind(
  contractAddress: string | null,
  noRecipient: boolean,
  inferredKind: ContractDeploymentKind
): ContractDeploymentKind {
  if (inferredKind !== 'unknown') return inferredKind
  if (contractAddress || noRecipient) return 'contract_create'
  return 'unknown'
}

function inferDeploymentKind(...sources: unknown[]): ContractDeploymentKind {
  const markers = collectDeploymentMarkers(...sources)
  if (markers.some((marker) => marker.includes('manifest'))) return 'manifest_deploy'
  if (markers.some((marker) => marker.includes('package'))) return 'package_publish'
  if (markers.some((marker) => marker.includes('deploy') || marker.includes('contractcreate') || marker.includes('createcontract'))) {
    return 'contract_create'
  }
  return 'unknown'
}

function collectDeploymentMarkers(...sources: unknown[]): string[] {
  const queue: unknown[] = [...sources]
  const out: string[] = []
  const visited = new Set<unknown>()
  let depth = 0

  while (queue.length && depth < 5) {
    const levelSize = queue.length
    for (let i = 0; i < levelSize; i += 1) {
      const value = queue.shift()
      if (!value || typeof value !== 'object' || visited.has(value)) continue
      visited.add(value)
      const record = value as Record<string, unknown>
      for (const key of ['kind', 'type', 'txType', 'tx_type', 'method', 'action', 'op', 'operation', 'module', 'function']) {
        const marker = normalizeMarker(record[key])
        if (marker) out.push(marker)
      }
      for (const child of Object.values(record)) {
        if (child && typeof child === 'object') queue.push(child)
      }
    }
    depth += 1
  }

  return out
}

function normalizeMarker(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const compact = value.toLowerCase().replace(/[^a-z0-9]/g, '')
  return compact.length ? compact : null
}

function hasNoRecipient(txDetail: TxDetail, rawTx: any): boolean {
  const toCandidate =
    txDetail.to ??
    rawTx?.to ??
    rawTx?.tx?.to ??
    rawTx?.body?.to ??
    rawTx?.tx?.payload?.v?.to ??
    rawTx?.payload?.v?.to
  if (toCandidate === undefined || toCandidate === null) return true
  if (typeof toCandidate !== 'string') return false
  const trimmed = toCandidate.trim().toLowerCase()
  return trimmed.length === 0 || trimmed === '0x' || trimmed === '0x0' || /^0x0+$/.test(trimmed)
}

function extractContractAddress(value: unknown): string | null {
  return findFirstStringByKey(value, [
    'contractAddress',
    'contract_address',
    'createdContract',
    'created_contract',
    'createdAddress',
    'created_address',
    'deployedAddress',
    'deployed_address',
    'deployAddress',
    'deploy_address'
  ])
}

function findFirstStringByKey(root: unknown, keys: string[]): string | null {
  if (!root || typeof root !== 'object') return null
  const queue: unknown[] = [root]
  const visited = new Set<unknown>()
  let depth = 0

  while (queue.length && depth < 5) {
    const levelSize = queue.length
    for (let i = 0; i < levelSize; i += 1) {
      const value = queue.shift()
      if (!value || typeof value !== 'object' || visited.has(value)) continue
      visited.add(value)
      const record = value as Record<string, unknown>

      for (const key of keys) {
        const candidate = sanitizeString(record[key])
        if (candidate) return candidate
      }

      for (const child of Object.values(record)) {
        if (child && typeof child === 'object') queue.push(child)
      }
    }
    depth += 1
  }

  return null
}

function sanitizeString(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  if (!trimmed) return null
  if (trimmed === '0x' || trimmed === '0x0' || /^0x0+$/.test(trimmed)) return null
  return trimmed
}

function inferCodeSizeBytes(...sources: unknown[]): number | null {
  for (const source of sources) {
    if (!source || typeof source !== 'object') continue
    const record = source as Record<string, unknown>
    for (const numericKey of ['codeSizeBytes', 'code_size_bytes', 'codeSize', 'code_size', 'bytecodeSize', 'bytecode_size']) {
      const numeric = toPositiveInt(record[numericKey])
      if (numeric !== null) return numeric
    }
    for (const blobKey of ['bytecode', 'code', 'package', 'packageBytes', 'payload']) {
      const size = byteLengthFromEncoded(record[blobKey])
      if (size !== null) return size
    }
  }
  return null
}

function toPositiveInt(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return Math.floor(value)
  if (typeof value === 'string') {
    const parsed = Number.parseInt(value, value.startsWith('0x') ? 16 : 10)
    if (!Number.isNaN(parsed) && parsed >= 0) return parsed
  }
  return null
}

function byteLengthFromEncoded(value: unknown): number | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  if (!trimmed) return null
  if (trimmed.startsWith('0x')) {
    const hex = trimmed.slice(2)
    if (!hex || hex.length % 2 !== 0 || /[^0-9a-f]/i.test(hex)) return null
    return hex.length / 2
  }
  if (/^[a-z0-9+/=]+$/i.test(trimmed) && trimmed.length >= 8) {
    try {
      return Buffer.from(trimmed, 'base64').byteLength
    } catch {
      return null
    }
  }
  return null
}

function isNumeric(value: string): boolean {
  return /^[0-9]+$/.test(value)
}

function isNotFoundError(message: string): boolean {
  return /not found|unknown block|missing|does not exist/i.test(message)
}
