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
  private backfillCursor = 0

  recordPending(hash: string, txData?: { from?: string; to?: string; value?: string; fee?: string; rawTx?: unknown }): string {
    const normalizedHash = normalizeTxHash(hash)
    const existing = this.records.get(normalizedHash)
    if (!existing) {
      this.records.set(normalizedHash, {
        tx_hash: normalizedHash,
        status: 'pending',
        included_height: null,
        included_block_hash: null,
        included_index: null,
        timestamp: null,
        from: txData?.from,
        to: txData?.to,
        value: txData?.value,
        fee: txData?.fee,
        rawTx: txData?.rawTx
      })
    } else if (existing.status === 'pending') {
      existing.from = existing.from ?? txData?.from
      existing.to = existing.to ?? txData?.to
      existing.value = existing.value ?? txData?.value
      existing.fee = existing.fee ?? txData?.fee
      existing.rawTx = existing.rawTx ?? txData?.rawTx
      this.records.set(normalizedHash, existing)
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
    const existing = this.records.get(normalizedHash)
    const updated: TxLookupRecord = {
      tx_hash: normalizedHash,
      status: 'confirmed',
      included_height: entry.includedHeight,
      included_block_hash: String(entry.includedBlockHash).toLowerCase(),
      included_index: entry.includedIndex,
      timestamp: entry.timestamp,
      from: entry.from ?? existing?.from,
      to: entry.to ?? existing?.to,
      value: entry.value ?? existing?.value,
      fee: entry.fee ?? existing?.fee,
      rawTx: entry.rawTx ?? existing?.rawTx,
      rawReceipt: entry.rawReceipt ?? existing?.rawReceipt
    }
    this.records.set(normalizedHash, updated)
    log.debug({ txHash: entry.hash, normalizedHash, result: 'upserted-confirmed' }, 'block ingestion processed tx')
    return updated
  }

  patchConfirmedFields(hash: string, patch: { from?: string; to?: string; value?: string; fee?: string; rawTx?: unknown; rawReceipt?: unknown }): TxLookupRecord | null {
    const normalizedHash = normalizeTxHash(hash)
    const existing = this.records.get(normalizedHash)
    if (!existing || existing.status !== 'confirmed') return null
    const updated: TxLookupRecord = {
      ...existing,
      from: patch.from ?? existing.from,
      to: patch.to ?? existing.to,
      value: patch.value ?? existing.value,
      fee: patch.fee ?? existing.fee,
      rawTx: patch.rawTx ?? existing.rawTx,
      rawReceipt: patch.rawReceipt ?? existing.rawReceipt
    }
    this.records.set(normalizedHash, updated)
    return updated
  }

  getMissingConfirmedFields(limit = 100): TxLookupRecord[] {
    const all = Array.from(this.records.values()).filter((record) =>
      record.status === 'confirmed' && (!record.from || !record.to || !record.value)
    )
    if (all.length === 0) return []
    const start = this.backfillCursor % all.length
    const ordered = [...all.slice(start), ...all.slice(0, start)]
    const selected = ordered.slice(0, limit)
    this.backfillCursor = (start + selected.length) % all.length
    return selected
  }


  countMissingConfirmedFields(): number {
    return Array.from(this.records.values()).filter((record) =>
      record.status === 'confirmed' && (!record.from || !record.to || !record.value)
    ).length
  }

  get(hash: string): TxLookupRecord | null {
    const normalizedHash = normalizeTxHash(hash)
    const found = this.records.get(normalizedHash) ?? null
    return found
  }
}
