/**
 * Tests for Ledger Invariants
 */

import { describe, it, expect } from 'vitest';
import { Decimal } from '@prisma/client/runtime/library';
import { EntryDirection, LedgerAccountType } from '@prisma/client';
import {
  validateDoubleEntry,
  validateOrderLock,
  calculateBuyOrderLock,
  calculateSellOrderLock,
  validateTradeSettlement,
  validateAccountTypeRules,
  isEqual,
  validateNonNegativeBalance,
  InvariantViolationError,
} from '../invariants/ledger.js';

describe('Ledger Invariants', () => {
  describe('validateDoubleEntry', () => {
    it('should validate balanced entries', () => {
      const entries = [
        { assetId: 'asset1', direction: EntryDirection.DEBIT, amount: new Decimal('100') },
        { assetId: 'asset1', direction: EntryDirection.CREDIT, amount: new Decimal('100') },
      ];

      const result = validateDoubleEntry(entries);
      expect(result.valid).toBe(true);
      expect(result.errors).toHaveLength(0);
    });

    it('should reject unbalanced entries', () => {
      const entries = [
        { assetId: 'asset1', direction: EntryDirection.DEBIT, amount: new Decimal('100') },
        { assetId: 'asset1', direction: EntryDirection.CREDIT, amount: new Decimal('90') },
      ];

      const result = validateDoubleEntry(entries);
      expect(result.valid).toBe(false);
      expect(result.errors.length).toBeGreaterThan(0);
      expect(result.errors[0]).toContain('unbalanced');
    });

    it('should validate multiple assets independently', () => {
      const entries = [
        { assetId: 'asset1', direction: EntryDirection.DEBIT, amount: new Decimal('100') },
        { assetId: 'asset1', direction: EntryDirection.CREDIT, amount: new Decimal('100') },
        { assetId: 'asset2', direction: EntryDirection.DEBIT, amount: new Decimal('50') },
        { assetId: 'asset2', direction: EntryDirection.CREDIT, amount: new Decimal('50') },
      ];

      const result = validateDoubleEntry(entries);
      expect(result.valid).toBe(true);
    });

    it('should reject negative amounts', () => {
      const entries = [
        { assetId: 'asset1', direction: EntryDirection.DEBIT, amount: new Decimal('-100') },
        { assetId: 'asset1', direction: EntryDirection.CREDIT, amount: new Decimal('-100') },
      ];

      const result = validateDoubleEntry(entries);
      expect(result.valid).toBe(false);
      expect(result.errors.some((e) => e.includes('positive'))).toBe(true);
    });

    it('should reject zero amounts', () => {
      const entries = [
        { assetId: 'asset1', direction: EntryDirection.DEBIT, amount: new Decimal('0') },
        { assetId: 'asset1', direction: EntryDirection.CREDIT, amount: new Decimal('0') },
      ];

      const result = validateDoubleEntry(entries);
      expect(result.valid).toBe(false);
    });
  });

  describe('validateOrderLock', () => {
    it('should validate sufficient funds', () => {
      const result = validateOrderLock(
        new Decimal('1000'),
        new Decimal('100'),
        new Decimal('200')
      );

      expect(result.valid).toBe(true);
    });

    it('should reject insufficient funds', () => {
      const result = validateOrderLock(
        new Decimal('1000'),
        new Decimal('900'),
        new Decimal('200')
      );

      expect(result.valid).toBe(false);
      expect(result.error).toContain('Insufficient funds');
    });

    it('should handle zero locked balance', () => {
      const result = validateOrderLock(
        new Decimal('1000'),
        new Decimal('500')
      );

      expect(result.valid).toBe(true);
    });
  });

  describe('calculateBuyOrderLock', () => {
    it('should calculate correct lock amount with fees', () => {
      const price = new Decimal('50000');
      const size = new Decimal('0.1');
      const feeRateBps = 30; // 0.3%

      const lockAmount = calculateBuyOrderLock(price, size, feeRateBps);

      // Expected: 50000 * 0.1 = 5000 + (5000 * 0.003) = 5015
      expect(lockAmount.equals(new Decimal('5015'))).toBe(true);
    });

    it('should handle zero fee rate', () => {
      const price = new Decimal('1000');
      const size = new Decimal('1');
      const feeRateBps = 0;

      const lockAmount = calculateBuyOrderLock(price, size, feeRateBps);
      expect(lockAmount.equals(new Decimal('1000'))).toBe(true);
    });
  });

  describe('calculateSellOrderLock', () => {
    it('should return size for sell orders', () => {
      const size = new Decimal('0.5');
      const lockAmount = calculateSellOrderLock(size);

      expect(lockAmount.equals(size)).toBe(true);
    });
  });

  describe('validateTradeSettlement', () => {
    it('should validate correct trade settlement', () => {
      const result = validateTradeSettlement(
        new Decimal('0.1'), // baseAmount
        new Decimal('5000'), // quoteAmount
        new Decimal('50000'), // price
        new Decimal('10'), // makerFee
        new Decimal('15') // takerFee
      );

      expect(result.valid).toBe(true);
      expect(result.errors).toHaveLength(0);
    });

    it('should reject mismatched quote amount', () => {
      const result = validateTradeSettlement(
        new Decimal('0.1'),
        new Decimal('4999'), // Wrong quote amount
        new Decimal('50000'),
        new Decimal('10'),
        new Decimal('15')
      );

      expect(result.valid).toBe(false);
      expect(result.errors.some((e) => e.includes('mismatch'))).toBe(true);
    });

    it('should reject negative fees', () => {
      const result = validateTradeSettlement(
        new Decimal('0.1'),
        new Decimal('5000'),
        new Decimal('50000'),
        new Decimal('-10'), // Negative fee
        new Decimal('15')
      );

      expect(result.valid).toBe(false);
      expect(result.errors.some((e) => e.includes('negative'))).toBe(true);
    });
  });

  describe('validateAccountTypeRules', () => {
    it('should allow valid operations on AVAILABLE accounts', () => {
      const debitResult = validateAccountTypeRules(LedgerAccountType.AVAILABLE, 'debit');
      const creditResult = validateAccountTypeRules(LedgerAccountType.AVAILABLE, 'credit');

      expect(debitResult.valid).toBe(true);
      expect(creditResult.valid).toBe(true);
    });

    it('should allow valid operations on LOCKED accounts', () => {
      const debitResult = validateAccountTypeRules(LedgerAccountType.LOCKED, 'debit');
      const creditResult = validateAccountTypeRules(LedgerAccountType.LOCKED, 'credit');

      expect(debitResult.valid).toBe(true);
      expect(creditResult.valid).toBe(true);
    });

    it('should allow operations on FEE accounts', () => {
      const debitResult = validateAccountTypeRules(LedgerAccountType.FEE, 'debit');
      const creditResult = validateAccountTypeRules(LedgerAccountType.FEE, 'credit');

      expect(debitResult.valid).toBe(true);
      expect(creditResult.valid).toBe(true);
    });
  });

  describe('isEqual', () => {
    it('should return true for equal decimals', () => {
      expect(isEqual(new Decimal('100'), new Decimal('100'))).toBe(true);
    });

    it('should return false for different decimals', () => {
      expect(isEqual(new Decimal('100'), new Decimal('101'))).toBe(false);
    });

    it('should handle tolerance for floating point comparison', () => {
      const a = new Decimal('100.000000000000000001');
      const b = new Decimal('100');

      expect(isEqual(a, b, new Decimal('0.000000000000001'))).toBe(true);
    });
  });

  describe('validateNonNegativeBalance', () => {
    it('should accept positive balance', () => {
      const result = validateNonNegativeBalance(new Decimal('100'));
      expect(result.valid).toBe(true);
    });

    it('should accept zero balance', () => {
      const result = validateNonNegativeBalance(new Decimal('0'));
      expect(result.valid).toBe(true);
    });

    it('should reject negative balance', () => {
      const result = validateNonNegativeBalance(new Decimal('-0.01'));
      expect(result.valid).toBe(false);
      expect(result.error).toContain('negative');
    });
  });

  describe('InvariantViolationError', () => {
    it('should create error with invariant name', () => {
      const error = new InvariantViolationError('Test violation', 'DOUBLE_ENTRY_BALANCE');
      
      expect(error.message).toBe('Test violation');
      expect(error.invariant).toBe('DOUBLE_ENTRY_BALANCE');
      expect(error.name).toBe('InvariantViolationError');
    });
  });
});
