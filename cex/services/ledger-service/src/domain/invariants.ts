/**
 * Invariant checks for double-entry accounting
 */

import type { LedgerEntry } from "./types.js";

/**
 * Verify that a set of ledger entries balance (debits = credits) per asset
 * This is the core invariant of double-entry bookkeeping
 */
export function verifyBalanced(entries: LedgerEntry[]): { ok: boolean; errors: string[] } {
  const errors: string[] = [];
  
  // Group by asset
  const byAsset = new Map<string, { debits: bigint; credits: bigint }>();
  
  for (const entry of entries) {
    if (!byAsset.has(entry.assetId)) {
      byAsset.set(entry.assetId, { debits: 0n, credits: 0n });
    }
    
    const totals = byAsset.get(entry.assetId)!;
    
    if (entry.direction === "DEBIT") {
      totals.debits += entry.amountAtoms;
    } else if (entry.direction === "CREDIT") {
      totals.credits += entry.amountAtoms;
    } else {
      errors.push(`Invalid direction: ${entry.direction} for entry ${entry.id}`);
    }
  }
  
  // Check that debits = credits for each asset
  for (const [assetId, totals] of byAsset.entries()) {
    if (totals.debits !== totals.credits) {
      errors.push(
        `Asset ${assetId} not balanced: debits=${totals.debits}, credits=${totals.credits}`
      );
    }
  }
  
  return {
    ok: errors.length === 0,
    errors
  };
}

/**
 * Check that all amounts are positive
 */
export function verifyPositiveAmounts(entries: LedgerEntry[]): { ok: boolean; errors: string[] } {
  const errors: string[] = [];
  
  for (const entry of entries) {
    if (entry.amountAtoms <= 0n) {
      errors.push(`Non-positive amount in entry ${entry.id}: ${entry.amountAtoms}`);
    }
  }
  
  return {
    ok: errors.length === 0,
    errors
  };
}

/**
 * Validate that a balance is non-negative
 */
export function verifyNonNegativeBalance(balance: bigint, accountId: string, assetId: string): void {
  if (balance < 0n) {
    throw new Error(`Negative balance for account ${accountId}, asset ${assetId}: ${balance}`);
  }
}

/**
 * Validate that locked + available balances are consistent
 */
export function verifyBalanceConsistency(
  availableAtoms: bigint,
  lockedAtoms: bigint,
  accountId: string,
  assetId: string
): { ok: boolean; errors: string[] } {
  const errors: string[] = [];
  
  if (availableAtoms < 0n) {
    errors.push(`Negative available balance for ${accountId}/${assetId}: ${availableAtoms}`);
  }
  
  if (lockedAtoms < 0n) {
    errors.push(`Negative locked balance for ${accountId}/${assetId}: ${lockedAtoms}`);
  }
  
  return {
    ok: errors.length === 0,
    errors
  };
}

/**
 * Ensure sequence is monotonic (no gaps)
 */
export function verifySequenceMonotonic(
  currentSeq: bigint,
  nextSeq: bigint,
  allowGaps: boolean = false
): { ok: boolean; error?: string } {
  if (nextSeq <= currentSeq) {
    return {
      ok: false,
      error: `Sequence not monotonic: current=${currentSeq}, next=${nextSeq}`
    };
  }
  
  if (!allowGaps && nextSeq !== currentSeq + 1n) {
    return {
      ok: false,
      error: `Sequence gap detected: current=${currentSeq}, next=${nextSeq}`
    };
  }
  
  return { ok: true };
}
