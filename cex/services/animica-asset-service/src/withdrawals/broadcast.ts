/**
 * Broadcast Transaction to Animica Network
 */

import type { Logger } from "pino";
import type { AnimicaRpcClient } from "../rpc/client.js";

export interface BroadcastResult {
  success: boolean;
  txid?: string;
  error?: string;
}

/**
 * Broadcast a signed transaction to the network
 */
export async function broadcastTransaction(
  rawTx: string,
  rpcClient: AnimicaRpcClient,
  logger: Logger
): Promise<BroadcastResult> {
  try {
    logger.info({ rawTx: rawTx.slice(0, 16) + "..." }, "Broadcasting transaction");
    
    // If rawTx looks like a txid (from wallet.send), it's already broadcast
    if (rawTx.match(/^[0-9a-f]{64}$/i)) {
      logger.info({ txid: rawTx }, "Transaction already broadcast via wallet.send");
      return {
        success: true,
        txid: rawTx,
      };
    }
    
    // Otherwise, send raw transaction
    const txid = await rpcClient.sendRawTransaction(rawTx);
    
    logger.info({ txid }, "Transaction broadcast successful");
    
    return {
      success: true,
      txid,
    };
  } catch (error: any) {
    logger.error({ error, rawTx: rawTx.slice(0, 16) + "..." }, "Failed to broadcast transaction");
    
    return {
      success: false,
      error: error.message || "Unknown broadcast error",
    };
  }
}
