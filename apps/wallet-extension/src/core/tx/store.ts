// Transaction store with idempotent state machine

import { TxStatus, type PendingTx } from '../../types/tx';
import { decodeAddress } from '../crypto/address';

export class TxStore {
  private txs: Map<string, PendingTx> = new Map();

  // Add or update transaction (idempotent)
  upsert(tx: PendingTx): void {
    const existing = this.txs.get(tx.txid);
    
    if (existing) {
      // Only update if new status is "later" in lifecycle
      if (this.isStatusLater(tx.status, existing.status)) {
        this.txs.set(tx.txid, {
          ...existing,
          ...tx,
          lastCheckedAt: Date.now(),
        });
      }
    } else {
      this.txs.set(tx.txid, tx);
    }
  }

  get(txid: string): PendingTx | undefined {
    return this.txs.get(txid);
  }

  getAll(): PendingTx[] {
    return Array.from(this.txs.values());
  }

  getByStatus(status: TxStatus): PendingTx[] {
    return this.getAll().filter(tx => tx.status === status);
  }

  // Get total pending outgoing amount (for balance calculation)
  getPendingOutgoing(fromAddress: string): bigint {
    let total = BigInt(0);
    const senderAddress = fromAddress.trim().toLowerCase();
    let senderDigestHex: string | null = null;

    try {
      senderDigestHex = this.toHex(decodeAddress(fromAddress).digest);
    } catch {
      senderDigestHex = null;
    }
    
    for (const tx of this.txs.values()) {
      // Only count active transactions
      if (this.isActive(tx.status)) {
        if (!this.isFromSender(tx, senderAddress, senderDigestHex)) continue;

        const amount = this.extractAmount(tx);
        if (amount !== null) total += amount;
      }
    }
    
    return total;
  }

  remove(txid: string): void {
    this.txs.delete(txid);
  }

  clear(): void {
    this.txs.clear();
  }

  // Check if status is "later" in lifecycle
  private isStatusLater(newStatus: TxStatus, oldStatus: TxStatus): boolean {
    const order: TxStatus[] = [
      TxStatus.CREATED_LOCAL,
      TxStatus.SUBMITTED,
      TxStatus.MEMPOOL_ACCEPTED,
      TxStatus.INCLUDED,
      TxStatus.CONFIRMED,
    ];
    
    const newIndex = order.indexOf(newStatus);
    const oldIndex = order.indexOf(oldStatus);
    
    return newIndex > oldIndex;
  }

  // Check if transaction is still active (not finalized)
  private isActive(status: TxStatus): boolean {
    return ![
      TxStatus.CONFIRMED,
      TxStatus.DROPPED,
      TxStatus.REORGED_OUT,
    ].includes(status);
  }

  private extractAmount(tx: PendingTx): bigint | null {
    const txBody = (tx as any)?.signedTx?.tx as any;
    if (!txBody || typeof txBody !== 'object') return null;

    const legacyAmount = txBody?.payload?.v?.amount;
    const directValue = txBody?.value;
    const nestedValue = txBody?.body?.value;

    for (const candidate of [legacyAmount, directValue, nestedValue]) {
      const parsed = this.toBigInt(candidate);
      if (parsed !== null) return parsed;
    }

    return null;
  }

  private isFromSender(tx: PendingTx, senderAddress: string, senderDigestHex: string | null): boolean {
    const txBody = (tx as any)?.signedTx?.tx as any;
    if (!txBody || typeof txBody !== 'object') return false;

    const fromAddressCandidates = [txBody.from, txBody?.body?.from]
      .filter((value): value is string => typeof value === 'string')
      .map((value) => value.trim().toLowerCase());

    if (fromAddressCandidates.includes(senderAddress)) return true;

    if (!senderDigestHex) return false;

    const digestCandidates = [
      txBody.from_addr,
      txBody.from,
      txBody?.body?.from_addr,
      txBody?.body?.from,
    ];

    for (const candidate of digestCandidates) {
      const bytes = this.toBytes(candidate);
      if (!bytes) continue;
      if (this.toHex(bytes) === senderDigestHex) return true;
    }

    return false;
  }

  private toBigInt(value: unknown): bigint | null {
    if (typeof value === 'bigint') return value;
    if (typeof value === 'number' && Number.isFinite(value) && Number.isInteger(value)) return BigInt(value);
    if (typeof value === 'string' && value.trim().length > 0) {
      try {
        return BigInt(value.trim());
      } catch {
        return null;
      }
    }
    return null;
  }

  private toBytes(value: unknown): Uint8Array | null {
    if (value instanceof Uint8Array) return value;
    if (Array.isArray(value) && value.every((item) => Number.isInteger(item) && item >= 0 && item <= 255)) {
      return Uint8Array.from(value as number[]);
    }
    return null;
  }

  private toHex(bytes: Uint8Array): string {
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  }

  // Serialize for storage
  toJSON(): Record<string, PendingTx> {
    const obj: Record<string, PendingTx> = {};
    for (const [txid, tx] of this.txs.entries()) {
      obj[txid] = tx;
    }
    return obj;
  }

  // Deserialize from storage
  static fromJSON(obj: Record<string, PendingTx>): TxStore {
    const store = new TxStore();
    for (const [txid, tx] of Object.entries(obj)) {
      store.txs.set(txid, tx);
    }
    return store;
  }
}
