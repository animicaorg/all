/**
 * Fee calculation module
 * Implements deterministic maker/taker fee calculation
 */

import { calculateFee } from "./deterministic.js";
import type { MarketConfig } from "./types.js";

export interface FeeCalculation {
  makerFeeAtoms: bigint;
  takerFeeAtoms: bigint;
  feeBpsMaker: number;
  feeBpsTaker: number;
  feeAsset: string;
}

/**
 * Calculate maker and taker fees for a trade
 * 
 * Fees are calculated on the quote amount (price * size)
 * Rounding policy: Always round UP to ensure no dust accumulation
 * 
 * @param quoteAmountAtoms - quote amount in atoms (price * size)
 * @param market - market configuration
 * @returns fee calculation result
 */
export function calculateTradeFees(
  quoteAmountAtoms: bigint,
  market: MarketConfig
): FeeCalculation {
  const makerFeeAtoms = calculateFee(quoteAmountAtoms, market.makerFeeBps);
  const takerFeeAtoms = calculateFee(quoteAmountAtoms, market.takerFeeBps);

  return {
    makerFeeAtoms,
    takerFeeAtoms,
    feeBpsMaker: market.makerFeeBps,
    feeBpsTaker: market.takerFeeBps,
    feeAsset: market.feeAsset
  };
}

/**
 * Calculate total fees for multiple fills
 */
export function aggregateFees(
  fees: Array<{ makerFeeAtoms: bigint; takerFeeAtoms: bigint }>
): { totalMakerFee: bigint; totalTakerFee: bigint } {
  let totalMakerFee = 0n;
  let totalTakerFee = 0n;

  for (const fee of fees) {
    totalMakerFee += fee.makerFeeAtoms;
    totalTakerFee += fee.takerFeeAtoms;
  }

  return { totalMakerFee, totalTakerFee };
}
