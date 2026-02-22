import { normalizeTxHash } from './txHash.js'
import pino from 'pino'

const log = pino({ name: 'tx-lifecycle' })

export interface TxLookupRecord {
  tx_hash: string
  status: 'pending' | 'confirmed'
  included_height: number | null
  included_block_hash: string | null
  included_index: number | null
  timestamp: number | null
  from?: string
  to?: string
  value?: string
  fee?: string
  rawTx?: unknown
  rawReceipt?: unknown
}

export class TxLifecycleStore {
  private readonly records = new Map<string, TxLookupRecord>()

  recordPending(hash: string): string {
    const normalizedHash = normalizeTxHash(hash)
    const existing = this.records.get(normalizedHash)
    if (!existing) {
      this.records.set(normalizedHash, {
        tx_hash: normalizedHash,
        status: 'pending',
        included_height: null,
        included_block_hash: null,
        included_index: null,
        timestamp: null
      })
    }
    log.debug({ txHash: hash, normalizedHash, storageKey: normalizedHash }, 'mempool observed tx')
    return normalizedHash
  }

  upsertConfirmed(entry: {
    hash: string
    includedHeight: number
    includedBlockHash: string
    includedIndex: number
    timestamp: number | null
    from?: string
    to?: string
    value?: string
    fee?: string
    rawTx?: unknown
    rawReceipt?: unknown
  }): TxLookupRecord {
    const normalizedHash = normalizeTxHash(entry.hash)
    const updated: TxLookupRecord = {
      tx_hash: normalizedHash,
      status: 'confirmed',
      included_height: entry.includedHeight,
      included_block_hash: String(entry.includedBlockHash).toLowerCase(),
      included_index: entry.includedIndex,
      timestamp: entry.timestamp,
      from: entry.from,
      to: entry.to,
      value: entry.value,
      fee: entry.fee,
      rawTx: entry.rawTx,
      rawReceipt: entry.rawReceipt
    }
    this.records.set(normalizedHash, updated)
    log.debug({ txHash: entry.hash, normalizedHash, result: 'upserted-confirmed' }, 'block ingestion processed tx')
    return updated
  }

  get(hash: string): TxLookupRecord | null {
    const normalizedHash = normalizeTxHash(hash)
    const found = this.records.get(normalizedHash) ?? null
    return found
  }
}
