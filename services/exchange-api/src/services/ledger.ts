/**
 * Double-Entry Ledger Service
 * 
 * Implements strict double-entry accounting with the following invariants:
 * 1. For any ledger transaction, debits must equal credits per asset
 * 2. All entries are immutable (no updates/deletes)
 * 3. Every change to user funds must be represented as balanced ledger entries
 */

import { PrismaClient, Prisma, LedgerTransactionType, EntryDirection, LedgerAccountOwnerType, LedgerAccountType } from '@prisma/client';
import { Decimal } from '@prisma/client/runtime/library';

export interface LedgerEntryInput {
  accountId: string;
  direction: EntryDirection;
  amount: Decimal | string | number;
}

export interface CreateTransactionInput {
  type: LedgerTransactionType;
  entries: LedgerEntryInput[];
  externalRef?: string;
  idempotencyKey?: string;
  metadata?: Record<string, any>;
}

export class LedgerService {
  constructor(private prisma: PrismaClient) {}

  /**
   * Create a new ledger transaction with balanced entries.
   * Validates that debits equal credits per asset before committing.
   */
  async createTransaction(input: CreateTransactionInput): Promise<{ transactionId: string }> {
    // Validate entries before creating transaction
    await this.validateBalancedEntries(input.entries);

    // Use a transaction to ensure atomicity
    const result = await this.prisma.$transaction(async (tx) => {
      // Create the ledger transaction
      const transaction = await tx.ledgerTransaction.create({
        data: {
          type: input.type,
          externalRef: input.externalRef,
          idempotencyKey: input.idempotencyKey,
          metadata: input.metadata || {},
        },
      });

      // Create all entries
      const entryPromises = input.entries.map((entry) =>
        tx.ledgerEntry.create({
          data: {
            ledgerTransactionId: transaction.id,
            accountId: entry.accountId,
            direction: entry.direction,
            amount: new Decimal(entry.amount.toString()),
          },
        })
      );

      await Promise.all(entryPromises);

      // Update balance cache for all affected accounts
      await this.updateBalanceCaches(tx, input.entries);

      return { transactionId: transaction.id };
    }, {
      isolationLevel: Prisma.TransactionIsolationLevel.Serializable,
    });

    return result;
  }

  /**
   * Validate that debits equal credits per asset in the given entries.
   * Throws an error if the entries are not balanced.
   */
  private async validateBalancedEntries(entries: LedgerEntryInput[]): Promise<void> {
    // Group entries by account to get asset information
    const accountIds = [...new Set(entries.map((e) => e.accountId))];
    const accounts = await this.prisma.ledgerAccount.findMany({
      where: { id: { in: accountIds } },
      select: { id: true, assetId: true },
    });

    const accountAssetMap = new Map(accounts.map((a) => [a.id, a.assetId]));

    // Group by asset and calculate sums
    const assetBalances = new Map<string, { debits: Decimal; credits: Decimal }>();

    for (const entry of entries) {
      const assetId = accountAssetMap.get(entry.accountId);
      if (!assetId) {
        throw new Error(`Account not found: ${entry.accountId}`);
      }

      if (!assetBalances.has(assetId)) {
        assetBalances.set(assetId, { debits: new Decimal(0), credits: new Decimal(0) });
      }

      const balance = assetBalances.get(assetId)!;
      const amount = new Decimal(entry.amount.toString());

      if (amount.lte(0)) {
        throw new Error(`Amount must be positive: ${amount.toString()}`);
      }

      if (entry.direction === EntryDirection.DEBIT) {
        balance.debits = balance.debits.add(amount);
      } else {
        balance.credits = balance.credits.add(amount);
      }
    }

    // Verify balance for each asset
    for (const [assetId, balance] of assetBalances) {
      if (!balance.debits.equals(balance.credits)) {
        throw new Error(
          `Unbalanced transaction for asset ${assetId}: debits=${balance.debits.toString()}, credits=${balance.credits.toString()}`
        );
      }
    }
  }

  /**
   * Update balance caches for all affected accounts
   */
  private async updateBalanceCaches(
    tx: Omit<PrismaClient, '$connect' | '$disconnect' | '$on' | '$transaction' | '$use' | '$extends'>,
    entries: LedgerEntryInput[]
  ): Promise<void> {
    const accountIds = [...new Set(entries.map((e) => e.accountId))];

    for (const accountId of accountIds) {
      // Recalculate balance from ledger entries
      const balance = await this.calculateAccountBalance(tx, accountId);

      // Upsert balance cache
      await tx.balanceCache.upsert({
        where: { accountId },
        create: {
          accountId,
          available: balance.available,
          locked: balance.locked,
        },
        update: {
          available: balance.available,
          locked: balance.locked,
        },
      });
    }
  }

  /**
   * Calculate account balance from ledger entries
   * 
   * Note: This calculates the raw balance for a single account.
   * For user-facing balance queries, you should aggregate across account types:
   * - AVAILABLE accounts: represent spendable balance
   * - LOCKED accounts: represent funds locked in orders
   * Total balance = sum(AVAILABLE) + sum(LOCKED)
   */
  private async calculateAccountBalance(
    tx: Omit<PrismaClient, '$connect' | '$disconnect' | '$on' | '$transaction' | '$use' | '$extends'>,
    accountId: string
  ): Promise<{ available: Decimal; locked: Decimal }> {
    const account = await tx.ledgerAccount.findUnique({
      where: { id: accountId },
      include: { entries: true },
    });

    if (!account) {
      throw new Error(`Account not found: ${accountId}`);
    }

    let balance = new Decimal(0);

    for (const entry of account.entries) {
      if (entry.direction === EntryDirection.DEBIT) {
        balance = balance.add(entry.amount);
      } else {
        balance = balance.sub(entry.amount);
      }
    }

    // Return balance based on account type
    // LOCKED accounts report as locked, all others as available
    if (account.accountType === LedgerAccountType.LOCKED) {
      return { available: new Decimal(0), locked: balance };
    }

    return { available: balance, locked: new Decimal(0) };
  }

  /**
   * Get or create a ledger account for a user and asset
   */
  async getOrCreateAccount(
    ownerId: string | null,
    ownerType: LedgerAccountOwnerType,
    accountType: LedgerAccountType,
    assetId: string
  ): Promise<{ accountId: string }> {
    // For SYSTEM accounts, use a special sentinel value to handle the unique constraint
    const ownerIdForQuery = ownerId ?? '__SYSTEM__';
    
    const account = await this.prisma.ledgerAccount.upsert({
      where: {
        ownerType_ownerId_accountType_assetId: {
          ownerType,
          ownerId: ownerIdForQuery,
          accountType,
          assetId,
        },
      },
      create: {
        ownerType,
        ownerId,
        accountType,
        assetId,
      },
      update: {},
    });

    return { accountId: account.id };
  }

  /**
   * Credit a deposit to a user's account
   */
  async creditDeposit(
    userId: string,
    assetId: string,
    amount: Decimal | string | number,
    externalRef: string,
    idempotencyKey?: string
  ): Promise<{ transactionId: string }> {
    // Get user's available account
    const userAccount = await this.getOrCreateAccount(
      userId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.AVAILABLE,
      assetId
    );

    // Get system clearing account
    const systemAccount = await this.getOrCreateAccount(
      null,
      LedgerAccountOwnerType.SYSTEM,
      LedgerAccountType.CLEARING,
      assetId
    );

    // Create balanced transaction: SYSTEM:CLEARING -> USER:AVAILABLE
    return this.createTransaction({
      type: LedgerTransactionType.DEPOSIT_CREDIT,
      entries: [
        {
          accountId: systemAccount.accountId,
          direction: EntryDirection.DEBIT,
          amount,
        },
        {
          accountId: userAccount.accountId,
          direction: EntryDirection.CREDIT,
          amount,
        },
      ],
      externalRef,
      idempotencyKey,
    });
  }

  /**
   * Lock funds for an order (move from AVAILABLE to LOCKED)
   */
  async lockFunds(
    userId: string,
    assetId: string,
    amount: Decimal | string | number,
    orderId: string
  ): Promise<{ transactionId: string }> {
    const availableAccount = await this.getOrCreateAccount(
      userId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.AVAILABLE,
      assetId
    );

    const lockedAccount = await this.getOrCreateAccount(
      userId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.LOCKED,
      assetId
    );

    // Create balanced transaction: USER:AVAILABLE -> USER:LOCKED
    return this.createTransaction({
      type: LedgerTransactionType.TRANSFER,
      entries: [
        {
          accountId: availableAccount.accountId,
          direction: EntryDirection.DEBIT,
          amount,
        },
        {
          accountId: lockedAccount.accountId,
          direction: EntryDirection.CREDIT,
          amount,
        },
      ],
      externalRef: orderId,
      metadata: { type: 'order_lock', orderId },
    });
  }

  /**
   * Unlock funds (move from LOCKED back to AVAILABLE)
   */
  async unlockFunds(
    userId: string,
    assetId: string,
    amount: Decimal | string | number,
    orderId: string
  ): Promise<{ transactionId: string }> {
    const availableAccount = await this.getOrCreateAccount(
      userId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.AVAILABLE,
      assetId
    );

    const lockedAccount = await this.getOrCreateAccount(
      userId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.LOCKED,
      assetId
    );

    // Create balanced transaction: USER:LOCKED -> USER:AVAILABLE
    return this.createTransaction({
      type: LedgerTransactionType.TRANSFER,
      entries: [
        {
          accountId: lockedAccount.accountId,
          direction: EntryDirection.DEBIT,
          amount,
        },
        {
          accountId: availableAccount.accountId,
          direction: EntryDirection.CREDIT,
          amount,
        },
      ],
      externalRef: orderId,
      metadata: { type: 'order_unlock', orderId },
    });
  }

  /**
   * Settle a trade (transfer assets between buyer and seller, collect fees)
   */
  async settleTrade(input: {
    buyerUserId: string;
    sellerUserId: string;
    baseAssetId: string;
    quoteAssetId: string;
    baseAmount: Decimal | string | number;
    quoteAmount: Decimal | string | number;
    buyerFee: Decimal | string | number;
    sellerFee: Decimal | string | number;
    tradeId: string;
  }): Promise<{ transactionId: string }> {
    const {
      buyerUserId,
      sellerUserId,
      baseAssetId,
      quoteAssetId,
      baseAmount,
      quoteAmount,
      buyerFee,
      sellerFee,
      tradeId,
    } = input;

    // Get accounts
    const buyerLockedQuote = await this.getOrCreateAccount(
      buyerUserId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.LOCKED,
      quoteAssetId
    );
    const buyerAvailableBase = await this.getOrCreateAccount(
      buyerUserId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.AVAILABLE,
      baseAssetId
    );
    const sellerLockedBase = await this.getOrCreateAccount(
      sellerUserId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.LOCKED,
      baseAssetId
    );
    const sellerAvailableQuote = await this.getOrCreateAccount(
      sellerUserId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.AVAILABLE,
      quoteAssetId
    );
    const feeAccountQuote = await this.getOrCreateAccount(
      null,
      LedgerAccountOwnerType.SYSTEM,
      LedgerAccountType.FEE,
      quoteAssetId
    );

    const entries: LedgerEntryInput[] = [];

    // Transfer base asset: seller locked -> buyer available
    entries.push(
      {
        accountId: sellerLockedBase.accountId,
        direction: EntryDirection.DEBIT,
        amount: baseAmount,
      },
      {
        accountId: buyerAvailableBase.accountId,
        direction: EntryDirection.CREDIT,
        amount: baseAmount,
      }
    );

    // Calculate net quote amounts after fees
    const buyerFeeDecimal = new Decimal(buyerFee.toString());
    const sellerFeeDecimal = new Decimal(sellerFee.toString());
    const quoteAmountDecimal = new Decimal(quoteAmount.toString());
    const totalFees = buyerFeeDecimal.add(sellerFeeDecimal);
    const netQuoteToSeller = quoteAmountDecimal.sub(sellerFeeDecimal);

    // Transfer quote asset: buyer locked -> seller available (minus fees)
    entries.push(
      {
        accountId: buyerLockedQuote.accountId,
        direction: EntryDirection.DEBIT,
        amount: quoteAmount,
      },
      {
        accountId: sellerAvailableQuote.accountId,
        direction: EntryDirection.CREDIT,
        amount: netQuoteToSeller.toString(),
      }
    );

    // Collect fees to system account
    if (totalFees.gt(0)) {
      entries.push({
        accountId: feeAccountQuote.accountId,
        direction: EntryDirection.CREDIT,
        amount: totalFees.toString(),
      });
    }

    return this.createTransaction({
      type: LedgerTransactionType.TRADE_SETTLE,
      entries,
      externalRef: tradeId,
      metadata: { tradeId },
    });
  }

  /**
   * Get account balance (from cache or calculate)
   */
  async getBalance(accountId: string): Promise<{ available: Decimal; locked: Decimal }> {
    const cache = await this.prisma.balanceCache.findUnique({
      where: { accountId },
    });

    if (cache) {
      return {
        available: cache.available,
        locked: cache.locked,
      };
    }

    // If no cache, calculate from entries
    return this.calculateAccountBalance(this.prisma, accountId);
  }

  /**
   * Get total user balance for an asset (aggregated across AVAILABLE and LOCKED accounts)
   * 
   * Use this method for user-facing balance queries to get the complete picture.
   */
  async getUserBalance(
    userId: string,
    assetId: string
  ): Promise<{ available: Decimal; locked: Decimal; total: Decimal }> {
    // Get available account balance
    const availableAccount = await this.getOrCreateAccount(
      userId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.AVAILABLE,
      assetId
    );
    const availableBalance = await this.getBalance(availableAccount.accountId);

    // Get locked account balance
    const lockedAccount = await this.getOrCreateAccount(
      userId,
      LedgerAccountOwnerType.USER,
      LedgerAccountType.LOCKED,
      assetId
    );
    const lockedBalance = await this.getBalance(lockedAccount.accountId);

    const available = availableBalance.available;
    const locked = lockedBalance.locked;
    const total = available.add(locked);

    return { available, locked, total };
  }
}
