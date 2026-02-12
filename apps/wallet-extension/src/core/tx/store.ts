// Transaction store with idempotent state machine

import { TxStatus, type PendingTx } from '../../types/tx';

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
    
    for (const tx of this.txs.values()) {
      // Only count active transactions
      if (this.isActive(tx.status)) {
        const payload = tx.signedTx.tx.payload.v as any;
        if (payload.amount !== undefined) {
          total += BigInt(payload.amount);
        }
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
