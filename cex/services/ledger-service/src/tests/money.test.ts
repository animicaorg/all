/**
 * Tests for money/atom utilities
 */

import { describe, it, expect } from "vitest";
import {
  decimalToAtoms,
  atomsToDecimal,
  calculateFeeBps,
  multiplyAtoms,
  addAtoms,
  subtractAtoms,
  formatAtoms,
  parseAtoms,
  getAssetDecimals
} from "../domain/money.js";

describe("money utilities", () => {
  describe("decimalToAtoms", () => {
    it("converts decimal string to atoms", () => {
      expect(decimalToAtoms("1.5", 6)).toBe(1_500_000n);
      expect(decimalToAtoms("0.01", 6)).toBe(10_000n);
      expect(decimalToAtoms("1000", 6)).toBe(1_000_000_000n);
    });

    it("handles different decimal places", () => {
      expect(decimalToAtoms("1.0", 8)).toBe(100_000_000n);
      expect(decimalToAtoms("0.00000001", 8)).toBe(1n);
    });
  });

  describe("atomsToDecimal", () => {
    it("converts atoms to decimal string", () => {
      expect(atomsToDecimal(1_500_000n, 6)).toBe("1.500000");
      expect(atomsToDecimal(10_000n, 6)).toBe("0.010000");
      expect(atomsToDecimal(1_000_000_000n, 6)).toBe("1000.000000");
    });

    it("handles zero", () => {
      expect(atomsToDecimal(0n, 6)).toBe("0.000000");
    });
  });

  describe("calculateFeeBps", () => {
    it("calculates fee with basis points", () => {
      // 1_000_000 atoms * 20 bps = 2000 atoms (0.2%)
      expect(calculateFeeBps(1_000_000n, 20)).toBe(2000n);
    });

    it("rounds up when there's a remainder", () => {
      // 1_000_001 atoms * 20 bps = 2000.002 atoms => rounds to 2001
      expect(calculateFeeBps(1_000_001n, 20)).toBe(2001n);
    });

    it("handles zero", () => {
      expect(calculateFeeBps(0n, 20)).toBe(0n);
    });
  });

  describe("multiplyAtoms", () => {
    it("multiplies atoms by price", () => {
      // 1.5 BTC (8 decimals) * 50_000 quote units with 8 price decimals
      const sizeAtoms = 150_000_000n; // 1.5 BTC
      const priceAtoms = 5_000_000_000_000n;
      const result = multiplyAtoms(sizeAtoms, priceAtoms, 8);
      expect(result).toBe(7_500_000_000_000n);
    });
  });

  describe("addAtoms", () => {
    it("adds two atom values", () => {
      expect(addAtoms(1_000_000n, 500_000n)).toBe(1_500_000n);
    });
  });

  describe("subtractAtoms", () => {
    it("subtracts atoms", () => {
      expect(subtractAtoms(1_000_000n, 500_000n)).toBe(500_000n);
    });

    it("throws on negative result", () => {
      expect(() => subtractAtoms(500_000n, 1_000_000n)).toThrow("Negative balance");
    });
  });

  describe("formatAtoms and parseAtoms", () => {
    it("formats and parses round-trip", () => {
      const atoms = 1_500_000_000_000_000_000n;
      const formatted = formatAtoms(atoms, "USDT");
      expect(formatted).toBe("1.500000000000000000 USDT");
      
      const parsed = parseAtoms(formatted, "USDT");
      expect(parsed).toBe(atoms);
    });
  });

  describe("asset decimals", () => {
    it("uses 8 decimals for supported BitGo UTXO assets", () => {
      expect(getAssetDecimals("BTC")).toBe(8);
      expect(getAssetDecimals("LTC")).toBe(8);
      expect(getAssetDecimals("DOGE")).toBe(8);
      expect(getAssetDecimals("ZEC")).toBe(8);
      expect(formatAtoms(8_694_835n, "LTC")).toBe("0.08694835 LTC");
    });

    it("uses 18 decimals for BNB Smart Chain USDT", () => {
      expect(getAssetDecimals("USDT")).toBe(18);
    });
  });
});
