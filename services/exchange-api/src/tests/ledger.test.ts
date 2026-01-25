/**
 * Tests for Double-Entry Ledger Service
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { PrismaClient, LedgerAccountOwnerType, LedgerAccountType, EntryDirection } from '@prisma/client';
import { Decimal } from '@prisma/client/runtime/library';
import { LedgerService } from '../services/ledger.js';

const prisma = new PrismaClient();
const ledgerService = new LedgerService(prisma);

describe('LedgerService', () => {
  let testUserId: string;
  let testAssetId: string;

  beforeEach(async () => {
    // Create test user
    const user = await prisma.user.create({
      data: {
        email: `test-${Date.now()}@example.com`,
        status: 'ACTIVE',
        role: 'USER',
      },
    });
    testUserId = user.id;

    // Create test asset
    const asset = await prisma.asset.create({
      data: {
        symbol: `TST${Date.now()}`,
        name: 'Test Asset',
        decimals: 18,
        kind: 'NATIVE',
        isEnabled: true,
      },
    });
    testAssetId = asset.id;
  });

  afterEach(async () => {
    // Clean up test data
    await prisma.ledgerEntry.deleteMany({
      where: {
        account: {
          ownerId: testUserId,
        },
      },
    });
    await prisma.ledgerTransaction.deleteMany();
    await prisma.balanceCache.deleteMany({
      where: {
        account: {
          ownerId: testUserId,
        },
      },
    });
    await prisma.ledgerAccount.deleteMany({
      where: {
        ownerId: testUserId,
      },
    });
    await prisma.asset.delete({ where: { id: testAssetId } });
    await prisma.user.delete({ where: { id: testUserId } });
  });

  describe('Account Creation', () => {
    it('should create accounts for a user and asset', async () => {
      const { accountId: availableAccountId } = await ledgerService.getOrCreateAccount(
        testUserId,
        LedgerAccountOwnerType.USER,
        LedgerAccountType.AVAILABLE,
        testAssetId
      );

      expect(availableAccountId).toBeDefined();

      const account = await prisma.ledgerAccount.findUnique({
        where: { id: availableAccountId },
      });

      expect(account).toBeDefined();
      expect(account?.ownerId).toBe(testUserId);
      expect(account?.assetId).toBe(testAssetId);
      expect(account?.accountType).toBe(LedgerAccountType.AVAILABLE);
    });

    it('should return existing account on duplicate creation', async () => {
      const { accountId: accountId1 } = await ledgerService.getOrCreateAccount(
        testUserId,
        LedgerAccountOwnerType.USER,
        LedgerAccountType.AVAILABLE,
        testAssetId
      );

      const { accountId: accountId2 } = await ledgerService.getOrCreateAccount(
        testUserId,
        LedgerAccountOwnerType.USER,
        LedgerAccountType.AVAILABLE,
        testAssetId
      );

      expect(accountId1).toBe(accountId2);
    });
  });

  describe('Deposit Credit', () => {
    it('should create balanced deposit credit transaction', async () => {
      const depositAmount = new Decimal('100.50');
      const depositTxid = 'test-deposit-123';

      const { transactionId } = await ledgerService.creditDeposit(
        testUserId,
        testAssetId,
        depositAmount,
        depositTxid
      );

      // Verify transaction was created
      const transaction = await prisma.ledgerTransaction.findUnique({
        where: { id: transactionId },
        include: { entries: true },
      });

      expect(transaction).toBeDefined();
      expect(transaction?.entries).toHaveLength(2);

      // Verify entries are balanced
      const debits = transaction!.entries
        .filter((e) => e.direction === EntryDirection.DEBIT)
        .reduce((sum, e) => sum.add(e.amount), new Decimal(0));

      const credits = transaction!.entries
        .filter((e) => e.direction === EntryDirection.CREDIT)
        .reduce((sum, e) => sum.add(e.amount), new Decimal(0));

      expect(debits.equals(credits)).toBe(true);
      expect(debits.equals(depositAmount)).toBe(true);
    });

    it('should update user balance after deposit', async () => {
      const depositAmount = new Decimal('100.50');

      await ledgerService.creditDeposit(
        testUserId,
        testAssetId,
        depositAmount,
        'test-deposit-123'
      );

      // Get user's available account
      const { accountId } = await ledgerService.getOrCreateAccount(
        testUserId,
        LedgerAccountOwnerType.USER,
        LedgerAccountType.AVAILABLE,
        testAssetId
      );

      const balance = await ledgerService.getBalance(accountId);
      expect(balance.available.equals(depositAmount)).toBe(true);
    });

    it('should prevent duplicate deposits with same idempotency key', async () => {
      const depositAmount = new Decimal('100');
      const idempotencyKey = 'unique-deposit-key-123';

      await ledgerService.creditDeposit(
        testUserId,
        testAssetId,
        depositAmount,
        'txid-1',
        idempotencyKey
      );

      // Attempt duplicate deposit
      await expect(
        ledgerService.creditDeposit(
          testUserId,
          testAssetId,
          depositAmount,
          'txid-1',
          idempotencyKey
        )
      ).rejects.toThrow();
    });
  });

  describe('Order Lock', () => {
    beforeEach(async () => {
      // Fund the user account first
      await ledgerService.creditDeposit(
        testUserId,
        testAssetId,
        new Decimal('1000'),
        'initial-funding'
      );
    });

    it('should move funds from available to locked', async () => {
      const lockAmount = new Decimal('100');
      const orderId = 'test-order-123';

      const { transactionId } = await ledgerService.lockFunds(
        testUserId,
        testAssetId,
        lockAmount,
        orderId
      );

      expect(transactionId).toBeDefined();

      // Check available account
      const { accountId: availableAccountId } = await ledgerService.getOrCreateAccount(
        testUserId,
        LedgerAccountOwnerType.USER,
        LedgerAccountType.AVAILABLE,
        testAssetId
      );

      const availableBalance = await ledgerService.getBalance(availableAccountId);
      expect(availableBalance.available.equals(new Decimal('900'))).toBe(true);

      // Check locked account
      const { accountId: lockedAccountId } = await ledgerService.getOrCreateAccount(
        testUserId,
        LedgerAccountOwnerType.USER,
        LedgerAccountType.LOCKED,
        testAssetId
      );

      const lockedBalance = await ledgerService.getBalance(lockedAccountId);
      expect(lockedBalance.locked.equals(lockAmount)).toBe(true);
    });

    it('should create balanced lock transaction', async () => {
      const lockAmount = new Decimal('100');

      const { transactionId } = await ledgerService.lockFunds(
        testUserId,
        testAssetId,
        lockAmount,
        'order-123'
      );

      const transaction = await prisma.ledgerTransaction.findUnique({
        where: { id: transactionId },
        include: { entries: true },
      });

      const debits = transaction!.entries
        .filter((e) => e.direction === EntryDirection.DEBIT)
        .reduce((sum, e) => sum.add(e.amount), new Decimal(0));

      const credits = transaction!.entries
        .filter((e) => e.direction === EntryDirection.CREDIT)
        .reduce((sum, e) => sum.add(e.amount), new Decimal(0));

      expect(debits.equals(credits)).toBe(true);
    });
  });

  describe('Trade Settlement', () => {
    let buyerUserId: string;
    let sellerUserId: string;
    let baseAssetId: string;
    let quoteAssetId: string;

    beforeEach(async () => {
      // Create buyer
      const buyer = await prisma.user.create({
        data: {
          email: `buyer-${Date.now()}@example.com`,
          status: 'ACTIVE',
          role: 'USER',
        },
      });
      buyerUserId = buyer.id;

      // Create seller
      const seller = await prisma.user.create({
        data: {
          email: `seller-${Date.now()}@example.com`,
          status: 'ACTIVE',
          role: 'USER',
        },
      });
      sellerUserId = seller.id;

      // Create base asset (e.g., BTC)
      const baseAsset = await prisma.asset.create({
        data: {
          symbol: `BTC${Date.now()}`,
          name: 'Bitcoin',
          decimals: 8,
          kind: 'NATIVE',
        },
      });
      baseAssetId = baseAsset.id;

      // Create quote asset (e.g., USD)
      const quoteAsset = await prisma.asset.create({
        data: {
          symbol: `USD${Date.now()}`,
          name: 'US Dollar',
          decimals: 2,
          kind: 'NATIVE',
        },
      });
      quoteAssetId = quoteAsset.id;

      // Fund buyer with quote asset
      await ledgerService.creditDeposit(buyerUserId, quoteAssetId, '10000', 'buyer-funding');
      await ledgerService.lockFunds(buyerUserId, quoteAssetId, '10000', 'buyer-lock');

      // Fund seller with base asset
      await ledgerService.creditDeposit(sellerUserId, baseAssetId, '1', 'seller-funding');
      await ledgerService.lockFunds(sellerUserId, baseAssetId, '1', 'seller-lock');
    });

    afterEach(async () => {
      // Clean up
      await prisma.ledgerEntry.deleteMany({
        where: {
          OR: [
            { account: { ownerId: buyerUserId } },
            { account: { ownerId: sellerUserId } },
          ],
        },
      });
      await prisma.balanceCache.deleteMany({
        where: {
          OR: [
            { account: { ownerId: buyerUserId } },
            { account: { ownerId: sellerUserId } },
          ],
        },
      });
      await prisma.ledgerAccount.deleteMany({
        where: {
          OR: [{ ownerId: buyerUserId }, { ownerId: sellerUserId }],
        },
      });
      await prisma.asset.deleteMany({
        where: { id: { in: [baseAssetId, quoteAssetId] } },
      });
      await prisma.user.deleteMany({
        where: { id: { in: [buyerUserId, sellerUserId] } },
      });
    });

    it('should settle trade with balanced entries', async () => {
      const { transactionId } = await ledgerService.settleTrade({
        buyerUserId,
        sellerUserId,
        baseAssetId,
        quoteAssetId,
        baseAmount: '0.5',
        quoteAmount: '5000',
        buyerFee: '50',
        sellerFee: '25',
        tradeId: 'trade-123',
      });

      // Verify transaction is balanced
      const transaction = await prisma.ledgerTransaction.findUnique({
        where: { id: transactionId },
        include: {
          entries: {
            include: {
              account: true,
            },
          },
        },
      });

      // Group by asset and verify balance
      const baseEntries = transaction!.entries.filter(
        (e) => e.account.assetId === baseAssetId
      );
      const quoteEntries = transaction!.entries.filter(
        (e) => e.account.assetId === quoteAssetId
      );

      const baseDebits = baseEntries
        .filter((e) => e.direction === EntryDirection.DEBIT)
        .reduce((sum, e) => sum.add(e.amount), new Decimal(0));
      const baseCredits = baseEntries
        .filter((e) => e.direction === EntryDirection.CREDIT)
        .reduce((sum, e) => sum.add(e.amount), new Decimal(0));

      const quoteDebits = quoteEntries
        .filter((e) => e.direction === EntryDirection.DEBIT)
        .reduce((sum, e) => sum.add(e.amount), new Decimal(0));
      const quoteCredits = quoteEntries
        .filter((e) => e.direction === EntryDirection.CREDIT)
        .reduce((sum, e) => sum.add(e.amount), new Decimal(0));

      expect(baseDebits.equals(baseCredits)).toBe(true);
      expect(quoteDebits.equals(quoteCredits)).toBe(true);
    });

    it('should result in expected net changes after trade', async () => {
      // Get initial balances
      const { accountId: buyerBaseAvailId } = await ledgerService.getOrCreateAccount(
        buyerUserId,
        LedgerAccountOwnerType.USER,
        LedgerAccountType.AVAILABLE,
        baseAssetId
      );
      const initialBuyerBase = await ledgerService.getBalance(buyerBaseAvailId);

      await ledgerService.settleTrade({
        buyerUserId,
        sellerUserId,
        baseAssetId,
        quoteAssetId,
        baseAmount: '0.5',
        quoteAmount: '5000',
        buyerFee: '50',
        sellerFee: '25',
        tradeId: 'trade-123',
      });

      // Verify buyer received base asset
      const finalBuyerBase = await ledgerService.getBalance(buyerBaseAvailId);
      const baseIncrease = finalBuyerBase.available.sub(initialBuyerBase.available);
      expect(baseIncrease.equals(new Decimal('0.5'))).toBe(true);
    });
  });

  describe('Balance Validation', () => {
    it('should reject unbalanced transactions', async () => {
      const { accountId: account1 } = await ledgerService.getOrCreateAccount(
        testUserId,
        LedgerAccountOwnerType.USER,
        LedgerAccountType.AVAILABLE,
        testAssetId
      );

      const { accountId: account2 } = await ledgerService.getOrCreateAccount(
        null,
        LedgerAccountOwnerType.SYSTEM,
        LedgerAccountType.CLEARING,
        testAssetId
      );

      // Create unbalanced entries (100 debit vs 90 credit)
      await expect(
        ledgerService.createTransaction({
          type: 'TRANSFER',
          entries: [
            { accountId: account1, direction: EntryDirection.DEBIT, amount: '100' },
            { accountId: account2, direction: EntryDirection.CREDIT, amount: '90' },
          ],
        })
      ).rejects.toThrow(/unbalanced/i);
    });

    it('should reject negative amounts', async () => {
      const { accountId } = await ledgerService.getOrCreateAccount(
        testUserId,
        LedgerAccountOwnerType.USER,
        LedgerAccountType.AVAILABLE,
        testAssetId
      );

      await expect(
        ledgerService.createTransaction({
          type: 'TRANSFER',
          entries: [
            { accountId, direction: EntryDirection.DEBIT, amount: '-100' },
            { accountId, direction: EntryDirection.CREDIT, amount: '-100' },
          ],
        })
      ).rejects.toThrow(/positive/i);
    });
  });
});
