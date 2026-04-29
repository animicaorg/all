/**
 * Deterministic utilities for the matching engine
 * Ensures all operations are reproducible and avoid floating point issues
 */

/**
 * Convert decimal string to atoms (BigInt)
 * @param decimal - decimal string (e.g., "123.45")
 * @param decimals - number of decimal places (e.g., 8)
 * @returns atoms as BigInt
 */
export function decimalToAtoms(decimal: string, decimals: number): bigint {
  const [integer, fraction = ""] = decimal.split(".");
  const paddedFraction = fraction.padEnd(decimals, "0").slice(0, decimals);
  return BigInt(integer + paddedFraction);
}

/**
 * Convert atoms to decimal string
 * @param atoms - amount in atoms
 * @param decimals - number of decimal places
 * @returns decimal string (trailing zeros stripped)
 */
export function atomsToDecimal(atoms: bigint, decimals: number): string {
  const str = atoms.toString().padStart(decimals + 1, "0");
  const integer = str.slice(0, -decimals) || "0";
  const fraction = str.slice(-decimals);
  
  // Strip trailing zeros
  const strippedFraction = fraction.replace(/0+$/, "");
  
  return strippedFraction === "" ? integer : `${integer}.${strippedFraction}`;
}

/**
 * Calculate quote amount from price and size (both in atoms)
 * quote_amount = price * size / (10^decimals)
 * @param priceAtoms - price in atoms
 * @param sizeAtoms - size in atoms
 * @param priceDecimals - price decimals (typically 8)
 * @returns quote amount in atoms
 */
export function calculateQuoteAmount(
  priceAtoms: bigint,
  sizeAtoms: bigint,
  priceDecimals: number
): bigint {
  return (priceAtoms * sizeAtoms) / BigInt(10 ** priceDecimals);
}

/**
 * Calculate fee in atoms, always rounding up
 * @param amount - amount in atoms
 * @param feeBps - fee in basis points (1 bp = 0.01%)
 * @returns fee in atoms, rounded up
 */
export function calculateFee(amount: bigint, feeBps: number): bigint {
  const fee = (amount * BigInt(feeBps)) / 10000n;
  const remainder = (amount * BigInt(feeBps)) % 10000n;
  return remainder > 0n ? fee + 1n : fee;
}

/**
 * Validate that value is a multiple of step
 * @param value - value to validate
 * @param step - step size
 * @returns true if valid
 */
export function isValidStep(value: bigint, step: bigint): boolean {
  return value % step === 0n;
}

/**
 * Deterministic order comparison for FIFO at same price
 * Returns:
 *   < 0 if a comes before b
 *   > 0 if b comes before a
 *   = 0 if equal (should not happen)
 */
export function compareOrders(a: any, b: any): number {
  // First compare by accepted_at timestamp
  const timeA = a.acceptedAt.getTime();
  const timeB = b.acceptedAt.getTime();
  if (timeA !== timeB) {
    return timeA - timeB;
  }

  // If timestamps are equal, compare by order_id (lexicographic)
  return a.id.localeCompare(b.id);
}

/**
 * Deterministic price comparison for bids (highest first)
 */
export function compareBidPrices(a: bigint, b: bigint): number {
  if (a > b) return -1;
  if (a < b) return 1;
  return 0;
}

/**
 * Deterministic price comparison for asks (lowest first)
 */
export function compareAskPrices(a: bigint, b: bigint): number {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

/**
 * Generate a deterministic trade ID from components
 */
export function generateTradeId(
  marketId: string,
  sequence: bigint,
  makerOrderId: string,
  takerOrderId: string
): string {
  // Use sequence as primary key component for determinism
  return `${marketId}-${sequence}-${makerOrderId.slice(0, 8)}-${takerOrderId.slice(0, 8)}`;
}

/**
 * Generate a deterministic event key for deduplication
 */
export function generateEventKey(
  marketId: string,
  eventType: string,
  sequence: bigint,
  orderId?: string
): string {
  return orderId 
    ? `${marketId}:${eventType}:${sequence}:${orderId}`
    : `${marketId}:${eventType}:${sequence}`;
}
type Order = any;
