/**
 * Ledger Client
 * 
 * Convenience wrapper around the LedgerService with high-level methods
 * for querying user balances and account information.
 */

import { PrismaClient, LedgerAccountType, LedgerAccountOwnerType } from '@prisma/client';
import { Decimal } from '@prisma/client/runtime/library';
import { LedgerService } from './ledger.js';
import { Logger } from '../utils/logger.js';

export interface UserBalance {
  asset: string;
  available: string;
  locked: string;
  total: string;
}

export interface AccountBalance {
  accountId: string;
  accountType: LedgerAccountType;
  asset: string;
  balance: string;
}

export class LedgerClient {
  private ledgerService: LedgerService;
  private prisma: PrismaClient;
  private logger: Logger;

  constructor(prisma: PrismaClient, logger: Logger) {
    this.prisma = prisma;
    this.logger = logger;
    this.ledgerService = new LedgerService(prisma);
  }

  /**
   * Get all balances for a user across all assets
   */
  async getUserBalances(userId: string): Promise<UserBalance[]> {
    // Query all user accounts
    const accounts = await this.prisma.ledgerAccount.findMany({
      where: {
        ownerType: LedgerAccountOwnerType.USER,
        ownerId: userId,
      },
    });

    // Group by asset and aggregate balances
    const balancesByAsset = new Map<string, { available: Decimal; locked: Decimal }>();

    for (const account of accounts) {
      const asset = account.asset;
      if (!balancesByAsset.has(asset)) {
        balancesByAsset.set(asset, { available: new Decimal(0), locked: new Decimal(0) });
      }

      const balances = balancesByAsset.get(asset)!;

      if (account.type === LedgerAccountType.AVAILABLE) {
        balances.available = balances.available.add(account.balance);
      } else if (account.type === LedgerAccountType.LOCKED) {
        balances.locked = balances.locked.add(account.balance);
      }
    }

    // Convert to response format
    const result: UserBalance[] = [];
    for (const [asset, balances] of balancesByAsset.entries()) {
      const total = balances.available.add(balances.locked);
      result.push({
        asset,
        available: balances.available.toString(),
        locked: balances.locked.toString(),
        total: total.toString(),
      });
    }

    return result;
  }

  /**
   * Get available balance for a specific asset
   */
  async getAvailableBalance(userId: string, assetId: string): Promise<string> {
    const account = await this.prisma.ledgerAccount.findFirst({
      where: {
        ownerType: LedgerAccountOwnerType.USER,
        ownerId: userId,
        asset: assetId,
        type: LedgerAccountType.AVAILABLE,
      },
    });

    return account ? account.balance.toString() : '0';
  }

  /**
   * Get locked balance for a specific asset
   */
  async getLockedBalance(userId: string, assetId: string): Promise<string> {
    const account = await this.prisma.ledgerAccount.findFirst({
      where: {
        ownerType: LedgerAccountOwnerType.USER,
        ownerId: userId,
        asset: assetId,
        type: LedgerAccountType.LOCKED,
      },
    });

    return account ? account.balance.toString() : '0';
  }

  /**
   * Get total balance (available + locked) for a specific asset
   */
  async getTotalBalance(userId: string, assetId: string): Promise<string> {
    const [available, locked] = await Promise.all([
      this.getAvailableBalance(userId, assetId),
      this.getLockedBalance(userId, assetId),
    ]);

    const total = new Decimal(available).add(new Decimal(locked));
    return total.toString();
  }

  /**
   * Get all accounts for a user
   */
  async getUserAccounts(userId: string): Promise<AccountBalance[]> {
    const accounts = await this.prisma.ledgerAccount.findMany({
      where: {
        ownerType: LedgerAccountOwnerType.USER,
        ownerId: userId,
      },
    });

    return accounts.map(account => ({
      accountId: account.id,
      accountType: account.type,
      asset: account.asset,
      balance: account.balance.toString(),
    }));
  }

  /**
   * Check if user has sufficient available balance
   */
  async hasSufficientBalance(userId: string, assetId: string, requiredAmount: string): Promise<boolean> {
    const available = await this.getAvailableBalance(userId, assetId);
    return new Decimal(available).greaterThanOrEqualTo(new Decimal(requiredAmount));
  }

  /**
   * Get the underlying LedgerService for direct access
   */
  getLedgerService(): LedgerService {
    return this.ledgerService;
  }
}
