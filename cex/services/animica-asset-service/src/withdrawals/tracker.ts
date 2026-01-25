/**
 * Track Withdrawal Status
 * 
 * Polls blockchain for transaction confirmations
 */

import type { Logger } from "pino";
import type { AnimicaRpcClient } from "../rpc/client.js";

export interface WithdrawalStatus {
  txid: string;
  confirmations: number;
  status: "pending" | "confirmed" | "failed";
  block_height?: number;
  block_hash?: string;
  error?: string;
}

/**
 * Check withdrawal status by transaction ID
 */
export async function checkWithdrawalStatus(
  txid: string,
  requiredConfirmations: number,
  rpcClient: AnimicaRpcClient,
  logger: Logger
): Promise<WithdrawalStatus> {
  try {
    logger.debug({ txid }, "Checking withdrawal status");
    
    const txInfo = await rpcClient.getTransaction(txid);
    
    if (!txInfo) {
      return {
        txid,
        confirmations: 0,
        status: "pending",
      };
    }
    
    const confirmations = txInfo.confirmations || 0;
    
    // Determine status
    let status: "pending" | "confirmed" | "failed";
    if (txInfo.status === "failed") {
      status = "failed";
    } else if (confirmations >= requiredConfirmations) {
      status = "confirmed";
    } else {
      status = "pending";
    }
    
    logger.debug(
      {
        txid,
        confirmations,
        status,
        block_height: txInfo.block_height,
      },
      "Withdrawal status checked"
    );
    
    return {
      txid,
      confirmations,
      status,
      block_height: txInfo.block_height,
      block_hash: txInfo.block_hash,
    };
  } catch (error: any) {
    logger.warn({ error, txid }, "Failed to check withdrawal status");
    
    // If transaction not found, it might still be pending
    if (error.message?.includes("not found") || error.code === -5) {
      return {
        txid,
        confirmations: 0,
        status: "pending",
      };
    }
    
    return {
      txid,
      confirmations: 0,
      status: "pending",
      error: error.message,
    };
  }
}

/**
 * Wait for transaction confirmation (blocking)
 */
export async function waitForConfirmation(
  txid: string,
  requiredConfirmations: number,
  rpcClient: AnimicaRpcClient,
  logger: Logger,
  options: {
    maxAttempts?: number;
    pollIntervalMs?: number;
  } = {}
): Promise<WithdrawalStatus> {
  const maxAttempts = options.maxAttempts || 60;
  const pollIntervalMs = options.pollIntervalMs || 5000;
  
  logger.info(
    { txid, requiredConfirmations, maxAttempts, pollIntervalMs },
    "Waiting for transaction confirmation"
  );
  
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const status = await checkWithdrawalStatus(
      txid,
      requiredConfirmations,
      rpcClient,
      logger
    );
    
    if (status.status === "confirmed" || status.status === "failed") {
      logger.info(
        { txid, status: status.status, confirmations: status.confirmations },
        "Transaction reached final status"
      );
      return status;
    }
    
    if (attempt < maxAttempts) {
      logger.debug(
        { txid, attempt, maxAttempts, confirmations: status.confirmations },
        "Waiting for more confirmations"
      );
      await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
    }
  }
  
  logger.warn({ txid, maxAttempts }, "Reached max attempts waiting for confirmation");
  
  return await checkWithdrawalStatus(txid, requiredConfirmations, rpcClient, logger);
}
