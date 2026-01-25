/**
 * Tests for double-entry invariants
 */

import { describe, it, expect } from "vitest";
import { verifyBalanced, verifyPositiveAmounts } from "../domain/invariants.js";
import type { LedgerEntry } from "../domain/types.js";

describe("invariants", () => {
  describe("verifyBalanced", () => {
    it("passes for balanced entries (single asset)", () => {
      const entries: LedgerEntry[] = [
        {
          id: "1",
          transactionId: "tx1",
          accountId: "acc1",
          assetId: "USDT",
          direction: "DEBIT",
          amountAtoms: 1_000_000n,
          description: "test",
          createdAt: new Date()
        },
        {
          id: "2",
          transactionId: "tx1",
          accountId: "acc2",
          assetId: "USDT",
          direction: "CREDIT",
          amountAtoms: 1_000_000n,
          description: "test",
          createdAt: new Date()
        }
      ];

      const result = verifyBalanced(entries);
      expect(result.ok).toBe(true);
      expect(result.errors).toHaveLength(0);
    });

    it("passes for balanced entries (multiple assets)", () => {
      const entries: LedgerEntry[] = [
        // USDT entries
        {
          id: "1",
          transactionId: "tx1",
          accountId: "acc1",
          assetId: "USDT",
          direction: "DEBIT",
          amountAtoms: 1_000_000n,
          description: "test",
          createdAt: new Date()
        },
        {
          id: "2",
          transactionId: "tx1",
          accountId: "acc2",
          assetId: "USDT",
          direction: "CREDIT",
          amountAtoms: 1_000_000n,
          description: "test",
          createdAt: new Date()
        },
        // ANM entries
        {
          id: "3",
          transactionId: "tx1",
          accountId: "acc3",
          assetId: "ANM",
          direction: "DEBIT",
          amountAtoms: 500_000_000n,
          description: "test",
          createdAt: new Date()
        },
        {
          id: "4",
          transactionId: "tx1",
          accountId: "acc4",
          assetId: "ANM",
          direction: "CREDIT",
          amountAtoms: 500_000_000n,
          description: "test",
          createdAt: new Date()
        }
      ];

      const result = verifyBalanced(entries);
      expect(result.ok).toBe(true);
      expect(result.errors).toHaveLength(0);
    });

    it("fails for unbalanced entries", () => {
      const entries: LedgerEntry[] = [
        {
          id: "1",
          transactionId: "tx1",
          accountId: "acc1",
          assetId: "USDT",
          direction: "DEBIT",
          amountAtoms: 1_000_000n,
          description: "test",
          createdAt: new Date()
        },
        {
          id: "2",
          transactionId: "tx1",
          accountId: "acc2",
          assetId: "USDT",
          direction: "CREDIT",
          amountAtoms: 900_000n, // Imbalanced!
          description: "test",
          createdAt: new Date()
        }
      ];

      const result = verifyBalanced(entries);
      expect(result.ok).toBe(false);
      expect(result.errors.length).toBeGreaterThan(0);
      expect(result.errors[0]).toContain("not balanced");
    });
  });

  describe("verifyPositiveAmounts", () => {
    it("passes for all positive amounts", () => {
      const entries: LedgerEntry[] = [
        {
          id: "1",
          transactionId: "tx1",
          accountId: "acc1",
          assetId: "USDT",
          direction: "DEBIT",
          amountAtoms: 1_000_000n,
          description: "test",
          createdAt: new Date()
        },
        {
          id: "2",
          transactionId: "tx1",
          accountId: "acc2",
          assetId: "USDT",
          direction: "CREDIT",
          amountAtoms: 1n,
          description: "test",
          createdAt: new Date()
        }
      ];

      const result = verifyPositiveAmounts(entries);
      expect(result.ok).toBe(true);
    });

    it("fails for zero amounts", () => {
      const entries: LedgerEntry[] = [
        {
          id: "1",
          transactionId: "tx1",
          accountId: "acc1",
          assetId: "USDT",
          direction: "DEBIT",
          amountAtoms: 0n,
          description: "test",
          createdAt: new Date()
        }
      ];

      const result = verifyPositiveAmounts(entries);
      expect(result.ok).toBe(false);
      expect(result.errors.length).toBeGreaterThan(0);
    });

    it("fails for negative amounts", () => {
      const entries: LedgerEntry[] = [
        {
          id: "1",
          transactionId: "tx1",
          accountId: "acc1",
          assetId: "USDT",
          direction: "DEBIT",
          amountAtoms: -1000n,
          description: "test",
          createdAt: new Date()
        }
      ];

      const result = verifyPositiveAmounts(entries);
      expect(result.ok).toBe(false);
    });
  });
});
