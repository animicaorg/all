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

  // Mark any active pending tx from `fromAddress` whose nonce has already
  // been consumed on chain (i.e. nonce < committedNonce) as INCLUDED.
  // Returns the number of txs whose status changed. Used by the balance
  // path so confirmed sends stop being counted as pending outgoing once
  // the chain has actually applied them.
  markIncludedByCommittedNonce(fromAddress: string, committedNonce: number): number {
    if (!Number.isFinite(committedNonce) || committedNonce < 0) return 0;
    const senderAddress = fromAddress.trim().toLowerCase();
    let senderDigestHex: string | null = null;
    try {
      senderDigestHex = this.toHex(decodeAddress(fromAddress).digest);
    } catch {
      senderDigestHex = null;
    }

    let changed = 0;
    for (const tx of this.txs.values()) {
      if (!this.isActive(tx.status)) continue;
      if (!this.isFromSender(tx, senderAddress, senderDigestHex)) continue;
      const txNonce = this.extractNonce(tx);
      if (txNonce === null) continue;
      if (txNonce < committedNonce) {
        tx.status = TxStatus.INCLUDED;
        tx.lastCheckedAt = Date.now();
        changed += 1;
      }
    }
    return changed;
  }

  private extractNonce(tx: PendingTx): number | null {
    const txBody = (tx as any)?.signedTx?.tx as any;
    if (!txBody || typeof txBody !== 'object') return null;
    const candidates = [txBody.nonce, txBody?.body?.nonce];
    for (const candidate of candidates) {
      if (typeof candidate === 'number' && Number.isInteger(candidate) && candidate >= 0) return candidate;
      if (typeof candidate === 'string' && /^\d+$/.test(candidate.trim())) {
        const parsed = Number(candidate.trim());
        if (Number.isFinite(parsed) && Number.isInteger(parsed) && parsed >= 0) return parsed;
      }
      if (typeof candidate === 'bigint' && candidate >= 0n && candidate <= BigInt(Number.MAX_SAFE_INTEGER)) {
        return Number(candidate);
      }
    }
    return null;
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
    // Numeric-keyed object form produced by toJsonSafe(value, 'storage'):
    // { "0": 12, "1": 34, ... }. Must be recovered when comparing the
    // sender of a stored pending tx against an active wallet address.
    if (value && typeof value === 'object') {
      const obj = value as Record<string, unknown>;
      const keys = Object.keys(obj);
      if (keys.length > 0 && keys.every((k) => /^\d+$/.test(k))) {
        const indices = keys.map((k) => Number(k)).sort((a, b) => a - b);
        if (indices[0] !== 0 || indices[indices.length - 1] !== indices.length - 1) return null;
        const bytes = new Uint8Array(indices.length);
        for (const i of indices) {
          const v = obj[String(i)];
          if (typeof v !== 'number' || !Number.isInteger(v) || v < 0 || v > 255) return null;
          bytes[i] = v;
        }
        return bytes;
      }
    }
    // 0x-hex string form (used by toJsonSafe 'rpc' encoding).
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (/^0x[0-9a-f]*$/i.test(trimmed) && trimmed.length % 2 === 0) {
        const hex = trimmed.slice(2);
        const bytes = new Uint8Array(hex.length / 2);
        for (let i = 0; i < hex.length; i += 2) {
          bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
        }
        return bytes;
      }
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
