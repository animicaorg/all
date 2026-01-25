/**
 * Ledger Snapshot
 * 
 * Captures point-in-time snapshot of ledger state for reconciliation.
 */

import { AdminAPIClient } from '../../http_client.js';

export interface LedgerEntry {
  id: string;
  userId: string;
  asset: string;
  amount: string;
  balance: string;
  type: 'deposit' | 'withdrawal' | 'trade' | 'fee' | 'transfer';
  referenceId?: string;
  timestamp: string;
  metadata?: Record<string, any>;
}

export interface LedgerSnapshot {
  timestamp: string;
  entries: LedgerEntry[];
  balancesByUser: Map<string, Map<string, string>>;
  totalsByAsset: Map<string, string>;
  entryCount: number;
  snapshotHash: string;
}

/**
 * Take ledger snapshot
 */
export async function takeLedgerSnapshot(
  adminClient: AdminAPIClient
): Promise<LedgerSnapshot> {
  console.log(`[Ledger Snapshot] Fetching ledger data...`);
  
  // Fetch raw ledger data from admin API
  const response = await adminClient.getLedgerSnapshot();
  
  if (response.status !== 200) {
    throw new Error(`Failed to fetch ledger: ${response.status}`);
  }
  
  const entries: LedgerEntry[] = response.data.entries || [];
  
  console.log(`[Ledger Snapshot] Processing ${entries.length} entries...`);
  
  // Build balance maps
  const balancesByUser = new Map<string, Map<string, string>>();
  const totalsByAsset = new Map<string, string>();
  
  for (const entry of entries) {
    // Update user balances
    if (!balancesByUser.has(entry.userId)) {
      balancesByUser.set(entry.userId, new Map());
    }
    
    const userBalances = balancesByUser.get(entry.userId)!;
    userBalances.set(entry.asset, entry.balance);
    
    // Update asset totals
    const currentTotal = totalsByAsset.get(entry.asset) || '0';
    const newTotal = addBigInt(currentTotal, entry.balance);
    totalsByAsset.set(entry.asset, newTotal);
  }
  
  // Calculate snapshot hash
  const snapshotHash = calculateSnapshotHash(entries);
  
  const snapshot: LedgerSnapshot = {
    timestamp: new Date().toISOString(),
    entries,
    balancesByUser,
    totalsByAsset,
    entryCount: entries.length,
    snapshotHash,
  };
  
  console.log(`[Ledger Snapshot] Snapshot complete`);
  console.log(`[Ledger Snapshot] Hash: ${snapshotHash}`);
  console.log(`[Ledger Snapshot] Entries: ${entries.length}`);
  console.log(`[Ledger Snapshot] Users: ${balancesByUser.size}`);
  console.log(`[Ledger Snapshot] Assets: ${totalsByAsset.size}`);
  
  return snapshot;
}

/**
 * Get user balance from snapshot
 */
export function getUserBalance(
  snapshot: LedgerSnapshot,
  userId: string,
  asset: string
): string {
  const userBalances = snapshot.balancesByUser.get(userId);
  return userBalances?.get(asset) || '0';
}

/**
 * Get total asset balance
 */
export function getAssetTotal(
  snapshot: LedgerSnapshot,
  asset: string
): string {
  return snapshot.totalsByAsset.get(asset) || '0';
}

/**
 * Compare two snapshots
 */
export function compareSnapshots(
  snapshot1: LedgerSnapshot,
  snapshot2: LedgerSnapshot
): {
  identical: boolean;
  entryCountDiff: number;
  hashMatch: boolean;
  balanceDifferences: Array<{
    userId: string;
    asset: string;
    balance1: string;
    balance2: string;
    diff: string;
  }>;
} {
  const hashMatch = snapshot1.snapshotHash === snapshot2.snapshotHash;
  const entryCountDiff = snapshot2.entryCount - snapshot1.entryCount;
  const balanceDifferences: any[] = [];
  
  // Check all users from both snapshots
  const allUsers = new Set([
    ...snapshot1.balancesByUser.keys(),
    ...snapshot2.balancesByUser.keys(),
  ]);
  
  for (const userId of allUsers) {
    const balances1 = snapshot1.balancesByUser.get(userId) || new Map();
    const balances2 = snapshot2.balancesByUser.get(userId) || new Map();
    
    const allAssets = new Set([
      ...balances1.keys(),
      ...balances2.keys(),
    ]);
    
    for (const asset of allAssets) {
      const balance1 = balances1.get(asset) || '0';
      const balance2 = balances2.get(asset) || '0';
      
      if (balance1 !== balance2) {
        balanceDifferences.push({
          userId,
          asset,
          balance1,
          balance2,
          diff: subtractBigInt(balance2, balance1),
        });
      }
    }
  }
  
  return {
    identical: hashMatch && entryCountDiff === 0 && balanceDifferences.length === 0,
    entryCountDiff,
    hashMatch,
    balanceDifferences,
  };
}

/**
 * Calculate snapshot hash
 */
function calculateSnapshotHash(entries: LedgerEntry[]): string {
  const crypto = await import('crypto');
  
  // Sort entries by ID for deterministic hashing
  const sortedEntries = [...entries].sort((a, b) => a.id.localeCompare(b.id));
  
  // Create canonical representation
  const canonical = sortedEntries.map(e => 
    `${e.id}:${e.userId}:${e.asset}:${e.amount}:${e.balance}:${e.type}`
  ).join('|');
  
  return crypto.createHash('sha256').update(canonical).digest('hex');
}

/**
 * Add two bigint strings
 */
function addBigInt(a: string, b: string): string {
  return (BigInt(a) + BigInt(b)).toString();
}

/**
 * Subtract two bigint strings
 */
function subtractBigInt(a: string, b: string): string {
  return (BigInt(a) - BigInt(b)).toString();
}

/**
 * Export snapshot to JSON
 */
export function exportSnapshot(snapshot: LedgerSnapshot): string {
  return JSON.stringify({
    timestamp: snapshot.timestamp,
    snapshotHash: snapshot.snapshotHash,
    entryCount: snapshot.entryCount,
    entries: snapshot.entries,
    balancesByUser: Array.from(snapshot.balancesByUser.entries()).map(([userId, balances]) => ({
      userId,
      balances: Array.from(balances.entries()).map(([asset, balance]) => ({
        asset,
        balance,
      })),
    })),
    totalsByAsset: Array.from(snapshot.totalsByAsset.entries()).map(([asset, total]) => ({
      asset,
      total,
    })),
  }, null, 2);
}

/**
 * Load snapshot from JSON
 */
export function loadSnapshot(json: string): LedgerSnapshot {
  const data = JSON.parse(json);
  
  const balancesByUser = new Map<string, Map<string, string>>();
  for (const user of data.balancesByUser) {
    const balances = new Map<string, string>();
    for (const { asset, balance } of user.balances) {
      balances.set(asset, balance);
    }
    balancesByUser.set(user.userId, balances);
  }
  
  const totalsByAsset = new Map<string, string>();
  for (const { asset, total } of data.totalsByAsset) {
    totalsByAsset.set(asset, total);
  }
  
  return {
    timestamp: data.timestamp,
    snapshotHash: data.snapshotHash,
    entryCount: data.entryCount,
    entries: data.entries,
    balancesByUser,
    totalsByAsset,
  };
}
