import type {
  AccountType,
  AddressSummary,
  BlockDetail,
  BlockSummary,
  ContractDetailResponse,
  ContractDeployment,
  ContractDeploymentFeed,
  ContractDeploymentKind,
  ContractProfile,
  ContractVerificationJob,
  ContractVerificationRecord,
  ContractVerificationSubmitRequest,
  HeadView,
  MempoolView,
  TxClassification,
  TxDetail
} from '@animica/explorer2-shared'
import { createHash } from 'node:crypto'
import { RequestCoalescer } from './cache.js'
import { HttpError } from './errors.js'
import {
  canonicalAddressKey,
  learnAddressAlg,
  normalizeAddress,
  normalizeBlockDetail,
  normalizeBlockSummary,
  normalizeHead,
  normalizeTxDetail,
  normalizeTxSummary
} from './normalize.js'
import { clampLimit, nextCursorForHeight, parseCursor } from './pagination.js'
import { normalizeTxHash } from './txHash.js'
import pino from 'pino'
import { TxLifecycleStore } from './txLifecycle.js'
import { ExplorerStore } from './explorerStore.js'
import { ContractVerifier } from './contractVerifier.js'
import { classifyTransaction, extractDeployCode, extractManifest, extractTxInputData } from './txClassifier.js'

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
  getAccount?: (address: string) => Promise<unknown>
  getCode?: (address: string) => Promise<string | null>
  getRichList?: (limit: number, offset: number) => Promise<unknown>
  getTotalSupply?: () => Promise<unknown>
}

const RECENT_BLOCK_WINDOW = 20
const ADDRESS_SCAN_MAX = Math.max(1, Number(process.env.EXPLORER_ADDRESS_SCAN_MAX) || 250)
// Wall-clock budget for the synchronous address tx-history scan. Without it a
// *sparse* address (no txs in the recent window) scans the full ADDRESS_SCAN_MAX
// blocks live — ~37s for 250 — so the page appears to "hang" and the balance
// card renders blank ("—"), which reads as a missing/wrong balance. The balance
// itself is a single fast RPC call fetched up-front, so we cap the history scan
// and let pagination (nextCursor) fetch deeper history on demand.
const ADDRESS_SCAN_MS_BUDGET = Math.max(500, Number(process.env.EXPLORER_ADDRESS_SCAN_MS) || 3500)
const CONTRACT_DEPLOYMENT_SCAN_DEFAULT = 240
const CONTRACT_DEPLOYMENT_SCAN_MAX = 1000
const CONTRACT_DEPLOYMENT_LIMIT_MAX = 200
const CONTRACT_CREATION_DISCOVERY_SCAN_MAX = 600

export interface ExplorerServiceOptions {
  store?: ExplorerStore
  verifier?: ContractVerifier
  [key: string]: unknown
}

export interface ThetaHistoryPoint {
  height: number
  time: number
  thetaMicro: number | null
}

export class ExplorerService {
  private coalescer = new RequestCoalescer()
  private txLifecycle = new TxLifecycleStore()
  private store: ExplorerStore | null = null
  private verifier: ContractVerifier | null = null

  constructor(
    private rpc: ChainClient,
    options: ExplorerServiceOptions = {}
  ) {
    this.store = options.store ?? null
    this.verifier = options.verifier ?? null
  }

  async getHead(): Promise<{ head: HeadView; stats: any; thetaHistory: ThetaHistoryPoint[] }> {
    return this.coalescer.run('head', async () => {
      const headRaw = await this.safeRpc(() => this.rpc.getHead())
      const head = normalizeHead(headRaw)

      const [blocks, mempool, peers] = await Promise.all([
        this.getRecentBlocks(head.height),
        this.safeRpc(() => this.rpc.getMempoolStats()).catch(() => null),
        this.safeRpc(() => this.rpc.getPeers()).catch(() => [])
      ])

      const stats = buildNetworkStats(blocks, mempool, peers)
      const thetaHistory = blocks
        .map((block) => ({
          height: block.height,
          time: block.time,
          thetaMicro: isDefinedNumber(block.thetaMicro) ? block.thetaMicro : null
        }))
        .reverse()
      return { head, stats, thetaHistory }
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
          const raw = await this.safeRpc(() => this.rpc.getBlockByNumber(height, false, false), { allowNotFound: true })
          if (!raw) return null
          return normalizeBlockSummary(raw)
        })
      )
    )

    const minHeight = heights.length ? heights[heights.length - 1] : startHeight
    return {
      items: blocks.filter((block): block is BlockSummary => block !== null),
      nextCursor: nextCursorForHeight(minHeight)
    }
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
        const includedHeight = enrichedRecord.included_height
        const hasIncludedHeight = isDefinedNumber(includedHeight)
        const confirmations = hasIncludedHeight ? Math.max(0, head.height - includedHeight + 1) : 0
        log.debug({ normalizedHash, store: 'lifecycle-store-confirmed', result: 'hit' }, 'tx lookup result')
        return this.attachClassification({
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
        })
      }

      const tx = await this.safeRpc(() => this.rpc.getTransactionByHash(normalizedHash)).catch(() => null)
      const receipt = await this.safeRpc(() => this.rpc.getTransactionReceipt(normalizedHash)).catch(() => null)
      if (tx || receipt) {
        const detail = normalizeTxDetail(tx ?? { hash: normalizedHash }, receipt)
        const includedHeight = detail.blockHeight ?? null
        const includedBlockHash = detail.blockHash ? String(detail.blockHash) : null
        const hasIncludedHeight = isDefinedNumber(includedHeight)
        const confirmations = hasIncludedHeight ? Math.max(0, head.height - includedHeight + 1) : 0
        const confirmedTimestamp = hasIncludedHeight
          ? await this.resolveIncludedTxTimestamp(includedHeight, includedBlockHash)
          : null

        if (hasIncludedHeight) {
          this.txLifecycle.upsertConfirmed({
            hash: normalizedHash,
            includedHeight,
            includedBlockHash: includedBlockHash ?? normalizedHash,
            includedIndex: 0,
            timestamp: confirmedTimestamp,
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

        log.debug({ normalizedHash, store: hasIncludedHeight ? 'confirmed-rpc' : 'pending-rpc', result: 'hit' }, 'tx lookup result')
        return this.attachClassification({
          ...detail,
          tx_hash: normalizedHash,
          included_height: includedHeight,
          included_block_hash: includedBlockHash,
          confirmations,
          timestamp: hasIncludedHeight ? confirmedTimestamp : null,
          explorer_head_height: head.height,
          fee: detail.feePaid
        })
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
        return this.attachClassification({
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
        })
      }

      if (storeRecord?.status === 'pending') {
        log.debug({ normalizedHash, store: 'lifecycle-store-pending', result: 'hit' }, 'tx lookup result')
        return this.attachClassification({
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
        })
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
        return this.attachClassification({
          ...recentBlockMatch.tx,
          tx_hash: normalizedHash,
          included_height: recentBlockMatch.includedHeight,
          included_block_hash: recentBlockMatch.includedBlockHash,
          confirmations,
          timestamp: recentBlockMatch.timestamp,
          explorer_head_height: head.height
        })
      }

      log.debug({ normalizedHash, store: 'confirmed+mempool+lifecycle-store', result: 'miss' }, 'tx lookup result')
      throw new HttpError(404, 'Transaction not found')
    })
  }

  async getAddressDetail(address: string, limitInput: number, cursor?: string): Promise<AddressSummary> {
    const limit = clampLimit(limitInput)
    // The requested bech32m address carries its own alg_id; learn it so this
    // account renders with the correct alg_id wherever it appears below
    // (notably as a recipient of incoming transfers).
    learnAddressAlg(address)
    const normalizedAddress = normalizeAddress(address) ?? address
    const targetAddressKey = canonicalAddressKey(normalizedAddress) ?? canonicalAddressKey(address)
    const [confirmedBalance, pendingBalance] = await Promise.all([
      this.safeRpc(() => this.rpc.getBalance(normalizedAddress, 'latest')).catch(() => null),
      this.safeRpc(() => this.rpc.getBalance(normalizedAddress, 'pending')).catch(() => null)
    ])

    const headRaw = await this.safeRpc(() => this.rpc.getHead())
    const head = normalizeHead(headRaw)
    const cursorHeight = parseCursor(cursor)
    const startHeight = cursorHeight ?? head.height
    const txs: any[] = []
    let scannedBlocks = 0
    let nextHeight = startHeight
    // Stop the live scan once the budget is spent so the balance/head always
    // return promptly; hasMore + nextCursor let the client resume for history.
    const scanDeadline = Date.now() + ADDRESS_SCAN_MS_BUDGET

    while (
      nextHeight >= 0 &&
      scannedBlocks < ADDRESS_SCAN_MAX &&
      txs.length < limit &&
      Date.now() < scanDeadline
    ) {
      const height = nextHeight
      nextHeight -= 1
      scannedBlocks += 1
      const block = await this.safeRpc(() => this.rpc.getBlockByNumber(height, true, true)).catch(() => null)
      if (!block) {
        // A failed fetch must become the RESUME POINT, not count as scanned:
        // wallets advance a per-address watermark from scannedBlocks, so a
        // silently skipped block would make its txs permanently invisible.
        nextHeight = height
        scannedBlocks -= 1
        break
      }

      // Block timestamp (seconds) for the txs below — wallets sort their
      // merged sent+received history by time, so every entry needs one.
      const blockHeader = (block as any)?.header ?? block
      const blockTimeRaw = Number(
        blockHeader?.time ?? blockHeader?.timestamp ?? (block as any)?.timestamp ?? 0
      )
      const blockTime = Number.isFinite(blockTimeRaw) && blockTimeRaw > 0 ? blockTimeRaw : null

      const blockTxs = extractBlockTransactions(block)
      const receipts = extractBlockReceipts(block)
      for (let i = 0; i < blockTxs.length; i += 1) {
        const tx = blockTxs[i]
        const summary = normalizeTxSummary(tx)
        const receipt = findReceiptForTx(tx, i, receipts)
        const detail = normalizeTxDetail(tx, receipt)
        const classification = await this.classifyAndPersistTx(detail, tx, receipt)
        const fromKey = canonicalAddressKey(summary.from)
        const toKey = canonicalAddressKey(summary.to)
        const createdKey = canonicalAddressKey(classification.createdContractAddress)
        const touchesAddress =
          (targetAddressKey !== null &&
            (fromKey === targetAddressKey || toKey === targetAddressKey || createdKey === targetAddressKey)) ||
          summary.from === normalizedAddress ||
          summary.to === normalizedAddress ||
          classification.createdContractAddress === normalizedAddress
        if (!touchesAddress) continue
        // These txs came out of a block, so 'pending' (normalizeTxDetail's
        // verdict when the raw tx carries no blockNumber field) is wrong —
        // anything found here is at least included.
        summary.status = detail.status === 'pending' ? 'confirmed' : detail.status
        summary.value = summary.value ?? detail.value
        summary.classification = classification
        const gasRaw = (tx as any)?.gas
        ;(summary as any).blockNumber = height
        ;(summary as any).timestamp = blockTime
        ;(summary as any).gasPrice =
          (tx as any)?.gasPrice ??
          (tx as any)?.gas_price ??
          (gasRaw && typeof gasRaw === 'object' ? gasRaw.price : undefined) ??
          // v2 canonical bodies carry the per-gas price as maxFee/tip.
          (tx as any)?.maxFee ??
          (tx as any)?.max_fee ??
          (tx as any)?.tip ??
          null
        ;(summary as any).gasLimit =
          (tx as any)?.gasLimit ??
          (tx as any)?.gas_limit ??
          (gasRaw && typeof gasRaw === 'object'
            ? gasRaw.limit
            : typeof gasRaw === 'number'
              ? gasRaw
              : undefined) ??
          null
        txs.push(summary)
      }
    }

    const profile = await this.resolveContractProfile(normalizedAddress)
    const accountType = profile?.accountType ?? (await this.resolveAccountType(normalizedAddress))
    const hasMore = nextHeight >= 0
    return {
      address: normalizedAddress,
      accountType,
      confirmedBalance,
      pendingBalance,
      // No slice: the loop already stops adding blocks once `limit` is hit,
      // and cutting a scanned block's overflow txs here would lose them —
      // the cursor resumes BELOW that block, so nothing would ever re-serve
      // them (wallets would then watermark right past the gap).
      txs,
      contract: accountType === 'contract' ? profile ?? null : null,
      // `nextHeight` is already the first UNSCANNED height (the loop
      // decrements before scanning); nextCursorForHeight would subtract 1
      // again and permanently skip one block per page boundary.
      nextCursor: hasMore ? String(nextHeight) : null,
      scannedBlocks,
      partial: hasMore
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
      const txs = extractBlockTransactions(block)
      const receipts = extractBlockReceipts(block)

      for (let i = 0; i < txs.length; i += 1) {
        const tx = txs[i]
        const receipt = findReceiptForTx(tx, i, receipts)
        const receiptForStatus =
          receipt ??
          {
            txHash: tx?.hash ?? tx?.txHash,
            blockNumber: blockDetail.height,
            blockHash: blockDetail.hash,
            status: tx?.status ?? 'SUCCESS'
          }
        const txDetail = normalizeTxDetail(tx, receiptForStatus)
        const classification = await this.classifyAndPersistTx(txDetail, tx, receiptForStatus)
        if (classification.type !== 'contract_deployment') continue
        const deployment = buildContractDeployment(txDetail, tx, receipt, blockDetail, classification)
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

  async getContractDetail(address: string, limitInput: number, cursor?: string): Promise<ContractDetailResponse> {
    const summary = await this.getAddressDetail(address, limitInput, cursor)
    const profile = await this.resolveContractProfile(address)
    if (!profile || profile.accountType !== 'contract') {
      throw new HttpError(404, 'Contract not found')
    }
    return {
      address,
      profile,
      txs: summary.txs
    }
  }

  async getContractByCreationTx(txHash: string): Promise<ContractProfile | null> {
    const normalized = normalizeTxHash(txHash)
    const row = this.store ? this.store.findContractProfileByCreatorTx(normalized) : null
    if (row) return row

    const detail = await this.getTxDetail(normalized).catch(() => null)
    if (!detail) return null
    const classification = detail.classification ?? (await this.classifyAndPersistTx(detail, detail.raw, detail.receipt))
    if (!classification.createdContractAddress) return null
    return this.resolveContractProfile(classification.createdContractAddress)
  }

  async getContractCode(address: string): Promise<{ address: string; code: string | null; codeHash: string | null }> {
    const code = this.rpc.getCode ? await this.safeRpc(() => this.rpc.getCode!(address)).catch(() => null) : null
    const normalizedCode = typeof code === 'string' ? code : null
    const codeHash = normalizedCode ? hashHex(normalizedCode) : null
    if (this.store) {
      this.store.upsertContractProfile({
        address,
        accountType: normalizedCode ? 'contract' : 'unknown',
        runtimeCodeHash: codeHash,
        codeSizeBytes: normalizedCode ? Math.max(0, (normalizedCode.length - 2) / 2) : null
      })
    }
    return { address, code: normalizedCode, codeHash }
  }

  async submitContractVerification(request: ContractVerificationSubmitRequest): Promise<ContractVerificationJob> {
    if (!this.verifier) {
      throw new HttpError(503, 'Contract verifier unavailable')
    }
    if (!request.address || typeof request.address !== 'string') {
      throw new HttpError(400, 'address is required')
    }
    if (!request.language || typeof request.language !== 'string') {
      throw new HttpError(400, 'language is required')
    }
    return this.verifier.submit(request)
  }

  getContractVerificationJob(jobId: string): ContractVerificationJob | null {
    if (!this.verifier) return null
    return this.verifier.getJob(jobId)
  }

  getContractVerification(address: string): ContractVerificationRecord | undefined {
    if (!this.store) return undefined
    return this.store.getLatestVerificationForAddress(address)
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

          const rawRecord = asRecord(raw)
          const items = extractRichListItems(rawRecord)
          const height = toPositiveInt(rawRecord.height ?? rawRecord.blockHeight ?? rawRecord.number) ?? 0
          const totalAddresses =
            toPositiveInt(rawRecord.totalAddresses ?? rawRecord.total_addresses ?? rawRecord.total) ?? items.length

          // Get total supply for percentage calculation
          let totalSupply = 0n
          try {
            const supplyRaw = await this.safeRpc(() => this.rpc.getTotalSupply!())
            const supplyRecord = asRecord(supplyRaw)
            totalSupply = toBigIntLike(
              supplyRecord.totalSupply ??
                supplyRecord.total_supply ??
                supplyRecord.supply ??
                supplyRecord.value ??
                '0x0'
            )
          } catch {
            // If total supply fails, percentages will be 0
          }

          // Format items with percentages
          const formattedItems = items.map((item: any, index: number) => {
            const balance = toBigIntLike(item.balance ?? item.amount ?? item.value ?? '0x0')
            const pctSupply = totalSupply > 0n
              ? Number((balance * 10000n) / totalSupply) / 100
              : 0

            return {
              rank: toPositiveInt(item.rank) ?? safeOffset + index + 1,
              address: typeof item.address === 'string' ? item.address : typeof item.addr === 'string' ? item.addr : '',
              balance: toHexQuantity(balance),
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

          const rawRecord = asRecord(raw)
          const height = toPositiveInt(rawRecord.height ?? rawRecord.blockHeight ?? rawRecord.number) ?? 0
          const totalSupplyBig = toBigIntLike(
            rawRecord.totalSupply ?? rawRecord.total_supply ?? rawRecord.supply ?? rawRecord.value ?? '0x0'
          )
          const totalSupply = toHexQuantity(totalSupplyBig)
          const addressCount = toPositiveInt(rawRecord.addressCount ?? rawRecord.address_count ?? rawRecord.totalAddresses) ?? 0

          // Get top addresses to compute concentration metrics
          let top10Pct: number | undefined
          let top100Pct: number | undefined
          let top1000Pct: number | undefined

          try {
            // Get top 10
            if (this.rpc.getRichList) {
              const top10Raw = await this.safeRpc(() => this.rpc.getRichList!(10, 0))
              const top10Sum = sumRichListBalances(top10Raw)
              top10Pct = totalSupplyBig > 0n
                ? Number((top10Sum * 10000n) / totalSupplyBig) / 100
                : 0

              // Get top 100
              const top100Raw = await this.safeRpc(() => this.rpc.getRichList!(100, 0))
              const top100Sum = sumRichListBalances(top100Raw)
              top100Pct = totalSupplyBig > 0n
                ? Number((top100Sum * 10000n) / totalSupplyBig) / 100
                : 0

              // Get top 1000 (if addressCount >= 1000)
              if (addressCount >= 1000) {
                const top1000Raw = await this.safeRpc(() => this.rpc.getRichList!(1000, 0))
                const top1000Sum = sumRichListBalances(top1000Raw)
                top1000Pct = totalSupplyBig > 0n
                  ? Number((top1000Sum * 10000n) / totalSupplyBig) / 100
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

  /** Circulating supply in whole ANM as a plain JS number — CMC/CoinGecko-style
   *  supply endpoints expect a bare JSON number, not a hex/string quantity.
   *  All mined ANM is liquid (no foundation locks), so circulating = total.
   *  BigInt division first keeps precision beyond Number's 2^53 in raw nANM. */
  async getCirculatingSupply(): Promise<number> {
    return this.coalescer.run('supply:circulating', async () => {
      if (!this.rpc.getTotalSupply) {
        throw new HttpError(501, 'Circulating supply not available', 'Node does not support state.getTotalSupply RPC method')
      }
      const raw = await this.safeRpc(() => this.rpc.getTotalSupply!())
      const rawRecord = asRecord(raw)
      const totalSupplyBig = toBigIntLike(
        rawRecord.totalSupply ?? rawRecord.total_supply ?? rawRecord.supply ?? rawRecord.value ?? '0x0'
      )
      const NANO_PER_ANM = 1_000_000_000n
      return Number(totalSupplyBig / NANO_PER_ANM) + Number(totalSupplyBig % NANO_PER_ANM) / 1e9
    })
  }

  private async attachClassification<
    T extends TxDetail & {
      tx_hash?: string
      included_height?: number | null
      included_block_hash?: string | null
      confirmations?: number
      timestamp?: number | null
      explorer_head_height?: number
    }
  >(tx: T): Promise<T> {
    const classification = await this.classifyAndPersistTx(tx, tx.raw, tx.receipt ?? null, {
      timestamp: tx.timestamp ?? null
    })
    return { ...tx, classification }
  }

  private async classifyAndPersistTx(
    txDetail: TxDetail,
    rawTx: unknown,
    receipt: unknown,
    options: { timestamp?: number | null } = {}
  ): Promise<TxClassification> {
    const txHash = normalizeTxHash(String(txDetail.tx_hash ?? txDetail.hash))
    const cached = this.store?.getTxClassification(txHash)
    const toAddress = txDetail.to ?? findToAddress(rawTx)
    const targetAccountType = toAddress ? await this.resolveAccountType(toAddress) : 'unknown'
    const targetIsContract = targetAccountType === 'contract'
    const targetProfile = targetIsContract && toAddress ? await this.resolveContractProfile(toAddress) : null
    const targetAbi = targetProfile?.verification?.abi ?? targetProfile?.abi ?? null

    const classification = classifyTransaction({
      txDetail,
      rawTx,
      receipt,
      knownTargetIsContract: targetIsContract,
      abi: targetAbi
    })

    const needRefreshCachedDecoded =
      cached &&
      targetAbi &&
      classification.type === 'contract_interaction' &&
      (!cached.decodedCall || !cached.decodedEvents || cached.decodedEvents.length === 0)
    if (cached && !needRefreshCachedDecoded && classification.type === cached.type && classification.failed === cached.failed) {
      return cached
    }

    if (this.store) {
      this.store.upsertTxClassification({
        txHash,
        fromAddress: txDetail.from,
        toAddress: toAddress ?? undefined,
        classification
      })
    }

    if (classification.createdContractAddress) {
      await this.persistContractCreationFromClassification({
        classification,
        txDetail,
        rawTx,
        receipt,
        timestamp: options.timestamp ?? txDetail.timestamp ?? null
      })
    }

    return classification
  }

  private async persistContractCreationFromClassification(params: {
    classification: TxClassification
    txDetail: TxDetail
    rawTx: unknown
    receipt: unknown
    timestamp: number | null
  }): Promise<void> {
    const { classification, txDetail, rawTx, timestamp } = params
    const createdAddress = classification.createdContractAddress
    if (!this.store || !createdAddress) return

    const code = this.rpc.getCode ? await this.safeRpc(() => this.rpc.getCode!(createdAddress)).catch(() => null) : null
    const normalizedCode = normalizeBytecode(code)
    const runtimeCodeHash = normalizedCode ? hashHex(normalizedCode) : null
    const deployCode = extractDeployCode(rawTx) ?? extractTxInputData(rawTx, txDetail)
    const codeHash = deployCode ? hashHex(deployCode) : null
    const manifest = extractManifest(rawTx)

    this.store.upsertContractProfile({
      address: createdAddress,
      accountType: 'contract',
      creatorAddress: txDetail.from ?? null,
      creatorTxHash: normalizeTxHash(String(txDetail.tx_hash ?? txDetail.hash)),
      creationBlockHeight: txDetail.blockHeight ?? txDetail.included_height ?? null,
      creationBlockHash: txDetail.blockHash ? String(txDetail.blockHash) : txDetail.included_block_hash ?? null,
      creationTimestamp: timestamp ?? null,
      codeHash,
      runtimeCodeHash,
      codeSizeBytes: normalizedCode ? Math.max(0, (normalizedCode.length - 2) / 2) : null,
      metadataJson: manifest ?? null
    })
  }

  private async resolveAccountType(address: string): Promise<AccountType> {
    if (!address) return 'unknown'
    const cached = this.store?.getContractProfile(address)
    if (cached?.accountType === 'contract' || cached?.accountType === 'eoa') {
      return cached.accountType
    }

    const codeRaw = this.rpc.getCode ? await this.safeRpc(() => this.rpc.getCode!(address)).catch(() => null) : null
    const code = normalizeBytecode(codeRaw)
    if (code) {
      this.store?.upsertContractProfile({
        address,
        accountType: 'contract',
        runtimeCodeHash: hashHex(code),
        codeSizeBytes: Math.max(0, (code.length - 2) / 2)
      })
      return 'contract'
    }

    const accountRaw = this.rpc.getAccount ? await this.safeRpc(() => this.rpc.getAccount!(address)).catch(() => null) : null
    const hasAccount = hasAccountSignals(accountRaw)
    const accountType: AccountType = hasAccount ? 'eoa' : 'unknown'

    this.store?.upsertContractProfile({
      address,
      accountType
    })
    return accountType
  }

  private async resolveContractProfile(address: string): Promise<ContractProfile | null> {
    const accountType = await this.resolveAccountType(address)
    if (!this.store) {
      if (accountType !== 'contract') return null
      const code = this.rpc.getCode ? await this.safeRpc(() => this.rpc.getCode!(address)).catch(() => null) : null
      const normalizedCode = normalizeBytecode(code)
      return {
        address,
        accountType: 'contract',
        runtimeCodeHash: normalizedCode ? hashHex(normalizedCode) : null,
        codeSizeBytes: normalizedCode ? Math.max(0, (normalizedCode.length - 2) / 2) : null,
        isVerified: false
      }
    }

    let profile = this.store.getContractProfile(address)
    if (accountType !== 'contract') {
      return profile ?? { address, accountType, isVerified: false }
    }

    if (!profile?.creatorTxHash) {
      const creation = await this.discoverCreationTxForContract(address)
      if (creation) {
        this.store.upsertContractProfile({
          address,
          accountType: 'contract',
          creatorAddress: creation.creatorAddress,
          creatorTxHash: creation.creatorTxHash,
          creationBlockHeight: creation.creationBlockHeight,
          creationBlockHash: creation.creationBlockHash,
          creationTimestamp: creation.creationTimestamp,
          codeHash: creation.codeHash
        })
      }
    }

    if (!profile?.runtimeCodeHash || !profile?.codeSizeBytes) {
      const code = this.rpc.getCode ? await this.safeRpc(() => this.rpc.getCode!(address)).catch(() => null) : null
      const normalizedCode = normalizeBytecode(code)
      if (normalizedCode) {
        this.store.upsertContractProfile({
          address,
          accountType: 'contract',
          runtimeCodeHash: hashHex(normalizedCode),
          codeSizeBytes: Math.max(0, (normalizedCode.length - 2) / 2)
        })
      }
    }

    profile = this.store.getContractProfile(address)
    return profile ?? { address, accountType: 'contract', isVerified: false }
  }

  private async discoverCreationTxForContract(address: string): Promise<{
    creatorAddress: string | null
    creatorTxHash: string
    creationBlockHeight: number
    creationBlockHash: string
    creationTimestamp: number | null
    codeHash: string | null
  } | null> {
    const head = normalizeHead(await this.safeRpc(() => this.rpc.getHead()))
    const scanBlocks = Math.min(head.height + 1, CONTRACT_CREATION_DISCOVERY_SCAN_MAX)
    const heights = Array.from({ length: scanBlocks }, (_, i) => head.height - i).filter((h) => h >= 0)

    for (const height of heights) {
      const block = await this.safeRpc(() => this.rpc.getBlockByNumber(height, true, true)).catch(() => null)
      if (!block) continue
      const blockDetail = normalizeBlockDetail(block)
      const txs = extractBlockTransactions(block)
      const receipts = extractBlockReceipts(block)

      for (let i = 0; i < txs.length; i += 1) {
        const tx = txs[i]
        const receipt = findReceiptForTx(tx, i, receipts)
        const detail = normalizeTxDetail(tx, receipt)
        const classification = classifyTransaction({
          txDetail: detail,
          rawTx: tx,
          receipt,
          knownTargetIsContract: false
        })
        if (classification.createdContractAddress !== address) continue
        const deployCode = extractDeployCode(tx) ?? extractTxInputData(tx, detail)
        return {
          creatorAddress: detail.from ?? null,
          creatorTxHash: normalizeTxHash(String(detail.hash)),
          creationBlockHeight: detail.blockHeight ?? blockDetail.height,
          creationBlockHash: detail.blockHash ? String(detail.blockHash) : String(blockDetail.hash),
          creationTimestamp: isDefinedNumber(blockDetail.time) ? blockDetail.time : null,
          codeHash: deployCode ? hashHex(deployCode) : null
        }
      }
    }

    return null
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
      const txs = extractBlockTransactions(block)
      const receipts = extractBlockReceipts(block)

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

        const receipt = findReceiptForTx(tx, i, receipts)
        const receiptForStatus =
          receipt ??
          {
            txHash: tx?.hash ?? tx?.txHash,
            blockNumber: detail.height,
            blockHash: detail.hash,
            status: tx?.status ?? 'SUCCESS'
          }
        const txDetail = normalizeTxDetail(tx, receiptForStatus)
        return {
          tx: txDetail,
          includedHeight: detail.height,
          includedBlockHash: String(detail.hash),
          includedIndex: i,
          timestamp: isDefinedNumber(detail.time) ? detail.time : null
        }
      }
    }

    return null
  }

  private async resolveIncludedTxTimestamp(includedHeight: number, includedBlockHash: string | null): Promise<number | null> {
    const blockLoaders: Array<() => Promise<unknown>> = []
    if (includedBlockHash) {
      blockLoaders.push(() => this.rpc.getBlockByHash(includedBlockHash, false, false))
    }
    blockLoaders.push(() => this.rpc.getBlockByNumber(includedHeight, false, false))

    for (const loadBlock of blockLoaders) {
      const rawBlock = await this.safeRpc(loadBlock).catch(() => null)
      if (!rawBlock) continue
      const summary = normalizeBlockSummary(rawBlock)
      if (isDefinedNumber(summary.time) && summary.time > 0) return summary.time
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

function isDefinedNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function toBigIntLike(value: unknown): bigint {
  if (typeof value === 'bigint') return value >= 0n ? value : 0n
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return 0n
    return BigInt(Math.floor(value))
  }
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return 0n
    try {
      const parsed = BigInt(trimmed)
      return parsed >= 0n ? parsed : 0n
    } catch {
      return 0n
    }
  }
  return 0n
}

function toHexQuantity(value: bigint): string {
  return `0x${value.toString(16)}`
}

function extractRichListItems(raw: Record<string, unknown>): any[] {
  const fromItems = raw.items
  if (Array.isArray(fromItems)) return fromItems
  const fromAddresses = raw.addresses
  if (Array.isArray(fromAddresses)) return fromAddresses
  const fromList = raw.list
  if (Array.isArray(fromList)) return fromList
  return []
}

function sumRichListBalances(raw: unknown): bigint {
  const items = extractRichListItems(asRecord(raw))
  return items.reduce((sum: bigint, item: any) => {
    if (Array.isArray(item) && item.length >= 2) {
      return sum + toBigIntLike(item[1])
    }
    return sum + toBigIntLike(item?.balance ?? item?.amount ?? item?.value)
  }, 0n)
}

function extractBlockTransactions(block: unknown): any[] {
  const txs = (block as any)?.txs
  if (Array.isArray(txs)) return txs
  const transactions = (block as any)?.transactions
  if (Array.isArray(transactions)) return transactions
  return []
}

function extractBlockReceipts(block: unknown): any[] {
  const receipts = (block as any)?.receipts
  if (Array.isArray(receipts)) return receipts
  const fromResults = (block as any)?.receiptResults
  if (Array.isArray(fromResults)) return fromResults
  return []
}

function findReceiptForTx(tx: any, index: number, receipts: any[]): any | null {
  if (tx?.receipt) return tx.receipt
  const direct = receipts[index]
  if (direct) return direct
  const txHash = tx?.hash ?? tx?.txHash
  if (!txHash) return null
  const match = receipts.find((receipt) => {
    const receiptHash = receipt?.txHash ?? receipt?.hash
    return typeof receiptHash === 'string' && receiptHash === txHash
  })
  return match ?? null
}

function buildContractDeployment(
  txDetail: TxDetail,
  rawTx: any,
  receipt: any,
  block: BlockDetail,
  classification: TxClassification
): ContractDeployment | null {
  if (txDetail.status === 'pending') return null
  if (classification.type !== 'contract_deployment') return null

  const contractAddress =
    classification.createdContractAddress ??
    extractContractAddressFromRecord(receipt) ??
    extractContractAddressFromRecord(rawTx?.receipt) ??
    extractContractAddressFromRecord(txDetail.receipt) ??
    extractContractAddressFromRecord(txDetail.raw)
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
    blockTime: isDefinedNumber(block.time) ? block.time : null,
    deployer: txDetail.from,
    contractAddress,
    status: classification.failed || txDetail.status === 'failed' ? 'failed' : 'confirmed',
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
      for (const key of ['kind', 'type', 'txType', 'tx_type', 'deploymentType', 'deployment_type', 'method', 'action', 'op', 'operation', 'module', 'function']) {
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

function extractContractAddressFromRecord(value: unknown): string | null {
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
  if (typeof value === 'bigint' && value >= 0n) return Number(value)
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

function findToAddress(rawTx: unknown): string | undefined {
  if (!rawTx || typeof rawTx !== 'object') return undefined
  const tx = rawTx as any
  const candidate = tx.to ?? tx.tx?.to ?? tx.body?.to ?? tx.payload?.v?.to ?? tx.tx?.payload?.v?.to
  return typeof candidate === 'string' && candidate.trim().length ? candidate : undefined
}

function normalizeBytecode(code: unknown): string | null {
  if (typeof code !== 'string') return null
  const trimmed = code.trim().toLowerCase()
  if (!trimmed.length || trimmed === '0x') return null
  if (trimmed.startsWith('0x')) {
    const body = trimmed.slice(2)
    if (!body.length || /^0+$/.test(body) || /[^0-9a-f]/i.test(body)) return null
    return `0x${body}`
  }
  if (/^[0-9a-f]+$/i.test(trimmed) && !/^0+$/i.test(trimmed)) {
    return `0x${trimmed}`
  }
  return null
}

function hasAccountSignals(account: unknown): boolean {
  if (!account || typeof account !== 'object') return false
  const record = account as Record<string, unknown>
  const nonce = toPositiveInt(record.nonce ?? record.seq)
  if (nonce !== null && nonce > 0) return true

  const balanceValue = record.balance
  if (typeof balanceValue === 'string') {
    try {
      const parsed = BigInt(balanceValue)
      if (parsed > 0n) return true
    } catch {
      // ignore
    }
  }
  if (typeof balanceValue === 'number' && Number.isFinite(balanceValue) && balanceValue > 0) return true
  if (typeof balanceValue === 'bigint' && balanceValue > 0n) return true
  return Object.keys(record).length > 0
}

function decodeHexString(value: string): Buffer | null {
  const normalized = normalizeBytecode(value)
  if (!normalized) return null
  const body = normalized.slice(2)
  if (body.length % 2 !== 0 || /[^0-9a-f]/i.test(body)) return null
  return Buffer.from(body, 'hex')
}

function hashHex(value: string): string {
  const raw = decodeHexString(value)
  const payload = raw ?? Buffer.from(value, 'utf-8')
  return `0x${createHash('sha3-256').update(payload).digest('hex')}`
}

function isNumeric(value: string): boolean {
  return /^[0-9]+$/.test(value)
}

function isNotFoundError(message: string): boolean {
  return /not found|unknown block|missing|does not exist/i.test(message)
}
