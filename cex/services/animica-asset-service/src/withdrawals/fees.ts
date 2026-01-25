/**
 * Fee Estimation for Animica Withdrawals
 */

import type { Logger } from "pino";
import type { AnimicaRpcClient } from "../rpc/client.js";
import type { Config } from "../config.js";

export interface FeeEstimateResult {
  gas_limit: number;
  gas_price: string; // in atoms
  estimated_fee: string; // total fee in atoms
}

/**
 * Estimate fees for a withdrawal transaction
 */
export async function estimateFee(
  rpcClient: AnimicaRpcClient,
  config: Config,
  logger: Logger
): Promise<FeeEstimateResult> {
  // Use dynamic fee estimation if enabled
  if (config.ANIMICA_FEE_POLICY === "dynamic") {
    try {
      const feeEstimate = await rpcClient.estimateFee();
      
      // Apply min/max bounds
      const estimatedFee = BigInt(feeEstimate.estimated_fee);
      const minFee = BigInt(config.ANIMICA_MIN_FEE_ATOMS);
      const maxFee = BigInt(config.ANIMICA_MAX_FEE_ATOMS);
      
      let boundedFee = estimatedFee;
      if (estimatedFee < minFee) {
        boundedFee = minFee;
        logger.debug({ estimatedFee: estimatedFee.toString(), minFee: minFee.toString() }, "Fee below minimum, using min");
      } else if (estimatedFee > maxFee) {
        boundedFee = maxFee;
        logger.warn({ estimatedFee: estimatedFee.toString(), maxFee: maxFee.toString() }, "Fee above maximum, capping");
      }
      
      // Standard gas limit for simple transfer
      const gasLimit = 21000;
      
      return {
        gas_limit: gasLimit,
        gas_price: feeEstimate.gas_price,
        estimated_fee: boundedFee.toString(),
      };
    } catch (error) {
      logger.warn({ error }, "Dynamic fee estimation failed, falling back to fixed fee");
    }
  }
  
  // Fixed fee policy or fallback
  const fixedFee = config.ANIMICA_MIN_FEE_ATOMS;
  const gasLimit = 21000;
  
  // Calculate gas price ensuring no truncation
  // gasPrice = fixedFee / gasLimit (rounded up)
  const gasPrice = (BigInt(fixedFee) + BigInt(gasLimit) - 1n) / BigInt(gasLimit);
  
  logger.debug({ fixedFee, gasLimit, gasPrice: gasPrice.toString() }, "Using fixed fee policy");
  
  return {
    gas_limit: gasLimit,
    gas_price: gasPrice.toString(),
    estimated_fee: fixedFee,
  };
}
