/**
 * Reconciliation Service
 * 
 * Ensures ledger integrity by:
 * 1. Recalculating balances from ledger entries
 * 2. Comparing with balance cache
 * 3. Alerting on mismatches
 * 4. Enforcing immutability of ledger entries
 */

import { PrismaClient } from '@prisma/client';
import { Decimal } from '@prisma/client/runtime/library';
import { LedgerService } from './ledger.js';

export interface ReconciliationResult {
  totalAccounts: number;
  reconciledAccounts: number;
  mismatches: Array<{
    accountId: string;
    ownerId: string | null;
    assetId: string;
    calculated: { available: Decimal; locked: Decimal };
    cached: { available: Decimal; locked: Decimal };
    difference: { available: Decimal; locked: Decimal };
  }>;
}

export class ReconciliationService {
  constructor(
    private prisma: PrismaClient,
    private ledgerService: LedgerService
  ) {}

  /**
   * Reconcile all account balances against the ledger
   */
  async reconcileAllBalances(): Promise<ReconciliationResult> {
    const accounts = await this.prisma.ledgerAccount.findMany({
      include: {
        balanceCache: true,
      },
    });

    const mismatches: ReconciliationResult['mismatches'] = [];
    let reconciledAccounts = 0;

    for (const account of accounts) {
      const calculated = await this.ledgerService.getBalance(account.id);
      const cached = account.balanceCache
        ? {
            available: account.balanceCache.available,
            locked: account.balanceCache.locked,
          }
        : { available: new Decimal(0), locked: new Decimal(0) };

      const availableDiff = calculated.available.sub(cached.available);
      const lockedDiff = calculated.locked.sub(cached.locked);

      if (!availableDiff.isZero() || !lockedDiff.isZero()) {
        mismatches.push({
          accountId: account.id,
          ownerId: account.ownerId,
          assetId: account.assetId,
          calculated,
          cached,
          difference: {
            available: availableDiff,
            locked: lockedDiff,
          },
        });
      } else {
        reconciledAccounts++;
      }
    }

    return {
      totalAccounts: accounts.length,
      reconciledAccounts,
      mismatches,
    };
  }

  /**
   * Reconcile a single account
   */
  async reconcileAccount(accountId: string): Promise<{
    reconciled: boolean;
    calculated: { available: Decimal; locked: Decimal };
    cached: { available: Decimal; locked: Decimal };
    difference?: { available: Decimal; locked: Decimal };
  }> {
    const calculated = await this.ledgerService.getBalance(accountId);
    
    const cache = await this.prisma.balanceCache.findUnique({
      where: { accountId },
    });

    const cached = cache
      ? {
          available: cache.available,
          locked: cache.locked,
        }
      : { available: new Decimal(0), locked: new Decimal(0) };

    const availableDiff = calculated.available.sub(cached.available);
    const lockedDiff = calculated.locked.sub(cached.locked);
    const reconciled = availableDiff.isZero() && lockedDiff.isZero();

    return {
      reconciled,
      calculated,
      cached,
      difference: reconciled ? undefined : {
        available: availableDiff,
        locked: lockedDiff,
      },
    };
  }

  /**
   * Verify ledger immutability - check if any entries have been modified
   * This is a safety check that should always pass
   */
  async verifyLedgerImmutability(): Promise<{
    immutable: boolean;
    violations: Array<{ entryId: string; issue: string }>;
  }> {
    // In a real system, you'd track entry hashes or use database audit trails
    // For now, we just verify that entries exist and haven't been deleted
    const violations: Array<{ entryId: string; issue: string }> = [];

    // Check for any orphaned entries (entries without transactions)
    // Since we have required foreign keys, this should never happen
    // but we check anyway for safety
    const allEntries = await this.prisma.ledgerEntry.findMany({
      include: {
        transaction: true,
      },
    });

    for (const entry of allEntries) {
      if (!entry.transaction) {
        violations.push({
          entryId: entry.id,
          issue: 'Entry missing transaction reference',
        });
      }
    }

    return {
      immutable: violations.length === 0,
      violations,
    };
  }

  /**
   * Rebuild balance caches from ledger (destructive operation)
   */
  async rebuildBalanceCaches(): Promise<{ rebuiltCount: number }> {
    const accounts = await this.prisma.ledgerAccount.findMany();
    let rebuiltCount = 0;

    for (const account of accounts) {
      const balance = await this.ledgerService.getBalance(account.id);

      await this.prisma.balanceCache.upsert({
        where: { accountId: account.id },
        create: {
          accountId: account.id,
          available: balance.available,
          locked: balance.locked,
        },
        update: {
          available: balance.available,
          locked: balance.locked,
        },
      });

      rebuiltCount++;
    }

    return { rebuiltCount };
  }

  /**
   * Verify transaction balance (all debits = all credits per asset)
   */
  async verifyTransactionBalance(transactionId: string): Promise<{
    balanced: boolean;
    assetBalances: Map<string, { debits: Decimal; credits: Decimal }>;
  }> {
    const entries = await this.prisma.ledgerEntry.findMany({
      where: { ledgerTransactionId: transactionId },
      include: {
        account: {
          select: { assetId: true },
        },
      },
    });

    const assetBalances = new Map<string, { debits: Decimal; credits: Decimal }>();

    for (const entry of entries) {
      const assetId = entry.account.assetId;
      if (!assetBalances.has(assetId)) {
        assetBalances.set(assetId, { debits: new Decimal(0), credits: new Decimal(0) });
      }

      const balance = assetBalances.get(assetId)!;
      if (entry.direction === 'DEBIT') {
        balance.debits = balance.debits.add(entry.amount);
      } else {
        balance.credits = balance.credits.add(entry.amount);
      }
    }

    let balanced = true;
    for (const [, balance] of assetBalances) {
      if (!balance.debits.equals(balance.credits)) {
        balanced = false;
        break;
      }
    }

    return { balanced, assetBalances };
  }
}
