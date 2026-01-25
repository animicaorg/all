/**
 * Invariant Checks
 * 
 * Comprehensive invariant verification for exchange correctness:
 * - Double-entry integrity
 * - Solvency
 * - No negative balances
 * - No duplicate credits
 * - Trade-ledger consistency
 */

import { LedgerSnapshot, LedgerEntry } from './ledger_snapshot.js';
import { AdminAPIClient } from '../../http_client.js';

export interface InvariantResult {
  name: string;
  passed: boolean;
  message: string;
  details?: any;
}

export interface InvariantReport {
  timestamp: string;
  allPassed: boolean;
  results: InvariantResult[];
  summary: {
    passed: number;
    failed: number;
    total: number;
  };
}

/**
 * Run all invariant checks
 */
export async function checkAllInvariants(
  snapshot: LedgerSnapshot,
  adminClient: AdminAPIClient
): Promise<InvariantReport> {
  console.log(`[Invariants] Running all checks...`);
  
  const results: InvariantResult[] = [];
  
  // 1. Double-entry integrity
  results.push(await checkDoubleEntry(snapshot));
  
  // 2. Solvency
  results.push(await checkSolvency(snapshot, adminClient));
  
  // 3. No negative balances
  results.push(checkNoNegativeBalances(snapshot));
  
  // 4. No duplicate credits
  results.push(checkNoDuplicateCredits(snapshot));
  
  // 5. Trade-ledger consistency
  results.push(await checkTradeLedgerConsistency(snapshot, adminClient));
  
  // 6. Balance sum consistency
  results.push(checkBalanceSumConsistency(snapshot));
  
  const allPassed = results.every(r => r.passed);
  const passed = results.filter(r => r.passed).length;
  const failed = results.filter(r => !r.passed).length;
  
  const report: InvariantReport = {
    timestamp: new Date().toISOString(),
    allPassed,
    results,
    summary: {
      passed,
      failed,
      total: results.length,
    },
  };
  
  console.log(`[Invariants] Check complete: ${passed}/${results.length} passed`);
  
  if (!allPassed) {
    console.error(`[Invariants] FAILED: ${failed} invariant(s) violated`);
    results.filter(r => !r.passed).forEach(r => {
      console.error(`  ✗ ${r.name}: ${r.message}`);
    });
  } else {
    console.log(`[Invariants] ✓ All invariants satisfied`);
  }
  
  return report;
}

/**
 * Invariant 1: Double-entry integrity
 * Every debit must have a corresponding credit
 */
async function checkDoubleEntry(snapshot: LedgerSnapshot): Promise<InvariantResult> {
  console.log(`[Invariants] Checking double-entry integrity...`);
  
  // Group entries by reference ID
  const entriesByRef = new Map<string, LedgerEntry[]>();
  
  for (const entry of snapshot.entries) {
    if (entry.referenceId) {
      if (!entriesByRef.has(entry.referenceId)) {
        entriesByRef.set(entry.referenceId, []);
      }
      entriesByRef.get(entry.referenceId)!.push(entry);
    }
  }
  
  const violations: string[] = [];
  
  // Check each group balances to zero
  for (const [refId, entries] of entriesByRef) {
    const totalByAsset = new Map<string, bigint>();
    
    for (const entry of entries) {
      const current = totalByAsset.get(entry.asset) || 0n;
      const amount = BigInt(entry.amount);
      totalByAsset.set(entry.asset, current + amount);
    }
    
    // Each asset should sum to zero
    for (const [asset, total] of totalByAsset) {
      if (total !== 0n) {
        violations.push(`Reference ${refId}: ${asset} doesn't balance (total: ${total})`);
      }
    }
  }
  
  return {
    name: 'double_entry_integrity',
    passed: violations.length === 0,
    message: violations.length === 0
      ? 'All entries balance correctly'
      : `${violations.length} double-entry violations found`,
    details: violations.length > 0 ? { violations } : undefined,
  };
}

/**
 * Invariant 2: Solvency
 * Total user balances <= Total deposits - Total withdrawals
 */
async function checkSolvency(
  snapshot: LedgerSnapshot,
  adminClient: AdminAPIClient
): Promise<InvariantResult> {
  console.log(`[Invariants] Checking solvency...`);
  
  try {
    // Get deposit and withdrawal totals
    const deposits = await adminClient.getDeposits({ status: 'confirmed' });
    const withdrawals = await adminClient.getWithdrawals({ status: 'confirmed' });
    
    const depositsByAsset = new Map<string, bigint>();
    const withdrawalsByAsset = new Map<string, bigint>();
    
    for (const deposit of deposits.data) {
      const current = depositsByAsset.get(deposit.asset) || 0n;
      depositsByAsset.set(deposit.asset, current + BigInt(deposit.amount));
    }
    
    for (const withdrawal of withdrawals.data) {
      const current = withdrawalsByAsset.get(withdrawal.asset) || 0n;
      withdrawalsByAsset.set(withdrawal.asset, current + BigInt(withdrawal.amount));
    }
    
    // Check solvency for each asset
    const violations: string[] = [];
    
    for (const [asset, userTotal] of snapshot.totalsByAsset) {
      const deposits = depositsByAsset.get(asset) || 0n;
      const withdrawals = withdrawalsByAsset.get(asset) || 0n;
      const expected = deposits - withdrawals;
      const actual = BigInt(userTotal);
      
      if (actual > expected) {
        violations.push(
          `${asset}: user balances (${actual}) exceed net deposits (${expected})`
        );
      }
    }
    
    return {
      name: 'solvency',
      passed: violations.length === 0,
      message: violations.length === 0
        ? 'Exchange is solvent'
        : `${violations.length} solvency violations`,
      details: violations.length > 0 ? { violations } : undefined,
    };
    
  } catch (error) {
    return {
      name: 'solvency',
      passed: false,
      message: `Failed to check solvency: ${(error as Error).message}`,
    };
  }
}

/**
 * Invariant 3: No negative balances
 */
function checkNoNegativeBalances(snapshot: LedgerSnapshot): InvariantResult {
  console.log(`[Invariants] Checking for negative balances...`);
  
  const violations: string[] = [];
  
  for (const [userId, balances] of snapshot.balancesByUser) {
    for (const [asset, balance] of balances) {
      if (BigInt(balance) < 0n) {
        violations.push(`User ${userId}: ${asset} balance is negative (${balance})`);
      }
    }
  }
  
  return {
    name: 'no_negative_balances',
    passed: violations.length === 0,
    message: violations.length === 0
      ? 'No negative balances found'
      : `${violations.length} negative balances found`,
    details: violations.length > 0 ? { violations } : undefined,
  };
}

/**
 * Invariant 4: No duplicate credits
 * Each external transaction (deposit) should be credited only once
 */
function checkNoDuplicateCredits(snapshot: LedgerSnapshot): InvariantResult {
  console.log(`[Invariants] Checking for duplicate credits...`);
  
  const depositEntries = snapshot.entries.filter(e => e.type === 'deposit');
  
  // Group by transaction hash
  const txHashCounts = new Map<string, number>();
  
  for (const entry of depositEntries) {
    const txHash = entry.metadata?.txHash;
    if (txHash) {
      txHashCounts.set(txHash, (txHashCounts.get(txHash) || 0) + 1);
    }
  }
  
  const duplicates = Array.from(txHashCounts.entries())
    .filter(([_, count]) => count > 1)
    .map(([txHash, count]) => ({ txHash, count }));
  
  return {
    name: 'no_duplicate_credits',
    passed: duplicates.length === 0,
    message: duplicates.length === 0
      ? 'No duplicate deposits found'
      : `${duplicates.length} transactions credited multiple times`,
    details: duplicates.length > 0 ? { duplicates } : undefined,
  };
}

/**
 * Invariant 5: Trade-ledger consistency
 * Sum of trade entries should match ledger entries
 */
async function checkTradeLedgerConsistency(
  snapshot: LedgerSnapshot,
  adminClient: AdminAPIClient
): Promise<InvariantResult> {
  console.log(`[Invariants] Checking trade-ledger consistency...`);
  
  try {
    // Get all trades
    const tradesResponse = await adminClient.get('/api/admin/trades', { limit: 10000 });
    const trades = tradesResponse.data.trades || [];
    
    const tradeEntries = snapshot.entries.filter(e => e.type === 'trade');
    
    // Check that every trade has corresponding ledger entries
    const violations: string[] = [];
    
    for (const trade of trades) {
      const tradeId = trade.id;
      const relatedEntries = tradeEntries.filter(e => e.referenceId === tradeId);
      
      if (relatedEntries.length === 0) {
        violations.push(`Trade ${tradeId} has no ledger entries`);
        continue;
      }
      
      // Should have 2 entries per trade (buyer and seller)
      if (relatedEntries.length !== 2) {
        violations.push(`Trade ${tradeId} has ${relatedEntries.length} entries (expected 2)`);
      }
    }
    
    return {
      name: 'trade_ledger_consistency',
      passed: violations.length === 0,
      message: violations.length === 0
        ? 'Trades and ledger are consistent'
        : `${violations.length} inconsistencies found`,
      details: violations.length > 0 ? { violations } : undefined,
    };
    
  } catch (error) {
    return {
      name: 'trade_ledger_consistency',
      passed: false,
      message: `Failed to check: ${(error as Error).message}`,
    };
  }
}

/**
 * Invariant 6: Balance sum consistency
 * Sum of all balance fields should match totalsByAsset
 */
function checkBalanceSumConsistency(snapshot: LedgerSnapshot): InvariantResult {
  console.log(`[Invariants] Checking balance sum consistency...`);
  
  const calculatedTotals = new Map<string, bigint>();
  
  for (const [_, balances] of snapshot.balancesByUser) {
    for (const [asset, balance] of balances) {
      const current = calculatedTotals.get(asset) || 0n;
      calculatedTotals.set(asset, current + BigInt(balance));
    }
  }
  
  const violations: string[] = [];
  
  for (const [asset, expectedTotal] of snapshot.totalsByAsset) {
    const calculatedTotal = calculatedTotals.get(asset) || 0n;
    
    if (calculatedTotal !== BigInt(expectedTotal)) {
      violations.push(
        `${asset}: calculated total (${calculatedTotal}) != stored total (${expectedTotal})`
      );
    }
  }
  
  return {
    name: 'balance_sum_consistency',
    passed: violations.length === 0,
    message: violations.length === 0
      ? 'Balance sums are consistent'
      : `${violations.length} inconsistencies found`,
    details: violations.length > 0 ? { violations } : undefined,
  };
}
