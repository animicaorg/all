/**
 * Money/Atom utilities for deterministic BigInt arithmetic
 * 
 * All monetary values are stored as "atoms" - the smallest indivisible unit.
 * For example:
 * - 1 USDT = 1_000_000_000_000_000_000 atoms (18 decimals on BNB Smart Chain)
 * - 1 ANM = 1_000_000_000 atoms (9 decimals)
 * - 1 BTC = 100_000_000 atoms (8 decimals)
 * 
 * This avoids floating point precision issues entirely.
 */

/**
 * Asset decimal configuration
 */
export const ASSET_DECIMALS: Record<string, number> = {
  USDT: 18,
  USDC: 6,
  ANM: 9,
  BTC: 8,
  BNB: 18,
  LTC: 8,
  DOGE: 8,
  ZEC: 8,
  ETH: 18,
  SOL: 9,
};

/**
 * Get decimals for an asset (default 9 if unknown)
 */
export function getAssetDecimals(assetId: string): number {
  return ASSET_DECIMALS[assetId] ?? 9;
}

/**
 * Convert a decimal string or number to atoms
 * Example: decimalToAtoms("1.5", 6) => 1_500_000n
 */
export function decimalToAtoms(value: string | number, decimals: number): bigint {
  const str = typeof value === "number" ? value.toFixed(decimals) : value;
  const [whole, fraction = ""] = str.split(".");
  
  // Pad or truncate fraction to exact decimal places
  const paddedFraction = fraction.padEnd(decimals, "0").slice(0, decimals);
  const atomStr = whole + paddedFraction;
  
  return BigInt(atomStr);
}

/**
 * Convert atoms to decimal string
 * Example: atomsToDecimal(1_500_000n, 6) => "1.500000"
 */
export function atomsToDecimal(atoms: bigint, decimals: number): string {
  const atomStr = atoms.toString();
  const isNegative = atomStr.startsWith("-");
  const absStr = isNegative ? atomStr.slice(1) : atomStr;
  
  const padded = absStr.padStart(decimals + 1, "0");
  const whole = padded.slice(0, -decimals) || "0";
  const fraction = padded.slice(-decimals);
  
  return `${isNegative ? "-" : ""}${whole}.${fraction}`;
}

/**
 * Calculate fee in atoms using basis points
 * Example: calculateFeeBps(1_000_000n, 20) => 200n (0.2%)
 * Rounds UP to favor the exchange
 */
export function calculateFeeBps(amountAtoms: bigint, bps: number): bigint {
  const numerator = amountAtoms * BigInt(bps);
  const fee = numerator / 10000n;
  // Round up if there's a remainder
  const remainder = numerator % 10000n;
  return remainder > 0n ? fee + 1n : fee;
}

/**
 * Multiply atoms by a price (also in atoms)
 * Used for: sizeAtoms * priceAtoms => quoteAtoms
 * Must divide by 10^decimals to get correct units
 * 
 * Example: 
 * - size = 1.5 BTC = 150_000_000 atoms (8 decimals)
 * - price = 50_000 quote units with 8 price decimals
 * - quote = (150_000_000 * 5_000_000_000_000) / 10^8 = 7_500_000_000_000 quote atoms
 */
export function multiplyAtoms(
  amountAtoms: bigint,
  priceAtoms: bigint,
  priceDecimals: number
): bigint {
  return (amountAtoms * priceAtoms) / BigInt(10 ** priceDecimals);
}

/**
 * Add atoms safely (check for overflow would go here in production)
 */
export function addAtoms(a: bigint, b: bigint): bigint {
  return a + b;
}

/**
 * Subtract atoms safely (prevent negative results if needed)
 */
export function subtractAtoms(a: bigint, b: bigint): bigint {
  const result = a - b;
  if (result < 0n) {
    throw new Error(`Negative balance: ${a} - ${b} = ${result}`);
  }
  return result;
}

/**
 * Check if amount is positive
 */
export function isPositive(atoms: bigint): boolean {
  return atoms > 0n;
}

/**
 * Check if amount is zero
 */
export function isZero(atoms: bigint): boolean {
  return atoms === 0n;
}

/**
 * Compare atoms
 */
export function compareAtoms(a: bigint, b: bigint): number {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

/**
 * Format atoms for display (with currency symbol)
 */
export function formatAtoms(atoms: bigint, assetId: string): string {
  const decimals = getAssetDecimals(assetId);
  const decimal = atomsToDecimal(atoms, decimals);
  return `${decimal} ${assetId}`;
}

/**
 * Parse atoms from string (inverse of formatAtoms)
 */
export function parseAtoms(value: string, assetId: string): bigint {
  const decimals = getAssetDecimals(assetId);
  // Remove currency symbol if present
  const cleaned = value.replace(assetId, "").trim();
  return decimalToAtoms(cleaned, decimals);
}
